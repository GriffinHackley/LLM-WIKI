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

from wiki_cli import vec
from wiki_cli.cache import Cache
from wiki_cli.models import Embedder, ModelUnavailable, Reranker
from wiki_cli.search import RERANK_CHARS, keyword_query, search

DEFAULT_MAX_PAGES = 6
MAX_WHY = 400
MAX_REQUERIES = 3
SECTION_CAP = 6000
SESSION_TTL_DAYS = 14
LINKED_LIMIT = 8
SIMILAR_LIMIT = 4
EARLIER_LIMIT = 3
CANDIDATE_SUMMARY_CHARS = 200  # candidate lists show many pages; keep each entry short
MIN_SECTION_CHARS = 200  # the default section is chosen among sections at least this long

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


class Navigator:
    def __init__(self, cache: Cache, *, embedder: Embedder | None = None, reranker: Reranker | None = None):
        self.cache = cache
        self.conn = cache.conn
        self.embedder = embedder
        self.reranker = reranker
        for statement in SCHEMA:
            self.conn.execute(statement)
        cutoff = time.time() - SESSION_TTL_DAYS * 86400
        expired = [row[0] for row in self.conn.execute("SELECT id FROM nav_sessions WHERE updated < ?", (cutoff,))]
        for session_id in expired:
            for table in TABLES:
                column = "id" if table == "nav_sessions" else "session_id"
                self.conn.execute(f"DELETE FROM {table} WHERE {column} = ?", (session_id,))

    # -- commands --------------------------------------------------------------

    def start(self, question: str, *, max_pages: int = DEFAULT_MAX_PAGES, limit: int = 3) -> dict:
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
        self._log(session, "start", None, {"results": [hit["slug"] for hit in result["results"]]})
        return {"session": session_id, "max_pages": max_pages, **result}

    def read(self, session_id: str, slug: str, why: str, *, section: str | None = None, full: bool = False) -> dict:
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
        if visited and not (section or full):
            raise NavError("already-read", f"'{slug}' was already read; ask for another --section or --full, "
                                           "or pick an unvisited page")
        if not visited and len(reads) >= session.max_pages:
            raise NavError("page-limit", f"page limit of {session.max_pages} reached; answer with what you have, "
                                         "state the gap, and run 'wiki nav end'")

        info = self.cache.page_info(slug)
        content, heading, sections, truncated = self._content(session, slug, info, section, full)
        part = "full" if full else heading.casefold()
        if visited and part in reads[slug]:
            raise NavError("already-read", f"that part of '{slug}' was already returned")
        pages_read = len(reads) + (0 if visited else 1)
        self.conn.execute("UPDATE nav_sessions SET current = ? WHERE id = ?", (slug, session.id))
        self._log(session, "read", slug, {"why": why, "part": part})

        result = {"session": session.id, "slug": slug, "title": info["title"]}
        if info["type"]:
            result["type"] = info["type"]
        if info["summary"] and not full:
            result["summary"] = info["summary"]  # context for a narrow section
        result["section"] = "full page" if full else (heading or "(intro)")
        result["content"] = content
        if truncated:
            result["truncated"] = True
        result["sections"] = sections
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

    def end(self, session_id: str, cited: list[str]) -> dict:
        session = self._open(session_id)
        reads = self._reads(session)
        unknown = [slug for slug in cited if not self.cache.page_exists(slug)]
        self.conn.execute("UPDATE nav_sessions SET ended = 1, cited = ?, updated = ? WHERE id = ?",
                          (json.dumps(cited), time.time(), session.id))
        self._log(session, "end", None, {"cited": cited})
        result = {"session": session.id, "pages_read": list(reads), "cited": cited}
        uncited = [slug for slug in cited if slug not in reads]
        if uncited:
            result["cited_without_reading"] = uncited
        if unknown:
            result["unknown"] = unknown
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
            reads.setdefault(slug, set()).add(json.loads(detail).get("part", ""))
        return reads

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
        payload = {"results": [hit.to_dict() for hit in result.hits]}
        if result.notes:
            payload["notes"] = result.notes
        return payload

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

    def _content(self, session: Session, slug: str, info: dict, section: str | None,
                 full: bool) -> tuple[str, str, list[str], bool]:
        path = self.cache.settings.root / info["path"]
        text = path.read_bytes().decode("utf-8", errors="replace").lstrip("﻿")
        rows = self.conn.execute(
            """SELECT c.id, c.heading_path, c.start_offset, c.end_offset FROM chunks c
               JOIN pages p ON p.id = c.page_id WHERE p.slug = ? AND c.ordinal > 0 ORDER BY c.ordinal""",
            (slug,)).fetchall()
        headings = list(dict.fromkeys(row[1] for row in rows))
        label = _labeler(headings)
        sections = list(dict.fromkeys(label(heading) for heading in headings))
        if full or not rows:
            body = text[rows[0][2]:] if rows else text
            content, truncated = self._cap(body.strip())
            return content, "", sections, truncated

        if section:
            wanted = section.casefold().strip()
            matches = [heading for heading in headings
                       if wanted in (heading.casefold(), label(heading).casefold(),
                                     heading.casefold().rsplit(" > ", 1)[-1])
                       or (wanted == "(intro)" and heading == "")]
            if not matches:
                raise NavError("no-such-section", f"no section '{section}' in '{slug}'; sections: {', '.join(sections)}")
            heading = matches[0]
        else:
            heading = self._best_heading(session, rows)

        if info["kind"] != "page":
            # Raw text has no headings; return the best-matching chunk, not the whole file.
            best = self._best_chunk(session, rows)
            content, truncated = self._cap(text[best[2]:best[3]].strip())
            return content, label(heading), sections, truncated
        spans = [(start, end) for _, chunk_heading, start, end in rows if chunk_heading == heading]
        content, truncated = self._cap(text[min(s for s, _ in spans):max(e for _, e in spans)].strip())
        return content, label(heading), sections, truncated

    def _best_chunk(self, session: Session, rows: list[tuple]) -> tuple:
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
        if self.reranker is not None and len(rows) > 1:
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

    def _best_heading(self, session: Session, rows: list[tuple]) -> str:
        return self._best_chunk(session, rows)[1]

    @staticmethod
    def _cap(text: str) -> tuple[str, bool]:
        if len(text) <= SECTION_CAP:
            return text, False
        return text[:SECTION_CAP].rsplit("\n", 1)[0], True


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
