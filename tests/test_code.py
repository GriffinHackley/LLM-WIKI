"""The code preset: a wiki in its own repo describing a code repo."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from wiki_cli.cli import main
from wiki_cli.config import ConfigError, load_settings
from wiki_cli.scaffold import ScaffoldError, scaffold

pytestmark = pytest.mark.skipif(not shutil.which("git"), reason="needs git")


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo,
                          capture_output=True, text=True, check=True)
    return done.stdout.strip()


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return path


def run_json(capsys, *args) -> tuple[int, dict]:
    capsys.readouterr()
    code = main([*args, "--format", "json"])
    out = capsys.readouterr().out
    return code, json.loads(out) if out else {}


@pytest.fixture
def code_repo(tmp_path) -> Path:
    repo = tmp_path / "app"
    repo.mkdir()
    git(repo, "init", "-q")
    write(repo, "src/app/search.py", "def search():\n    return []\n")
    write(repo, "src/app/nav.py", "def nav():\n    pass\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "initial")
    return repo


@pytest.fixture
def code_wiki(tmp_path, code_repo) -> Path:
    wiki = tmp_path / "app-wiki"
    scaffold(wiki, preset="code", code=code_repo)
    return wiki


def module_page(wiki: Path, slug: str, covers: list[str], verified: str | None, body: str = "") -> Path:
    front = f"---\ntitle: {slug}\ntype: module\ncovers: {json.dumps(covers)}\n"
    if verified:
        front += f'verified: "{verified}"\n'
    return write(wiki, f"wiki/modules/{slug}.md", front + f"---\n# {slug}\n\n## Summary\nThe {slug} module.\n\n{body}")


class TestNew:
    def test_creates_a_code_wiki_pointing_at_the_repo(self, code_wiki, code_repo):
        settings = load_settings(code_wiki)
        assert settings.preset == "code" and settings.code_repo == code_repo.resolve()
        assert (code_wiki / "templates/module.md").is_file() and (code_wiki / "raw/.gitkeep").is_file()
        assert "source" in [page_type.name for page_type in settings.types]  # documents about the code
        assert "../app" in (code_wiki / "AGENTS.md").read_text(encoding="utf-8")

    def test_code_setup_is_printed_not_written(self, tmp_path, code_repo):
        result = scaffold(tmp_path / "w", preset="code", code=code_repo, agent="claude")
        files = {item["file"]: item["text"] for item in result["code_setup"]}
        assert files[".wiki-cli.toml"] == 'wiki = "../w"\n'
        assert "wiki guide sync" in files["AGENTS.md"]
        claude = json.loads(files[".claude/settings.json"])
        assert claude["permissions"]["additionalDirectories"] == [(tmp_path / "w").resolve().as_posix()]
        assert not (code_repo / ".wiki-cli.toml").exists() and not (code_repo / "AGENTS.md").exists()
        wiki_settings = json.loads((tmp_path / "w/.claude/settings.json").read_text(encoding="utf-8"))
        assert wiki_settings["permissions"]["additionalDirectories"] == [code_repo.resolve().as_posix()]
        assert (tmp_path / "w/.claude/skills/wiki-sync/SKILL.md").is_file()

    def test_refusals(self, tmp_path, code_repo):
        with pytest.raises(ScaffoldError, match="needs --code"):
            scaffold(tmp_path / "w", preset="code")
        with pytest.raises(ScaffoldError, match="pass its top folder"):
            scaffold(tmp_path / "w", preset="code", code=code_repo / "src")
        with pytest.raises(ScaffoldError, match="outside the code repo"):
            scaffold(code_repo / "docs" / "wiki", preset="code", code=code_repo)
        with pytest.raises(ScaffoldError, match="not in a git repository"):
            scaffold(tmp_path / "w", preset="code", code=tmp_path)
        with pytest.raises(ScaffoldError, match="--code is for the code preset"):
            scaffold(tmp_path / "w", code=code_repo)


class TestRedirect:
    def test_commands_in_the_code_repo_reach_the_wiki(self, code_wiki, code_repo, monkeypatch):
        write(code_repo, ".wiki-cli.toml", 'wiki = "../app-wiki"\n')
        monkeypatch.chdir(code_repo / "src")
        settings = load_settings()
        assert settings.root == code_wiki.resolve() and settings.redirected_from == code_repo.resolve()
        assert settings.cache_path.is_relative_to(code_wiki.resolve())

    @pytest.mark.parametrize("text, message", [
        ('wiki = "../app-wiki"\npages = ["*.md"]\n', "only setting"),
        ('wiki = "../nowhere"\n', "not a wiki"),
    ])
    def test_bad_redirects(self, code_wiki, code_repo, text, message):
        write(code_repo, ".wiki-cli.toml", text)
        with pytest.raises(ConfigError, match=message):
            load_settings(code_repo)


class TestCodeLinksAndChecks:
    def test_code_links_are_file_references(self, code_wiki, code_repo, capsys):
        head = git(code_repo, "rev-parse", "HEAD")
        module_page(code_wiki, "search", ["src/app/search.py"], head,
                    "## How it works\n[search()](code:src/app/search.py#L1) returns results.\n")
        code, result = run_json(capsys, "neighbors", "search", "--root", str(code_wiki))
        [ref] = result["neighbors"]
        assert (ref["type"], ref["slug"]) == ("refers-to-code", "code:src/app/search.py")
        code, unwritten = run_json(capsys, "unwritten", "--root", str(code_wiki))
        assert unwritten["unwritten"] == []
        code, report = run_json(capsys, "check", "--all", "--root", str(code_wiki))
        assert report["issues"] == []

    def test_check_reports_missing_files_empty_covers_and_unverified(self, code_wiki, code_repo, capsys):
        module_page(code_wiki, "ghost", ["src/app/gone/**"], None, "See [old](code:src/app/old.py).\n")
        code, report = run_json(capsys, "check", "ghost", "--root", str(code_wiki))
        codes = sorted(issue["code"] for issue in report["issues"])
        assert codes == ["covers-nothing", "missing-code-file", "not-verified"]

    def test_wrong_or_missing_code_repo(self, code_wiki, code_repo, capsys, monkeypatch, tmp_path):
        module_page(code_wiki, "search", ["src/app/search.py"], "abc")
        config = code_wiki / ".wiki-cli.toml"
        config.write_text(config.read_text(encoding="utf-8").replace('origin = ""',
                          'origin = "https://github.com/someone/app.git"'), encoding="utf-8")
        git(code_repo, "remote", "add", "origin", "git@github.com:other/app.git")
        code, report = run_json(capsys, "check", "--all", "--root", str(code_wiki))
        [issue] = report["issues"]
        assert issue["code"] == "code-repo" and "other/app" in issue["message"]
        assert main(["stale", "--root", str(code_wiki)]) == 2
        monkeypatch.setenv("WIKI_CODE_REPO", str(tmp_path / "elsewhere"))
        assert main(["stale", "--root", str(code_wiki)]) == 2
        assert "WIKI_CODE_REPO" in capsys.readouterr().err

    def test_same_remote_in_https_and_ssh_forms(self, code_wiki, code_repo, capsys):
        config = code_wiki / ".wiki-cli.toml"
        config.write_text(config.read_text(encoding="utf-8").replace('origin = ""',
                          'origin = "https://github.com/someone/app.git"'), encoding="utf-8")
        git(code_repo, "remote", "add", "origin", "git@github.com:someone/app.git")
        assert main(["stale", "--root", str(code_wiki)]) == 0


class TestStale:
    def test_changes_show_until_the_page_is_verified_again(self, code_wiki, code_repo, capsys):
        first = git(code_repo, "rev-parse", "HEAD")
        page = module_page(code_wiki, "search", ["src/app/search.py"], first)
        module_page(code_wiki, "nav", ["src/app/nav.py"], first)
        module_page(code_wiki, "new", ["src/app/*.py"], None)
        code, result = run_json(capsys, "stale", "--root", str(code_wiki))
        assert result["stale"] == [] and result["unverified"] == ["new"]

        write(code_repo, "src/app/search.py", "def search():\n    return [1]\n")
        code, result = run_json(capsys, "stale", "--root", str(code_wiki))
        assert [(item["slug"], item.get("uncommitted")) for item in result["stale"]] == [
            ("search", ["src/app/search.py"])]

        git(code_repo, "commit", "-qam", "change search")
        second = git(code_repo, "rev-parse", "HEAD")
        code, result = run_json(capsys, "stale", "--root", str(code_wiki))
        assert [(item["slug"], item["changed"]) for item in result["stale"]] == [("search", ["src/app/search.py"])]

        page.write_text(page.read_text(encoding="utf-8").replace(first, second), encoding="utf-8")
        code, result = run_json(capsys, "stale", "--root", str(code_wiki))
        assert result["stale"] == [] and result["head"] == second

    def test_unknown_verified_commit(self, code_wiki, capsys):
        module_page(code_wiki, "search", ["src/app/search.py"], "0" * 40)
        code, result = run_json(capsys, "stale", "--root", str(code_wiki))
        assert "not in the code repo" in result["stale"][0]["problem"]


class TestGuides:
    def test_code_guides_name_the_code_repo(self, code_wiki, code_repo, capsys):
        code, listing = run_json(capsys, "guide", "--root", str(code_wiki))
        assert {item["name"] for item in listing["guides"]} == {"ingest", "lint", "query", "sync"}
        for name in ("ingest", "sync", "lint", "query"):
            code, guide = run_json(capsys, "guide", name, "--root", str(code_wiki))
            assert code_repo.resolve().as_posix() in guide["text"] and "{{" not in guide["text"]

    def test_the_ingest_guide_keeps_its_code_parts_in_a_code_wiki(self, code_wiki, capsys):
        code, guide = run_json(capsys, "guide", "ingest", "--root", str(code_wiki))
        text = guide["text"]
        assert "**Part of the code:**" in text and "`covers:`" in text and "the code wins" in text
        assert "**A document:**" in text and "wiki pending" in text
        assert "{{" not in text and "}}" not in text and "\n\n\n" not in text

    def test_code_query_guide_differs_from_the_generic_one_only_in_step_seven(self):
        guides = Path(__file__).parents[1] / "src/wiki_cli/guides"
        generic = (guides / "query.md").read_text(encoding="utf-8").split("\n7. ")
        code = (guides / "code/query.md").read_text(encoding="utf-8").split("\n7. ")
        assert generic[0] == code[0]
        assert generic[1].split("\n8. ", 1)[1] == code[1].split("\n8. ", 1)[1]
