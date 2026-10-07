"""`wiki map`: the cache's embeddings laid out in three dimensions, as one HTML page.

Pages (or chunks) are placed by UMAP over their vectors (cosine, fixed seed), or by PCA
when asked or when UMAP cannot be used, which is always reported. The fitted layout is
kept beside the cache, keyed by what was embedded, so an unchanged wiki reopens
instantly. A question (`--query`) and navigation sessions (`--nav`) are placed into the
same layout with the fitted model, so they show where a question lands among the pages.

Any projection to three dimensions distorts; the map is for exploring, not proof.
"""

from __future__ import annotations

import hashlib
import json
import pickle
import time
import warnings
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Callable

import numpy as np

from wiki_cli import clusters
from wiki_cli.cache import Cache
from wiki_cli.models import Embedder

METHODS = ("auto", "umap", "pca")
COLOR_BY = ("type", "cluster", "age", "visits")
SEED = 0
NEIGHBORS = 15
NEAREST = 10  # a search hit's rank among the question's nearest points is shown up to this
RADIUS = 100.0  # coordinates are scaled so most points fit a sphere of this radius
SPREAD_PERCENTILE = 95  # ...this share of them, so one outlying island does not shrink the rest
LAYOUT_VERSION = "1"
HTML_NAME = "map.html"


class MapError(Exception):
    pass


@dataclass
class Points:
    kind: str  # "pages" or "chunks"
    rows: list[dict]
    vectors: np.ndarray  # one unit-length row per point
    key_parts: list[tuple]  # what the layout depends on: (point id, embedded hash)

    def __len__(self) -> int:
        return len(self.rows)


@dataclass
class Layout:
    method: str
    coords: np.ndarray
    model: object = None  # fitted UMAP, or (mean, components) for PCA
    fallback_reason: str | None = None
    cached: bool = False

    def place(self, vectors: np.ndarray, points: Points) -> np.ndarray:
        """Coordinates for new vectors (questions) in this layout."""
        vectors = np.atleast_2d(np.asarray(vectors, dtype=np.float32))
        if self.method == "pca":
            mean, components = self.model
            return _pad((vectors - mean) @ components.T)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                placed = np.asarray(self.model.transform(vectors), dtype=np.float64)
            if np.all(np.isfinite(placed)):
                return placed
        except Exception:  # noqa: BLE001 - a failed transform falls back to the neighbours below
            pass
        return _nearest_average(vectors, points.vectors, self.coords)


@dataclass
class MapResult:
    payload: dict
    notes: list[str] = field(default_factory=list)


# -- loading -----------------------------------------------------------------

def load_points(cache: Cache, *, chunks: bool = False) -> Points:
    pages = {row[0]: row for row in cache.conn.execute(
        "SELECT id, slug, COALESCE(title, slug), page_type, summary, path, mtime_ns, embedded_hash "
        "FROM pages WHERE kind = 'page'")}
    if not cache.has_vectors:
        return Points("chunks" if chunks else "pages", [], np.zeros((0, 0), dtype=np.float32), [])
    now = time.time()
    rows: list[dict] = []
    blobs: list[bytes] = []
    keys: list[tuple] = []

    def page_row(page) -> dict:
        page_id, slug, title, page_type, summary, path, mtime_ns, _ = page
        return {"slug": slug, "title": title, "type": page_type, "summary": summary, "path": path,
                "age_days": round(max(now - mtime_ns / 1e9, 0) / 86400, 1)}

    if chunks:
        chunk_pages = {chunk_id: (page_id, heading) for chunk_id, page_id, heading in cache.conn.execute(
            "SELECT id, page_id, heading_path FROM chunks")}
        for chunk_id, blob in cache.conn.execute("SELECT rowid, embedding FROM chunk_vectors ORDER BY rowid"):
            page_id, heading = chunk_pages.get(chunk_id, (None, None))
            if page_id not in pages:
                continue
            rows.append({**page_row(pages[page_id]), "chunk": chunk_id, "section": heading})
            blobs.append(blob)
            keys.append((chunk_id, pages[page_id][7]))
    else:
        for page_id, blob in cache.conn.execute("SELECT rowid, embedding FROM summary_vectors ORDER BY rowid"):
            if page_id not in pages:
                continue
            rows.append(page_row(pages[page_id]))
            blobs.append(blob)
            keys.append((page_id, pages[page_id][7]))
    vectors = np.frombuffer(b"".join(blobs), dtype=np.float32).reshape(len(blobs), -1) if blobs else \
        np.zeros((0, 0), dtype=np.float32)
    return Points("chunks" if chunks else "pages", rows, _unit(vectors), keys)


# -- layout ------------------------------------------------------------------

def fit(points: Points, method: str, *, store: Path | None = None, embed_model: str | None = None,
        progress: Callable[[str], None] | None = None) -> Layout:
    """Lay ``points`` out in 3D. ``auto`` uses UMAP and falls back to PCA with a reason;
    an explicit ``umap`` fails instead."""
    if method not in METHODS:
        raise MapError(f"unknown method '{method}'; choose one of {', '.join(METHODS)}")
    if method in ("auto", "umap"):
        version = _umap_version()
        reason = version if version.startswith("cannot import") else None
        if reason is None:
            key = _key(points, "umap", embed_model, version)
            layout = _load(store, key)
            if layout is not None:
                return layout
            try:
                if progress:
                    progress(f"Fitting UMAP on {len(points)} {points.kind}")
                layout = _fit_umap(points.vectors)
            except Exception as exc:  # noqa: BLE001 - any failure is reported, then PCA is used
                reason = f"fitting failed: {type(exc).__name__}: {exc}"
            else:
                _save(store, key, layout)
                return layout
        if method == "umap":
            raise MapError(f"UMAP cannot be used ({reason}); run without --method umap to use PCA instead")
        layout = _fit_pca(points.vectors)
        layout.fallback_reason = reason
        return layout
    return _fit_pca(points.vectors)


def _umap_version() -> str:
    """umap-learn's version, or 'cannot import umap: <why>'."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            import umap  # noqa: F401 - heavy (numba); only this command imports it
    except Exception as exc:  # noqa: BLE001 - ImportError, or a broken numba/llvmlite install
        return f"cannot import umap: {type(exc).__name__}: {exc}"
    return str(getattr(umap, "__version__", "unknown"))


def _fit_umap(vectors: np.ndarray) -> Layout:
    import umap

    count = len(vectors)
    if count < 4:
        raise ValueError(f"{count} points are too few for UMAP (it takes 4)")
    model = umap.UMAP(n_components=3, metric="cosine", n_neighbors=min(NEIGHBORS, count - 1),
                      random_state=SEED, init="spectral" if count > 10 else "random")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # n_jobs is forced to 1 by the seed; small graphs warn on init
        coords = np.asarray(model.fit_transform(vectors), dtype=np.float64)
    if not np.all(np.isfinite(coords)):
        raise ValueError("UMAP produced non-finite coordinates")
    return Layout("umap", coords, model)


def _fit_pca(vectors: np.ndarray) -> Layout:
    mean = vectors.mean(axis=0) if len(vectors) else np.zeros(vectors.shape[1:], dtype=np.float32)
    centered = vectors - mean
    if len(vectors) > 1:
        _, _, vt = np.linalg.svd(centered, full_matrices=False)
        components = vt[:3]
        # SVD signs are arbitrary; fix them so the same wiki always gives the same picture.
        signs = np.sign(components[np.arange(len(components)), np.abs(components).argmax(axis=1)])
        components = components * np.where(signs == 0, 1, signs)[:, None]
    else:
        components = np.zeros((0, vectors.shape[1]), dtype=np.float32)
    return Layout("pca", _pad(centered @ components.T), (mean, components))


def _key(points: Points, method: str, embed_model: str | None, version: str) -> str:
    digest = hashlib.sha256()
    digest.update(json.dumps([LAYOUT_VERSION, points.kind, method, embed_model, version, SEED]).encode())
    for point_id, embedded in points.key_parts:
        digest.update(f"{point_id}:{embedded};".encode())
    return digest.hexdigest()


def _load(store: Path | None, key: str) -> Layout | None:
    if store is None or not store.is_file():
        return None
    try:
        with store.open("rb") as handle:
            saved = pickle.load(handle)  # written by _save below, beside the cache
        if saved.get("key") != key:
            return None
        return Layout("umap", saved["coords"], saved["model"], cached=True)
    except Exception:  # noqa: BLE001 - an unreadable layout is refitted
        return None


def _save(store: Path | None, key: str, layout: Layout) -> None:
    if store is None:
        return
    try:
        store.parent.mkdir(parents=True, exist_ok=True)
        temporary = store.with_suffix(".tmp")
        with temporary.open("wb") as handle:
            pickle.dump({"key": key, "coords": layout.coords, "model": layout.model}, handle)
        temporary.replace(store)
    except Exception:  # noqa: BLE001 - the layout is only a speed-up
        pass


# -- the map -----------------------------------------------------------------

def build(cache: Cache, *, method: str = "auto", chunks: bool = False, color_by: str = "type",
          query: str | None = None, nav: str | None = None, embedder: Embedder | None = None,
          searcher: Callable[[str], list] | None = None,
          progress: Callable[[str], None] | None = None) -> MapResult:
    """Everything the viewer draws. ``embedder`` must be the cache's model (for a query
    or a nav session's follow-up searches); ``searcher`` returns search hits for a query."""
    if color_by not in COLOR_BY:
        raise MapError(f"unknown --color-by '{color_by}'; choose one of {', '.join(COLOR_BY)}")
    if color_by == "visits" and not nav:
        raise MapError("--color-by visits needs --nav")
    points = load_points(cache, chunks=chunks)
    if not len(points):
        raise MapError("no embedded pages to map; run 'wiki index refresh'")
    notes: list[str] = []
    pages = len({row["slug"] for row in points.rows})
    if pages < clusters.MIN_WIKI_PAGES:
        notes.append(f"{pages} pages are too few for the layout to mean much (it takes {clusters.MIN_WIKI_PAGES})")
    pending = cache.pending_embeddings()
    if pending:
        notes.append(f"{pending} files are not embedded yet, or changed since; run 'wiki index refresh'")

    store = cache.settings.cache_path.parent / f"map-{points.kind}.pickle"
    layout = fit(points, method, store=store, embed_model=cache.embed_model, progress=progress)
    if layout.fallback_reason:
        notes.insert(0, f"UMAP unavailable ({layout.fallback_reason}); used PCA instead")

    community = clusters.membership(cache)
    by_page: dict[str, list[int]] = {}
    by_chunk: dict[int, int] = {}
    for index, row in enumerate(points.rows):
        by_page.setdefault(row["slug"], []).append(index)
        if "chunk" in row:
            by_chunk[row["chunk"]] = index
        row["cluster"] = community.get(row["slug"])

    def point_of(slug: str | None, chunk: int | None = None, section: str | None = None) -> int | None:
        if chunk is not None and chunk in by_chunk:
            return by_chunk[chunk]
        indexes = by_page.get(slug or "")
        if not indexes:
            return None
        if section:
            for index in indexes:
                if points.rows[index].get("section") == section:
                    return index
        return indexes[0]

    def place(text: str, vector=None) -> list[float] | None:
        if vector is None:
            if embedder is None:
                return None
            vector = embedder.embed_query(text)
        vector = _unit(np.atleast_2d(np.asarray(vector, dtype=np.float32)))
        if vector.shape[1] != points.vectors.shape[1]:
            return None
        return layout.place(vector, points)[0].tolist()

    query_part = None
    if query:
        if embedder is None:
            raise MapError("placing a question needs the embedding model the cache was built with")
        vector = _unit(np.atleast_2d(np.asarray(embedder.embed_query(query), dtype=np.float32)))
        similarity = points.vectors @ vector[0]
        nearest = [int(index) for index in np.argsort(-similarity)]
        hits = []
        for rank, hit in enumerate(searcher(query) if searcher else [], start=1):
            index = point_of(hit.slug, hit.chunk_id if chunks else None, hit.section)
            vector_rank = nearest.index(index) + 1 if index is not None else None
            hits.append({"rank": rank, "slug": hit.slug, "section": hit.section or None, "point": index,
                         "score": round(hit.score, 4),
                         "vector_rank": vector_rank if vector_rank and vector_rank <= NEAREST else None})
        query_part = {"text": query, "pos": layout.place(vector, points)[0].tolist(), "hits": hits,
                      "nearest": [_point_label(points, index) for index in nearest[:NEAREST]]}

    nav_part = _nav(cache, nav, point_of, place) if nav else []
    if nav and not nav_part:
        notes.append("no navigation sessions to draw (sessions live in the cache and expire)")
    visits = [0] * len(points)
    for session in nav_part:
        for step in session["steps"]:
            if step.get("point") is not None and step["kind"] == "read":
                visits[step["point"]] += 1
    for row, count in zip(points.rows, visits):
        row["visits"] = count

    edges = [] if chunks else _edges(cache, by_page)
    _scale(layout.coords, query_part, nav_part)
    for row, position in zip(points.rows, layout.coords):
        row["pos"] = [round(float(value), 3) for value in position]

    payload = {
        "root": cache.settings.root.name,
        "generated": time.strftime("%Y-%m-%d %H:%M"),
        "embed_model": cache.embed_model,
        "method": layout.method,
        "fallback_reason": layout.fallback_reason,
        "points_kind": points.kind,
        "color_by": color_by,
        "points": points.rows,
        "edges": edges,
        "query": query_part,
        "nav": nav_part,
        "notes": notes,
    }
    return MapResult(payload, notes)


def _point_label(points: Points, index: int) -> dict:
    row = points.rows[index]
    return {"point": index, "slug": row["slug"], **({"section": row["section"]} if "section" in row else {})}


def _edges(cache: Cache, by_page: dict[str, list[int]]) -> list[dict]:
    edges = []
    seen = set()
    for source, target, relation in cache.conn.execute(
            "SELECT source_slug, target_slug, relation_type FROM relations WHERE resolved = 1 "
            "ORDER BY source_slug, target_slug"):
        if source == target or source not in by_page or target not in by_page or (source, target) in seen:
            continue
        seen.add((source, target))
        edges.append({"s": by_page[source][0], "t": by_page[target][0], "relation": relation,
                      "typed": relation not in clusters.PLAIN})
    return edges


def _nav(cache: Cache, which: str, point_of, place) -> list[dict]:
    """Navigation sessions as paths: the question, then each step in order."""
    recorded = cache.conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'nav_sessions'").fetchone()
    if not recorded:  # the nav tables are created by the first session
        ids = []
    elif which == "all":
        ids = [row[0] for row in cache.conn.execute("SELECT id FROM nav_sessions ORDER BY created")]
    elif which == "last":
        ids = [row[0] for row in cache.conn.execute("SELECT id FROM nav_sessions ORDER BY created DESC LIMIT 1")]
    else:
        ids = [row[0] for row in cache.conn.execute("SELECT id FROM nav_sessions WHERE id = ?", (which,))]
    if not ids and which not in ("all", "last"):
        raise MapError(f"no navigation session '{which}' (sessions live in the cache and expire)")

    sessions = []
    for session_id in ids:
        question, vector, model, ended, cited = cache.conn.execute(
            "SELECT question, query_vector, embed_model, ended, cited FROM nav_sessions WHERE id = ?",
            (session_id,)).fetchone()
        usable = vector if vector and model == cache.embed_model else None
        start = place(question, np.frombuffer(usable, dtype=np.float32) if usable else None)
        steps = [{"kind": "question", "text": question, "pos": start}]
        for kind, slug, detail in cache.conn.execute(
                "SELECT kind, slug, detail FROM nav_events WHERE session_id = ? ORDER BY seq", (session_id,)):
            detail = json.loads(detail or "{}")
            if kind == "read":
                steps.append({"kind": "read", "slug": slug, "point": point_of(slug, section=detail.get("part")),
                              "why": detail.get("why"), "part": detail.get("part") or None})
            elif kind == "search":
                steps.append({"kind": "search", "text": detail.get("query", ""),
                              "pos": place(detail.get("query", ""))})
        sessions.append({"session": session_id, "question": question, "ended": bool(ended),
                         "cited": json.loads(cited) if cited else [], "steps": steps})
    return sessions


def _scale(coords: np.ndarray, query_part: dict | None, nav_part: list[dict]) -> None:
    """Center the points and scale most of them (all but outlying islands) to RADIUS,
    moving overlay positions the same way."""
    center = np.median(coords, axis=0) if len(coords) else np.zeros(3)
    spread = float(np.percentile(np.linalg.norm(coords - center, axis=1), SPREAD_PERCENTILE)) if len(coords) else 0.0
    factor = RADIUS / spread if spread > 1e-9 else 1.0
    coords -= center
    coords *= factor

    def move(position):
        if position is None:
            return None
        return [round(float(value), 3) for value in (np.asarray(position) - center) * factor]

    if query_part:
        query_part["pos"] = move(query_part["pos"])
    for session in nav_part:
        for step in session["steps"]:
            if "pos" in step:
                step["pos"] = move(step["pos"])


def _unit(vectors: np.ndarray) -> np.ndarray:
    vectors = np.asarray(vectors, dtype=np.float32)
    if not vectors.size:
        return vectors
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.maximum(norms, 1e-12)


def _pad(coords: np.ndarray) -> np.ndarray:
    """Three columns, filling missing ones with zeros (fewer than 3 components)."""
    coords = np.asarray(coords, dtype=np.float64).reshape(len(coords), -1)
    if coords.shape[1] < 3:
        coords = np.hstack([coords, np.zeros((len(coords), 3 - coords.shape[1]))])
    return coords


def _nearest_average(vectors: np.ndarray, known: np.ndarray, coords: np.ndarray, k: int = 5) -> np.ndarray:
    """Place each vector at the similarity-weighted mean of its k nearest known points."""
    similarity = _unit(vectors) @ known.T
    placed = []
    for row in similarity:
        top = np.argsort(-row)[:k]
        weights = np.maximum(row[top], 0) + 1e-6
        placed.append((coords[top] * weights[:, None]).sum(axis=0) / weights.sum())
    return np.asarray(placed)


# -- the page ----------------------------------------------------------------

def render(payload: dict) -> str:
    """The self-contained HTML page: template, the viewer library and the data, inlined."""
    folder = resources.files("wiki_cli") / "map"
    template = (folder / "template.html").read_text(encoding="utf-8")
    library = (folder / "3d-force-graph.min.js").read_text(encoding="utf-8")
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return (template.replace("/*__LIBRARY__*/", library.replace("</script", "<\\/script"))
            .replace("/*__DATA__*/null", data))


def write(payload: dict, cache_path: Path) -> Path:
    path = cache_path.parent / HTML_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(payload), encoding="utf-8", newline="\n")
    return path
