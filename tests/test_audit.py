"""Template checks in `wiki check`, and `wiki check <source> --ingested`."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from wiki_cli.cli import main
from wiki_cli.config import ConfigError, load_settings
from wiki_cli.scaffold import scaffold

SOURCE = """---
title: Report, 2024-03
type: source
date: 2024-03-01
ingested: 2026-09-29
last_updated: 2026-09-29
---
# Report, 2024-03

## Summary
A report on the budget.

## Key points
- The budget passed (p. 2).

## Entities mentioned
- [[ada-lovelace]] - its author (p. 1)

## Original
[[raw/report.pdf]]
"""

PERSON = """---
title: Ada Lovelace
type: person
sources: [report-2024-03]
last_updated: 2026-09-29
---
# Ada Lovelace

## Summary
The report's author ([[report-2024-03]], p. 1).
"""


def write(root: Path, rel: str, text: str | bytes) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8") if isinstance(text, str) else text)
    return path


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=root, check=True,
                   capture_output=True)


def check(capsys, *args) -> tuple[int, dict]:
    capsys.readouterr()
    code = main(["check", *args, "--format", "json"])
    return code, json.loads(capsys.readouterr().out)


def codes(report: dict) -> list[str]:
    return sorted(issue["code"] for issue in report["issues"])


@pytest.fixture
def wiki(tmp_path) -> Path:
    root = tmp_path / "wiki"
    scaffold(root, preset="research")
    return root


class TestTemplateChecks:
    def test_a_page_following_its_template_is_clean(self, wiki, capsys):
        write(wiki, "raw/report.pdf", b"%PDF")
        write(wiki, "wiki/sources/report-2024-03.md", SOURCE)
        write(wiki, "wiki/people/ada-lovelace.md", PERSON)
        code, report = check(capsys, "--all", "--root", str(wiki))
        assert code == 0 and report["issues"] == []

    def test_missing_sections_and_fields(self, wiki, capsys):
        # what the first OpenCode run wrote: the right folder, its own sections, no type
        write(wiki, "wiki/sources/httpx.md", "---\ntitle: httpx README\ndate: 2025-04-24\n---\n\n## Summary\n"
                                              "An HTTP client.\n\n## What It Says\n- Things.\n\n## Links\n"
                                              "- [[raw/httpx.txt]]\n")
        code, report = check(capsys, "httpx", "--root", str(wiki))
        messages = {issue["code"]: issue["message"] for issue in report["issues"]}
        assert code == 0  # warnings
        assert messages["missing-field"] == ("frontmatter has no 'type', 'ingested', 'last_updated'; every source "
                                             "page has them (see templates/source.md)")
        assert messages["missing-section"] == ("no ## Key points, ## Entities mentioned, ## Original sections; every "
                                               "source page has them (see templates/source.md)")

    def test_headings_match_at_any_level_and_case_but_not_in_code(self, wiki, capsys):
        write(wiki, "wiki/people/ada.md", "---\ntitle: Ada\ntype: person\nsources: []\nlast_updated: x\n---\n"
                                          "```\n## Summary\n```\n")
        code, report = check(capsys, "ada", "--root", str(wiki))
        assert "missing-section" in codes(report)
        write(wiki, "wiki/people/ada.md", "---\ntitle: Ada\ntype: person\nsources: []\nlast_updated: x\n---\n"
                                          "### SUMMARY\nA mathematician.\n")
        code, report = check(capsys, "ada", "--root", str(wiki))
        assert codes(report) == []

    def test_listed_sources_must_be_cited(self, wiki, capsys):
        write(wiki, "wiki/people/ada.md", PERSON.replace("sources: [report-2024-03]",
                                                         "sources: [report-2024-03, \"[[memo]]\"]"))
        code, report = check(capsys, "ada", "--root", str(wiki))
        [issue] = [issue for issue in report["issues"] if issue["code"] == "uncited-sources"]
        assert "[[memo]]" in issue["message"]
        assert "report-2024-03" not in issue["message"]

    def test_a_plain_text_citation_counts(self, wiki, capsys):
        # a wiki's own citation format, as in Politics: "(senate-report-2024, p. 4)"
        write(wiki, "wiki/people/ada.md", PERSON.replace("([[report-2024-03]], p. 1)", "(report-2024-03, p. 1)"))
        code, report = check(capsys, "ada", "--root", str(wiki))
        assert codes(report) == []
        write(wiki, "wiki/people/ada.md", PERSON.replace("([[report-2024-03]], p. 1)", "(report-2024-03-b, p. 1)"))
        code, report = check(capsys, "ada", "--root", str(wiki))
        assert codes(report) == ["uncited-sources"]

    def test_types_without_sections_or_fields_check_nothing_extra(self, tmp_path, capsys):
        root = tmp_path / "vault"
        write(root, ".wiki-cli.toml", '[types.note]\nfolder = "notes"\n')
        write(root, "notes/a.md", "# A\n\nText.\n")
        code, report = check(capsys, "a", "--root", str(root))
        assert report["issues"] == []

    def test_values_limit_a_field(self, tmp_path, capsys):
        root = tmp_path / "vault"
        write(root, ".wiki-cli.toml", '[types.note]\nfolder = "notes"\nvalues = { kind = ["epic", "story"] }\n')
        write(root, "notes/a.md", "---\ntitle: A\ntype: note\nkind: Story\n---\n# A\n")
        write(root, "notes/b.md", "---\ntitle: B\ntype: note\nkind: [epic, banana]\n---\n# B\n")
        write(root, "notes/c.md", "---\ntitle: C\ntype: note\n---\n# C\n")  # absent: `fields` covers that
        assert check(capsys, "a", "--root", str(root))[1]["issues"] == []
        assert check(capsys, "c", "--root", str(root))[1]["issues"] == []
        code, report = check(capsys, "b", "--root", str(root))
        assert [issue["message"] for issue in report["issues"]] == [
            "'kind' is 'banana'; in a note page it is one of epic, story"]

    @pytest.mark.parametrize("entry, message", [
        ('values = ["epic"]', "[types.note.values] must be a table"),
        ('values = { kind = "epic" }', "'[types.note.values] kind' must be a list of strings"),
        ('sections = "Summary"', "'[types.note] sections' must be a list of strings"),
        ("fields = [1]", "'[types.note] fields' must be a list of strings"),
        ('template = ["x"]', "must be strings"),
    ])
    def test_config_errors(self, tmp_path, entry, message):
        write(tmp_path, ".wiki-cli.toml", f"[types.note]\n{entry}\n")
        with pytest.raises(ConfigError, match=message.replace("[", r"\[").replace("]", r"\]")):
            load_settings(tmp_path)


@pytest.mark.skipif(not shutil.which("git"), reason="needs git")
class TestIngested:
    @pytest.fixture
    def ingested(self, wiki) -> Path:
        write(wiki, "raw/report.pdf", b"%PDF")
        write(wiki, "wiki/sources/report-2024-03.md", SOURCE)
        write(wiki, "wiki/people/ada-lovelace.md", PERSON)
        git(wiki, "add", "-A")
        git(wiki, "commit", "-qm", "Ingest report-2024-03")
        return wiki

    def test_a_complete_ingest_is_clean(self, ingested, capsys):
        code, report = check(capsys, "report-2024-03", "--ingested", "--root", str(ingested))
        assert code == 0 and report["issues"] == [] and report["pages"] == 2

    def test_what_the_first_opencode_run_left_undone(self, wiki, capsys):
        write(wiki, "raw/httpx.txt", "httpx")
        write(wiki, "wiki/sources/httpx.md", "---\ntitle: httpx README\n---\n\n## Summary\nAn HTTP client.\n")
        code, report = check(capsys, "httpx", "--ingested", "--root", str(wiki))
        assert code == 1
        assert codes(report) == ["missing-field", "missing-section", "names-nothing", "no-original", "not-cited",
                                 "uncommitted"]
        uncommitted = next(issue for issue in report["issues"] if issue["code"] == "uncommitted")
        assert "raw/httpx.txt" in uncommitted["message"] and "git add <each file>" in uncommitted["message"]

    def test_open_questions_do_not_count(self, ingested, capsys):
        write(ingested, "wiki/people/ada-lovelace.md", PERSON.replace("([[report-2024-03]], p. 1)", ""))
        write(ingested, "wiki/open-questions.md", "# Open questions\n\n- Who? ([[report-2024-03]])\n")
        source = SOURCE.replace("- [[ada-lovelace]] - its author (p. 1)", "- Questions: [[open-questions]]")
        write(ingested, "wiki/sources/report-2024-03.md", source)
        git(ingested, "commit", "-qam", "edit")
        code, report = check(capsys, "report-2024-03", "--ingested", "--root", str(ingested))
        # ada still lists it in sources: without citing it
        assert codes(report) == ["names-nothing", "uncited-sources"]
        write(ingested, "wiki/people/ada-lovelace.md", PERSON.replace("([[report-2024-03]], p. 1)", "")
              .replace("[report-2024-03]", "[]"))
        git(ingested, "commit", "-qam", "edit")
        code, report = check(capsys, "report-2024-03", "--ingested", "--root", str(ingested))
        assert codes(report) == ["names-nothing", "not-cited"]

    def test_pages_citing_it_must_pass_check(self, ingested, capsys):
        write(ingested, "wiki/people/ada-lovelace.md", PERSON.replace("## Summary", "## About"))
        git(ingested, "commit", "-qam", "edit")
        code, report = check(capsys, "report-2024-03", "--ingested", "--root", str(ingested))
        [issue] = report["issues"]
        assert (issue["code"], issue["path"]) == ("missing-section", "wiki/people/ada-lovelace.md")

    def test_only_wiki_files_need_committing(self, ingested, capsys):
        write(ingested, ".obsidian/workspace.json", "{}")  # the user's editor state
        write(ingested, "notes.txt", "todo")
        code, report = check(capsys, "report-2024-03", "--ingested", "--root", str(ingested))
        assert code == 0 and report["issues"] == []
        write(ingested, "wiki/people/ada-lovelace.md", PERSON.replace("The report's author", "The author"))
        code, report = check(capsys, "report-2024-03", "--ingested", "--root", str(ingested))
        assert codes(report) == ["uncommitted"] and "wiki/people/ada-lovelace.md" in report["issues"][0]["message"]

    def test_not_a_git_repository(self, tmp_path, capsys):
        root = tmp_path / "plain"
        root.mkdir()
        (root / ".git").mkdir()  # stops wiki new from running git init; not a real repository
        scaffold(root, preset="research")
        shutil.rmtree(root / ".git")
        write(root, "raw/report.pdf", b"%PDF")
        write(root, "wiki/sources/report-2024-03.md", SOURCE)
        write(root, "wiki/people/ada-lovelace.md", PERSON)
        code, report = check(capsys, "report-2024-03", "--ingested", "--root", str(root))
        assert code == 0 and report["issues"] == []

    def test_usage(self, ingested, capsys):
        assert main(["check", "--all", "--ingested", "--root", str(ingested)]) == 2
        assert "--ingested takes one source page" in capsys.readouterr().err
