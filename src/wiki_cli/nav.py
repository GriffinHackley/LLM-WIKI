"""Traversal sessions: start from search, read sections, follow relations, within limits.

A session remembers the question (and its vector), the pages read, and every
candidate shown, so the tool can refuse revisits, enforce a page limit, and offer
earlier candidates when the current page leads nowhere. Sessions live in the
cache database and expire after SESSION_TTL_DAYS.
"""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass

from wiki_cli import links, vec
from wiki_cli.cache import Cache
from wiki_cli.pages import Resolver
from wiki_cli.models import Embedder, ModelUnavailable, Reranker
from wiki_cli.search import RERANK_CHARS, SearchResult, keyword_query, search

DEFAULT_MAX_PAGES = 6
MAX_WHY = 400
MAX_REQUERIES = 3
MAX_CHARS = 4000  # per response: small enough that an agent's shell shows all of it
MAX_PARTS = 5
SESSION_TTL_DAYS = 14
LINKED_LIMIT = 8
SIMILAR_LIMIT = 4
EARLIER_LIMIT = 3
CANDIDATE_SUMMARY_CHARS = 200  # candidate lists show many pages; keep each entry short
MIN_SECTION_CHARS = 200  # the default section is chosen among sections at least this long
PART_RESULTS = 2  # pages listed per question part at `nav start`

# Reranker score bands for "how well does the wiki cover this?": (good at or above, none below).
# From docs/model-selection.md: jina-turbo's median top score is +0.70 for answerable questions
# and -1.21 for unanswerable ones; MiniLM's +5.3 and -5.0. Unmeasured rerankers say "unknown".
COVERAGE_BANDS = {
    "jinaai/jina-reranker-v1-turbo-en": (0.0, -1.0),
    "Xenova/ms-marco-MiniLM-L-12-v2": (0.0, -3.0),
    "fake:overlap": (0.5, 0.2),
}

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS nav_sessions (
        id TEXT PRIMARY KEY,
        question TEXT NOT NULL,
        max_pages INTEGER NOT NULL,
        query_vector BLOB,
        embed_model TEXT,
        current TEXT,
        ended INTEGER NOT NULL DEFAULT 0,
        cited TEXT,
        created REAL NOT NULL,
        updated REAL NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS nav_events (
        session_id TEXT NOT NULL,
        seq INTEGER NOT NULL,
        at REAL NOT NULL,
        kind TEXT NOT NULL,
        slug TEXT,
        detail TEXT,
        PRIMARY KEY (session_id, seq))""",
    """CREATE TABLE IF NOT EXISTS nav_frontier (
        session_id TEXT NOT NULL,
        slug TEXT NOT NULL,
        score REAL NOT NULL,
        source TEXT NOT NULL,
        reason TEXT,
        PRIMARY KEY (session_id, slug))""",
)
TABLES = ("nav_frontier", "nav_events", "nav_sessions")


class NavError(Exception):
    """A refused navigation step. ``code`` is stable for agents to branch on."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class Session:
    id: str
    question: str
    max_pages: int
    query_vector: bytes | None
    embed_model: str | None
    current: str | None
    ended: bool


@dataclass
class _Page:
    """A page's text and its sections, as the cache chunked them."""
    text: str
    rows: list[tuple]  # (chunk id, heading path, start, end), in order
    headings: list[str]
    spans: dict[str, tuple[int, int]]  # heading path -> (start, end) of the whole section
    label: object  # heading path -> the section's name (see _labeler)
    outline: list[tuple[str, int]]  # (section name, characters)
    summary_heading: str | None  # the section the page's summary comes from


class Navigator:
    def __init__(self, cache: Cache, *, embedder: Embedder | None = None, reranker: Reranker | None = None):
        self.cache = cache
        self.conn = cache.conn
        self.embedder = embedder
        self.reranker = reranker
        self._resolver: Resolver | None = None  # built on the first read; see _titled
        self._titles: dict[str, str] = {}
        for statement in SCHEMA:
            self.conn.execute(statement)
        cutoff = time.time() - SESSION_TTL_DAYS * 86400
        expired = [row[0] for row in self.conn.execute("SELECT id FROM nav_sessions WHERE updated < ?", (cutoff,))]
        for session_id in expired:
            for table in TABLES:
                column = "id" if table == "nav_sessions" else "session_id"
                self.conn.execute(f"DELETE FROM {table} WHERE {column} = ?", (session_id,))

    # -- commands --------------------------------------------------------------

    def start(self, question: str, *, parts: list[str] | None = None, max_pages: int = DEFAULT_MAX_PAGES,
              limit: int = 3) -> dict:
        parts = [part.strip() for part in parts or [] if part.strip()]
        if len(parts) > MAX_PARTS:
            raise NavError("too-many-parts", f"at most {MAX_PARTS} --part per question; merge the closest ones")
        self.cache.refresh()
        session_id = self._new_id()
        vector = self._query_vector(question)
        now = time.time()
        self.conn.execute(
            "INSERT INTO nav_sessions (id, question, max_pages, query_vector, embed_model, created, updated) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (session_id, question, max_pages, vector, self.cache.embed_model if vector else None, now, now))
        session = self._session(session_id)
        result = self._search(session, question, limit, frozenset())
        if parts:
            # Each part searched on its own: a page that answers one part well can rank low for the
            # whole question, and the part's own coverage says which parts the wiki may lack.
            seen = {hit["slug"] for hit in result["results"]}
            result["parts"] = []
            for part in parts:
                found = self._search(session, part, PART_RESULTS, frozenset())
                for hit in found["results"]:
                    if hit["slug"] not in seen:
                        seen.add(hit["slug"])
                        result["results"].append(hit)
                result["parts"].append({"part": part, "coverage": found["coverage"],
                                        "pages": [hit["slug"] for hit in found["results"]]})
        self._log(session, "start", None, {"results": [hit["slug"] for hit in result["results"]],
                                           **({"parts": parts} if parts else {})})
        return {"session": session_id, "max_pages": max_pages, **result}

    def read(self, session_id: str, slug: str, why: str, *, sections: list[str] | None = None, full: bool = False,
             more: bool = False, max_chars: int = MAX_CHARS) -> dict:
        session = self._open(session_id)
        why = (why or "").strip()
        if not why:
            raise NavError("why-required", "--why is required: name the open question and why this page beats the alternatives")
        if len(why) > MAX_WHY:
            raise NavError("why-too-long", f"--why is {len(why)} characters; keep it to a few sentences (at most {MAX_WHY})")
        if not self.cache.ensure_fresh(slug):
            raise NavError("no-such-page", f"no indexed page '{slug}'")

        reads = self._reads(session)
        visited = slug in reads
        if visited and not (sections or full or more):
            raise NavError("already-read", f"'{slug}' was already read; ask for another --section, --more or --full, "
                                           "or pick an unvisited page")
        if not visited and len(reads) >= session.max_pages:
            raise NavError("page-limit", f"page limit of {session.max_pages} reached; answer with what you have, "
                                         "state the gap, and run 'wiki nav end'")
        if more and not self._pending(session).get(slug):
            raise NavError("nothing-more", f"nothing left to continue on '{slug}': the last read of it was not cut")

        info = self.cache.page_info(slug)
        page = self._page(slug, info)
        if more:
            spans, parts, label = self._pending(session)[slug], ["more"], "continued"
        else:
            spans, parts, label = self._choose(session, slug, info, page, sections or [], full)
        content, rest = self._cut(page.text, spans, max_chars)
        pages_read = len(reads) + (0 if visited else 1)
        self.conn.execute("UPDATE nav_sessions SET current = ? WHERE id = ?", (slug, session.id))
        self._log(session, "read", slug, {"why": why, "parts": parts, "rest": rest, **({"more": True} if more else {})})

        result = {"session": session.id, "slug": slug, "title": info["title"]}
        if info["type"]:
            result["type"] = info["type"]
        if info["summary"] and not (full or more or visited):
            result["summary"] = info["summary"]  # context for a narrow section, once per page
        result["section"] = label
        result["content"] = content
        if rest:
            left = sum(end - begin for begin, end in rest)
            result["more"] = (f"{left:,} more characters: wiki nav read {session.id} {slug} --more "
                              f"--why \"<why>\"")
        read_parts = self._reads(session)[slug]
        outline = []
        for name, chars in page.outline:
            entry = {"name": name, "chars": chars}
            if name.casefold() in read_parts or "full" in read_parts:
                entry["read"] = True
            outline.append(entry)
        result["sections"] = outline
        unread = [row for row in page.rows if page.label(row[1]).casefold() not in read_parts
                  and "full" not in read_parts and row[1] != page.summary_heading]
        if unread and info["kind"] == "page":
            result["next_section"] = page.label(self._best_chunk(session, unread, rerank=False)[1])
        result["pages_read"] = pages_read
        result["pages_left"] = session.max_pages - pages_read
        return result

    def candidates(self, session_id: str, *, limit: int = LINKED_LIMIT) -> dict:
        session = self._open(session_id)
        if not session.current:
            raise NavError("nothing-read", "read a page first ('wiki nav read')")
        visited = set(self._reads(session)) | {session.current}
        current = session.current

        linked: dict[str, dict] = {}
        for entry in self.cache.neighbors(current, include_unresolved=False):
            if entry["slug"] in visited or entry["slug"] in linked:
                continue
            linked[entry["slug"]] = entry
        if linked:
            marks = ",".join("?" * len(linked))
            for (raw_slug,) in self.conn.execute(
                    f"SELECT slug FROM pages WHERE kind != 'page' AND slug IN ({marks})", list(linked)):
                del linked[raw_slug]  # raw source text is reached by search, not as a next page
        question_scores = self._question_similarity(session, list(linked))
        ranked = sorted(linked, key=lambda slug: (-question_scores.get(slug, 0.0), list(linked).index(slug)))
        shown_linked = ranked[:limit]

        similar = self._similar(session, current, exclude=visited | set(linked))[:SIMILAR_LIMIT]
        shown = set(shown_linked) | {slug for slug, _ in similar}
        earlier = [row for row in self.conn.execute(
            "SELECT slug, source, reason FROM nav_frontier WHERE session_id = ? ORDER BY score DESC",
            (session.id,)) if row[0] not in visited and row[0] not in shown and row[0] not in linked][:EARLIER_LIMIT]

        result = {"session": session.id, "from": current, "linked": [], "similar": [], "earlier": []}
        for slug in shown_linked:
            entry = linked[slug]
            item = self._describe(slug)
            item["relation"] = entry["type"]
            item["reason"] = entry["reason"]
            result["linked"].append(item)
            self._remember(session, slug, question_scores.get(slug, 0.5), current, entry["reason"])
        if len(linked) > len(shown_linked):
            result["more_linked"] = len(linked) - len(shown_linked)
        for slug, score in similar:
            item = self._describe(slug)
            item["similarity"] = round(score, 3)
            result["similar"].append(item)
            self._remember(session, slug, score, current, "similar")
        for slug, source, reason in earlier:
            item = self._describe(slug)
            item["from"] = source
            if reason and reason != "similar":
                item["reason"] = reason
            result["earlier"].append(item)
        pages_read = len(self._reads(session))
        result["pages_left"] = session.max_pages - pages_read
        self._log(session, "candidates", current, {"linked": shown_linked, "similar": [s for s, _ in similar]})
        return result

    def requery(self, session_id: str, question: str, *, limit: int = 3) -> dict:
        session = self._open(session_id)
        previous = [json.loads(detail or "{}").get("query", "") for (detail,) in self.conn.execute(
            "SELECT detail FROM nav_events WHERE session_id = ? AND kind = 'search'", (session.id,))]
        normalized = " ".join(question.casefold().split())
        if normalized in previous:
            raise NavError("repeated-search", "this question was already searched in this session")
        if len(previous) >= MAX_REQUERIES:
            raise NavError("search-limit", f"at most {MAX_REQUERIES} re-searches per session; answer with what you "
                                           "have and state the gap")
        visited = frozenset(self._reads(session))
        result = self._search(session, question, limit, visited)
        self._log(session, "search", None, {"query": normalized, "results": [hit["slug"] for hit in result["results"]]})
        return {"session": session.id, **result, "pages_left": session.max_pages - len(visited)}

    def end(self, session_id: str, cited: list[str], *, gaps: list[str] | None = None) -> dict:
        session = self._open(session_id)
        reads = self._reads(session)
        unknown = [slug for slug in cited if not self.cache.page_exists(slug)]
        gaps = [gap.strip() for gap in gaps or [] if gap.strip()]
        checked = [self._check_gap(session, gap, reads) for gap in gaps]
        leads = [lead for check in checked for lead in check["leads"]]
        if leads and len(reads) < session.max_pages:
            # The gap check the guide asks for, run by the tool: a gap can't be claimed while a page
            # the wiki ranks well for it is unread. Reading the lead (or the section) clears it.
            self._log(session, "check", None, {"gaps": gaps, "leads": [lead["slug"] for lead in leads]})
            found = "; ".join(f"'{check['part']}': " + ", ".join(
                f"{lead['slug']} § {lead['section']}" for lead in check["leads"]) for check in checked if check["leads"])
            raise NavError("possible-answer", f"the wiki may cover what you call a gap ({found}); read that "
                                              "with 'wiki nav read', then run 'wiki nav end' again")
        self.conn.execute("UPDATE nav_sessions SET ended = 1, cited = ?, updated = ? WHERE id = ?",
                          (json.dumps(cited), time.time(), session.id))
        self._log(session, "end", None, {"cited": cited, **({"gaps": gaps} if gaps else {})})
        result = {"session": session.id, "pages_read": list(reads), "cited": cited}
        uncited = [slug for slug in cited if slug not in reads]
        if uncited:
            result["cited_without_reading"] = uncited
        cut = {slug: sum(end - begin for begin, end in rest) for slug, rest in self._pending(session).items() if rest}
        if cut:
            result["cut_short"] = [{"slug": slug, "chars_not_read": chars} for slug, chars in cut.items()]
        if checked:
            result["not_covered"] = [{"part": check["part"], "checked": check["checked"]} for check in checked]
        if unknown:
            result["unknown"] = unknown
        result["sources"] = self._sources_line(reads, cited, cut, checked)
        return result

    def log(self, session_id: str) -> dict:
        session = self._session(session_id)
        events = [{"kind": kind, **({"slug": slug} if slug else {}), **json.loads(detail or "{}")}
                  for kind, slug, detail in self.conn.execute(
                      "SELECT kind, slug, detail FROM nav_events WHERE session_id = ? ORDER BY seq", (session.id,))]
        return {"session": session.id, "question": session.question, "ended": session.ended, "events": events}

    # -- helpers ---------------------------------------------------------------

    def _new_id(self) -> str:
        while True:
            session_id = secrets.token_hex(3)
            if not self.conn.execute("SELECT 1 FROM nav_sessions WHERE id = ?", (session_id,)).fetchone():
                return session_id

    def _session(self, session_id: str) -> Session:
        row = self.conn.execute(
            "SELECT id, question, max_pages, query_vector, embed_model, current, ended FROM nav_sessions WHERE id = ?",
            (session_id,)).fetchone()
        if not row:
            raise NavError("no-such-session", f"no session '{session_id}' (sessions expire after {SESSION_TTL_DAYS} days)")
        return Session(row[0], row[1], row[2], row[3], row[4], row[5], bool(row[6]))

    def _open(self, session_id: str) -> Session:
        session = self._session(session_id)
        if session.ended:
            raise NavError("session-ended", f"session '{session_id}' has ended; start a new one")
        return session

    def _log(self, session: Session, kind: str, slug: str | None, detail: dict) -> None:
        seq = self.conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM nav_events WHERE session_id = ?",
                                (session.id,)).fetchone()[0]
        now = time.time()
        self.conn.execute("INSERT INTO nav_events (session_id, seq, at, kind, slug, detail) VALUES (?, ?, ?, ?, ?, ?)",
                          (session.id, seq, now, kind, slug, json.dumps(detail, ensure_ascii=False)))
        self.conn.execute("UPDATE nav_sessions SET updated = ? WHERE id = ?", (now, session.id))

    def _reads(self, session: Session) -> dict[str, set[str]]:
        """Pages read so far, in order, with the parts of each already returned."""
        reads: dict[str, set[str]] = {}
        for slug, detail in self.conn.execute(
                "SELECT slug, detail FROM nav_events WHERE session_id = ? AND kind = 'read' ORDER BY seq", (session.id,)):
            detail = json.loads(detail)
            reads.setdefault(slug, set()).update(detail.get("parts") or [detail.get("part", "")])
        return reads

    def _pending(self, session: Session) -> dict[str, list[list[int]]]:
        """Per page, the text spans the last cut read left unreturned (what `--more` continues)."""
        pending: dict[str, list[list[int]]] = {}
        for slug, detail in self.conn.execute(
                "SELECT slug, detail FROM nav_events WHERE session_id = ? AND kind = 'read' ORDER BY seq", (session.id,)):
            detail = json.loads(detail)
            if detail.get("rest") or detail.get("more"):
                pending[slug] = detail.get("rest") or []
        return pending

    def _remember(self, session: Session, slug: str, score: float, source: str, reason: str | None) -> None:
        self.conn.execute(
            """INSERT INTO nav_frontier (session_id, slug, score, source, reason) VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(session_id, slug) DO UPDATE SET score = MAX(score, excluded.score)""",
            (session.id, slug, score, source, reason))

    def _query_vector(self, text: str) -> bytes | None:
        if self.embedder is None or self.cache.embed_model != self.embedder.name:
            return None
        try:
            return vec.serialize(self.embedder.embed_query(text))
        except ModelUnavailable:
            return None

    def _vectors_usable(self, session: Session) -> bool:
        return session.query_vector is not None and session.embed_model == self.cache.embed_model

    def _search(self, session: Session, question: str, limit: int, exclude: frozenset[str]) -> dict:
        result = search(self.conn, question, embedder=self.embedder, reranker=self.reranker,
                        embed_model=self.cache.embed_model, limit=limit, exclude=exclude)
        for rank, hit in enumerate(result.hits, start=1):
            self._remember(session, hit.slug, 1.0 / rank, "search", hit.section or None)
        payload = {"coverage": self._coverage(result, result.hits[0].score) if result.hits else "none",
                   "results": [hit.to_dict() for hit in result.hits]}
        if result.notes:
            payload["notes"] = result.notes
        return payload

    def _coverage(self, result: SearchResult, score: float) -> str:
        """good / weak / none from a reranker score, or unknown when no measured reranker ran."""
        bands = COVERAGE_BANDS.get(self.reranker.name) if self.reranker is not None else None
        if bands is None or "rerank" not in result.modes:
            return "unknown"
        good, none = bands
        return "good" if score >= good else "none" if score < none else "weak"

    def _section_label(self, slug: str, heading: str) -> str:
        headings = [row[0] for row in self.conn.execute(
            "SELECT DISTINCT c.heading_path FROM chunks c JOIN pages p ON p.id = c.page_id WHERE p.slug = ?", (slug,))]
        return _labeler(headings)(heading)

    def _check_gap(self, session: Session, part: str, reads: dict[str, set[str]]) -> dict:
        """Search the wiki for a part the agent says it lacks; leads are well-ranked text not yet seen."""
        found = search(self.conn, part, embedder=self.embedder, reranker=self.reranker,
                       embed_model=self.cache.embed_model, limit=3)
        coverage = [self._coverage(found, hit.score) for hit in found.hits]
        leads = []
        for hit, level in zip(found.hits, coverage):
            if level != "good":
                continue
            seen = reads.get(hit.slug, set())
            label = self._section_label(hit.slug, hit.section)
            if "full" in seen or label.casefold() in seen:
                continue
            leads.append({"slug": hit.slug, "section": label})
        if found.hits:
            best = found.hits[0]
            checked = (f"searched the wiki: best match {best.title} § "
                       f"{self._section_label(best.slug, best.section)} ({coverage[0]})")
        else:
            checked = "searched the wiki: no match"
        return {"part": part, "leads": leads, "checked": checked}

    def _sources_line(self, reads: dict[str, set[str]], cited: list[str], cut: dict[str, int],
                      checked: list[dict]) -> str:
        """One line for the answer: what it drew on, how fully, and what the wiki lacks."""
        def title(slug: str) -> str:
            info = self.cache.page_info(slug)
            return info["title"] if info else slug

        used = [slug for slug in cited if slug in reads] or list(reads)
        lines = []
        if used:
            lines.append("From the wiki: " + "; ".join(
                title(slug) + (f" (partly read: {cut[slug]:,} characters not read)" if slug in cut else "")
                for slug in used) + ".")
        summary_only = [slug for slug in cited if slug not in reads and self.cache.page_exists(slug)]
        if summary_only:
            lines.append("From page summaries only: " + "; ".join(title(slug) for slug in summary_only) + ".")
        if checked:
            lines.append("Not found in the wiki: " + "; ".join(check["part"] for check in checked) + ".")
        return " ".join(lines) or "Nothing from the wiki."

    def _describe(self, slug: str) -> dict:
        info = self.cache.page_info(slug) or {"slug": slug, "title": slug}
        item = {"slug": slug}
        if info.get("title") and info["title"] != slug:
            item["title"] = info["title"]
        if info.get("type"):
            item["type"] = info["type"]
        summary = info.get("summary")
        if summary:
            if len(summary) > CANDIDATE_SUMMARY_CHARS:
                summary = summary[:CANDIDATE_SUMMARY_CHARS].rsplit(" ", 1)[0].rstrip(",;:") + "…"
            item["summary"] = summary
        return item

    def _question_similarity(self, session: Session, slugs: list[str]) -> dict[str, float]:
        """Cosine similarity of each page's summary to the session question."""
        if not slugs or not self._vectors_usable(session):
            return {}
        marks = ",".join("?" * len(slugs))
        rows = self.conn.execute(
            f"""SELECT p.slug, vec_distance_cosine(s.embedding, ?) FROM summary_vectors s
                JOIN pages p ON p.id = s.rowid WHERE p.slug IN ({marks})""",
            (session.query_vector, *slugs)).fetchall()
        return {slug: 1.0 - distance for slug, distance in rows}

    def _similar(self, session: Session, current: str, *, exclude: set[str]) -> list[tuple[str, float]]:
        """Pages similar to the question and to the current page, by summary vector."""
        if not self._vectors_usable(session):
            query = keyword_query(session.question)
            if not query:
                return []
            rows = self.conn.execute(
                """SELECT DISTINCT p.slug FROM chunks_fts f JOIN chunks c ON c.id = f.rowid
                   JOIN pages p ON p.id = c.page_id
                   WHERE chunks_fts MATCH ? AND p.kind = 'page' ORDER BY bm25(chunks_fts) LIMIT 40""",
                (query,)).fetchall()
            return [(slug, 0.0) for (slug,) in rows if slug not in exclude and slug != current][:SIMILAR_LIMIT]
        probes = [session.query_vector]
        own = self.conn.execute("SELECT s.embedding FROM summary_vectors s JOIN pages p ON p.id = s.rowid "
                                "WHERE p.slug = ?", (current,)).fetchone()
        if own:
            probes.append(own[0])
        scores: dict[str, float] = {}
        k = SIMILAR_LIMIT + len(exclude) + 10
        for probe in probes:
            for slug, distance in self.conn.execute(
                    """SELECT p.slug, v.distance FROM summary_vectors v JOIN pages p ON p.id = v.rowid
                       WHERE v.embedding MATCH ? AND k = ? AND p.kind = 'page'""", (probe, k)):
                if slug in exclude or slug == current:
                    continue
                scores[slug] = max(scores.get(slug, 0.0), 1.0 - distance)
        return sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))

    def _page(self, slug: str, info: dict) -> _Page:
        path = self.cache.settings.root / info["path"]
        text = path.read_bytes().decode("utf-8", errors="replace").lstrip("﻿")
        rows = self.conn.execute(
            """SELECT c.id, c.heading_path, c.start_offset, c.end_offset FROM chunks c
               JOIN pages p ON p.id = c.page_id WHERE p.slug = ? AND c.ordinal > 0 ORDER BY c.ordinal""",
            (slug,)).fetchall()
        headings = list(dict.fromkeys(row[1] for row in rows))
        label = _labeler(headings)
        spans = {heading: (min(row[2] for row in rows if row[1] == heading),
                           max(row[3] for row in rows if row[1] == heading)) for heading in headings}
        outline: dict[str, int] = {}
        for heading in headings:
            outline[label(heading)] = outline.get(label(heading), 0) + spans[heading][1] - spans[heading][0]
        summary_heading = None
        wanted = " ".join((info.get("summary") or "").split())[:80]
        if wanted and info["kind"] == "page":
            for heading in headings:
                body = text[spans[heading][0]:spans[heading][1]].split("\n", 1)[-1]
                if " ".join(body.split()).startswith(wanted):
                    summary_heading = heading
                    break
        return _Page(text, rows, headings, spans, label, list(outline.items()), summary_heading)

    def _choose(self, session: Session, slug: str, info: dict, page: _Page, sections: list[str],
                full: bool) -> tuple[list[list[int]], list[str], str]:
        """The spans a read returns, the parts it marks read, and its label."""
        if full or not page.rows:
            begin = page.rows[0][2] if page.rows else 0
            return [[begin, len(page.text)]], ["full"], "full page"
        if sections:
            chosen = []
            for section in sections:
                wanted = section.casefold().strip()
                matches = [heading for heading in page.headings
                           if wanted in (heading.casefold(), page.label(heading).casefold(),
                                         heading.casefold().rsplit(" > ", 1)[-1])
                           or (wanted == "(intro)" and heading == "")]
                if not matches:
                    names = ", ".join(name for name, _ in page.outline)
                    raise NavError("no-such-section", f"no section '{section}' in '{slug}'; sections: {names}")
                if matches[0] not in chosen:
                    chosen.append(matches[0])
        elif info["kind"] != "page":
            # Raw text has no headings; return the best-matching chunk, not the whole file.
            best = self._best_chunk(session, page.rows)
            return [[best[2], best[3]]], [page.label(best[1]).casefold()], page.label(best[1])
        else:
            chosen = [self._default_heading(session, slug, page)]
        labels = [page.label(heading) for heading in chosen]
        return ([list(page.spans[heading]) for heading in chosen], [name.casefold() for name in labels],
                ", ".join(labels))

    def _default_heading(self, session: Session, slug: str, page: _Page) -> str:
        """The section `nav start` or `nav search` showed for this page, else the closest to the
        question; never the summary section, which the read returns anyway."""
        row = self.conn.execute("SELECT reason FROM nav_frontier WHERE session_id = ? AND slug = ? AND source = 'search'",
                                (session.id, slug)).fetchone()
        if row and row[0] in page.spans and row[0] != page.summary_heading:
            return row[0]
        rows = [row for row in page.rows if row[1] != page.summary_heading] or page.rows
        return self._best_chunk(session, rows)[1]

    def _cut(self, text: str, spans: list[list[int]], max_chars: int) -> tuple[str, list[list[int]]]:
        """The spans' text within ``max_chars``, cut at a line end, and the spans left over."""
        pieces: list[str] = []
        used = 0
        rest: list[list[int]] = []
        for index, (begin, end) in enumerate(spans):
            while begin < end and text[begin].isspace():
                begin += 1
            if begin >= end:
                continue
            room = max_chars - used
            if end - begin <= room:
                pieces.append(text[begin:end].rstrip())
                used += end - begin + 2
                continue
            if pieces and room < MIN_SECTION_CHARS:
                rest = [[begin, end], *spans[index + 1:]]
                break
            cut = text.rfind("\n", begin, begin + room)
            if cut <= begin:
                cut = text.rfind(" ", begin, begin + room)
            if cut <= begin:
                cut = begin + room
            pieces.append(text[begin:cut].rstrip())
            rest = [[cut, end], *spans[index + 1:]]
            break
        return self._titled("\n\n".join(pieces)), [list(span) for span in rest]

    def _best_chunk(self, session: Session, rows: list[tuple], *, rerank: bool = True) -> tuple:
        # Tiny chunks ("None new.") score deceptively well on similarity; prefer real sections.
        # The intro before the first "##" is usually the title and file metadata; skip it too.
        substantial = [row for row in rows if row[3] - row[2] >= MIN_SECTION_CHARS and " > " in row[1]]
        rows = substantial or [row for row in rows if row[3] - row[2] >= MIN_SECTION_CHARS] or rows
        ids = [row[0] for row in rows]
        marks = ",".join("?" * len(ids))
        if self._vectors_usable(session):
            # The question's vector was stored at `nav start`, so this loads no model. The reranker
            # picked the same section on 51 of 68 evaluation answer pages, with no clear winner on
            # the rest, and costs about 0.6 s per read (docs/navigation.md).
            best = self.conn.execute(
                f"""SELECT rowid FROM chunk_vectors WHERE rowid IN ({marks})
                    ORDER BY vec_distance_cosine(embedding, ?) LIMIT 1""", (*ids, session.query_vector)).fetchone()
            if best:
                return next(row for row in rows if row[0] == best[0])
        if rerank and self.reranker is not None and len(rows) > 1:
            texts = dict(self.conn.execute(
                f"SELECT rowid, heading_path || char(10) || text FROM chunks_fts WHERE rowid IN ({marks})", ids))
            try:
                scores = self.reranker.score(session.question, [texts[row[0]][:RERANK_CHARS] for row in rows])
            except ModelUnavailable:
                pass
            else:
                best = max(range(len(rows)), key=lambda index: (scores[index], -index))  # ties: earlier section
                return rows[best]
        query = keyword_query(session.question)
        if query:
            best = self.conn.execute(
                f"""SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? AND rowid IN ({marks})
                    ORDER BY bm25(chunks_fts) LIMIT 1""", (query, *ids)).fetchone()
            if best:
                return next(row for row in rows if row[0] == best[0])
        return rows[0]

    def _titled(self, text: str) -> str:
        """``[[slug]]`` -> ``[[slug|Title]]``, so the reader sees each linked page's name next
        to the slug it navigates by. Links with display text, embeds, and links to pages whose
        title is just the slug are left as written; the file itself is never changed."""
        if self._resolver is None:
            rows = self.conn.execute("SELECT slug, path, title FROM pages WHERE kind = 'page'").fetchall()
            self._resolver = Resolver([(slug, path) for slug, path, _ in rows])
            self._titles = {slug: title for slug, _, title in rows if title}

        def add_title(match, pipe: str) -> str:
            target, _, display = links.split_target(match.group(2))
            if match.group(1) or display:
                return match.group(0)
            title = self._titles.get(self._resolver.resolve(target) or "")
            if not title or title.casefold() == target.rsplit("/", 1)[-1].casefold():
                return match.group(0)
            return f"[[{match.group(2)}{pipe}{title}]]"

        lines = text.split("\n")
        for index, line in enumerate(lines):
            if "[[" in line:
                pipe = "\\|" if line.lstrip().startswith("|") else "|"  # a bare | would split a table cell
                lines[index] = links.WIKILINK.sub(lambda match: add_title(match, pipe), line)
        return "\n".join(lines)


def _labeler(headings: list[str]):
    """Section labels without the page's own title, when every heading starts with it.

    Chunk heading paths begin with the page's H1 ("Fifty-day delay > What happened");
    repeating it on every section only costs tokens.
    """
    named = [heading for heading in headings if heading]
    first = named[0].split(" > ", 1)[0] if named else ""
    shared = bool(first) and all(heading == first or heading.startswith(first + " > ") for heading in named)

    def label(heading: str) -> str:
        if not heading:
            return "(intro)"
        if shared:
            return heading[len(first) + 3:] if heading != first else "(intro)"
        return heading

    return label
