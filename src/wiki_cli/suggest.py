"""Suggest pages a page should link to or that should be updated alongside it.

Used during /ingest: after writing a document page, find the entity pages it names
but does not link, pages that share its sources, and pages with similar summaries.
Suggestions are leads for review, never edits.
"""

from __future__ import annotations

import math
import re

from wiki_cli.cache import Cache
from wiki_cli.pages import PageFile, load

MIN_NAME_LENGTH = 4
SHARED_MINIMUM = 2
# Two shared pages each linked from 10 pages score 2 / ln 10: sharing only broader hubs
# than that is not a lead.
MIN_SHARED_SCORE = 2 / math.log(10)
SIMILAR_LIMIT = 5


def suggest(cache: Cache, slug: str, *, limit: int = 10) -> list[dict]:
    info = cache.page_info(slug)
    if info is None:
        return []
    linked = {row[0] for row in cache.conn.execute(
        "SELECT target_slug FROM relations WHERE source_slug = ? UNION SELECT source_slug FROM relations "
        "WHERE target_slug = ? AND resolved = 1", (slug, slug))}
    skip = linked | {slug}
    found: dict[str, dict] = {}

    for target, name in _named(cache, slug, info, skip):
        found.setdefault(target, {"slug": target, "reasons": []})["reasons"].append(
            f"Named in the text as '{name}' but not linked.")
        found[target]["rank"] = (0, 0.0)

    for source, score, targets in _shared(cache, slug):
        if source in skip:
            continue
        entry = found.setdefault(source, {"slug": source, "reasons": []})
        shown = ", ".join(targets[:3]) + (", …" if len(targets) > 3 else "")
        entry["reasons"].append(f"Shares {len(targets)} linked pages: {shown}.")
        entry.setdefault("rank", (1, -score))

    if cache.has_vectors:
        own = cache.conn.execute("SELECT s.embedding FROM summary_vectors s JOIN pages p ON p.id = s.rowid "
                                 "WHERE p.slug = ?", (slug,)).fetchone()
        if own:
            rows = cache.conn.execute(
                """SELECT p.slug, v.distance FROM summary_vectors v JOIN pages p ON p.id = v.rowid
                   WHERE v.embedding MATCH ? AND k = ? AND p.kind = 'page'""",
                (own[0], SIMILAR_LIMIT + len(skip) + 1)).fetchall()
            added = 0
            for other, distance in rows:
                if other in skip or added >= SIMILAR_LIMIT:
                    continue
                added += 1
                entry = found.setdefault(other, {"slug": other, "reasons": []})
                entry["reasons"].append(f"Similar summary ({1 - distance:.2f}).")
                entry.setdefault("rank", (2, distance))

    results = sorted(found.values(), key=lambda entry: (entry["rank"], entry["slug"]))[:limit]
    for entry in results:
        entry.pop("rank")
        page = cache.page_info(entry["slug"])
        if page:
            if page["title"] != entry["slug"]:
                entry["title"] = page["title"]
            if page["type"]:
                entry["type"] = page["type"]
    return results


def _shared(cache: Cache, slug: str) -> list[tuple[str, float, list[str]]]:
    """Pages linking to at least two of the pages this one links to, as
    ``(slug, score, shared targets, rarest first)``, best first. A shared target counts
    ``1 / log(pages linking to it)`` (Adamic-Adar), so a hub that most pages link to
    adds little: pages that share only hubs fall below ``MIN_SHARED_SCORE``."""
    pairs = cache.conn.execute(
        """SELECT DISTINCT r2.source_slug, r2.target_slug
           FROM relations r1 JOIN relations r2 ON r1.target_slug = r2.target_slug
           WHERE r1.source_slug = ? AND r2.source_slug != ? AND r2.target_slug != ?
             AND r1.resolved = 1 AND r2.resolved = 1""", (slug, slug, slug)).fetchall()
    targets = sorted({target for _, target in pairs})
    if not targets:
        return []
    marks = ",".join("?" * len(targets))
    degree = dict(cache.conn.execute(
        f"SELECT target_slug, COUNT(DISTINCT source_slug) FROM relations WHERE resolved = 1 "
        f"AND target_slug IN ({marks}) GROUP BY target_slug", targets))
    shared: dict[str, list[str]] = {}
    for source, target in pairs:
        shared.setdefault(source, []).append(target)
    results = []
    for source, common in shared.items():
        if len(common) < SHARED_MINIMUM:
            continue
        score = sum(1 / math.log(max(degree.get(target, 2), 2)) for target in common)
        if score >= MIN_SHARED_SCORE:
            common.sort(key=lambda target: (degree.get(target, 2), target))
            results.append((source, score, common))
    return sorted(results, key=lambda item: (-item[1], item[0]))


def _named(cache: Cache, slug: str, info: dict, skip: set[str]) -> list[tuple[str, str]]:
    """Entity pages whose title or alias appears in this page (or its raw text) without a link."""
    texts = [_read(cache, info["path"])]
    # Raw source text the page links to is searched too: names often appear there first.
    for (raw_path,) in cache.conn.execute(
            """SELECT p.path FROM relations r JOIN pages p ON p.slug = r.target_slug
               WHERE r.source_slug = ? AND p.kind != 'page'""", (slug,)):
        texts.append(_read(cache, raw_path))
    text = "\n".join(texts)
    named_types = cache.settings.named_types
    if named_types:
        marks = ",".join("?" * len(named_types))
        candidates = cache.conn.execute(
            f"SELECT slug, path, title FROM pages WHERE kind = 'page' AND page_type IN ({marks})",
            named_types).fetchall()
    else:
        candidates = cache.conn.execute("SELECT slug, path, title FROM pages WHERE kind = 'page'").fetchall()
    found: list[tuple[str, str]] = []
    for other, path, title in candidates:
        if other in skip:
            continue
        names = [title or ""]
        try:
            data = load(PageFile(cache.settings.root / path, path, other), cache.settings).data or {}
        except OSError:
            data = {}
        aliases = data.get("aliases")
        if isinstance(aliases, list):
            names += [str(alias) for alias in aliases]
        for name in names:
            name = name.strip()
            if len(name) >= MIN_NAME_LENGTH and re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text):
                found.append((other, name))
                break
    return found


def _read(cache: Cache, rel: str) -> str:
    try:
        return (cache.settings.root / rel).read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return ""
