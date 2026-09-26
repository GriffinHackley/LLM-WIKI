import codecs
import os

import pytest

from wiki_cli import pages
from wiki_cli.block import END_MARKER, START_MARKER
from wiki_cli.pages import PageNotFound, discover, is_excluded, load, resolve, slug_for
from wiki_cli.sync import sync_page


@pytest.mark.parametrize("rel, slug", [
    ("a.md", "a"),
    ("tms/save.md", "tms/save"),
    ("concepts/moe/index.md", "concepts/moe"),
    ("index.md", "index"),
])
def test_slug_for(rel, slug):
    assert slug_for(rel) == slug


def test_discover_skips_dot_dirs_and_non_markdown(wiki):
    wiki.page("a")
    wiki.page("tms/b")
    (wiki.root / ".obsidian").mkdir()
    (wiki.root / ".obsidian" / "x.md").write_text("x")
    (wiki.root / "notes.txt").write_text("x")
    assert [page_file.slug for page_file in discover(wiki.settings())] == ["a", "tms/b"]


def test_discover_applies_ingest_exclude(wiki):
    (wiki.repo / "wiki.toml").write_text('name = "sp"\n[ingest]\nexclude = ["drafts/**", "scratch"]\n')
    wiki.page("keep")
    wiki.page("drafts/one")
    wiki.page("scratch/two")
    assert [page_file.slug for page_file in discover(wiki.settings())] == ["keep"]


@pytest.mark.parametrize("slug, patterns, excluded", [
    ("drafts/a", ("drafts/**",), True),
    ("drafts/a", ("drafts",), True),
    ("x/drafts/a", ("drafts",), True),
    ("drafts-old/a", ("drafts",), False),
    ("a", ("*.tmp",), False),
])
def test_is_excluded(slug, patterns, excluded):
    assert is_excluded(slug, patterns) is excluded


def test_space_read_from_wiki_toml(wiki):
    assert wiki.settings().space == "sp"
    assert wiki.settings(space="other").space == "other"


def test_resolve_by_slug_uri_and_path(wiki):
    wiki.page("tms/save")
    settings = wiki.settings()
    assert resolve("tms/save", settings).rel == "tms/save.md"
    assert resolve("wiki://sp/tms/save", settings).slug == "tms/save"
    assert resolve("tms/save.md", settings).slug == "tms/save"
    assert resolve(str(wiki.root / "tms" / "save.md"), settings).slug == "tms/save"
    with pytest.raises(PageNotFound):
        resolve("missing", settings)


@pytest.mark.skipif(os.name != "nt", reason="case-insensitive filesystem behavior")
def test_resolve_uses_on_disk_case(wiki):
    wiki.page("tms/Save")
    page_file = resolve("TMS/save", wiki.settings())
    assert (page_file.rel, page_file.slug) == ("tms/Save.md", "tms/Save")


def test_resolve_rejects_paths_outside_root(wiki, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("---\ntitle: x\n---\n")
    with pytest.raises(PageNotFound):
        resolve(str(outside), wiki.settings())


def load_page(wiki, slug):
    return load(resolve(slug, wiki.settings()), "sp")


def test_sync_writes_block_and_is_idempotent(wiki):
    wiki.page("b")
    wiki.page("a", ("b", "depends-on", "Needs b."), ("tms/c", "documents", "Docs."), ("b", "tested-by", "Tests."))
    result = sync_page(load_page(wiki, "a"), "sp")
    assert result.written
    assert wiki.read("a").endswith(f"{START_MARKER}\n[[b]]\n[[tms/c]]\n{END_MARKER}\n")
    before = os.stat(wiki.root / "a.md").st_mtime_ns
    again = sync_page(load_page(wiki, "a"), "sp")
    assert not again.changed and not again.written
    assert os.stat(wiki.root / "a.md").st_mtime_ns == before


def test_sync_allows_missing_targets(wiki):
    wiki.page("a", ("not-yet", "depends-on", "Future page."))
    assert sync_page(load_page(wiki, "a"), "sp").written


def test_sync_refuses_structural_errors(wiki):
    wiki.write("a", {"title": "a", "relations": [{"target": "a", "type": "depends-on", "reason": "x"}]})
    original = wiki.read("a")
    result = sync_page(load_page(wiki, "a"), "sp")
    assert not result.ok and not result.written
    assert wiki.read("a") == original


def test_sync_refuses_malformed_block(wiki):
    wiki.write("a", raw=f"---\ntitle: a\n---\n{START_MARKER}\n[[x]]\n")
    result = sync_page(load_page(wiki, "a"), "sp")
    assert [issue.code for issue in result.issues] == ["malformed-block"]


def test_sync_omits_cross_wiki_targets(wiki):
    wiki.write("a", {"title": "a", "relations": [
        {"target": "wiki://other/x", "type": "related-to", "reason": "Elsewhere."},
    ]})
    result = sync_page(load_page(wiki, "a"), "sp")
    assert not result.changed
    assert START_MARKER not in wiki.read("a")


def test_superseded_by_not_in_block(wiki):
    wiki.page("a", superseded_by="b")
    assert not sync_page(load_page(wiki, "a"), "sp").changed


def test_sync_dry_run_does_not_write(wiki):
    wiki.page("a", ("b", "depends-on", "x"))
    original = wiki.read("a")
    result = sync_page(load_page(wiki, "a"), "sp", dry_run=True)
    assert result.changed and not result.written
    assert wiki.read("a") == original


def test_sync_preserves_crlf_and_bom(wiki):
    path = wiki.write("a", {"title": "a", "relations": [
        {"target": "wiki://sp/b", "type": "depends-on", "reason": "x"}]}, newline="\r\n")
    path.write_bytes(codecs.BOM_UTF8 + path.read_bytes())
    sync_page(load_page(wiki, "a"), "sp")
    data = path.read_bytes()
    assert data.startswith(codecs.BOM_UTF8)
    text = data[3:].decode("utf-8")
    assert text.count("\n") == text.count("\r\n")
    assert f"{START_MARKER}\r\n[[b]]\r\n{END_MARKER}\r\n" in text


def test_sync_preserves_frontmatter_formatting(wiki):
    raw = (
        "---\n"
        "# a comment the YAML library would drop\n"
        "title:   'A'\n"
        "relations:\n"
        "  - {target: wiki://sp/b, type: depends-on, reason: \"x\"}\n"
        "---\n"
        "Body.\n"
    )
    wiki.write("a", raw=raw)
    sync_page(load_page(wiki, "a"), "sp")
    assert wiki.read("a").startswith(raw)


def test_atomic_write_failure_leaves_original(wiki, monkeypatch):
    wiki.page("a", ("b", "depends-on", "x"))
    original = wiki.read("a")

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(pages.os, "replace", boom)
    with pytest.raises(OSError):
        sync_page(load_page(wiki, "a"), "sp")
    assert wiki.read("a") == original
    assert [path.name for path in wiki.root.iterdir()] == ["a.md"]


def test_invalid_utf8_is_reported(wiki):
    (wiki.root / "bad.md").write_bytes(b"---\ntitle: \xff\n---\n")
    page = load_page(wiki, "bad")
    assert page.issues[0].code == "invalid-encoding"
    assert not sync_page(page, "sp").ok


@pytest.mark.parametrize("body, expected", [
    ("# Title\n\nFirst para\ncontinues.\n\nSecond.\n", "First para continues."),
    ("```\ncode\n```\nAfter [[a/b]] and [x](http://y).\n", "After a/b and x."),
    ("# Only heading\n", None),
])
def test_placeholder_summary(body, expected):
    assert pages.placeholder_summary(body) == expected


def test_placeholder_summary_truncates():
    summary = pages.placeholder_summary("word " * 100)
    assert len(summary) <= pages.PLACEHOLDER_LENGTH + 1
    assert summary.endswith("…")
