"""`wiki commit`: the wiki's changes, never the user's originals."""

import json
import subprocess
import threading
from pathlib import Path

import pytest

from wiki_cli import commit
from wiki_cli.cli import main
from wiki_cli.config import load_settings


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          check=True, capture_output=True, text=True).stdout


@pytest.fixture
def wiki(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "stories"
    write(root, ".wiki-cli.toml", 'pages = ["wiki/**/*.md"]\n\n[originals]\n')
    write(root, "wiki/characters/mara.md", "# Mara\n")
    write(root, "wiki/characters/old.md", "# Old\n")
    write(root, "originals/the-salt-road/02-storm.md", "The storm.\n")
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "commit", "-qm", "start")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "t")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@t")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "t")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@t")
    return root


def test_commits_the_wiki_and_leaves_the_originals(wiki, capsys):
    write(wiki, "wiki/characters/mara.md", "# Mara\n\nShe cannot swim.\n")
    write(wiki, "wiki/scenes/the-salt-road-02-storm.md", "# The storm\n")
    write(wiki, "raw/tides.txt", "a new source")
    (wiki / "wiki/characters/old.md").unlink()
    write(wiki, "originals/the-salt-road/02-storm.md", "The storm, half rewritten")
    git(wiki, "add", "originals")  # even staged by the user, it stays out
    assert main(["commit", "ingest: the-salt-road/02-storm", "--root", str(wiki), "--format", "json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] and sorted(result["files"]) == [
        "raw/tides.txt", "wiki/characters/mara.md", "wiki/characters/old.md", "wiki/scenes/the-salt-road-02-storm.md"]
    assert git(wiki, "log", "-1", "--format=%s").strip() == "ingest: the-salt-road/02-storm"
    assert git(wiki, "status", "--porcelain").strip() == "M  originals/the-salt-road/02-storm.md"


def test_nothing_to_commit(wiki, capsys):
    write(wiki, "originals/the-salt-road/02-storm.md", "Only the user's writing changed.\n")
    assert main(["commit", "ingest: nothing", "--root", str(wiki)]) == 0
    assert capsys.readouterr().out.strip() == "Nothing to commit."
    assert git(wiki, "rev-list", "--count", "HEAD").strip() == "1"


def test_waits_out_a_locked_index(wiki):
    write(wiki, "wiki/characters/mara.md", "# Mara, revised\n")
    lock = wiki / ".git" / "index.lock"
    lock.write_text("", encoding="utf-8")
    threading.Timer(0.3, lock.unlink).start()
    result = commit.commit(load_settings(wiki), "ingest: after the lock", wait=0.1)
    assert result.files == ["wiki/characters/mara.md"]


def test_reports_a_failing_hook_and_an_empty_message(wiki, capsys):
    hook = write(wiki, ".git/hooks/pre-commit", "#!/bin/sh\necho 'wiki check failed' >&2\nexit 1\n")
    hook.chmod(0o755)
    write(wiki, "wiki/characters/mara.md", "# Mara, revised\n")
    assert main(["commit", "ingest: blocked", "--root", str(wiki)]) == 1
    assert "wiki check failed" in capsys.readouterr().err
    assert main(["commit", " ", "--root", str(wiki)]) == 1
    assert "message is empty" in capsys.readouterr().err
