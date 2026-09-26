import os
import sqlite3
import threading
import time

import pytest

from wiki_cli import cache as cache_module
from wiki_cli.cache import Cache, CacheUnavailable
from wiki_cli.pages import discover, load
from wiki_cli.validation import compare_cache


def bump(path):
    """Force a distinct mtime so change detection does not depend on clock resolution."""
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))


@pytest.fixture
def graph(wiki):
    wiki.page("pipeline", ("store", "depends-on", "Persists to the store."))
    wiki.page("editor", ("pipeline", "depends-on", "Saves through the pipeline."),
              ("widget", "implemented-by", "Concrete widget."),
              ("wiki-other", "related-to", "Other."))
    wiki.page("widget")
    wiki.page("store")
    wiki.page("tests/editor", ("editor", "tested-by", "Wrong direction but valid."))
    wiki.write("editor-old", {"title": "old", "summary": "Old.", "superseded_by": "editor"})
    return wiki


def slugs(results, direction=None):
    return [(r["slug"], r["type"]) for r in results if direction is None or r["direction"] == direction]


def test_neighbors_outgoing_and_incoming(graph):
    with Cache(graph.settings()) as cache:
        assert cache.ensure_fresh("editor")
        results = cache.neighbors("editor")
    assert slugs(results, "outgoing") == [
        ("pipeline", "depends-on"), ("widget", "implemented-by"), ("wiki-other", "related-to")]
    incoming = [r for r in results if r["direction"] == "incoming"]
    assert [(r["slug"], r["type"], r["inverse"]) for r in incoming] == [
        ("editor-old", "superseded-by", "supersedes"), ("tests/editor", "tested-by", "tests")]
    unresolved = next(r for r in results if r["slug"] == "wiki-other")
    assert unresolved["unresolved"] is True


def test_neighbors_filters_and_limit(graph):
    with Cache(graph.settings()) as cache:
        cache.refresh()
        assert slugs(cache.neighbors("editor", incoming=False, relation_type="depends-on")) == [("pipeline", "depends-on")]
        assert slugs(cache.neighbors("pipeline", outgoing=False)) == [("editor", "depends-on")]
        assert len(cache.neighbors("editor", limit=2)) == 2


def test_external_targets_are_marked(wiki):
    wiki.write("a", {"title": "a", "relations": [{"target": "wiki://other/x", "type": "related-to", "reason": "r"}]})
    with Cache(wiki.settings()) as cache:
        cache.refresh()
        [entry] = cache.neighbors("a")
    assert entry == {"slug": "x", "direction": "outgoing", "type": "related-to", "reason": "r",
                     "external": True, "target": "wiki://other/x"}


def test_same_slug_in_another_space_is_not_incoming(wiki):
    wiki.page("x")
    wiki.write("a", {"title": "a", "relations": [{"target": "wiki://other/x", "type": "related-to", "reason": "r"}]})
    with Cache(wiki.settings()) as cache:
        cache.refresh()
        assert cache.neighbors("x") == []


def test_incremental_refresh(graph):
    settings = graph.settings()
    with Cache(settings) as cache:
        first = cache.refresh()
        assert first.added == 6
        assert cache.refresh().unchanged == 6

        graph.page("new", ("store", "depends-on", "x"))
        widget = graph.root / "widget.md"
        graph.page("widget", ("store", "used-by", "changed"))
        bump(widget)
        (graph.root / "store.md").rename(graph.root / "moved-store.md")
        (graph.root / "editor-old.md").unlink()
        stats = cache.refresh()

    assert stats.added == 2  # new + moved-store
    assert stats.changed == 1
    assert stats.removed == 2  # store + editor-old
    assert stats.moved == 1


def test_touched_file_with_same_content_is_not_reparsed(graph, monkeypatch):
    with Cache(graph.settings()) as cache:
        cache.refresh()
        bump(graph.root / "widget.md")
        monkeypatch.setattr(cache_module, "parse", lambda *a, **k: pytest.fail("unchanged content was reparsed"))
        stats = cache.refresh()
    assert stats.touched == 1 and stats.changed == 0


def test_ensure_fresh_refreshes_single_changed_page(graph):
    with Cache(graph.settings()) as cache:
        cache.refresh()
        graph.page("editor", ("store", "configures", "Now configures."))
        bump(graph.root / "editor.md")
        assert cache.ensure_fresh("editor")
        assert slugs(cache.neighbors("editor", incoming=False)) == [("store", "configures")]


def test_ensure_fresh_after_rebuild_does_not_rescan(graph, monkeypatch):
    with Cache(graph.settings()) as cache:
        cache.rebuild()
        monkeypatch.setattr(Cache, "refresh", lambda self: pytest.fail("full refresh after rebuild"))
        assert cache.ensure_fresh("editor")


def test_ensure_fresh_finds_new_page(graph):
    with Cache(graph.settings()) as cache:
        cache.refresh()
        graph.page("brand-new")
        assert cache.ensure_fresh("brand-new")
        assert not cache.ensure_fresh("never-existed")


def test_schema_version_mismatch_rebuilds(graph):
    settings = graph.settings()
    with Cache(settings) as cache:
        cache.refresh()
        cache.conn.execute("UPDATE metadata SET value = 'old' WHERE key = 'schema_version'")
    with Cache(settings) as cache:
        assert cache.rebuilt
        assert cache.status()["pages"] == 0
        cache.ensure_fresh("editor")
        assert cache.status()["pages"] == 6


def test_readonly_requires_current_cache(graph):
    with pytest.raises(CacheUnavailable):
        Cache(graph.settings(), readonly=True)


def test_rollback_on_interrupted_refresh(graph, monkeypatch):
    settings = graph.settings()
    with Cache(settings) as cache:
        cache.refresh()
        before = cache.snapshot()
        graph.page("editor", ("store", "configures", "changed"))
        graph.page("widget", ("store", "configures", "changed"))
        bump(graph.root / "editor.md")
        bump(graph.root / "widget.md")
        real_store = Cache._store
        calls = []

        def failing_store(self, page, stat):
            calls.append(page.slug)
            if len(calls) == 2:
                raise RuntimeError("interrupted")
            real_store(self, page, stat)

        monkeypatch.setattr(Cache, "_store", failing_store)
        with pytest.raises(RuntimeError):
            cache.refresh()
        assert cache.snapshot() == before


def test_concurrent_reader_sees_consistent_state(wiki):
    for i in range(200):
        wiki.page(f"p{i}", (f"p{(i + 1) % 200}", "depends-on", "next"))
    settings = wiki.settings()
    with Cache(settings) as cache:
        cache.refresh()
    for i in range(200):
        wiki.page(f"p{i}", (f"p{(i + 1) % 200}", "depends-on", "next"), (f"p{(i + 2) % 200}", "used-by", "n2"))
        bump(wiki.root / f"p{i}.md")

    counts = []
    done = threading.Event()

    def reader():
        conn = sqlite3.connect(settings.cache_path, timeout=30)
        while not done.is_set():
            counts.append(conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0])
            time.sleep(0.001)
        conn.close()

    thread = threading.Thread(target=reader)
    thread.start()
    with Cache(settings) as cache:
        cache.refresh()
    done.set()
    thread.join()
    assert counts and set(counts) <= {200, 400}


def test_verify_cache_detects_drift(graph):
    settings = graph.settings()
    with Cache(settings) as cache:
        cache.refresh()
        cache.conn.execute("UPDATE relations SET reason = 'tampered' WHERE source_slug = 'editor'")
        snapshot = cache.snapshot()
    corpus = [load(page_file, "sp") for page_file in discover(settings)]
    issues = compare_cache(corpus, snapshot, "sp")
    assert [(issue.code, issue.slug) for issue in issues] == [("cache-mismatch", "editor")]


def test_summary_staleness(wiki):
    wiki.page("a", summary="Original.")
    settings = wiki.settings()
    with Cache(settings) as cache:
        cache.refresh()
        assert cache.stale_summaries() == {}

        wiki.write("a", {"title": "a", "summary": "Original."}, body="Body rewritten entirely.\n")
        bump(wiki.root / "a.md")
        cache.refresh()
        assert "a.md" in cache.stale_summaries()

        wiki.write("a", {"title": "a", "summary": "Updated."}, body="Body rewritten entirely.\n")
        bump(wiki.root / "a.md")
        cache.refresh()
        assert cache.stale_summaries() == {}


def test_relation_edit_alone_does_not_stale_summary(wiki):
    wiki.page("a", summary="S.")
    with Cache(wiki.settings()) as cache:
        cache.refresh()
        wiki.page("a", ("b", "depends-on", "x"), summary="S.")
        bump(wiki.root / "a.md")
        cache.refresh()
        assert cache.stale_summaries() == {}


def test_placeholder_summary_stored(wiki):
    wiki.write("a", {"title": "a"}, body="First paragraph here.\n")
    with Cache(wiki.settings()) as cache:
        cache.refresh()
        row = cache.conn.execute("SELECT summary, summary_is_placeholder FROM pages").fetchone()
        assert row == ("First paragraph here.", 1)
        assert cache.status()["placeholder_summaries"] == 1


def test_files_without_frontmatter_are_not_pages(wiki):
    wiki.write("plain", raw="# no frontmatter\n")
    wiki.page("a", ("plain", "depends-on", "x"))
    with Cache(wiki.settings()) as cache:
        cache.refresh()
        assert cache.status()["pages"] == 1
        assert cache.neighbors("a")[0]["unresolved"] is True
        assert not cache.ensure_fresh("plain")


def test_lookups_use_indexes(wiki):
    with Cache(wiki.settings()) as cache:
        for column in ("source_slug", "target_slug"):
            plan = cache.conn.execute(
                f"EXPLAIN QUERY PLAN SELECT * FROM relations WHERE {column} = ?", ("x",)).fetchall()
            assert any("USING INDEX" in row[-1] for row in plan), plan
