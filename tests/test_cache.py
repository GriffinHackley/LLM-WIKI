import sqlite3
import threading
import time

import pytest

from conftest import bump
from wiki_cli import cache as cache_module
from wiki_cli.cache import Cache, CacheUnavailable
from wiki_cli.pages import Resolver, discover, load
from wiki_cli.validation import compare_cache


@pytest.fixture
def graph(wiki):
    wiki.page("person", "mike-johnson", {"Relationships": "- [[adelita-grijalva]] — administered her oath",
                                         "Appearances in sources": "- [[abc-doc]] — quoted (p. 2)"})
    wiki.page("person", "adelita-grijalva")
    wiki.page("document", "abc-doc", {"Entities mentioned": "- [[mike-johnson]] — Speaker\n- [[unwritten-person]]"})
    wiki.page("event", "swearing-in", {"Participants": "- [[mike-johnson]] — presided"})
    return wiki


def pairs(results, direction=None):
    return [(r["slug"], r["type"]) for r in results if direction is None or r["direction"] == direction]


def test_neighbors_both_directions(graph):
    with Cache(graph.settings()) as cache:
        assert cache.ensure_fresh("mike-johnson")
        results = cache.neighbors("mike-johnson")
    assert pairs(results, "outgoing") == [("adelita-grijalva", "associated-with"), ("abc-doc", "appears-in")]
    assert pairs(results, "incoming") == [("swearing-in", "participant-in"), ("abc-doc", "mentioned-in")]
    incoming_doc = next(r for r in results if r["direction"] == "incoming" and r["slug"] == "abc-doc")
    assert incoming_doc["reason"] == "Speaker"


def test_unresolved_targets_marked_and_last(graph):
    with Cache(graph.settings()) as cache:
        cache.refresh()
        results = cache.neighbors("abc-doc", incoming=False)
        assert results[-1] == {"slug": "unwritten-person", "direction": "outgoing", "type": "mentions",
                               "reason": "Listed under Entities mentioned.", "unresolved": True}
        assert "unwritten-person" not in pairs(cache.neighbors("abc-doc", include_unresolved=False))


def test_filters_and_limit(graph):
    with Cache(graph.settings()) as cache:
        cache.refresh()
        assert pairs(cache.neighbors("mike-johnson", incoming=False, relation_type="appears-in")) == [
            ("abc-doc", "appears-in")]
        assert len(cache.neighbors("mike-johnson", limit=2)) == 2


def test_titles_included_when_different(wiki):
    wiki.page("document", "abc-doc", title="ABC News, 2025-10-20")
    wiki.page("person", "p", {"Appearances in sources": "- [[abc-doc]] — quoted"})
    with Cache(wiki.settings()) as cache:
        cache.refresh()
        [entry] = cache.neighbors("p")
    assert entry["title"] == "ABC News, 2025-10-20"


def test_writing_a_page_resolves_links_to_it(graph):
    with Cache(graph.settings()) as cache:
        cache.refresh()
        graph.page("person", "unwritten-person")
        cache.refresh()
        entry = next(r for r in cache.neighbors("abc-doc", incoming=False) if r["slug"] == "unwritten-person")
        assert "unresolved" not in entry
        assert pairs(cache.neighbors("unwritten-person", outgoing=False)) == [("abc-doc", "mentioned-in")]


def test_deleting_a_page_unresolves_links(graph):
    with Cache(graph.settings()) as cache:
        cache.refresh()
        (graph.root / "wiki" / "people" / "adelita-grijalva.md").unlink()
        cache.refresh()
        entry = next(r for r in cache.neighbors("mike-johnson") if r["slug"] == "adelita-grijalva")
        assert entry["unresolved"] is True


def test_duplicate_name_changes_slugs(wiki):
    wiki.write("dossiers/a/claims.md", {"title": "A claims"})
    wiki.page("person", "p", {"Timeline": "See [[claims]]."})
    with Cache(wiki.settings()) as cache:
        cache.refresh()
        assert "unresolved" not in cache.neighbors("p")[0]
        wiki.write("dossiers/b/claims.md", {"title": "B claims"})
        cache.refresh()
        assert cache.page_exists("dossiers/a/claims") and not cache.page_exists("claims")
        assert cache.neighbors("p")[0]["unresolved"] is True  # [[claims]] is now ambiguous


def test_incremental_refresh(graph):
    with Cache(graph.settings()) as cache:
        assert cache.refresh().added == 4
        assert cache.refresh().unchanged == 4
        graph.page("person", "new")
        swearing = graph.page("event", "swearing-in", {"Participants": "- [[adelita-grijalva]] — sworn in"})
        bump(swearing)
        (graph.root / "wiki" / "documents" / "abc-doc.md").rename(graph.root / "wiki" / "documents" / "abc-doc-2.md")
        stats = cache.refresh()
    assert (stats.added, stats.changed, stats.removed, stats.moved) == (2, 1, 1, 1)


def test_touched_file_is_not_reparsed(graph, monkeypatch):
    with Cache(graph.settings()) as cache:
        cache.refresh()
        bump(graph.root / "wiki" / "people" / "mike-johnson.md")
        monkeypatch.setattr(cache_module, "parse", lambda *a, **k: pytest.fail("unchanged content was reparsed"))
        stats = cache.refresh()
    assert stats.touched == 1


def test_ensure_fresh_refreshes_one_changed_page(graph, monkeypatch):
    with Cache(graph.settings()) as cache:
        cache.refresh()
        path = graph.page("person", "mike-johnson", {"Relationships": "- [[swearing-in]] — presided"})
        bump(path)
        monkeypatch.setattr(Cache, "refresh", lambda self: pytest.fail("full scan for one changed page"))
        assert cache.ensure_fresh("mike-johnson")
        assert pairs(cache.neighbors("mike-johnson", incoming=False)) == [("swearing-in", "associated-with")]


def test_ensure_fresh_after_rebuild_does_not_rescan(graph, monkeypatch):
    with Cache(graph.settings()) as cache:
        cache.rebuild()
        monkeypatch.setattr(Cache, "refresh", lambda self: pytest.fail("full refresh after rebuild"))
        assert cache.ensure_fresh("mike-johnson")


def test_schema_mismatch_rebuilds(graph):
    settings = graph.settings()
    with Cache(settings) as cache:
        cache.refresh()
        cache.conn.execute("UPDATE metadata SET value = 'old' WHERE key = 'schema_version'")
    with Cache(settings) as cache:
        assert cache.rebuilt and cache.status()["pages"] == 0


def test_readonly_requires_existing_cache(graph):
    with pytest.raises(CacheUnavailable):
        Cache(graph.settings(), readonly=True)


def test_rollback_on_interrupted_refresh(graph, monkeypatch):
    with Cache(graph.settings()) as cache:
        cache.refresh()
        before = cache.snapshot()
        for slug in ("mike-johnson", "adelita-grijalva"):
            bump(graph.page("person", slug, {"Timeline": "Changed."}))
        real_store, calls = Cache._store, []

        def failing_store(self, page, stat, resolver):
            calls.append(page.slug)
            if len(calls) == 2:
                raise RuntimeError("interrupted")
            real_store(self, page, stat, resolver)

        monkeypatch.setattr(Cache, "_store", failing_store)
        with pytest.raises(RuntimeError):
            cache.refresh()
        assert cache.snapshot() == before


def test_concurrent_reader_sees_consistent_state(wiki):
    for i in range(150):
        wiki.page("person", f"p{i}", {"Relationships": f"- [[p{(i + 1) % 150}]] — next"})
    settings = wiki.settings()
    with Cache(settings) as cache:
        cache.refresh()
    for i in range(150):
        bump(wiki.page("person", f"p{i}", {"Relationships": f"- [[p{(i + 1) % 150}]] — next\n- [[p{(i + 2) % 150}]] — two"}))
    counts, done = [], threading.Event()

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
    assert counts and set(counts) <= {150, 300}


def test_verify_cache_detects_drift(graph):
    settings = graph.settings()
    with Cache(settings) as cache:
        cache.refresh()
        cache.conn.execute("UPDATE relations SET relation_type = 'links-to' WHERE source_slug = 'abc-doc'")
        snapshot = cache.snapshot()
    files = discover(settings)
    issues = compare_cache([load(f) for f in files], snapshot, Resolver([(f.slug, f.rel) for f in files]))
    assert [(issue.code, issue.slug) for issue in issues] == [("cache-mismatch", "abc-doc")]


def test_summary_staleness(wiki):
    wiki.page("person", "a", {"Documented role": "Original role."}, summary="Same summary.")
    with Cache(wiki.settings()) as cache:
        cache.refresh()
        bump(wiki.page("person", "a", {"Documented role": "Rewritten role."}, summary="Same summary."))
        cache.refresh()
        assert "wiki/people/a.md" in cache.stale_summaries()
        bump(wiki.page("person", "a", {"Documented role": "Rewritten role."}, summary="New summary."))
        cache.refresh()
        assert cache.stale_summaries() == {}


def test_raw_files_indexed_but_not_in_graph(wiki):
    wiki.raw_text("src.txt", "Raw source text.")
    with Cache(wiki.settings()) as cache:
        cache.refresh()
        status = cache.status()
        assert (status["pages"], status["raw"]) == (0, 1)
        assert cache.neighbors("raw/src") == []


def test_lookups_use_indexes(wiki):
    with Cache(wiki.settings()) as cache:
        for column in ("source_slug", "target_slug"):
            plan = cache.conn.execute(f"EXPLAIN QUERY PLAN SELECT * FROM relations WHERE {column} = ?", ("x",)).fetchall()
            assert any("USING INDEX" in row[-1] for row in plan), plan


def test_attachment_links_leave_unwritten_list(wiki):
    wiki.page("document", "memo", {"What this is": "Original: [[scan.pdf]]. Also [[ghost]]."})
    with Cache(wiki.settings()) as cache:
        cache.refresh()
        unresolved = lambda: {row[0] for row in cache.conn.execute(  # noqa: E731
            "SELECT target_slug FROM relations WHERE resolved = 0")}
        assert unresolved() == {"scan.pdf", "ghost"}
        wiki.write("raw/scan.pdf", raw="%PDF")  # the attachment arrives later
        wiki.page("person", "unrelated")  # any page change triggers re-resolution
        cache.refresh()
        assert unresolved() == {"ghost"}


def test_single_page_refresh_skips_attachment_links(wiki):
    wiki.write("raw/scan.pdf", raw="%PDF")
    wiki.page("document", "memo")
    with Cache(wiki.settings()) as cache:
        cache.refresh()
        bump(wiki.page("document", "memo", {"What this is": "Original: [[scan.pdf]]."}))
        assert cache.ensure_fresh("memo")
        assert cache.neighbors("memo") == []


def test_fixing_invalid_frontmatter_does_not_stale_the_summary(wiki):
    path = wiki.write("wiki/documents/memo.md", raw=(
        '---\ntitle: Memo\ntype: document\nheadline: "RE: x" — tail\n---\n# Memo\n\n## What this is\nA memo.\n'))
    with Cache(wiki.settings()) as cache:
        cache.refresh()
        wiki.write("wiki/documents/memo.md", raw=('---\ntitle: Memo\ntype: document\nheadline: "\\"RE: x\\" — tail"\n'
                                                  '---\n# Memo\n\n## What this is\nA memo.\n'))
        bump(path)
        cache.refresh()
        assert cache.stale_summaries() == {}
