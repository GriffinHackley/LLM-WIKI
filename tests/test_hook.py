import io
import json
from pathlib import Path

import pytest

from wiki_cli import hook
from wiki_cli.cli import main


@pytest.fixture
def wiki(tmp_path) -> Path:
    root = tmp_path / "w"
    root.mkdir()
    (root / ".wiki-cli.toml").write_text('pages = ["wiki/**/*.md"]\n', encoding="utf-8")
    return root


def call(monkeypatch, capsys, args: list[str], stdin: str) -> tuple[int, str, str]:
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    code = main(["hook", *args])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def log_entries(root: Path) -> list[dict]:
    path = root / ".cache" / hook.LOG_FILE
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_log_records_each_call_and_prints_nothing(wiki, monkeypatch, capsys):
    payload = {"tool_name": "Read", "tool_input": {"file_path": "src/a.py"}, "session_id": "s1"}
    assert call(monkeypatch, capsys, ["PostToolUse", "--agent", "claude-code", "--log", "--root", str(wiki)],
                json.dumps(payload)) == (0, "", "")
    assert call(monkeypatch, capsys, ["preToolUse", "--agent", "copilot", "--log", "--root", str(wiki)],
                '{"toolName": "view"}')[0] == 0
    first, second = log_entries(wiki)
    assert first["event"] == "PostToolUse" and first["agent"] == "claude-code"
    assert first["payload"] == payload
    assert second["payload"] == {"toolName": "view"}


def test_long_text_is_cut_short_so_file_contents_stay_out_of_the_log(wiki, monkeypatch, capsys):
    content = "x" * 5000
    payload = {"tool_response": {"content": content}, "lines": [content]}
    call(monkeypatch, capsys, ["PostToolUse", "--log", "--root", str(wiki)], json.dumps(payload))
    logged = log_entries(wiki)[0]["payload"]
    assert logged["tool_response"]["content"].endswith("... (5000 characters)")
    assert len(logged["tool_response"]["content"]) < 400
    assert logged["lines"][0].endswith("... (5000 characters)")


def test_input_that_is_not_json_is_kept_as_text(wiki, monkeypatch, capsys):
    call(monkeypatch, capsys, ["Stop", "--log", "--root", str(wiki)], "not json")
    assert log_entries(wiki)[0]["payload"] == {"unparsed": "not json"}


def test_without_log_nothing_is_written(wiki, monkeypatch, capsys):
    assert call(monkeypatch, capsys, ["PreToolUse", "--root", str(wiki)], "{}") == (0, "", "")
    assert not (wiki / ".cache").exists()


def test_it_exits_0_even_with_no_wiki_and_an_unknown_agent(tmp_path, monkeypatch, capsys):
    """A failing pre-tool hook makes Copilot deny the tool call, so nothing may fail."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("WIKI_ROOT", raising=False)
    assert call(monkeypatch, capsys, ["preToolUse", "--agent", "no-such-agent", "--log",
                                      "--root", str(tmp_path / "missing")], "{}") == (0, "", "")


def test_in_a_repo_with_no_wiki_it_writes_nothing(tmp_path, monkeypatch, capsys):
    """Other commands fall back to the current folder as the wiki; a hook must not, or it
    would litter every repo it runs in."""
    repo = tmp_path / "code"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.chdir(repo)
    monkeypatch.delenv("WIKI_ROOT", raising=False)
    assert call(monkeypatch, capsys, ["PostToolUse", "--log"], "{}") == (0, "", "")
    assert list(repo.iterdir()) == [repo / ".git"]


def test_from_a_code_repo_it_follows_the_pointer_to_the_wiki(wiki, tmp_path, monkeypatch, capsys):
    repo = tmp_path / "code"
    repo.mkdir()
    (repo / ".wiki-cli.toml").write_text(f'wiki = "{wiki.as_posix()}"\n', encoding="utf-8")
    monkeypatch.chdir(repo)
    monkeypatch.delenv("WIKI_ROOT", raising=False)
    call(monkeypatch, capsys, ["PostToolUse", "--log"], '{"tool_name": "Read"}')
    assert log_entries(wiki)[0]["payload"] == {"tool_name": "Read"}
    assert not (repo / ".cache").exists()


def test_a_log_that_cannot_be_written_is_ignored(wiki, monkeypatch, capsys):
    (wiki / ".cache").write_text("a file where the folder should be", encoding="utf-8")
    assert call(monkeypatch, capsys, ["PostToolUse", "--log", "--root", str(wiki)], "{}") == (0, "", "")


# -- what the hook does for a configured agent ---------------------------------------------

from test_code import code_repo, code_wiki, git, module_page, write  # noqa: E402,F401  (fixtures)
from wiki_cli import tasks  # noqa: E402
from wiki_cli.config import load_settings  # noqa: E402


@pytest.fixture
def connected(code_wiki, code_repo, monkeypatch):
    """A code repo whose pointer names both agents, with one module page and a proposed decision."""
    head = git(code_repo, "rev-parse", "HEAD")
    module_page(code_wiki, "search", ["src/app/search.py"], head)
    write(code_wiki, "wiki/notes/plain-lists.md", "---\ntitle: plain-lists\ntype: decision\nstatus: proposed\n"
          "---\n# plain-lists\n\n## Summary\nResults stay plain lists.\n\n## Affects\n- [[search]] — here\n")
    write(code_repo, ".wiki-cli.toml", 'wiki = "../app-wiki"\nagents = ["claude-code", "copilot"]\n')
    monkeypatch.chdir(code_repo)
    monkeypatch.delenv("WIKI_ROOT", raising=False)
    return code_repo


def reply(monkeypatch, capsys, event, agent, payload) -> dict | None:
    code, out, err = call(monkeypatch, capsys, [event, "--agent", agent], json.dumps(payload))
    assert code == 0 and not err
    return json.loads(out) if out else None


def claude(event, session="s1", **fields):
    return {"session_id": session, "hook_event_name": event, **fields}


def test_session_start_points_at_the_wiki_and_lists_proposed_decisions(connected, monkeypatch, capsys):
    out = reply(monkeypatch, capsys, "SessionStart", "claude-code", claude("SessionStart", source="startup"))
    context = out["hookSpecificOutput"]["additionalContext"]
    assert "../app-wiki" in context and "wiki guide work" in context and "git commit" in context
    assert "- plain-lists: Results stay plain lists." in context


def test_git_commit_is_denied_and_wiki_finish_asks(connected, monkeypatch, capsys):
    for command in ("git commit -m x", "cd src; git -C .. commit -am 'x'", "git.exe --no-pager commit"):
        out = reply(monkeypatch, capsys, "PreToolUse", "claude-code",
                    claude("PreToolUse", tool_name="PowerShell", tool_input={"command": command}))
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny", command
    out = reply(monkeypatch, capsys, "preToolUse", "copilot",
                {"sessionId": "c1", "timestamp": 1, "toolName": "bash", "toolArgs": {"command": "wiki finish"}})
    assert out["permissionDecision"] == "ask" and "no task is open" in out["permissionDecisionReason"]
    for command in ("git status", "git log --grep commit", "echo wiki finished"):
        assert reply(monkeypatch, capsys, "PreToolUse", "claude-code",
                     claude("PreToolUse", tool_name="Bash", tool_input={"command": command})) is None, command


def open_task(code_repo, session):
    return tasks.start(load_settings(code_repo), "Fix search", session)


def test_claude_code_sees_a_files_notes_before_its_first_edit_only(connected, monkeypatch, capsys):
    open_task(connected, "s1")
    path = str(connected / "src/app/search.py").lower()  # Claude Code sends c:\... on Windows
    edit = claude("PreToolUse", tool_name="Edit", tool_input={"file_path": path})
    context = reply(monkeypatch, capsys, "PreToolUse", "claude-code", edit)["hookSpecificOutput"]["additionalContext"]
    assert "Wiki notes on src/app/search.py" in context and "plain-lists (decision, proposed)" in context
    assert reply(monkeypatch, capsys, "PreToolUse", "claude-code", edit) is None
    read_after = claude("PostToolUse", session="s2", tool_name="Read", tool_input={"file_path": path})
    assert "Wiki notes" in reply(monkeypatch, capsys, "PostToolUse", "claude-code", read_after)[
        "hookSpecificOutput"]["additionalContext"]


def test_copilot_sees_notes_after_the_tool_and_edits_are_recorded(connected, code_wiki, monkeypatch, capsys):
    open_task(connected, "c1")
    payload = {"sessionId": "c1", "timestamp": 1, "toolName": "edit", "toolArgs": {"path": "src/app/search.py"}}
    assert reply(monkeypatch, capsys, "preToolUse", "copilot", payload) is None
    assert "Wiki notes on src/app/search.py" in reply(monkeypatch, capsys, "postToolUse", "copilot",
                                                      payload)["additionalContext"]
    reply(monkeypatch, capsys, "postToolUse", "copilot", payload)
    edited = tasks.load(load_settings(connected)).edited
    assert edited == [(connected / "src/app/search.py").resolve().as_posix()]


def test_only_the_configured_agents_and_their_own_format_get_answers(connected, monkeypatch, capsys):
    write(connected, ".wiki-cli.toml", 'wiki = "../app-wiki"\nagents = ["copilot"]\n')
    assert reply(monkeypatch, capsys, "SessionStart", "claude-code", claude("SessionStart")) is None
    write(connected, ".wiki-cli.toml", 'wiki = "../app-wiki"\nagents = ["claude-code"]\n')
    # Copilot running the Claude Code hook file sends snake_case with a timestamp: ignored.
    assert reply(monkeypatch, capsys, "SessionStart", "claude-code",
                 claude("SessionStart", timestamp="2026-10-10T00:00:00Z")) is None
    assert reply(monkeypatch, capsys, "SessionStart", "claude-code", "not an object") is None


@pytest.fixture
def writing(tmp_path) -> Path:
    root = tmp_path / "stories"
    (root / "originals" / "the-salt-road").mkdir(parents=True)
    (root / "originals" / "the-salt-road" / "02-storm.md").write_text("The storm.\n", encoding="utf-8")
    (root / "raw").mkdir()
    (root / "raw" / "tides.txt").write_text("a source\n", encoding="utf-8")
    (root / ".wiki-cli.toml").write_text('agents = ["claude-code"]\npages = ["wiki/**/*.md"]\n\n[originals]\n',
                                         encoding="utf-8")
    return root


def edit(monkeypatch, capsys, root: Path, rel: str, tool: str = "Edit") -> str:
    payload = {"hook_event_name": "PreToolUse", "tool_name": tool, "session_id": "s1", "cwd": str(root),
               "tool_input": {"file_path": str(root / rel)}}
    code, out, _ = call(monkeypatch, capsys, ["PreToolUse", "--agent", "claude-code", "--root", str(root)],
                        json.dumps(payload))
    assert code == 0
    return out


def test_a_wiki_without_a_code_repo_refuses_edits_to_originals_and_sources(writing, monkeypatch, capsys):
    refused = json.loads(edit(monkeypatch, capsys, writing, "originals/the-salt-road/02-storm.md"))
    assert refused["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "the user's own writing" in refused["hookSpecificOutput"]["permissionDecisionReason"]
    assert "deny" in edit(monkeypatch, capsys, writing, "originals/loose/new-idea.md", "Write")  # new ones too
    assert "never edited" in edit(monkeypatch, capsys, writing, "raw/tides.txt")
    assert edit(monkeypatch, capsys, writing, "raw/new-source.txt", "Write") == ""  # saving a new source
    assert edit(monkeypatch, capsys, writing, "wiki/characters/mara.md") == ""  # no task needed


def test_an_agent_the_config_does_not_name_gets_nothing(writing, monkeypatch, capsys):
    (writing / ".wiki-cli.toml").write_text('pages = ["wiki/**/*.md"]\n\n[originals]\n', encoding="utf-8")
    assert edit(monkeypatch, capsys, writing, "originals/the-salt-road/02-storm.md") == ""
