"""Search-quality evaluation against a question set with known answer pages."""

from __future__ import annotations

import random
import re
import statistics
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

from wiki_cli.cache import Cache
from wiki_cli.config import Settings
from wiki_cli.models import Embedder, Reranker
from wiki_cli.search import search

KINDS = ("single", "multi", "unanswerable")
SPLITS = ("tune", "test")
RESULT_DEPTH = 10


class EvalError(Exception):
    pass


@dataclass(frozen=True)
class Question:
    id: str
    question: str
    answers: tuple[str, ...]
    kind: str
    split: str


def load_questions(path: Path) -> list[Question]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise EvalError(f"cannot read {path}: {exc}") from exc
    if not isinstance(raw, list):
        raise EvalError(f"{path} must contain a list of questions")
    questions: list[Question] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        where = f"{path.name}[{index}]"
        if not isinstance(item, dict):
            raise EvalError(f"{where} must be a mapping")
        qid, text = str(item.get("id", "")), item.get("question")
        kind, split = item.get("kind"), item.get("split", "test")
        answers = item.get("answers") or []
        if not qid or qid in seen:
            raise EvalError(f"{where} needs a unique id")
        if not isinstance(text, str) or not text.strip():
            raise EvalError(f"{where} needs a question")
        if kind not in KINDS or split not in SPLITS:
            raise EvalError(f"{where} kind must be one of {KINDS} and split one of {SPLITS}")
        if not isinstance(answers, list) or not all(isinstance(slug, str) for slug in answers):
            raise EvalError(f"{where} answers must be a list of slugs")
        if (kind == "unanswerable") != (not answers):
            raise EvalError(f"{where}: unanswerable questions have no answers; others need at least one")
        seen.add(qid)
        questions.append(Question(qid, text.strip(), tuple(answers), kind, split))
    return questions


def eval_cache_path(settings: Settings, embed_model: str | None) -> Path:
    """A separate cache per embedding model, so comparing models never re-embeds the main cache."""
    tag = re.sub(r"[^A-Za-z0-9]+", "-", embed_model or "keyword").strip("-").lower()
    return settings.cache_path.with_name(f"eval-{tag}.sqlite3")


def run(
    cache: Cache,
    questions: list[Question],
    *,
    embedder: Embedder | None,
    reranker: Reranker | None,
) -> dict:
    missing = sorted({slug for q in questions for slug in q.answers
                      if not cache.conn.execute("SELECT 1 FROM pages WHERE slug = ? AND kind = 'page'", (slug,)).fetchone()})
    if missing:
        raise EvalError(f"answer pages not in the wiki: {', '.join(missing)}")

    if questions and (embedder is not None or reranker is not None):
        # Warm-up so model load time does not count as query latency.
        search(cache.conn, questions[0].question, embedder=embedder, reranker=reranker,
               embed_model=cache.embed_model, limit=1)

    per_question = []
    latencies = []
    for question in questions:
        start = time.perf_counter()
        result = search(cache.conn, question.question, embedder=embedder, reranker=reranker,
                        embed_model=cache.embed_model, limit=RESULT_DEPTH)
        latencies.append((time.perf_counter() - start) * 1000)
        ranked = [hit.slug for hit in result.hits]
        ranks = [ranked.index(slug) + 1 for slug in question.answers if slug in ranked]
        per_question.append({
            "id": question.id,
            "kind": question.kind,
            "rank": min(ranks) if ranks else None,
            "all_in_top5": bool(question.answers) and all(slug in ranked[:5] for slug in question.answers),
            "top_score": result.hits[0].score if result.hits else None,
            "top": ranked[:3],
        })
    return summarize(per_question, latencies)


def summarize(per_question: list[dict], latencies: list[float]) -> dict:
    answerable = [q for q in per_question if q["kind"] != "unanswerable"]
    multi = [q for q in answerable if q["kind"] == "multi"]
    unanswerable = [q for q in per_question if q["kind"] == "unanswerable"]

    def share(items, predicate):
        return round(sum(1 for item in items if predicate(item)) / len(items), 3) if items else None

    summary = {
        "questions": len(per_question),
        "hit_at_1": share(answerable, lambda q: q["rank"] == 1),
        "hit_at_3": share(answerable, lambda q: q["rank"] is not None and q["rank"] <= 3),
        "mrr": round(statistics.fmean(1 / q["rank"] if q["rank"] else 0 for q in answerable), 3) if answerable else None,
        "multi_all_in_top5": share(multi, lambda q: q["all_in_top5"]),
        "latency_ms_p50": round(statistics.median(latencies), 1) if latencies else None,
    }
    top_answerable = [q["top_score"] for q in answerable if q["top_score"] is not None]
    top_unanswerable = [q["top_score"] for q in unanswerable if q["top_score"] is not None]
    if top_answerable and top_unanswerable:
        # How well the top score separates answerable from unanswerable questions.
        summary["top_score_answerable_median"] = round(statistics.median(top_answerable), 4)
        summary["top_score_unanswerable_median"] = round(statistics.median(top_unanswerable), 4)
    summary["misses"] = [{"id": q["id"], "top": q["top"]} for q in answerable if q["rank"] is None or q["rank"] > 3]
    return summary


def sample(cache: Cache, *, single: int, multi: int, seed: int) -> dict:
    """Pick pages (and linked page pairs) for a question writer to read."""
    rng = random.Random(seed)
    pages = cache.conn.execute(
        """SELECT slug, path, COALESCE(title, slug), summary, summary_is_placeholder
           FROM pages WHERE kind = 'page' AND page_type NOT IN ('meta', 'index') ORDER BY slug"""
    ).fetchall()
    chosen = rng.sample(pages, min(single, len(pages)))
    pairs = cache.conn.execute(
        """SELECT DISTINCT r.source_slug, r.target_slug, r.relation_type, r.reason
           FROM relations r
           JOIN pages p ON p.slug = r.target_slug AND p.kind = 'page'
           JOIN pages s ON s.slug = r.source_slug AND s.kind = 'page'
           WHERE r.resolved = 1 AND r.relation_type NOT IN ('links-to', 'embeds')
           ORDER BY r.source_slug, r.target_slug"""
    ).fetchall()
    chosen_pairs = rng.sample(pairs, min(multi, len(pairs)))

    def describe(row):
        slug, path, title, summary, placeholder = row
        entry = {"slug": slug, "path": path, "title": title}
        if summary and not placeholder:
            entry["summary"] = summary
        return entry

    return {
        "seed": seed,
        "single": [describe(row) for row in chosen],
        "multi": [{"pages": [source, target], "type": rtype, "reason": reason}
                  for source, target, rtype, reason in chosen_pairs],
    }
