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
    assert [result.outside for result in results] == ["outside the code repo", None, None]
    assert results[1].page == "app" and not results[1].notes  # a wiki page no note affects
    assert not results[2].pages
    assert lookup(settings, [str(notes_wiki / "AGENTS.md")], cwd=code_repo)[0].outside == "in the wiki, but not a page"


def test_a_wiki_page_gets_the_open_notes_that_affect_it_once_a_session(notes_wiki, code_repo, capsys, monkeypatch):
    monkeypatch.chdir(code_repo)
    found = lookup(load_settings(notes_wiki), [str(notes_wiki / "wiki/modules/search.md")], cwd=code_repo)[0]
    assert found.page == "search" and not found.pages
    assert slugs(found.notes) == ["plain-lists", "empty-results"]  # superseded left out
    args = ["covering", str(notes_wiki / "wiki/modules/search.md"), "--root", str(notes_wiki), "--session", "s1"]
    assert main(args) == 0
    out = capsys.readouterr().out
    assert "Gotchas and decisions that affect the wiki page search" in out and "empty-results (gotcha)" in out
    assert main(args) == 0
    assert "shown earlier in this session" in capsys.readouterr().out
    for name in ("search", "[[search]]"):  # a page named by its slug
        assert slugs(lookup(load_settings(notes_wiki), [name], cwd=code_repo)[0].notes) == ["plain-lists",
                                                                                         "empty-results"]


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


def test_check_names_a_decision_still_waiting_for_approval(notes_wiki, capsys):
    code, result = run_json(capsys, "check", "plain-lists", "--root", str(notes_wiki))
    assert "decision-proposed" in [issue["code"] for issue in result["issues"]]


def test_the_work_guide_is_the_code_presets(notes_wiki, capsys):
    assert main(["guide", "work", "--root", str(notes_wiki)]) == 0
    text = capsys.readouterr().out
    assert "wiki task start" in text and "wiki finish" in text and "verified:` to `\"pending\"" in text
    assert "Run `wiki guide plan`" in text and "wiki search" not in text  # the planning steps live in one guide
    assert main(["work"]) != 0


def test_the_plan_guide_stops_at_the_approved_plan(notes_wiki, capsys):
    assert main(["guide", "plan", "--root", str(notes_wiki)]) == 0
    text = capsys.readouterr().out
    assert "Split the task into parts" in text and "ask about the parts with no page" in text
    assert "get the plan approved" in text and "don't run `wiki task start`" in text and "wiki finish" not in text
    assert main(["plan"]) != 0
