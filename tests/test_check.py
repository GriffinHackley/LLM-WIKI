import pytest

from wiki_cli.block import END_MARKER, START_MARKER
from wiki_cli.pages import discover, load, resolve
from wiki_cli.sync import sync_page
from wiki_cli.validation import SlugIndex, check_corpus, check_page


def run_check(wiki, slug, **kwargs):
    settings = wiki.settings()
    slugs = SlugIndex.build(discover(settings))
    return check_page(load(resolve(slug, settings), "sp"), slugs, "sp", **kwargs)


def codes(issues, severity=None):
    return sorted(issue.code for issue in issues if severity is None or issue.severity == severity)


def synced(wiki, slug):
    sync_page(load(resolve(slug, wiki.settings()), "sp"), "sp")


def test_clean_page_has_no_errors(wiki):
    wiki.page("b")
    wiki.page("a", ("b", "depends-on", "Needs b."))
    synced(wiki, "a")
    assert codes(run_check(wiki, "a"), "error") == []


def test_missing_block(wiki):
    wiki.page("b")
    wiki.page("a", ("b", "depends-on", "x"))
    assert "missing-block" in codes(run_check(wiki, "a"))


def test_stale_block(wiki):
    wiki.page("b")
    wiki.page("c")
    wiki.page("a", ("b", "depends-on", "x"))
    synced(wiki, "a")
    wiki.page("a", ("c", "depends-on", "x"))
    text = wiki.read("a") + f"\n{START_MARKER}\n[[b]]\n{END_MARKER}\n"
    (wiki.root / "a.md").write_text(text, encoding="utf-8")
    assert "stale-block" in codes(run_check(wiki, "a"))


def test_block_without_relations_is_stale(wiki):
    wiki.write("a", raw=f"---\ntitle: a\nsummary: s\n---\n{START_MARKER}\n[[b]]\n{END_MARKER}\n")
    assert "stale-block" in codes(run_check(wiki, "a"))


def test_manual_content_in_block(wiki):
    wiki.page("b")
    wiki.page("a", ("b", "depends-on", "x"))
    synced(wiki, "a")
    text = wiki.read("a").replace("[[b]]\n", "[[b]]\nmy note\n")
    (wiki.root / "a.md").write_text(text, encoding="utf-8")
    assert "unexpected-block-content" in codes(run_check(wiki, "a"))


@pytest.mark.parametrize("relations, code", [
    ([("missing", "depends-on", "x")], "missing-target"),
    ([("a", "depends-on", "x")], "self-relation"),
    ([("b", "depends-on", "x"), ("b", "depends-on", "y")], "duplicate-relation"),
    ([("b", "implements", "x"), ("b", "implemented-by", "y")], "conflicting-relations"),
    ([("b", "depends-on", "x"), ("b", "used-by", "y")], "conflicting-relations"),
])
def test_relation_errors(wiki, relations, code):
    wiki.page("b")
    wiki.page("a", *relations)
    assert code in codes(run_check(wiki, "a"), "error")


def test_missing_target_suggests_case_match(wiki):
    wiki.page("tms/Save")
    wiki.page("a", ("tms/save", "depends-on", "x"))
    issue = next(i for i in run_check(wiki, "a") if i.code == "missing-target")
    assert "tms/Save" in issue.message


def test_external_target_not_checked_for_existence(wiki):
    wiki.write("a", {"title": "a", "summary": "s", "relations": [
        {"target": "wiki://other/x", "type": "related-to", "reason": "x"}]})
    assert "missing-target" not in codes(run_check(wiki, "a"))


def test_superseded_by_missing_target(wiki):
    wiki.page("a", superseded_by="gone")
    assert "missing-target" in codes(run_check(wiki, "a"), "error")


@pytest.mark.parametrize("setup, code", [
    (lambda w: w.page("a"), "no-relations"),
    (lambda w: w.page("a", summary=None), "missing-summary"),
    (lambda w: w.page("a", summary="x" * 300), "long-summary"),
    (lambda w: (w.page("b"), w.page("a", ("b", "depends-on", "r" * 200))), "long-reason"),
    (lambda w: (w.page("b"), w.page("a", ("b", "depends-on", "x"), ("b", "tested-by", "y"))), "multiple-types"),
    (lambda w: ([w.page(s) for s in "bcd"], w.page("a", *[(s, "related-to", "x") for s in "bcd"])), "related-to-overuse"),
    (lambda w: ([w.page(f"t{i}") for i in range(13)], w.page("a", *[(f"t{i}", "depends-on", "x") for i in range(13)])), "high-outgoing"),
])
def test_warnings(wiki, setup, code):
    setup(wiki)
    assert code in codes(run_check(wiki, "a"), "warning")


def test_require_summary_makes_it_an_error(wiki):
    wiki.page("a", summary=None)
    assert "missing-summary" in codes(run_check(wiki, "a", require_summary=True), "error")


def test_bundle_page_warning(wiki):
    wiki.write("concepts/moe/index", {"title": "moe", "summary": "s"})
    assert "bundle-page" in codes(run_check(wiki, "concepts/moe"), "warning")


def test_bundle_and_flat_file_collide(wiki):
    wiki.page("concepts/moe")
    wiki.write("concepts/moe/index", {"title": "moe", "summary": "s"})
    assert "duplicate-slug" in codes(run_check(wiki, "concepts/moe.md"), "error")


def test_invalid_frontmatter(wiki):
    wiki.write("a", raw="---\ntitle: [unclosed\n---\n")
    assert codes(run_check(wiki, "a")) == ["invalid-frontmatter"]


def test_corpus_reciprocal_and_incoming(wiki):
    wiki.page("a", ("b", "depends-on", "x"))
    wiki.page("b", ("a", "used-by", "x"))
    settings = wiki.settings()
    files = discover(settings)
    corpus = [load(page_file, "sp") for page_file in files]
    issues = check_corpus(corpus, SlugIndex.build(files), "sp")
    assert "reciprocal-relation" in codes(issues, "warning")
