"""Hybrid search: FTS5 BM25 + vector similarity, fused with RRF, then reranked."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from wiki_cli import vec
from wiki_cli.cache import embedding_text
from wiki_cli.models import Embedder, ModelUnavailable, Reranker, tokens

KEYWORD_K = 50
VECTOR_K = 50
RERANK_K = 20  # chosen in docs/model-selection.md
RRF_K = 60
RERANK_CHARS = 1200  # passage length sent to the reranker
BM25_WEIGHTS = (4.0, 2.0, 1.0)  # title, heading_path, text

# Common words that only dilute an OR query.
STOPWORDS = frozenset("""
a about an and are as at be by can do does for from how i in is it of on or should that the this
to was what when where which who why will with you your
""".split())


@dataclass
class Hit:
    slug: str
    title: str
    summary: str | None
    placeholder: bool
    section: str
    score: float
    chunk_id: int
    page_type: str | None = None

    def to_dict(self) -> dict:
        result = {"slug": self.slug, "title": self.title}
        if self.page_type:
            result["type"] = self.page_type
        result["score"] = round(self.score, 4)
        if self.summary:
            result["summary"] = self.summary
        if self.placeholder:
            result["summary_placeholder"] = True
        if self.section:
            result["section"] = self.section
        return result


@dataclass
class SearchResult:
    hits: list[Hit]
    modes: list[str] = field(default_factory=list)  # which retrievers contributed
    notes: list[str] = field(default_factory=list)


def keyword_query(question: str) -> str | None:
    terms = []
    for token in tokens(question):
        if len(token) > 1 and token not in STOPWORDS and token not in terms:
            terms.append(token)
    return " OR ".join(f'"{term}"' for term in terms) or None


def search(
    conn: sqlite3.Connection,
    question: str,
    *,
    embedder: Embedder | None,
    reranker: Reranker | None,
    embed_model: str | None,
    limit: int = 3,
    exclude: frozenset[str] = frozenset(),
    include_raw: bool = False,
) -> SearchResult:
    result = SearchResult(hits=[])
    rankings: list[list[int]] = []

    query = keyword_query(question)
    if query:
        weights = ", ".join(str(weight) for weight in BM25_WEIGHTS)
        rows = conn.execute(
            f"SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts, {weights}) LIMIT ?",
            (query, KEYWORD_K + len(exclude) * 4),
        ).fetchall()
        rankings.append([row[0] for row in rows])
        result.modes.append("keyword")

    if embedder is not None:
        if embed_model != embedder.name:
            result.notes.append(
                f"vector search skipped: cache vectors are for '{embed_model}', not '{embedder.name}'"
                if embed_model else "vector search skipped: no embeddings yet; run 'wiki index refresh'")
        else:
            try:
                query_vector = embedder.embed_query(question)
            except ModelUnavailable as exc:
                result.notes.append(f"vector search skipped: {exc}")
            else:
                vector = vec.serialize(query_vector)
                rows = conn.execute(
                    "SELECT rowid FROM chunk_vectors WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                    (vector, VECTOR_K + len(exclude) * 4),
                ).fetchall()
                rankings.append([row[0] for row in rows])
                result.modes.append("vector")

    fused = reciprocal_rank_fusion(rankings)
    if not fused:
        return result

    candidates = _load_chunks(conn, [chunk_id for chunk_id, _ in fused], exclude, include_raw)
    candidates = [candidate for candidate in candidates if candidate is not None][:RERANK_K]
    rrf = dict(fused)
    scores = None
    if reranker is not None and candidates:
        documents = [embedding_text(c["title"], c["heading"], c["text"])[:RERANK_CHARS] for c in candidates]
        try:
            scores = reranker.score(question, documents)
            result.modes.append("rerank")
        except ModelUnavailable as exc:
            result.notes.append(f"reranking skipped: {exc}")
    if scores is None:
        scores = [rrf[candidate["chunk_id"]] for candidate in candidates]

    best: dict[str, Hit] = {}
    for candidate, score in zip(candidates, scores):
        current = best.get(candidate["slug"])
        if current is None or score > current.score:
            best[candidate["slug"]] = Hit(
                slug=candidate["slug"],
                title=candidate["title"],
                summary=candidate["summary"],
                placeholder=candidate["placeholder"],
                section=candidate["heading"],
                score=score,
                chunk_id=candidate["chunk_id"],
                page_type=candidate["type"],
            )
    result.hits = sorted(best.values(), key=lambda hit: (-hit.score, hit.slug))[:limit]
    return result


def reciprocal_rank_fusion(rankings: list[list[int]], k: int = RRF_K) -> list[tuple[int, float]]:
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))


def _load_chunks(conn: sqlite3.Connection, chunk_ids: list[int], exclude: frozenset[str],
                 include_raw: bool) -> list[dict | None]:
    if not chunk_ids:
        return []
    marks = ",".join("?" * len(chunk_ids))
    kind_filter = "" if include_raw else " AND p.kind = 'page'"
    rows = conn.execute(
        f"""SELECT c.id, p.slug, COALESCE(p.title, p.slug), p.summary, p.summary_is_placeholder,
                   c.heading_path, f.text, p.page_type
            FROM chunks c
            JOIN pages p ON p.id = c.page_id
            JOIN chunks_fts f ON f.rowid = c.id
            WHERE c.id IN ({marks}){kind_filter}""",
        chunk_ids,
    ).fetchall()
    by_id = {
        row[0]: {"chunk_id": row[0], "slug": row[1], "title": row[2], "summary": row[3],
                 "placeholder": bool(row[4]), "heading": row[5], "text": row[6], "type": row[7]}
        for row in rows if row[1] not in exclude
    }
    return [by_id.get(chunk_id) for chunk_id in chunk_ids]
