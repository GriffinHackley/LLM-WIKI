import pytest

from wiki_cli.pages import PageNotFound, Resolver, assign_slugs, discover, load, resolve


def test_slugs_are_bare_names_unless_duplicated():
    slugs = assign_slugs([
        ("wiki/people/mike-johnson.md", "page"),
        ("dossiers/a/claims.md", "page"),
        ("dossiers/b/claims.md", "page"),
        ("raw/mike-johnson.txt", "raw"),
    ])
    assert slugs == {
        "wiki/people/mike-johnson.md": "mike-johnson",
        "dossiers/a/claims.md": "dossiers/a/claims",
        "dossiers/b/claims.md": "dossiers/b/claims",
        "raw/mike-johnson.txt": "raw/mike-johnson",
    }


@pytest.fixture
def resolver():
    return Resolver([
        ("mike-johnson", "wiki/people/mike-johnson.md"),
        ("doc-a", "wiki/documents/doc-a.md"),
        ("raw/doc-a", "raw/doc-a.txt"),
        ("dossiers/x/claims", "dossiers/x/claims.md"),
        ("dossiers/y/claims", "dossiers/y/claims.md"),
    ])


@pytest.mark.parametrize("target, expected", [
    ("mike-johnson", "mike-johnson"),
    ("Mike-Johnson", "mike-johnson"),
    ("wiki/people/mike-johnson", "mike-johnson"),
    ("people/mike-johnson", "mike-johnson"),  # trailing partial path
    ("mike-johnson.md", "mike-johnson"),
    ("doc-a", "doc-a"),  # the document page, not its raw text
    ("doc-a.txt", "raw/doc-a"),
    ("doc-a.pdf", "raw/doc-a"),  # an original resolves to its extracted text
    ("raw/doc-a", "raw/doc-a"),
    ("dossiers/x/claims", "dossiers/x/claims"),
    ("claims", None),  # ambiguous
    ("nobody", None),
])
def test_resolver(resolver, target, expected):
    assert resolver.resolve(target) == expected


def test_ambiguous(resolver):
    assert resolver.ambiguous("claims")
    assert not resolver.ambiguous("dossiers/x/claims")
    assert not resolver.ambiguous("doc-a")


def test_scan_applies_config(wiki):
    wiki.page("person", "a")
    wiki.write("wiki/index.md", {"title": "Index"})
    wiki.write("dossiers/d/claims.md", {"title": "Claims"})
    wiki.write("dossiers/d/PLAN.md", {"title": "Plan"})
    wiki.raw_text("src.txt", "text")
    wiki.write("raw/src.pdf", raw="%PDF")
    (wiki.root / ".claude").mkdir()
    (wiki.root / ".claude" / "skill.md").write_text("x")
    found = {(page_file.rel, page_file.slug, page_file.kind) for page_file in discover(wiki.settings())}
    assert found == {
        ("wiki/people/a.md", "a", "page"),
        ("dossiers/d/claims.md", "claims", "page"),
        ("raw/src.txt", "raw/src", "raw"),
    }


def test_default_config_without_toml(tmp_path):
    from wiki_cli.config import load_settings
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "a.md").write_text("---\ntitle: A\n---\n")
    assert [f.slug for f in discover(load_settings(tmp_path, tmp_path / "c.sqlite3"))] == ["a"]


@pytest.mark.parametrize("sections, expected, fallback", [
    ({"Summary": "The summary.", "Documented role": "Role."}, "The summary.", False),
    ({"Documented role": "Role text."}, "Role text.", True),
])
def test_summary_extraction(wiki, sections, expected, fallback):
    wiki.page("person", "p", sections, summary=None)
    page = load(resolve("p", wiki.settings()))
    assert page.summary() == (expected, fallback)


def test_summary_sections_by_type(wiki):
    wiki.page("event", "e", summary="It happened.")
    wiki.page("document", "d", summary="A memo.")
    wiki.page("claim", "EF-001", summary="Epstein was a financier.")
    settings = wiki.settings()
    assert [load(resolve(slug, settings)).summary()[0] for slug in ("e", "d", "EF-001")] == [
        "It happened.", "A memo.", "Epstein was a financier."]


def test_long_summary_truncated(wiki):
    wiki.page("person", "p", summary="word " * 200)
    summary, _ = load(resolve("p", wiki.settings())).summary()
    assert len(summary) <= 301 and summary.endswith("…")


def test_title_falls_back_to_heading(wiki):
    wiki.write("wiki/people/p.md", raw="# Heading Title\n\nText.\n")
    assert load(resolve("p", wiki.settings())).title == "Heading Title"


def test_resolve_by_slug_path_and_link(wiki):
    wiki.page("person", "mike-johnson")
    settings = wiki.settings()
    assert resolve("mike-johnson", settings).rel == "wiki/people/mike-johnson.md"
    assert resolve("wiki/people/mike-johnson.md", settings).slug == "mike-johnson"
    assert resolve("people/mike-johnson", settings).slug == "mike-johnson"
    with pytest.raises(PageNotFound):
        resolve("nobody", settings)


def test_invalid_frontmatter_reported(wiki):
    wiki.write("wiki/documents/bad.md", raw='---\nheadline: "RE: x" — trailing\n---\n# Bad\n')
    page = load(resolve("bad", wiki.settings()))
    assert page.issues[0].code == "invalid-frontmatter"
    assert page.title == "Bad"


def test_other_vault_files_are_not_unwritten():
    resolver = Resolver([("doc-a", "wiki/documents/doc-a.md")],
                        ["raw/scan-2025.pdf", "raw/photo.jpg", "dossiers/x/PLAN.md", "templates/person.md"])
    for target in ("scan-2025.pdf", "raw/scan-2025.pdf", "Photo.JPG", "dossiers/x/PLAN", "x/PLAN", "person"):
        assert resolver.resolve(target) is None
        assert resolver.is_other_file(target), target
    for target in ("scan-2025", "ghost-page", "missing.pdf", "raw/other.pdf"):
        assert not resolver.is_other_file(target), target


def test_other_files_callable_is_lazy():
    calls = []
    resolver = Resolver([("a", "wiki/a.md")], lambda: calls.append(1) or ["raw/x.pdf"])
    assert resolver.resolve("a") == "a" and calls == []
    assert resolver.is_other_file("x.pdf") and resolver.is_other_file("x.pdf") and calls == [1]


def test_invalid_frontmatter_is_not_body(wiki):
    wiki.write("wiki/documents/bad.md", raw='---\nheadline: "RE: x" — trailing\n---\n# Bad\n\nBody.\n')
    page = load(resolve("bad", wiki.settings()))
    assert page.body == "# Bad\n\nBody.\n"
