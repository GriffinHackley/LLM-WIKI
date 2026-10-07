"""`wiki map`: embeddings laid out in 3D, with a question and nav sessions placed in them."""

import json
import sys

import numpy as np
import pytest

from wiki_cli import vecmap
from wiki_cli.cache import Cache
from wiki_cli.cli import main
from wiki_cli.config import load_settings
from wiki_cli.models import HashEmbedder, OverlapReranker
from wiki_cli.nav import Navigator

TOPICS = {
    "river": "river water flood bank boat fishing current delta",
    "orbit": "orbit planet rocket launch moon gravity satellite telescope",
}


def page(root, slug, summary, links=()):
    related = "\n".join(f"- [[{target}]] — related" for target in links)
    (root / f"{slug}.md").write_text(
        f"---\ntitle: {slug}\ntype: note\n---\n# {slug}\n\n## Summary\n{summary}\n\n## Details\n"
        f"More about {summary.split()[0]}.\n\n## Related\n{related}\n", encoding="utf-8")


@pytest.fixture
def root(tmp_path):
    """Two topics of 18 pages each, every page linking its neighbours in the topic."""
    root = tmp_path / "w"
    root.mkdir()
    (root / ".wiki-cli.toml").write_text("[types.note]\n", encoding="utf-8")
    for topic, words in TOPICS.items():
        for index in range(18):
            page(root, f"{topic}-{index}", f"{words} part {index}",
                 [f"{topic}-{(index + 1) % 18}", f"{topic}-{(index + 2) % 18}"])
    assert main(["index", "refresh", "--root", str(root), "--format", "json"]) == 0
    return root


def run(root, capsys, *args):
    code = main(["map", *args, "--root", str(root)])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def run_json(root, capsys, *args):
    code, out, err = run(root, capsys, *args, "--format", "json")
    return code, (json.loads(out) if out.strip() else None), err


def no_umap(monkeypatch):
    monkeypatch.setitem(sys.modules, "umap", None)  # import umap now raises ImportError


def test_pca_map_points_edges_and_metadata(root, capsys):
    code, result, err = run_json(root, capsys, "--method", "pca")
    assert code == 0 and err == ""
    assert result["method"] == "pca" and result["fallback_reason"] is None
    assert result["points_kind"] == "pages" and len(result["points"]) == 36
    point = next(p for p in result["points"] if p["slug"] == "river-3")
    assert len(point["pos"]) == 3 and point["type"] == "note" and point["path"] == "river-3.md"
    assert point["cluster"] is not None and point["visits"] == 0
    assert len(result["edges"]) == 72 and not any(edge["typed"] for edge in result["edges"])
    assert result["notes"] == [] and result["query"] is None and result["nav"] == []


def test_pca_is_deterministic_and_separates_topics(root, capsys):
    _, first, _ = run_json(root, capsys, "--method", "pca")
    _, second, _ = run_json(root, capsys, "--method", "pca")
    assert first["points"] == second["points"]
    centers = {topic: np.mean([p["pos"] for p in first["points"] if p["slug"].startswith(topic)], axis=0)
               for topic in TOPICS}
    assert np.linalg.norm(centers["river"] - centers["orbit"]) > 30


def test_umap_is_default_deterministic_and_cached(root, capsys):
    code, first, err = run_json(root, capsys)
    assert code == 0 and first["method"] == "umap" and first["fallback_reason"] is None
    assert "UMAP" not in err
    store = root / ".cache" / "map-pages.pickle"
    assert store.is_file()
    _, cached, _ = run_json(root, capsys)
    store.unlink()
    _, refitted, _ = run_json(root, capsys)
    assert first["points"] == cached["points"] == refitted["points"]


def test_umap_unavailable_falls_back_to_pca_and_says_so(root, capsys, monkeypatch):
    no_umap(monkeypatch)
    code, result, err = run_json(root, capsys)
    assert code == 0 and result["method"] == "pca"
    assert result["fallback_reason"].startswith("cannot import umap")
    assert "UMAP unavailable" in err and "used PCA instead" in err
    assert result["notes"][0].startswith("UMAP unavailable")

    code, out, err = run(root, capsys)
    assert code == 0 and "(PCA)" in out and "used PCA instead" in err
    html = (root / ".cache" / "map.html").read_text(encoding="utf-8")
    assert "cannot import umap" in html and "UMAP unavailable" in html


def test_explicit_umap_fails_instead_of_falling_back(root, capsys, monkeypatch):
    no_umap(monkeypatch)
    code, out, err = run(root, capsys, "--method", "umap")
    assert code == 2 and out == ""
    assert "UMAP cannot be used" in err and "cannot import umap" in err


def test_umap_fit_failure_falls_back(root, capsys, monkeypatch):
    def broken(vectors):
        raise RuntimeError("no spectral layout")
    monkeypatch.setattr(vecmap, "_fit_umap", broken)
    code, result, err = run_json(root, capsys)
    assert code == 0 and result["method"] == "pca"
    assert result["fallback_reason"] == "fitting failed: RuntimeError: no spectral layout"
    assert "used PCA instead" in err


def test_html_is_self_contained(root, capsys):
    code, out, _ = run(root, capsys, "--method", "pca")
    path = root / ".cache" / "map.html"
    assert code == 0 and str(path) in out and "36 pages" in out
    html = path.read_text(encoding="utf-8")
    assert "ForceGraph3D" in html and '"slug":"river-3"' in html
    assert "/*__DATA__*/" not in html and "/*__LIBRARY__*/" not in html
    assert html.count("</script>") == 2  # nothing inlined closes a script early
    assert "cdn" not in html.split("<script>")[0].lower()  # no remote resources


def test_query_is_placed_with_lines_to_search_results(root, capsys):
    code, result, _ = run_json(root, capsys, "--method", "pca", "--query", "rocket launch to the moon", "--limit", "4")
    assert code == 0
    query = result["query"]
    assert query["text"] == "rocket launch to the moon" and len(query["pos"]) == 3
    assert [hit["rank"] for hit in query["hits"]] == [1, 2, 3, 4]
    for hit in query["hits"]:
        assert hit["slug"].startswith("orbit") and result["points"][hit["point"]]["slug"] == hit["slug"]
    assert len(query["nearest"]) == vecmap.NEAREST
    assert all(near["slug"].startswith("orbit") for near in query["nearest"][:3])
    orbit = np.mean([p["pos"] for p in result["points"] if p["slug"].startswith("orbit")], axis=0)
    river = np.mean([p["pos"] for p in result["points"] if p["slug"].startswith("river")], axis=0)
    assert np.linalg.norm(np.array(query["pos"]) - orbit) < np.linalg.norm(np.array(query["pos"]) - river)


def test_pca_places_new_vectors_by_the_fitted_projection(root):
    with Cache(load_settings(root)) as cache:
        points = vecmap.load_points(cache)
    layout = vecmap.fit(points, "pca")
    assert np.allclose(layout.place(points.vectors, points), layout.coords, atol=1e-4)


def test_umap_places_training_vectors_near_their_points(root):
    with Cache(load_settings(root)) as cache:
        points = vecmap.load_points(cache)
    layout = vecmap.fit(points, "umap")
    placed = layout.place(points.vectors[:3], points)
    spread = np.ptp(layout.coords, axis=0).max()
    assert np.all(np.linalg.norm(placed - layout.coords[:3], axis=1) < spread / 4)


def test_nav_session_drawn_as_a_path(root, capsys):
    with Cache(load_settings(root)) as cache:
        navigator = Navigator(cache, embedder=HashEmbedder(), reranker=OverlapReranker())
        session = navigator.start("river flood boat")["session"]
        navigator.read(session, "river-2", "it is about floods")
        navigator.requery(session, "fishing in the delta")
        navigator.read(session, "river-5", "fishing")
        navigator.end(session, ["river-2"])
    code, result, _ = run_json(root, capsys, "--method", "pca", "--nav", "last", "--color-by", "visits")
    assert code == 0 and result["color_by"] == "visits"
    [drawn] = result["nav"]
    assert drawn["session"] == session and drawn["ended"] and drawn["cited"] == ["river-2"]
    assert [step["kind"] for step in drawn["steps"]] == ["question", "read", "search", "read"]
    question, read, search, _ = drawn["steps"]
    assert question["text"] == "river flood boat" and len(question["pos"]) == 3
    assert result["points"][read["point"]]["slug"] == "river-2" and read["why"] == "it is about floods"
    assert search["text"] == "fishing in the delta" and len(search["pos"]) == 3
    visited = {p["slug"]: p["visits"] for p in result["points"] if p["visits"]}
    assert visited == {"river-2": 1, "river-5": 1}

    code, by_id, _ = run_json(root, capsys, "--method", "pca", "--nav", session)
    assert code == 0 and by_id["nav"] == result["nav"]


def test_nav_unknown_session_and_no_sessions(root, capsys):
    code, _, err = run(root, capsys, "--method", "pca", "--nav", "abc123")
    assert code == 2 and "no navigation session 'abc123'" in err
    code, result, err = run_json(root, capsys, "--method", "pca", "--nav", "all")
    assert code == 0 and result["nav"] == [] and "no navigation sessions" in err


def test_visits_colouring_needs_nav(root, capsys):
    code, _, err = run(root, capsys, "--method", "pca", "--color-by", "visits")
    assert code == 2 and "needs --nav" in err


def test_chunks_map_one_point_per_section(root, capsys):
    code, result, _ = run_json(root, capsys, "--method", "pca", "--chunks", "--query", "planet gravity")
    assert code == 0 and result["points_kind"] == "chunks"
    with Cache(load_settings(root)) as cache:
        chunks = cache.conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    assert len(result["points"]) == chunks > 36
    assert all("section" in point and "chunk" in point for point in result["points"])
    assert result["edges"] == []
    for hit in result["query"]["hits"]:
        assert result["points"][hit["point"]]["slug"] == hit["slug"]


def test_small_wiki_is_drawn_with_a_note(tmp_path, capsys):
    root = tmp_path / "small"
    root.mkdir()
    for index in range(5):
        page(root, f"p-{index}", f"river water part {index}")
    assert main(["index", "refresh", "--root", str(root), "--format", "json"]) == 0
    capsys.readouterr()
    code, result, err = run_json(root, capsys, "--method", "pca")
    assert code == 0 and len(result["points"]) == 5
    assert "too few for the layout to mean much" in err


def test_no_vectors_says_to_index(tmp_path, capsys):
    root = tmp_path / "bare"
    root.mkdir()
    page(root, "only", "nothing embedded")
    assert main(["index", "refresh", "--no-embed", "--root", str(root), "--format", "json"]) == 0
    capsys.readouterr()
    code, _, err = run(root, capsys)
    assert code == 2 and "wiki index refresh" in err
