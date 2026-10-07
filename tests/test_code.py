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
    front = f"---\ntitle: {slug}\ntype: module\nlast_updated: 2026-09-29\ncovers: {json.dumps(covers)}\n"
    if verified:
        front += f'verified: "{verified}"\n'
    return write(wiki, f"wiki/modules/{slug}.md", front + f"---\n# {slug}\n\n## Summary\nThe {slug} module.\n\n{body}")


class TestNew:
    def test_creates_a_code_wiki_pointing_at_the_repo(self, code_wiki, code_repo):
        settings = load_settings(code_wiki)
        assert settings.preset == "code" and settings.code_repo == code_repo.resolve()
        assert (code_wiki / "templates/module.md").is_file() and (code_wiki / "raw/.gitkeep").is_file()
        assert [page_type.name for page_type in settings.types] == [
            "file", "module", "concept", "decision", "instruction", "ticket", "epic", "pr", "dependency", "gotcha",
            "analysis"]
        for page_type in settings.types:  # each type's template ships with the preset
            assert (code_wiki / page_type.template).is_file()
        ticket = next(page_type for page_type in settings.types if page_type.name == "ticket")
        assert ticket.values == (("kind", ("story", "bug", "task")),)
        epic = next(page_type for page_type in settings.types if page_type.name == "epic")
        assert epic.record and epic.hub and not ticket.hub
        decision = next(page_type for page_type in settings.types if page_type.name == "decision")
        assert decision.values == (("status", ("proposed", "accepted", "rejected", "superseded", "deprecated")),)
        assert "../app" in (code_wiki / "AGENTS.md").read_text(encoding="utf-8")

    def test_code_setup_is_printed_not_written(self, tmp_path, code_repo):
        result = scaffold(tmp_path / "w", preset="code", code=code_repo, agent="claude")
        files = {item["file"]: item["text"] for item in result["code_setup"]}
        assert files[".wiki-cli.toml"] == 'wiki = "../w"\n'
        assert "wiki guide sync" in files["AGENTS.md"]
        claude = json.loads(files[".claude/settings.json"])
        assert claude["permissions"]["additionalDirectories"] == [(tmp_path / "w").resolve().as_posix()]
        wiki_raw = (tmp_path / "w/raw").resolve().as_posix()
        if wiki_raw[1] == ":":  # Claude Code matches C:/Users as /c/Users
            wiki_raw = f"/{wiki_raw[0].lower()}{wiki_raw[2:]}"
        assert claude["permissions"]["deny"] == [f"Edit(/{wiki_raw}/**)"]
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
        assert "a page of the type it is" in text and "*the source type*" not in text
        assert "{{" not in text and "}}" not in text and "\n\n\n" not in text

    def test_code_query_guide_differs_from_the_generic_one_only_in_step_seven(self):
        guides = Path(__file__).parents[1] / "src/wiki_cli/guides"
        generic = (guides / "query.md").read_text(encoding="utf-8").split("\n7. ")
        code = (guides / "code/query.md").read_text(encoding="utf-8").split("\n7. ")
        assert generic[0] == code[0]
        assert generic[1].split("\n8. ", 1)[1] == code[1].split("\n8. ", 1)[1]


class TestNestedModules:
    """Files name the most specific module; a module inside a larger one names it under
    `## Part of`, so the larger one does not become the hub of every area under it."""

    @staticmethod
    def build(wiki: Path, nested: bool) -> None:
        module_page(wiki, "core", ["src/core/*.py"], None, "## How it works\nThe core.\n")
        for area in ("parser", "renderer"):
            if nested:
                module_page(wiki, area, [f"src/core/{area}/**"], None,
                            f"## Part of\n- [[core]] — the {area} inside the core\n")
            ring = [f"{area}-{index}" for index in range(8)]
            for index, slug in enumerate(ring):
                write(wiki, f"wiki/files/{slug}.md",
                      f"---\ntitle: {slug}\ntype: file\nlast_updated: 2026-09-29\n---\n# {slug}\n\n## Summary\n"
                      f"{area.title()} step {index}. Calls [[{ring[(index + 1) % 8]}]] and [[{ring[(index + 2) % 8]}]].\n\n"
                      f"## Part of\n- [[{area if nested else 'core'}]] — one step of the {area}\n")
        for index in range(12):
            write(wiki, f"wiki/concepts/idea-{index}.md",
                  f"---\ntitle: idea-{index}\ntype: concept\nlast_updated: 2026-09-29\n---\n# idea-{index}\n\n"
                  f"## Summary\nIdea {index}.\n")

    def hub_warnings(self, wiki: Path, capsys) -> list[str]:
        assert run_json(capsys, "index", "refresh", "--root", str(wiki))[0] == 0
        _, result = run_json(capsys, "check", "--all", "--root", str(wiki))
        return [issue["slug"] for issue in result["issues"] if issue["code"] == "hub-covers-clusters"]

    def test_flat_module_is_the_hub_of_every_area(self, code_wiki, capsys):
        self.build(code_wiki, nested=False)
        assert self.hub_warnings(code_wiki, capsys) == ["core"]

    def test_nested_modules_each_hub_their_own_area(self, code_wiki, capsys):
        self.build(code_wiki, nested=True)
        assert self.hub_warnings(code_wiki, capsys) == []
        _, result = run_json(capsys, "neighbors", "parser", "--outgoing", "--root", str(code_wiki))
        assert {"slug": "core", "direction": "outgoing", "type": "part-of",
                "reason": "the parser inside the core"} in result["neighbors"]
        _, result = run_json(capsys, "neighbors", "parser-0", "--outgoing", "--root", str(code_wiki))
        assert any(item["slug"] == "parser" and item["type"] == "part-of" for item in result["neighbors"])

    def test_module_template_has_a_part_of_section(self, code_wiki):
        assert "## Part of\n- [[larger-module]]" in (code_wiki / "templates/module.md").read_text(encoding="utf-8")


class TestPartOfBroaderModule:
    """A file page names the most specific module covering it under `## Part of`."""

    @pytest.fixture
    def wiki(self, tmp_path):
        repo = tmp_path / "engine"
        repo.mkdir()
        git(repo, "init", "-q")
        for rel in ("src/core/main.py", "src/core/parser/lexer.py", "src/core/parser/grammar.py",
                    "src/core/render/paint.py"):
            write(repo, rel, "x = 1\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "initial")
        wiki = tmp_path / "engine-wiki"
        scaffold(wiki, preset="code", code=repo)
        commit = git(repo, "rev-parse", "HEAD")
        module_page(wiki, "core", ["src/core/**"], commit)
        return wiki, commit

    @staticmethod
    def file_page(wiki: Path, slug: str, covers: str, part_of: str, commit: str) -> None:
        write(wiki, f"wiki/files/{slug}.md",
              f'---\ntitle: {slug}\ntype: file\nlast_updated: 2026-09-29\ncovers: ["{covers}"]\nverified: "{commit}"\n'
              f"---\n# {slug}\n\n## Summary\nThe {slug} file.\n\n## Part of\n- [[{part_of}]] — where it belongs\n")

    def warnings(self, wiki: Path, capsys, *target) -> list[dict]:
        assert run_json(capsys, "index", "refresh", "--no-embed", "--root", str(wiki))[0] == 0
        _, result = run_json(capsys, "check", *(target or ("--all",)), "--root", str(wiki))
        return [issue for issue in result["issues"] if issue["code"] == "part-of-broader-module"]

    def test_no_warning_while_only_the_broad_module_covers_it(self, wiki, capsys):
        root, commit = wiki
        self.file_page(root, "lexer", "src/core/parser/lexer.py", "core", commit)
        assert self.warnings(root, capsys) == []

    def test_warns_when_a_more_specific_module_covers_it(self, wiki, capsys):
        root, commit = wiki
        module_page(root, "parser", ["src/core/parser/**"], commit, "## Part of\n- [[core]] — parsing\n")
        self.file_page(root, "lexer", "src/core/parser/lexer.py", "core", commit)
        self.file_page(root, "main", "src/core/main.py", "core", commit)
        [issue] = self.warnings(root, capsys)
        assert issue["slug"] == "lexer" and issue["path"] == "wiki/files/lexer.md"
        assert "[[parser]] covers this page's files more specifically (2 files against 4)" in issue["message"]
        assert [issue["slug"] for issue in self.warnings(root, capsys, "lexer")] == ["lexer"]
        assert self.warnings(root, capsys, "main") == []  # no module covers main.py more specifically

    def test_no_warning_when_it_names_the_specific_module(self, wiki, capsys):
        root, commit = wiki
        module_page(root, "parser", ["src/core/parser/**"], commit, "## Part of\n- [[core]] — parsing\n")
        self.file_page(root, "lexer", "src/core/parser/lexer.py", "parser", commit)
        assert self.warnings(root, capsys) == []
        assert self.warnings(root, capsys, "parser") == []  # nested modules are not checked


class TestEpics:
    """An epic is a hub: its tickets gather around it. It is a record, so it is never told
    to split, though a feature's tickets fall into several clusters."""

    def test_epic_covers_its_tickets_and_is_never_told_to_split(self, code_wiki, capsys):
        write(code_wiki, "wiki/epics/proj-1-export.md",
              '---\ntitle: "PROJ-1: Export"\ntype: epic\nkey: PROJ-1\nstatus: open\nurl: https://jira.example/PROJ-1\n'
              'synced: "2026-09-29"\nlast_updated: 2026-09-29\n---\n# PROJ-1: Export\n\n## Summary\nThe export feature.\n')
        for area in ("csv", "pdf"):
            ring = [f"proj-{area}-{index}" for index in range(8)]
            for index, slug in enumerate(ring):
                write(code_wiki, f"wiki/tickets/{slug}.md",
                      f"---\ntitle: {slug}\ntype: ticket\nkind: story\nparent: proj-1-export\nlast_updated: 2026-09-29\n"
                      f"---\n# {slug}\n\n## Summary\n{area} export step {index}, after [[{ring[(index + 1) % 8]}]] "
                      f"and [[{ring[(index + 2) % 8]}]].\n")
        for index in range(14):
            write(code_wiki, f"wiki/concepts/idea-{index}.md",
                  f"---\ntitle: idea-{index}\ntype: concept\nlast_updated: 2026-09-29\n---\n# idea-{index}\n\n"
                  f"## Summary\nIdea {index}.\n")
        assert run_json(capsys, "index", "refresh", "--root", str(code_wiki))[0] == 0
        _, result = run_json(capsys, "clusters", "--all", "--root", str(code_wiki))
        assert "epic" in result["hub_types"] and result["covered"] == 2
        assert [cluster["covered_by"]["slug"] for cluster in result["clusters"]] == ["proj-1-export"] * 2
        _, result = run_json(capsys, "check", "--all", "--root", str(code_wiki))
        assert not any(issue["code"] == "hub-covers-clusters" for issue in result["issues"])

    def test_files_and_epics_do_not_link_each_other(self, code_wiki, capsys):
        write(code_wiki, "wiki/epics/proj-1-export.md",
              '---\ntitle: "PROJ-1: Export"\ntype: epic\nlast_updated: 2026-09-29\n---\n# PROJ-1\n\n## Summary\n'
              "Export, built in [[search]] and [[export]].\n")
        write(code_wiki, "wiki/tickets/proj-2-csv.md",
              "---\ntitle: PROJ-2\ntype: ticket\nkind: task\nparent: proj-1-export\nlast_updated: 2026-09-29\n---\n"
              "# PROJ-2\n\n## Summary\nCSV export, part of [[proj-1-export]].\n")
        write(code_wiki, "wiki/decisions/csv-format.md",
              "---\ntitle: CSV format\ntype: decision\nstatus: accepted\nlast_updated: 2026-09-29\n---\n"
              "# CSV format\n\n## Summary\nChosen for [[proj-1-export]].\n\n## Decision\nRFC 4180.\n")
        write(code_wiki, "wiki/files/export.md",
              "---\ntitle: export.py\ntype: file\nlast_updated: 2026-09-29\n---\n# export.py\n\n## Summary\n"
              "Writes the CSV for [[proj-1-export]], as [[proj-2-csv]] asked.\n\n## Part of\n- [[search]] — exports\n")
        assert run_json(capsys, "index", "refresh", "--no-embed", "--root", str(code_wiki))[0] == 0
        _, result = run_json(capsys, "check", "--all", "--root", str(code_wiki))
        found = {issue["slug"]: issue["message"] for issue in result["issues"] if issue["code"] == "link-not-allowed"}
        assert set(found) == {"export", "proj-1-export"}  # each direction, on the page holding the link
        assert "links [[proj-1-export]], an epic page, but file and epic pages should not link each other" \
            in found["export"]
        assert "links [[export]], a file page, but epic and file pages should not link each other" \
            in found["proj-1-export"]
        for slug in ("export", "proj-1-export"):
            _, result = run_json(capsys, "check", slug, "--root", str(code_wiki))
            assert [issue["code"] for issue in result["issues"]].count("link-not-allowed") == 1
        for allowed in ("proj-2-csv", "csv-format"):
            _, result = run_json(capsys, "check", allowed, "--root", str(code_wiki))
            assert not any(issue["code"] == "link-not-allowed" for issue in result["issues"])

    def test_no_links_with_must_name_declared_types(self, tmp_path):
        (tmp_path / ".wiki-cli.toml").write_text('[types.epic]\nno_links_with = ["files"]\n[types.file]\n',
                                                 encoding="utf-8")
        with pytest.raises(ConfigError, match=r"\[types.epic\] no_links_with names types not declared in \[types\]: files"):
            load_settings(tmp_path)

    def test_old_name_says_what_to_rename_it_to(self, tmp_path):
        (tmp_path / ".wiki-cli.toml").write_text('[types.epic]\nnot_linked_from = ["file"]\n[types.file]\n',
                                                 encoding="utf-8")
        with pytest.raises(ConfigError, match="not_linked_from is now no_links_with"):
            load_settings(tmp_path)
