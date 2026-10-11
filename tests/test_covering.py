import sqlite3

import pytest

from test_code import code_repo, code_wiki, git, module_page, run_json, write  # noqa: F401  (fixtures)
from wiki_cli.cli import main
from wiki_cli.config import load_settings
from wiki_cli.covering import lookup, matches


def note(wiki, slug, page_type, affects, status=None):
    front = f"---\ntitle: {slug}\ntype: {page_type}\nlast_updated: 2026-10-10\n"
    if status:
        front += f"status: {status}\n"
    return write(wiki, f"wiki/notes/{slug}.md", front + f"---\n# {slug}\n\n## Summary\nThe {slug} note.\n\n"
                 f"## Affects\n- [[{affects}]] — here\n")


@pytest.fixture
def notes_wiki(code_wiki, code_repo):
    head = git(code_repo, "rev-parse", "HEAD")
    module_page(code_wiki, "app", ["src/app/**"], head)
    module_page(code_wiki, "search", ["src/app/search.py"], head)
    note(code_wiki, "empty-results", "gotcha", "search")
    note(code_wiki, "plain-lists", "decision", "search", status="proposed")
    note(code_wiki, "old-ranking", "decision", "search", status="superseded")
    note(code_wiki, "nav-trap", "gotcha", "nav")
    return code_wiki


def slugs(entries):
    return [entry["slug"] for entry in entries]


def test_the_most_specific_page_comes_first_with_the_notes_affecting_it(notes_wiki, code_repo, capsys):
    code, result = run_json(capsys, "covering", str(code_repo / "src/app/search.py"), "--root", str(notes_wiki))
    assert code == 0
    found = result["files"][0]
    assert found["path"] == "src/app/search.py"
    assert slugs(found["pages"]) == ["search", "app"]
    assert slugs(found["notes"]) == ["plain-lists", "empty-results"]  # decisions, then gotchas; superseded left out
    assert found["notes"][0]["status"] == "proposed" and found["notes"][0]["via"] == ["search"]


def test_paths_match_without_regard_to_case(notes_wiki, code_repo, monkeypatch):
    monkeypatch.chdir(code_repo)
    settings = load_settings(notes_wiki)
    shouted = str(code_repo / "SRC" / "App" / "Search.py")
    assert slugs(lookup(settings, [shouted], cwd=code_repo)[0].pages) == ["search", "app"]
    assert lookup(settings, ["src/app/search.py"], cwd=code_repo)[0].path == "src/app/search.py"
    assert matches("src/app/x.py", "SRC/**") and not matches("src/app/x.py", "lib/**")


def test_files_with_nothing_to_look_up(notes_wiki, code_repo, tmp_path):
    settings = load_settings(notes_wiki)
    results = lookup(settings, [str(tmp_path / "elsewhere.py"), str(notes_wiki / "wiki/modules/app.md"),
                                str(code_repo / "README.md")], cwd=code_repo)
    assert [result.outside for result in results] == ["outside the code repo", "a wiki page", None]
    assert not results[2].pages


def test_a_session_sees_each_file_once(notes_wiki, code_repo, capsys, monkeypatch):
    args = ["covering", "src/app/search.py", "--root", str(notes_wiki), "--session", "s1"]
    monkeypatch.chdir(code_repo)
    assert main(args) == 0
    assert "Wiki notes on src/app/search.py" in capsys.readouterr().out
    assert main(args) == 0
    assert "shown earlier in this session" in capsys.readouterr().out
    assert main([*args[:-1], "s2"]) == 0
    assert "Wiki notes on" in capsys.readouterr().out


def test_a_page_checked_before_the_file_changed_says_so(notes_wiki, code_repo):
    write(code_repo, "src/app/search.py", "def search():\n    return [1]\n")
    git(code_repo, "commit", "-qam", "change")
    found = lookup(load_settings(notes_wiki), ["src/app/search.py"], cwd=code_repo)[0]
    assert all(page.get("changed") for page in found.pages)
    assert not lookup(load_settings(notes_wiki), ["src/app/nav.py"], cwd=code_repo)[0].pages[0].get("changed")


def test_an_older_cache_gains_the_values_table_without_a_rebuild(notes_wiki, code_repo):
    settings = load_settings(notes_wiki)
    lookup(settings, ["src/app/search.py"], cwd=code_repo)
    with sqlite3.connect(settings.cache_path) as conn:
        conn.execute("DROP TABLE page_values")
        pages = conn.execute("SELECT count(*) FROM pages").fetchone()[0]
    found = lookup(settings, ["src/app/search.py"], cwd=code_repo)[0]
    assert slugs(found.pages) == ["search", "app"]
    with sqlite3.connect(settings.cache_path) as conn:
        assert conn.execute("SELECT count(*) FROM pages").fetchone()[0] == pages


def test_a_wiki_without_code_is_refused(wiki, capsys):
    wiki.page("concept", "a")
    assert main(["covering", "x.py", "--root", str(wiki.root)]) != 0
    assert "no code repo" in capsys.readouterr().err
