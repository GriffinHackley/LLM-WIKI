"""`wiki new`, presets, `wiki guide` and `wiki list`: the starter kit."""

import json
import shutil
from pathlib import Path

import pytest
import yaml

from wiki_cli import guide
from wiki_cli import scaffold as scaffold_module
from wiki_cli.cli import main
from wiki_cli.config import ConfigError, load_settings
from wiki_cli.scaffold import ScaffoldError, scaffold


def run_json(capsys, *args) -> tuple[int, dict]:
    capsys.readouterr()
    code = main([*args, "--format", "json"])
    return code, json.loads(capsys.readouterr().out)


@pytest.fixture
def new_wiki(tmp_path, capsys):
    root = tmp_path / "my-wiki"
    code, result = run_json(capsys, "new", str(root))
    assert code == 0
    return root, result


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))  # exactly as given: no CRLF translation on Windows
    return path


class TestNew:
    def test_creates_a_research_wiki(self, new_wiki):
        root, result = new_wiki
        assert result["preset"] == "research"
        for rel in ("AGENTS.md", ".wiki-cli.toml", ".gitignore", "raw/.gitkeep", "templates/person.md",
                    "wiki/open-questions.md"):
            assert (root / rel).is_file(), rel
        assert not (root / "CLAUDE.md").exists()  # adapters only on request
        assert "# my-wiki: LLM wiki" in (root / "AGENTS.md").read_text(encoding="utf-8")
        assert "{{today}}" not in (root / "wiki/open-questions.md").read_text(encoding="utf-8")
        if shutil.which("git"):
            assert result["git_init"] and (root / ".git").is_dir()

    def test_fresh_wiki_checks_clean(self, new_wiki, capsys):
        root, _ = new_wiki
        assert main(["index", "refresh", "--no-embed", "--root", str(root)]) == 0
        code, report = run_json(capsys, "check", "--all", "--root", str(root))
        assert code == 0 and report["errors"] == 0 and report["warnings"] == 0

    def test_rerun_changes_nothing(self, new_wiki, capsys):
        root, first = new_wiki
        before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file() and ".git" not in path.parts}
        code, again = run_json(capsys, "new", str(root))
        assert code == 0 and again["created"] == [] and again["add"] == [] and again["notes"] == []
        assert sorted(again["kept"]) == sorted(first["created"])
        after = {path: path.read_bytes() for path in root.rglob("*") if path.is_file() and ".git" not in path.parts}
        assert after == before

    def test_existing_files_are_kept_and_the_missing_parts_named(self, tmp_path):
        root = tmp_path / "notes"
        write(root, "AGENTS.md", "# My rules\n")
        write(root, ".gitignore", "node_modules/\n")
        result = scaffold(root)
        assert (root / "AGENTS.md").read_text(encoding="utf-8") == "# My rules\n"
        assert (root / ".gitignore").read_text(encoding="utf-8") == "node_modules/\n"
        added = {item["file"]: item["text"] for item in result["add"]}
        assert "wiki guide ingest" in added["AGENTS.md"]
        assert added[".gitignore"] == ".cache/\n"

    def test_an_established_wiki_keeps_its_own_pages_and_instructions(self, tmp_path):
        root = tmp_path / "vault"
        write(root, ".wiki-cli.toml", 'pages = ["notes/**/*.md"]\n')
        write(root, "CLAUDE.md", "# Our rules\n")
        result = scaffold(root, agent="claude")
        assert not (root / "templates").exists() and not (root / "wiki").exists()
        assert not (root / "AGENTS.md").exists()
        assert (root / "CLAUDE.md").read_text(encoding="utf-8") == "# Our rules\n"
        assert "templates/person.md" in result["skipped"] and "AGENTS.md" in result["skipped"]
        assert any("agent instructions" in item["file"] for item in result["add"])
        assert (root / ".claude/skills/wiki-query/SKILL.md").is_file()

    def test_claude_adapter(self, tmp_path):
        root = tmp_path / "w"
        scaffold(root, agent="claude")
        assert (root / "CLAUDE.md").read_text(encoding="utf-8") == "@AGENTS.md\n"
        settings = json.loads((root / ".claude/settings.json").read_text(encoding="utf-8"))
        assert settings["permissions"]["allow"] == ["Bash(wiki:*)"]
        stub = (root / ".claude/skills/wiki-query/SKILL.md").read_text(encoding="utf-8")
        front = yaml.safe_load(stub.split("---")[1])
        assert front["name"] == "wiki-query" and "wiki guide query" in front["description"]
        assert "wiki guide query" in stub.split("---", 2)[2]
        assert front["allowed-tools"] == "Bash(wiki *)"

    def test_adapters_inline_the_guide_and_fall_back_to_running_it(self, tmp_path):
        root = tmp_path / "w"
        scaffold(root, agent="opencode")
        command = (root / ".opencode/commands/wiki-ingest.md").read_text(encoding="utf-8")
        front, body = command.split("---", 2)[1:]
        assert yaml.safe_load(front) == {"description": "Add a source to the wiki (wiki guide ingest)",
                                         "agent": "build"}
        lines = body.splitlines()
        # the injection must start its line to run; the sentence before it covers agents that don't run it
        assert "!`wiki guide ingest`" in lines and "run that command once" in body
        assert "applying the rules in AGENTS.md" in body and lines[-1].endswith("$ARGUMENTS")
        assert {path.name for path in (root / ".opencode/commands").iterdir()} == {
            "wiki-ingest.md", "wiki-lint.md", "wiki-query.md"}
        assert not (root / ".claude").exists() and not (root / ".github").exists()

    def test_copilot_adapter(self, tmp_path):
        root = tmp_path / "w"
        result = scaffold(root, agent="copilot")
        assert "AGENTS.md" in (root / ".github/copilot-instructions.md").read_text(encoding="utf-8")
        skill = (root / ".github/skills/wiki-lint/SKILL.md").read_text(encoding="utf-8")
        assert "name: wiki-lint" in skill and "!`wiki guide lint`" in skill
        assert not (root / ".claude").exists() and not result["notes"]

    def test_copilot_with_claude_shares_the_claude_skills(self, capsys, tmp_path):
        root = tmp_path / "w"
        code, result = run_json(capsys, "new", str(root), "--agent", "claude", "--agent", "copilot",
                                "--agent", "opencode")
        assert code == 0
        assert (root / ".claude/skills/wiki-ingest/SKILL.md").is_file() and not (root / ".github/skills").exists()
        assert (root / ".github/copilot-instructions.md").is_file()
        assert (root / ".opencode/commands/wiki-query.md").is_file()
        assert any("Copilot uses the workflow skills in .claude/skills/" in note for note in result["notes"])

    def test_copilot_instructions_are_kept(self, tmp_path):
        root = tmp_path / "w"
        write(root, ".github/copilot-instructions.md", "Use tabs.\n")
        result = scaffold(root, agent=["copilot"])
        assert (root / ".github/copilot-instructions.md").read_text(encoding="utf-8") == "Use tabs.\n"
        assert {"file": ".github/copilot-instructions.md", "text": scaffold_module.COPILOT_INSTRUCTIONS} in result["add"]

    def test_unknown_agent(self, tmp_path):
        with pytest.raises(ScaffoldError, match="no adapter for 'cursor'"):
            scaffold(tmp_path / "w", agent=["claude", "cursor"])

    def test_claude_adapter_names_a_missing_permission(self, tmp_path):
        root = tmp_path / "w"
        write(root, ".claude/settings.json", '{"permissions": {"allow": ["Bash(git:*)"]}}')
        result = scaffold(root, agent="claude")
        assert any(item["file"] == ".claude/settings.json" and "Bash(wiki:*)" in item["text"]
                   for item in result["add"])

    def test_refusals(self, tmp_path, capsys):
        with pytest.raises(ScaffoldError, match="not a wiki folder"):
            scaffold(Path.home())
        with pytest.raises(ScaffoldError, match="no preset"):
            scaffold(tmp_path / "w", preset="nonsense")
        write(tmp_path, "file.txt", "x")
        assert main(["new", str(tmp_path / "file.txt")]) == 2
        assert "is a file" in capsys.readouterr().err

    def test_custom_preset_folder(self, tmp_path):
        preset = tmp_path / "my-preset"
        write(preset, "files/dot-wiki-cli.toml", 'preset = "my-preset"\n[types.note]\ndescription = "A note."\n')
        write(preset, "files/notes/welcome.md", "# Welcome to {{name}}\n")
        root = tmp_path / "kb"
        result = scaffold(root, preset=str(preset))
        assert result["preset"] == "my-preset"
        assert (root / "notes/welcome.md").read_text(encoding="utf-8") == "# Welcome to kb\n"
        assert load_settings(root).types[0].name == "note"


class TestTypes:
    def test_types_give_folders_and_flag_unknown_types(self, new_wiki, capsys):
        root, _ = new_wiki
        write(root, "wiki/people/ada.md", "---\ntitle: Ada\n---\n# Ada\n\n## Summary\nA mathematician.\n")
        write(root, "wiki/people/bob.md", "---\ntitle: Bob\ntype: persn\n---\n# Bob\n\n## Summary\nA typo.\n")
        settings = load_settings(root)
        assert settings.page_type_for("wiki/people/ada.md", {}) == "person"
        code, report = run_json(capsys, "check", "--all", "--root", str(root))
        codes = [(issue["slug"], issue["code"]) for issue in report["issues"]]
        assert ("bob", "unknown-type") in codes and ("ada", "unknown-type") not in codes

    @pytest.mark.parametrize("text, message", [
        ('[types.a]\ncolour = "red"\n', "unknown key"),
        ('[types.a]\nfolder = 3\n', "must be strings"),
        ('types = 3\n', "must be a table"),
        ('preset = 3\n', "'preset'"),
        ('[guides]\ndir = 3\n', "folder path"),
    ])
    def test_invalid_types(self, tmp_path, text, message):
        write(tmp_path, ".wiki-cli.toml", text)
        with pytest.raises(ConfigError, match=message):
            load_settings(tmp_path)

    def test_research_relations(self, new_wiki, capsys):
        root, _ = new_wiki
        write(root, "wiki/sources/report.md", "---\ntitle: Report\ntype: source\n---\n# Report\n\n## Summary\nA report.\n")
        write(root, "wiki/people/ada.md", "---\ntitle: Ada\ntype: person\nsources: [report]\n---\n# Ada\n\n"
                                          "## Summary\nA mathematician ([[report]], p. 2).\n\n"
                                          "## Relationships\n- [[babbage]] — collaborator on the engine ([[report]], p. 4)\n")
        code, result = run_json(capsys, "neighbors", "ada", "--outgoing", "--root", str(root))
        edges = {item["slug"]: item for item in result["neighbors"]}
        assert edges["babbage"]["type"] == "associated-with"
        assert edges["babbage"]["reason"] == "collaborator on the engine (report, p. 4)"
        assert edges["report"]["type"] == "draws-on"
        assert edges["report"]["reason"] == "Summary: A mathematician (report, p. 2)."


class TestGuide:
    def test_lists_and_prints_without_a_wiki(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        code, listing = run_json(capsys, "guide")
        assert code == 0 and "query" in [item["name"] for item in listing["guides"]]
        code, query = run_json(capsys, "guide", "query")
        assert "wiki nav start" in query["text"] and "{{" not in query["text"]

    def test_unknown_guide(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        assert main(["guide", "nonsense"]) == 2
        assert "no guide 'nonsense'" in capsys.readouterr().err

    def test_types_and_rules_filled_in(self, new_wiki, capsys):
        root, _ = new_wiki
        from wiki_cli import guide
        text = guide.types_text(load_settings(root))
        assert "- `person`: A person. (pages in `wiki/people/`; template `templates/person.md`)" in text
        code, query = run_json(capsys, "guide", "query", "--root", str(root))
        assert "Your rules" in query["text"]

    def test_a_wiki_can_override_a_guide(self, new_wiki, capsys):
        root, _ = new_wiki
        config = root / ".wiki-cli.toml"
        config.write_text(config.read_text(encoding="utf-8") + '\n[guides]\ndir = "guides"\n', encoding="utf-8")
        write(root, "guides/query.md", "# Query: our way\n\nAsk the archivist.\n")
        write(root, "guides/claims.md", "# Claims: update the ledger\n\nSteps.\n")
        code, query = run_json(capsys, "guide", "query", "--root", str(root))
        assert query["text"] == "# Query: our way\n\n" + guide.preamble("query") + "\nAsk the archivist.\n"
        code, listing = run_json(capsys, "guide", "--root", str(root))
        assert {"name": "claims", "title": "Claims: update the ledger"} in listing["guides"]


class TestList:
    def test_lists_pages_by_type(self, new_wiki, capsys):
        root, _ = new_wiki
        write(root, "wiki/people/ada.md", "---\ntitle: Ada Lovelace\n---\n# Ada\n\n## Summary\nA mathematician.\n")
        write(root, "wiki/places/london.md", "---\ntitle: London\n---\n# London\n\n## Summary\nA city.\n")
        code, result = run_json(capsys, "list", "--root", str(root))
        assert [(page["slug"], page["type"]) for page in result["pages"]] == [
            ("ada", "person"), ("london", "place"), ("open-questions", None)]
        code, result = run_json(capsys, "list", "--type", "person", "--root", str(root))
        assert [page["title"] for page in result["pages"]] == ["Ada Lovelace"]
        assert result["pages"][0]["summary"] == "A mathematician."
        capsys.readouterr()
        assert main(["list", "--root", str(root)]) == 0
        text = capsys.readouterr().out
        assert "## person\n- ada: Ada Lovelace — A mathematician." in text


def test_ingest_guide_names_the_wikis_types(new_wiki, capsys):
    root, _ = new_wiki
    code, ingest = run_json(capsys, "guide", "ingest", "--root", str(root))
    assert code == 0
    assert "`place`: A location: a country" in ingest["text"] and "wiki suggest" in ingest["text"]
    assert "{{" not in ingest["text"] and "}}" not in ingest["text"] and "\n\n\n" not in ingest["text"]
    # a wiki with no code repo gets no code steps
    assert "code:" not in ingest["text"] and "covers:" not in ingest["text"] and "code repo" not in ingest["text"]
    assert "the source (or" not in ingest["text"] and "naming the source, the pages" in ingest["text"]
    scaffold(root, agent="claude")
    assert (root / ".claude/skills/wiki-ingest/SKILL.md").is_file()


@pytest.mark.skipif(not shutil.which("git"), reason="needs git")
def test_git_hook_blocks_edits_to_raw_and_check_errors(tmp_path):
    import subprocess
    root = tmp_path / "w"
    result = scaffold(root, git_hook=True)
    assert ".githooks/pre-commit" in result["created"] and ".gitattributes" in result["created"]
    assert b"\r\n" not in (root / ".githooks/pre-commit").read_bytes()

    def git(*args):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=root,
                              capture_output=True, text=True)

    assert git("config", "--get", "core.hooksPath").stdout.strip() == ".githooks"
    write(root, "raw/source.txt", "original\n")
    git("add", "-A")
    assert git("commit", "-qm", "add a source").returncode == 0  # adding sources is fine
    write(root, "raw/source.txt", "edited\n")
    git("add", "-A")
    refused = git("commit", "-qm", "edit a source")
    assert refused.returncode != 0 and "raw/source.txt" in refused.stderr


def test_summary_ok_clears_summary_stale(new_wiki, capsys):
    root, _ = new_wiki
    page = write(root, "wiki/people/ada.md", "---\ntitle: Ada\n---\n# Ada\n\n## Summary\nA mathematician.\n\n"
                                            "## Details\nWrote notes.\n")
    main(["index", "refresh", "--no-embed", "--root", str(root)])
    page.write_text(page.read_text(encoding="utf-8").replace("Wrote notes.", "Wrote the first program."),
                    encoding="utf-8")
    main(["index", "refresh", "--no-embed", "--root", str(root)])
    code, report = run_json(capsys, "check", "ada", "--root", str(root))
    assert [issue["code"] for issue in report["issues"]] == ["summary-stale"]
    code, report = run_json(capsys, "check", "ada", "--summary-ok", "--root", str(root))
    assert report["issues"] == []
    assert main(["check", "--all", "--summary-ok", "--root", str(root)]) == 2


def test_line_endings_alone_do_not_make_a_summary_stale(new_wiki, capsys):
    root, _ = new_wiki
    page = write(root, "wiki/people/ada.md", "---\ntitle: Ada\n---\n# Ada\n\n## Summary\nA mathematician.\n")
    main(["index", "refresh", "--no-embed", "--root", str(root)])
    page.write_bytes(page.read_bytes().replace(b"\n", b"\r\n"))
    main(["index", "refresh", "--no-embed", "--root", str(root)])
    code, report = run_json(capsys, "check", "ada", "--root", str(root))
    assert report["issues"] == []


def test_an_established_wiki_without_instructions_gets_a_short_agents_md(tmp_path):
    root = tmp_path / "notes"
    write(root, ".wiki-cli.toml", 'pages = ["**/*.md"]\n')
    result = scaffold(root)
    assert "AGENTS.md" in result["created"]
    text = (root / "AGENTS.md").read_text(encoding="utf-8")
    assert text.startswith("## LLM wiki") and "wiki guide ingest" in text


def test_every_guide_says_the_agent_does_the_work_and_is_ascii(new_wiki, capsys):
    # PowerShell 5.1 (OpenCode's shell on Windows) mangles non-ASCII in command output
    root, _ = new_wiki
    for name in ("ingest", "query", "lint"):
        code, rendered = run_json(capsys, "guide", name, "--root", str(root))
        text = rendered["text"]
        assert text.splitlines()[2].startswith("> **You carry out these steps yourself.**")
        assert f"there is no `wiki {name}`" in text
        assert all(ord(ch) < 127 for ch in text), [ch for ch in text if ord(ch) > 126]


@pytest.mark.parametrize("argv, message", [
    (["ingest", "raw/x.txt"], "there is no 'ingest' command. `wiki guide ingest` prints the steps"),
    (["guide", "ingest", "raw/x.txt"], "takes no other arguments: it prints steps for you to carry out yourself. "
                                       "Follow them, starting at step 1, with raw/x.txt."),
])
def test_running_a_workflow_as_a_command_explains_the_guide(new_wiki, capsys, monkeypatch, argv, message):
    root, _ = new_wiki
    monkeypatch.chdir(root)
    assert main(argv) == 2
    assert message in capsys.readouterr().err
