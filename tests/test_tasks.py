import io
import json

import pytest

from test_code import code_repo, code_wiki, git, module_page, write  # noqa: F401  (fixtures)
from wiki_cli import tasks
from wiki_cli.cli import main
from wiki_cli.config import load_settings


@pytest.fixture
def repo(code_wiki, code_repo, monkeypatch):
    """A code repo whose pointer names Claude Code, committed, with a page covering search.py."""
    head = git(code_repo, "rev-parse", "HEAD")
    module_page(code_wiki, "search", ["src/app/search.py"], head)
    git(code_wiki, "add", "-A")
    git(code_wiki, "commit", "-qm", "wiki")
    write(code_repo, ".wiki-cli.toml", 'wiki = "../app-wiki"\nagents = ["claude-code"]\n')
    git(code_repo, "add", "-A")
    git(code_repo, "commit", "-qm", "pointer")
    monkeypatch.chdir(code_repo)
    monkeypatch.delenv("WIKI_ROOT", raising=False)
    return code_repo


def hook(monkeypatch, capsys, event, session, **fields) -> dict | None:
    payload = {"session_id": session, "hook_event_name": event, **fields}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    capsys.readouterr()
    assert main(["hook", event, "--agent", "claude-code"]) == 0
    out = capsys.readouterr().out
    return json.loads(out)["hookSpecificOutput"] if out else None


def edit(monkeypatch, capsys, session, path, text):
    """An edit as Claude Code makes it: asked before, done, reported after."""
    before = hook(monkeypatch, capsys, "PreToolUse", session, tool_name="Write", tool_input={"file_path": str(path)})
    if before and before.get("permissionDecision") == "deny":
        return before["permissionDecisionReason"]
    path.write_text(text, encoding="utf-8")
    hook(monkeypatch, capsys, "PostToolUse", session, tool_name="Write", tool_input={"file_path": str(path)})
    return None


def shell(monkeypatch, capsys, session, command) -> dict:
    return hook(monkeypatch, capsys, "PreToolUse", session, tool_name="PowerShell", tool_input={"command": command})


def run(capsys, *args) -> tuple[int, str]:
    capsys.readouterr()
    code = main(list(args))
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def test_a_task_from_start_to_finish(repo, code_wiki, monkeypatch, capsys):
    search = repo / "src/app/search.py"
    assert "No task is open" in edit(monkeypatch, capsys, "s1", search, "x")

    asked = shell(monkeypatch, capsys, "s1", 'wiki task start "Fix search"')
    assert asked["permissionDecision"] == "ask"
    assert run(capsys, "task", "start", "Fix search")[0] == 0  # the session comes from the hook
    assert tasks.load(load_settings(repo)).session == "s1"

    assert "Another session holds the open task" in edit(monkeypatch, capsys, "s2", search, "x")
    assert edit(monkeypatch, capsys, "s1", search, "def search():\n    return [1]\n") is None
    write(repo, "src/app/nav.py", "changed by hand\n")  # not the task's

    code, out = run(capsys, "finish", "-m", "Return one result", "--dry-run")
    assert code != 0 and "[[search]] covers src/app/search.py" in out

    page = code_wiki / "wiki/modules/search.md"
    assert edit(monkeypatch, capsys, "s1", page, page.read_text(encoding="utf-8").replace(
        "verified: \"" + git(repo, "rev-parse", "HEAD~1"), "verified: \"pending").replace(
        "The search module.", "The search module, returning one result.")) is None
    reason = shell(monkeypatch, capsys, "s1", "wiki finish -m 'Return one result'")["permissionDecisionReason"]
    assert "src/app/search.py" in reason and "wiki/modules/search.md" in reason and "src/app/nav.py" in reason

    code, out = run(capsys, "finish", "-m", "Return one result")
    assert code == 0, out
    code_commit = git(repo, "rev-parse", "HEAD")
    assert git(repo, "show", "--name-only", "--format=%s", "HEAD").splitlines() == [
        "Return one result", "", "src/app/search.py"]
    assert git(repo, "status", "--porcelain") == "M src/app/nav.py"
    assert f'verified: "{code_commit}"' in page.read_text(encoding="utf-8")
    assert git(code_wiki, "log", "-1", "--format=%B") == f"Return one result\n\nCode: {code_commit}"
    assert git(code_wiki, "status", "--porcelain") == ""
    assert tasks.load(load_settings(repo)) is None
    assert "No task is open" in edit(monkeypatch, capsys, "s1", search, "x")


def test_one_task_at_a_time_and_takeover(repo, monkeypatch, capsys):
    tasks.start(load_settings(repo), "First", "s1")
    code, out = run(capsys, "task", "start", "Second", "--session", "s2")
    assert code != 0 and "a task is already open" in out
    assert shell(monkeypatch, capsys, "s2", "wiki task takeover")["permissionDecision"] == "ask"
    assert run(capsys, "task", "takeover")[0] == 0
    assert tasks.load(load_settings(repo)).session == "s2"
    assert edit(monkeypatch, capsys, "s2", repo / "src/app/nav.py", "x") is None
    assert "Another session" in edit(monkeypatch, capsys, "s1", repo / "src/app/nav.py", "y")


def test_a_start_no_session_asked_for_is_refused(repo, capsys):
    code, out = run(capsys, "task", "start", "Fix")
    assert code != 0 and "no agent session asked for this" in out


def test_cancel_keeps_the_changes(repo, monkeypatch, capsys):
    tasks.start(load_settings(repo), "Try", "s1")
    edit(monkeypatch, capsys, "s1", repo / "src/app/nav.py", "tried\n")
    assert run(capsys, "task", "cancel")[0] == 0
    assert tasks.load(load_settings(repo)) is None
    assert (repo / "src/app/nav.py").read_text(encoding="utf-8") == "tried\n"


def test_files_outside_the_code_and_the_wiki_are_not_guarded(repo, tmp_path, monkeypatch, capsys):
    assert edit(monkeypatch, capsys, "s1", tmp_path / "notes.txt", "x") is None


def test_include_adds_a_change_the_task_did_not_make(repo, monkeypatch, capsys):
    tasks.start(load_settings(repo), "Nav", "s1")
    write(repo, "src/app/nav.py", "formatted\n")  # say, by a formatter the agent ran
    code, out = run(capsys, "finish", "-m", "Format nav", "--include", "src/app/nav.py")
    assert code == 0, out
    assert git(repo, "show", "--name-only", "--format=", "HEAD") == "src/app/nav.py"


def test_stale_and_check_accept_a_pending_page(repo, code_wiki, capsys):
    page = code_wiki / "wiki/modules/search.md"
    text = page.read_text(encoding="utf-8")
    page.write_text(text[:text.index("verified:")] + 'verified: "pending"\n' + text[text.index("---\n#"):],
                    encoding="utf-8")
    write(repo, "src/app/search.py", "changed\n")
    code, out = run(capsys, "stale", "--format", "json")
    assert json.loads(out)["stale"] == []
    code, out = run(capsys, "check", "search")
    assert "verified" not in out
