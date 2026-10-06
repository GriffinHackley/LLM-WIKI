"""Records (items that live in another system, like tickets) and guide parts."""

import datetime
import json

import pytest

from wiki_cli import guide, records
from wiki_cli.audit import ingested
from wiki_cli.cli import main
from wiki_cli.config import ConfigError, load_settings

CONFIG = """\
pages = ["wiki/**/*.md"]

[types.ticket]
folder = "wiki/tickets"
record = true

[types.module]
folder = "wiki/modules"
"""
NOW = datetime.datetime(2026, 10, 6, tzinfo=datetime.timezone.utc)


def write(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def ticket(root, slug, **fields):
    front = {"title": slug.upper(), "type": "ticket", **fields}
    lines = "".join(f"{key}: {json.dumps(value)}\n" for key, value in front.items())
    write(root, f"wiki/tickets/{slug}.md", f"---\n{lines}---\n# {slug}\n\n## Summary\nAbout {slug}.\n\n"
                                           "Concerns [[search]].\n")


@pytest.fixture
def root(tmp_path):
    root = tmp_path / "w"
    write(root, ".wiki-cli.toml", CONFIG)
    write(root, "wiki/modules/search.md", "---\ntitle: Search\ntype: module\n---\n# Search\n\n## Summary\n"
                                          "Search, as asked in [[proj-1]].\n")
    return root


class TestRecords:
    def test_check_wants_key_url_and_a_time(self, root, capsys):
        ticket(root, "proj-1", key="PROJ-1", url="https://acme.atlassian.net/browse/PROJ-1",
               synced="2026-10-01T14:02:11.000+0000")
        ticket(root, "proj-2", url="jira/PROJ-2", synced="soon")
        capsys.readouterr()
        assert main(["check", "--all", "--root", str(root), "--format", "json"]) == 0
        issues = {(issue["path"].rsplit("/", 1)[-1], issue["code"]) for issue in json.loads(capsys.readouterr().out)["issues"]}
        assert issues == {("proj-2.md", "missing-field"), ("proj-2.md", "bad-url"), ("proj-2.md", "bad-synced")}

    def test_synced_reads_tracker_times(self):
        assert records.synced_at("2026-10-01T14:02:11.000+0000") == datetime.datetime(
            2026, 10, 1, 14, 2, 11, tzinfo=datetime.timezone.utc)
        assert records.synced_at("2026-10-01T14:02:11Z").tzinfo is not None
        assert records.synced_at(datetime.date(2026, 10, 1)).day == 1
        assert records.synced_at("next week") is None

    def test_due_for_a_recheck(self, root):
        ticket(root, "proj-1", key="PROJ-1", url="https://x.test/PROJ-1", status="open", synced="2026-10-01")
        ticket(root, "proj-2", key="PROJ-2", url="https://x.test/PROJ-2", status="open", synced="2026-07-01")
        ticket(root, "proj-3", key="PROJ-3", url="https://x.test/PROJ-3", status="Done", synced="2026-01-01")
        ticket(root, "proj-4", key="PROJ-4", url="https://x.test/PROJ-4", status="open")
        due = records.due(load_settings(root), NOW)
        assert [(entry["slug"], entry["reason"]) for entry in due] == [
            ("proj-4", "never synced"), ("proj-2", "synced 97 days ago")]  # recent and closed ones are fine

    def test_recheck_days_and_final_statuses_are_settings(self, root):
        write(root, ".wiki-cli.toml", CONFIG + '[records]\nrecheck_days = 3\nfinal = ["shipped"]\n')
        ticket(root, "proj-1", key="PROJ-1", url="https://x.test/1", status="open", synced="2026-10-01")
        ticket(root, "proj-3", key="PROJ-3", url="https://x.test/3", status="Done", synced="2026-01-01")
        assert {entry["slug"] for entry in records.due(load_settings(root), NOW)} == {"proj-1", "proj-3"}

    def test_stale_lists_records_without_a_code_repo(self, root, capsys):
        ticket(root, "proj-4", key="PROJ-4", url="https://x.test/PROJ-4", status="open")
        capsys.readouterr()
        assert main(["stale", "--root", str(root), "--format", "json"]) == 0
        assert [entry["slug"] for entry in json.loads(capsys.readouterr().out)["records"]] == ["proj-4"]
        assert main(["stale", "--root", str(root)]) == 0
        assert "Records to recheck (1)" in capsys.readouterr().out

    def test_stale_without_records_or_code_still_says_why(self, tmp_path, capsys):
        write(tmp_path, ".wiki-cli.toml", "")
        assert main(["stale", "--root", str(tmp_path)]) == 2

    def test_an_ingested_record_names_its_url_not_a_raw_file(self, root):
        ticket(root, "proj-1", key="PROJ-1", url="https://x.test/PROJ-1", synced="2026-10-01")
        issues, _ = ingested(load_settings(root), "proj-1")
        assert not [issue for issue in issues if issue.severity == "error"]
        ticket(root, "proj-1", key="PROJ-1", synced="2026-10-01")
        issues, _ = ingested(load_settings(root), "proj-1")
        assert "no-original" in {issue.code for issue in issues}

    def test_record_must_be_a_boolean(self, tmp_path):
        write(tmp_path, ".wiki-cli.toml", '[types.ticket]\nrecord = "yes"\n')
        with pytest.raises(ConfigError, match="record must be true or false"):
            load_settings(tmp_path)
        write(tmp_path, ".wiki-cli.toml", "[records]\nrecheck_days = 0\n")
        with pytest.raises(ConfigError, match="recheck_days"):
            load_settings(tmp_path)

    def test_guides_speak_of_records_only_where_there_are_some(self, root, tmp_path):
        assert "A source can also be a **record**" in guide.render("ingest", load_settings(root))
        write(tmp_path / "plain", ".wiki-cli.toml", "")
        plain = guide.render("ingest", load_settings(tmp_path / "plain"))
        assert "**record**" not in plain and "synced:" not in plain and "**A record**" not in plain


class TestParts:
    def test_a_wiki_part_replaces_the_default_at_its_step(self, root):
        write(root, "guides/parts/fetch-record.md", "Use the Jira tool.\nThen read the comments.\n")
        text = guide.render("ingest", load_settings(root))
        assert "   - **A record** (a ticket key or link): fetch it.\n     Use the Jira tool.\n" \
               "     Then read the comments.\n" in text
        assert "Use whatever access you have" not in text

    def test_empty_parts_leave_no_trace(self, root):
        text = guide.render("ingest", load_settings(root))
        assert "{{" not in text and "\n\n\n" not in text
        write(root, "guides/parts/before-commit.md", "Run the formatter.\n")
        assert "one commit per source.\n    Run the formatter.\n    Add each" in guide.render("ingest", load_settings(root))

    def test_parts_listing_flags_a_misspelled_part(self, root, capsys):
        write(root, "guides/parts/fetch-recrod.md", "typo\n")
        write(root, "guides/parts/ingest-extra.md", "## Also\nTell the team.\n")
        found = {part["name"]: part for part in guide.parts(load_settings(root))}
        assert found["fetch-record"]["source"] == "default" and found["ingest-extra"]["source"] == "wiki"
        assert found["fetch-recrod"]["source"] == "unused" and found["before-commit"]["source"] == "empty"
        assert found["fetch-record"]["guides"] == ["ingest", "lint"]
        capsys.readouterr()
        assert main(["guide", "--parts", "--root", str(root)]) == 0
        assert "no guide uses it: guides/parts/fetch-recrod.md" in capsys.readouterr().out

    def test_parts_are_not_pages(self, root, capsys):
        write(root, ".wiki-cli.toml", CONFIG.replace('pages = ["wiki/**/*.md"]', 'pages = ["**/*.md"]'))
        write(root, "guides/parts/before-commit.md", "Run the formatter.\n")
        capsys.readouterr()
        assert main(["list", "--root", str(root), "--format", "json"]) == 0
        assert "before-commit" not in {page["slug"] for page in json.loads(capsys.readouterr().out)["pages"]}

    def test_the_parts_folder_is_a_setting(self, root):
        write(root, ".wiki-cli.toml", CONFIG + '[guides]\nparts = "agent/steps"\n')
        write(root, "agent/steps/fetch-record.md", "Ask in #support.\n")
        assert "Ask in #support." in guide.render("ingest", load_settings(root))
