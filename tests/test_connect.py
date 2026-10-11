import json
import tomllib

from test_code import code_repo, code_wiki, run_json, write  # noqa: F401  (fixtures)
from wiki_cli.cli import main
from wiki_cli.config import load_settings
from wiki_cli.scaffold import scaffold


def connect(capsys, wiki, *args) -> tuple[int, dict]:
    return run_json(capsys, "connect", *args, "--root", str(wiki))


def files(result) -> dict:
    return {item["file"]: item["status"] for item in result["files"]}


def by_hand(result) -> dict:
    return {item["file"]: item["text"] for item in result["by_hand"]}


def test_claude_code_gets_the_wikis_files_and_the_lines_to_add(code_wiki, code_repo, capsys):
    code, result = connect(capsys, code_wiki, str(code_repo), "--agent", "claude")
    assert code == 0 and result["agents"] == ["claude-code"]
    written = files(result)
    assert written[".wiki-cli.toml"] == written["WIKI.md"] == written[".claude/skills/wiki-work/SKILL.md"] == "created"
    assert ".github/hooks/wiki.json" not in written
    assert load_settings(code_repo).agents == ("claude-code",)  # the pointer works, and names the agent
    assert "wiki guide work" in (code_repo / "WIKI.md").read_text(encoding="utf-8")

    lines = by_hand(result)
    assert "@WIKI.md" in lines["AGENTS.md"] and lines["CLAUDE.md"] == "@AGENTS.md\n"
    settings = json.loads(lines[".claude/settings.json"])
    assert settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "wiki hook PreToolUse --agent claude-code"
    assert settings["permissions"]["additionalDirectories"] == [code_wiki.resolve().as_posix()]
    assert not (code_repo / "AGENTS.md").exists() and not (code_repo / ".claude/settings.json").exists()


def test_check_reports_until_both_steps_are_done(code_wiki, code_repo, capsys):
    assert connect(capsys, code_wiki, str(code_repo), "--agent", "claude-code", "--check")[0] == 1
    assert not (code_repo / "WIKI.md").exists()
    code, result = connect(capsys, code_wiki, str(code_repo), "--agent", "claude-code")
    assert connect(capsys, code_wiki, str(code_repo), "--check")[0] == 1  # the agents come from the pointer now
    write(code_repo, "AGENTS.md", "# Rules\n\n" + by_hand(result)["AGENTS.md"])
    write(code_repo, "CLAUDE.md", "@AGENTS.md\n")
    write(code_repo, ".claude/settings.json", by_hand(result)[".claude/settings.json"])
    code, checked = connect(capsys, code_wiki, str(code_repo), "--check")
    assert code == 0 and checked["ok"], checked
    assert set(files(checked).values()) == {"current"}


def test_copilot_gets_its_hook_file_and_instructions(code_wiki, code_repo, capsys):
    code, result = connect(capsys, code_wiki, str(code_repo), "--agent", "copilot")
    hooks = json.loads((code_repo / ".github/hooks/wiki.json").read_text(encoding="utf-8"))
    assert hooks["version"] == 1 and set(hooks["hooks"]) == {"sessionStart", "preToolUse", "postToolUse"}
    assert hooks["hooks"]["preToolUse"][0]["powershell"] == "wiki hook preToolUse --agent copilot"
    assert (code_repo / ".github/instructions/wiki.instructions.md").is_file()
    assert (code_repo / ".github/skills/wiki-work/SKILL.md").is_file()
    assert set(by_hand(result)) == {"AGENTS.md"}


def test_a_rewritten_file_is_restored_and_no_agent_is_refused(code_wiki, code_repo, capsys):
    assert connect(capsys, code_wiki, str(code_repo))[0] != 0  # no agents named anywhere
    connect(capsys, code_wiki, str(code_repo), "--agent", "copilot")
    write(code_repo, "WIKI.md", "edited\n")
    code, result = connect(capsys, code_wiki, str(code_repo))
    assert files(result)["WIKI.md"] == "updated"


def test_a_wiki_is_not_taken_for_a_code_repo(code_wiki, tmp_path, capsys):
    other = tmp_path / "other-wiki"
    write(other, ".wiki-cli.toml", 'pages = ["*.md"]\n')
    code, _ = connect(capsys, code_wiki, str(other), "--agent", "copilot")
    assert code != 0


def test_wiki_new_saves_its_agents_in_the_config(tmp_path):
    root = tmp_path / "w"
    scaffold(root, agent=["claude", "opencode"])
    config = tomllib.loads((root / ".wiki-cli.toml").read_text(encoding="utf-8"))
    assert config["agents"] == ["claude-code", "opencode"]
    assert load_settings(root).agents == ("claude-code", "opencode")
    result = scaffold(root, agent=["copilot"])  # kept config already names agents: left alone
    assert not [item for item in result["add"] if item["file"] == ".wiki-cli.toml"]


def test_text_output(code_wiki, code_repo, capsys):
    assert main(["connect", str(code_repo), "--agent", "claude-code", "--root", str(code_wiki)]) == 0
    out = capsys.readouterr().out
    assert "1. The wiki's files" in out and "2. Add these by hand" in out and "@WIKI.md" in out
