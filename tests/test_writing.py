"""The writing preset: the user's originals, and a wiki of the stories built from them."""

import json
from pathlib import Path

import pytest

from wiki_cli import frontmatter
from wiki_cli.cli import main
from wiki_cli.config import load_settings
from wiki_cli.scaffold import scaffold


@pytest.fixture
def wiki(tmp_path) -> Path:
    root = tmp_path / "stories"
    scaffold(root, preset="writing", agent=["claude", "copilot"])
    return root


def test_the_preset_is_set_up(wiki):
    settings = load_settings(wiki)
    assert settings.preset == "writing" and settings.originals is not None
    assert settings.group_fields == ("book",) and settings.groups_exclusive
    assert "originals/**/*.md" in settings.raw
    assert (wiki / "originals" / "loose" / ".gitkeep").is_file()
    assert {path.name for path in (wiki / "templates" / "originals").iterdir()} == {"idea.md", "scene.md",
                                                                                     "chapter.md"}
    claude = json.loads((wiki / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert "Edit(/originals/**)" in claude["permissions"]["deny"]
    assert (wiki / ".github" / "hooks" / "wiki.json").is_file()  # Copilot's hook refuses edits to originals


def test_every_type_has_a_template_of_that_type(wiki):
    settings = load_settings(wiki)
    assert len(settings.types) == 16
    for page_type in settings.types:
        text = (wiki / page_type.template).read_text(encoding="utf-8")
        data = frontmatter.parse(text).data
        assert data["type"] == page_type.name, page_type.name
        assert all(key in data for key in page_type.fields), page_type.name
        headings = {line[3:].strip().lower() for line in text.splitlines() if line.startswith("## ")}
        assert all(section.lower() in headings for section in page_type.sections), page_type.name


def test_the_obsidian_templates_fill_what_check_asks_of_originals(wiki):
    settings = load_settings(wiki)
    for path in (wiki / "templates" / "originals").iterdir():
        data = frontmatter.parse(path.read_text(encoding="utf-8")).data
        assert set(data) == set(settings.originals.fields) and data["kind"] in settings.originals.kinds


def test_a_new_writing_wiki_checks_clean(wiki, capsys):
    assert main(["check", "--all", "--root", str(wiki), "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["issues"] == []


def test_the_relations_read_as_the_preset_means_them(wiki, capsys):
    (wiki / "originals" / "the-salt-road").mkdir()
    (wiki / "originals" / "the-salt-road" / "02-storm.md").write_text(
        "---\nkind: scene\ncreated: 2026-10-01\nlast_updated: 2026-10-01\n---\nThe storm.\n", encoding="utf-8")
    (wiki / "wiki" / "scenes").mkdir()
    (wiki / "wiki" / "scenes" / "the-salt-road-02-storm.md").write_text(
        "---\ntitle: The storm\ntype: scene\nbook: the-salt-road\n"
        "original: \"[[originals/the-salt-road/02-storm.md]]\"\n---\n# The storm\n\n## Summary\nA storm.\n\n"
        "## Appears\n- [[mara]] — nearly drowns\n", encoding="utf-8")
    main(["neighbors", "the-salt-road-02-storm", "--outgoing", "--root", str(wiki), "--format", "json"])
    found = {(item["slug"], item["type"]) for item in json.loads(capsys.readouterr().out)["neighbors"]}
    assert found == {("mara", "features"), ("originals/the-salt-road/02-storm", "draws-on")}
