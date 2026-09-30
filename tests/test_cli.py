import json

import pytest

from conftest import bump
from wiki_cli.cli import main


@pytest.fixture
def run(wiki, capsys):
    def invoke(*args):
        code = main([*args, "--root", str(wiki.root)])
        captured = capsys.readouterr()
        return code, captured.out, captured.err
    return invoke


def run_json(run, *args):
    code, out, _ = run(*args, "--format", "json")
    assert "\n" not in out.strip(), "JSON output must be compact"
    return code, json.loads(out)


def test_neighbors(wiki, run):
    wiki.page("person", "charles-babbage", {"Relationships": "- [[ada-lovelace]] — tutored her"})
    wiki.page("person", "ada-lovelace")
    code, result = run_json(run, "neighbors", "ada-lovelace")
    assert code == 0 and result == {"page": "ada-lovelace", "neighbors": [
        {"slug": "charles-babbage", "direction": "incoming", "type": "associated-with", "reason": "tutored her"}]}
    code, out, _ = run("neighbors", "charles-babbage")
    assert out.strip() == "-> associated-with  ada-lovelace  tutored her"


def test_neighbors_unknown_page(wiki, run):
    code, _, err = run("neighbors", "nobody")
    assert code == 2 and "nobody" in err


def test_root_found_from_working_directory(wiki, capsys, monkeypatch):
    wiki.page("person", "p")
    monkeypatch.chdir(wiki.root / "wiki")
    assert main(["index", "refresh", "--no-embed", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["added"] == 1
    assert (wiki.root / ".cache" / "wiki.sqlite3").is_file()


def test_no_config_uses_current_folder(tmp_path, capsys, monkeypatch):
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "a.md").write_text("# A\n\nSee [B](b.md).\n", encoding="utf-8")
    (tmp_path / "notes" / "b.md").write_text("# B\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert main(["neighbors", "a", "--format", "json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["neighbors"] == [{"slug": "b", "direction": "outgoing", "type": "links-to", "title": "B",
                                    "reason": "A: See B."}]
    assert (tmp_path / ".cache" / "wiki.sqlite3").is_file()


class TestCheck:
    def codes(self, run, *args):
        code, result = run_json(run, "check", *args)
        return code, sorted(issue["code"] for issue in result["issues"])

    def test_clean_page(self, wiki, run):
        wiki.page("person", "a", {"Relationships": "- [[b]] — colleague"})
        wiki.page("person", "b")
        assert self.codes(run, "a") == (0, [])

    def test_errors_and_warnings(self, wiki, run):
        wiki.write("wiki/documents/bad.md", raw='---\nheadline: "RE: x" — tail\n---\n# Bad\n')
        wiki.write("wiki/people/nofront.md", raw="# No frontmatter\n")
        wiki.write("notebooks/a/claims.md", {"title": "A"})
        wiki.write("notebooks/b/claims.md", {"title": "B"})
        wiki.page("person", "linker", {"Timeline": "See [[claims]] and [[ghost-1]], [[ghost-2]]."}, summary=None)
        assert self.codes(run, "bad") == (1, ["invalid-frontmatter"])
        assert self.codes(run, "nofront") == (1, ["missing-frontmatter"])
        assert self.codes(run, "linker") == (0, ["ambiguous-link", "missing-summary", "unwritten-links"])
        code, result = run_json(run, "check", "linker")
        unwritten = next(i for i in result["issues"] if i["code"] == "unwritten-links")
        assert unwritten["message"] == "2 links to pages not written yet: ghost-1, ghost-2"

    def test_strict_and_no_warnings(self, wiki, run):
        wiki.page("person", "a", summary=None)
        assert run("check", "a")[0] == 0
        assert run("check", "a", "--strict")[0] == 1
        code, result = run_json(run, "check", "a", "--no-warnings")
        assert result["issues"] == [] and result["warnings"] == 1

    def test_all_with_cache_verification_and_stale_summary(self, wiki, run):
        path = wiki.page("person", "a", {"Documented role": "Old."}, summary="Same.")
        code, result = run_json(run, "check", "--all", "--verify-cache")
        assert code == 1 and result["issues"][0]["code"] == "cache-mismatch"  # no cache yet
        run("index", "refresh")
        bump(wiki.page("person", "a", {"Documented role": "New."}, summary="Same."))
        run("index", "refresh")
        assert self.codes(run, "--all", "--verify-cache") == (0, ["summary-stale"])

    def test_verify_cache_requires_all(self, wiki, run):
        wiki.page("person", "a")
        assert run("check", "a", "--verify-cache")[0] == 2


def test_index_commands(wiki, run):
    wiki.page("person", "a", {"Relationships": "- [[b]] — colleague"})
    wiki.page("person", "b")
    wiki.raw_text("src.txt", "Raw.")
    code, stats = run_json(run, "index", "rebuild")
    assert code == 0 and stats["added"] == 3 and stats["embedded"] == 3
    code, status = run_json(run, "index", "status")
    assert status == {"version": "4", "pages": 2, "raw": 1, "relations": 1, "unresolved": 0, "chunks": 7,
                      "stale": 0, "pending_embedding": 0, "embed_model": "fake:hash"}


def test_vocab(wiki, run):
    code, result = run_json(run, "vocab")
    assert code == 0 and result["types"][0] == {"type": "rests-on", "inverse": "premise-of",
                                                "from": ["heading 'rests on'", "field 'rests_on'"]}
    assert result["types"][-4:] == [{"type": "embeds", "inverse": "embedded-in", "from": ["block embeds"]},
                                    {"type": "draws-on", "inverse": "drawn-on-by", "from": ["field 'sources'"]},
                                    {"type": "refers-to-code", "inverse": "referred-to-by", "from": ["code: links"]},
                                    {"type": "links-to", "inverse": "linked-from", "from": ["any other link"]}]


def test_check_and_unwritten_ignore_existing_attachments(wiki, run):
    wiki.write("raw/scan.pdf", raw="%PDF")
    wiki.page("document", "memo", {"What this is": "Original: [[scan.pdf]]. See [[ghost]]."})
    code, result = run_json(run, "check", "memo")
    [issue] = [i for i in result["issues"] if i["code"] == "unwritten-links"]
    assert issue["message"] == "1 links to pages not written yet: ghost"
    code, unwritten = run_json(run, "unwritten")
    assert [item["target"] for item in unwritten["unwritten"]] == ["ghost"]


def test_no_config_uses_git_repository_root(tmp_path, capsys, monkeypatch):
    (tmp_path / ".git").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "top.md").write_text("# Top\n", encoding="utf-8")
    (tmp_path / "docs" / "a.md").write_text("# A\n\nSee [[top]].\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path / "docs")
    assert main(["neighbors", "a", "--format", "json"]) == 0
    captured = capsys.readouterr()
    assert [n["slug"] for n in json.loads(captured.out)["neighbors"]] == ["top"]
    assert captured.err.startswith("note: no .wiki-cli.toml found; using the git repository")
    assert (tmp_path / ".cache" / "wiki.sqlite3").is_file()
    assert not (tmp_path / "docs" / ".cache").exists()


def test_no_config_refuses_home_folder(tmp_path, capsys, monkeypatch):
    (tmp_path / "a.md").write_text("# A\n", encoding="utf-8")
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: tmp_path))
    monkeypatch.chdir(tmp_path)
    assert main(["search", "a", "--keyword-only"]) == 2
    assert "is not a wiki folder" in capsys.readouterr().err
    assert not (tmp_path / ".cache").exists()
    assert main(["index", "status", "--root", str(tmp_path)]) == 0  # explicit --root still works
