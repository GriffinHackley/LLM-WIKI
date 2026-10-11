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
