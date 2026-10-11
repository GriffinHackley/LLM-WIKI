"""`[originals]`: the user's writing, read by the agent and never changed."""

import json
import subprocess
from pathlib import Path

import pytest

from wiki_cli import originals, precommit
from wiki_cli.cli import main
from wiki_cli.config import ConfigError, load_settings
from wiki_cli.pages import RAW, scan_vault
from wiki_cli.sources import raw_dirs

CONFIG = """\
pages = ["wiki/**/*.md"]
raw = ["raw/**/*.txt"]

[originals]
"""

ORIGINAL = "---\nkind: scene\ncreated: 2026-10-01\nlast_updated: 2026-10-02\n---\nThe storm came in at dusk.\n"


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def wiki(tmp_path) -> Path:
    root = tmp_path / "stories"
    write(root, ".wiki-cli.toml", CONFIG)
    write(root, "wiki/characters/mara.md", "---\ntitle: Mara\n---\n# Mara\n")
    return root


def test_the_originals_folder_is_a_raw_folder(wiki):
    write(wiki, "originals/the-salt-road/02-storm.md", ORIGINAL)
    settings = load_settings(wiki)
    assert "originals/**/*.md" in settings.raw and "originals" in raw_dirs(settings)
    kinds = {page_file.rel: page_file.kind for page_file, _ in scan_vault(settings)[0]}
    assert kinds["originals/the-salt-road/02-storm.md"] == RAW


def test_originals_are_never_pages_even_when_pages_match_them(wiki):
    write(wiki, ".wiki-cli.toml", CONFIG.replace('pages = ["wiki/**/*.md"]', 'pages = ["**/*.md"]'))
    write(wiki, "originals/loose/bell.md", ORIGINAL)
    kinds = {page_file.rel: page_file.kind for page_file, _ in scan_vault(load_settings(wiki))[0]}
    assert kinds["originals/loose/bell.md"] == RAW


def test_book_comes_from_the_folder(wiki):
    found = load_settings(wiki).originals
    assert found.book("originals/the-salt-road/02-the-crossing/01-departure.md") == "the-salt-road"
    assert found.book("originals/loose/bell.md") is None
    assert found.book("originals/stray.md") is None
    assert found.book("wiki/characters/mara.md") is None


def test_config_errors(wiki):
    write(wiki, ".wiki-cli.toml", CONFIG + 'colour = "red"\n')
    with pytest.raises(ConfigError, match="unknown key"):
        load_settings(wiki)
    write(wiki, ".wiki-cli.toml", CONFIG + 'kinds = "scene"\n')
    with pytest.raises(ConfigError, match="kinds"):
        load_settings(wiki)


def test_check_reports_an_original_its_template_missed(wiki, capsys):
    write(wiki, "originals/the-salt-road/01-arrival.md", ORIGINAL)
    write(wiki, "originals/the-salt-road/03-wreck.md", "No frontmatter at all.\n")
    write(wiki, "originals/loose/bell.md", ORIGINAL.replace("kind: scene", "kind: poem"))
    main(["check", "--all", "--root", str(wiki), "--format", "json"])
    issues = {(issue["path"], issue["code"]) for issue in json.loads(capsys.readouterr().out)["issues"]
              if issue["code"].startswith("original-")}
    assert issues == {("originals/the-salt-road/03-wreck.md", "original-missing-field"),
                      ("originals/loose/bell.md", "original-bad-kind")}


def test_no_originals_table_no_checks(tmp_path):
    root = tmp_path / "plain"
    write(root, ".wiki-cli.toml", 'pages = ["wiki/**/*.md"]\n')
    assert originals.check(load_settings(root)) == []


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def test_the_pre_commit_hook_lets_originals_change(wiki):
    write(wiki, "originals/the-salt-road/02-storm.md", ORIGINAL)
    write(wiki, "raw/report.txt", "a source")
    git(wiki, "init", "-q")
    git(wiki, "-c", "user.name=t", "-c", "user.email=t@t", "add", ".")
    git(wiki, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "start")
    write(wiki, "originals/the-salt-road/02-storm.md", ORIGINAL + "Then the rain.\n")
    write(wiki, "raw/report.txt", "an edited source")
    git(wiki, "add", ".")
    assert precommit.changed_sources(load_settings(wiki)) == ["raw/report.txt"]
