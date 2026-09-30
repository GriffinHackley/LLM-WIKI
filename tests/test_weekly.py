"""`wiki weekly`: notes per week from the git history, and the timeline."""

import datetime as dt
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from wiki_cli.cli import main
from wiki_cli.config import ConfigError, load_settings
from wiki_cli.pages import scan
from wiki_cli.weekly import END, START, Changes, Week, WeeklyError, bar, date_range, read_note, run

pytestmark = pytest.mark.skipif(not shutil.which("git"), reason="needs git")

TODAY = dt.date(2026, 9, 30)  # a Wednesday in 2026-W40
CONFIG = """pages = ["wiki/**/*.md"]

[types.module]
folder = "wiki/modules"
[types.file]
folder = "wiki/files"

[[relations]]
heading = "Part of"
page_type = "file"
type = "part-of"
inverse = "has-part"

[weekly]
group_by = "module"
"""


class Repo:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.git("init", "-q")

    def git(self, *args: str, when: str | None = None) -> str:
        env = dict(os.environ)
        if when:
            env.update(GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
        done = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=self.root,
                              capture_output=True, text=True, check=True, env=env)
        return done.stdout

    def write(self, rel: str, text: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))

    def commit(self, when: str, message: str = "change") -> None:
        self.git("add", "-A")
        self.git("commit", "-qm", message, when=when)


def page(title: str, page_type: str, summary: str, extra: str = "") -> str:
    return f"---\ntitle: {title}\ntype: {page_type}\n---\n# {title}\n\n## Summary\n{summary}\n{extra}"


@pytest.fixture
def repo(tmp_path) -> Repo:
    wiki = Repo(tmp_path / "wiki")
    wiki.write(".wiki-cli.toml", CONFIG)
    wiki.write("raw/.gitkeep", "")
    wiki.commit("2026-08-31T09:00:00+02:00", "scaffold")  # W36
    wiki.write("wiki/modules/search.md", page("Search", "module", "Finds pages."))
    wiki.write("wiki/modules/nav.md", page("Nav", "module", "Walks the graph."))
    wiki.commit("2026-09-01T10:00:00+02:00")
    wiki.write("wiki/files/index-py.md", page("index.py", "file", "The index.",
                                              "\n## Part of\n- [[search]] - its index\n"))
    wiki.write("wiki/open-questions.md", page("Open questions", "meta", "Gaps.", "\n## Open\n- Does nav loop?\n"))
    wiki.commit("2026-09-03T23:30:00-07:00")  # Thursday where the author is: still W36
    # W37: nothing. W38:
    wiki.write("wiki/modules/search.md", page("Search", "module", "Finds pages, faster."))
    wiki.commit("2026-09-14T10:00:00+02:00")
    wiki.git("mv", "wiki/modules/nav.md", "wiki/modules/navigation.md")
    wiki.commit("2026-09-16T10:00:00+02:00")
    wiki.write("raw/rfc-1.txt", "An RFC.\n")
    wiki.write("wiki/open-questions.md", page("Open questions", "meta", "Gaps.", "\n## Open\n- Is search fast?\n"))
    wiki.commit("2026-09-17T10:00:00+02:00")
    return wiki


def note(repo: Repo, week: str) -> str:
    return (repo.root / "weekly" / f"{week}.md").read_text(encoding="utf-8")


class TestWeeks:
    def test_dates(self):
        assert Week.of(TODAY).name == "2026-W40"
        assert (Week.parse("2026-W40").start, Week.parse("2026-W40").end) == (dt.date(2026, 9, 28), dt.date(2026, 10, 4))
        assert Week.parse("2026-W40").dates == "28 Sep - 4 Oct 2026"
        assert Week.parse("2026-W24").dates == "8 - 14 Jun 2026"
        assert date_range(dt.date(2025, 12, 29), dt.date(2026, 1, 4)) == "29 Dec 2025 - 4 Jan 2026"
        assert Week.parse("2026-W53").next() == Week(2027, 1)  # 2026 has 53 ISO weeks

    @pytest.mark.parametrize("text", ["2026-40", "2025-W53", "last week"])
    def test_bad_weeks(self, text):
        with pytest.raises(WeeklyError, match="is not a week"):
            Week.parse(text)

    def test_bar(self):
        assert bar(0, 5) == "" and bar(5, 5) == "█" * 20 and bar(1, 1000) == "▏"


class TestChanges:
    def test_net_effect_within_a_week(self):
        changes = Changes()
        changes.page("A", "a.md")
        changes.page("M", "a.md")  # still just added
        changes.page("A", "b.md")
        changes.page("D", "b.md")  # added and gone: nothing
        changes.page("M", "c.md")
        changes.page("R", "c.md", "c2.md")
        changes.page("R", "c2.md", "c3.md")  # one rename, from the first name to the last
        changes.page("D", "d.md")
        assert list(changes.added) == ["a.md"] and changes.renamed == {"c.md": "c3.md"}
        assert list(changes.updated) == ["c3.md"] and list(changes.deleted) == ["d.md"]


class TestNotes:
    def test_backfills_every_week_with_dates_and_links(self, repo):
        result = run(load_settings(repo.root), today=TODAY)
        assert result["written"] == ["weekly/2026-W36.md", "weekly/2026-W37.md", "weekly/2026-W38.md",
                                     "weekly/2026-W39.md", "weekly/timeline.md"]
        first = note(repo, "2026-W36")
        data = read_note(repo.root / "weekly/2026-W36.md")
        assert (data["week"], data["start"], data["end"]) == ("2026-W36", dt.date(2026, 8, 31), dt.date(2026, 9, 6))
        assert "\n# 31 Aug - 6 Sep 2026\n" in first and "Week 36" not in first
        assert "Previous:" not in first and "Next: [[2026-W37|7 - 13 Sep 2026]]" in first
        assert "Previous: [[2026-W37|7 - 13 Sep 2026]] | Next: [[2026-W39|21 - 27 Sep 2026]]" in note(repo, "2026-W38")
        assert "No changes this week." in note(repo, "2026-W37")

    def test_what_a_week_did(self, repo):
        run(load_settings(repo.root), today=TODAY)
        first = note(repo, "2026-W36")
        assert read_note(repo.root / "weekly/2026-W36.md")["commits"] == 3  # the late-evening commit counts here
        assert "- [[index-py]] (file): The index." in first and "- [[nav]] (module): Walks the graph." in first
        assert "Most work in [[search]] (2 pages)." in first  # search itself, and the file part of it
        assert "- Does nav loop?" in first and "`raw/.gitkeep`" not in first
        later = note(repo, "2026-W38")
        assert "- module: [[search]]" in later and "- nav -> [[navigation]]" in later
        assert "Added to raw/: `raw/rfc-1.txt`" in later and "(`wiki pending`): 1" in later
        assert "### Added\n- Is search fast?" in later and "### Resolved\n- Does nav loop?" in later
        assert "| Orphan pages |" in later and "Thu 17 Sep" in later

    def test_reruns_change_nothing_and_keep_highlights(self, repo):
        settings = load_settings(repo.root)
        run(settings, today=TODAY)
        path = repo.root / "weekly/2026-W38.md"
        path.write_text(path.read_text(encoding="utf-8") + "We made search faster.\n", encoding="utf-8")
        assert run(settings, today=TODAY)["written"] == []
        text = path.read_text(encoding="utf-8").replace("Finds pages, faster.", "stale")
        body = text.split(START)[0] + START + "\nold block\n" + END + text.split(END)[1]
        path.write_text(body, encoding="utf-8")
        assert run(settings, week="2026-W38", today=TODAY)["written"] == ["weekly/2026-W38.md"]
        text = path.read_text(encoding="utf-8")
        assert "old block" not in text and text.endswith("We made search faster.\n")

    def test_notes_stay_out_of_the_wiki(self, repo):
        settings = load_settings(repo.root)
        run(settings, today=TODAY)
        assert not [page_file for page_file, _ in scan(settings) if page_file.rel.startswith("weekly/")]
        repo.commit("2026-09-29T10:00:00+02:00", "notes")  # a commit of notes alone is not work
        (repo.root / "weekly/2026-W40.md").unlink(missing_ok=True)
        assert run(settings, today=dt.date(2026, 10, 6))["written"][0] == "weekly/2026-W40.md"
        assert "No changes this week." in note(repo, "2026-W40")

    def test_current_week_and_refusals(self, repo):
        settings = load_settings(repo.root)
        preview = run(settings, week="current", today=TODAY)
        assert preview["written"] == [] and "# 28 Sep - 4 Oct 2026" in preview["preview"]
        assert not (repo.root / "weekly").exists()
        with pytest.raises(WeeklyError, match="has not ended yet"):
            run(settings, week="2026-W40", today=TODAY)
        (repo.root / ".wiki-cli.toml").write_text('pages = ["wiki/**/*.md"]\n', encoding="utf-8")
        with pytest.raises(WeeklyError, match=r"add a \[weekly\] table"):
            run(load_settings(repo.root), today=TODAY)

    def test_timeline(self, repo):
        run(load_settings(repo.root), today=TODAY)
        text = (repo.root / "weekly/timeline.md").read_text(encoding="utf-8")
        rows = [line for line in text.splitlines() if line.startswith("| [[")]
        assert [row.split("\\|")[0] for row in rows] == ["| [[2026-W39", "| [[2026-W38", "| [[2026-W37", "| [[2026-W36"]
        # W38 touched 3 pages (search, open-questions, the rename) against W36's 4
        assert rows[1] == "| [[2026-W38\\|14 - 20 Sep 2026]] | 3 | 0 | 2 | " + "█" * 15 + " 3 |"
        assert "\n          search  nav  navigation\n" in text  # busiest first, ties by name


class TestCode:
    def test_code_section(self, tmp_path):
        code = Repo(tmp_path / "app")
        code.write("src/search/index.py", "a\n")
        code.write("src/other/x.py", "b\n")
        code.commit("2026-09-01T10:00:00+02:00")
        wiki = Repo(tmp_path / "wiki")
        wiki.write(".wiki-cli.toml", CONFIG + '\n[code]\nrepo = "../app"\n')
        verified = code.git("rev-parse", "HEAD").strip()
        wiki.write("wiki/modules/search.md", page("Search", "module", "Finds pages.").replace(
            "type: module\n", f'type: module\ncovers: ["src/search/**"]\nverified: "{verified}"\n'))
        wiki.commit("2026-09-01T11:00:00+02:00")
        code.write("src/search/index.py", "a2\n")
        code.write("src/other/x.py", "b2\n")
        code.commit("2026-09-02T10:00:00+02:00")
        text = (run(load_settings(wiki.root), week="current", today=dt.date(2026, 9, 3)))["preview"]
        assert "## Code\n2 code commits, 2 files changed." in text
        assert "Verified against the code this week: [[search]]" in text
        assert "Changed code no page covers: `src/other` (1)" in text


class TestConfigAndCli:
    @pytest.mark.parametrize("entry, message", [
        ('sections = ["summary", "gossip"]', "no section gossip"),
        ('group_by = "folder"', "group_by must be"),
        ("colour = 1", "unknown key"),
    ])
    def test_config_errors(self, tmp_path, entry, message):
        (tmp_path / ".wiki-cli.toml").write_text(f"[weekly]\n{entry}\n", encoding="utf-8")
        with pytest.raises(ConfigError, match=message):
            load_settings(tmp_path)

    def test_hook_does_nothing_without_weekly(self, tmp_path, capsys):
        wiki = Repo(tmp_path / "wiki")
        wiki.write(".wiki-cli.toml", 'pages = ["wiki/**/*.md"]\n')
        wiki.commit("2026-09-01T10:00:00+02:00")
        assert main(["weekly", "--hook", "--root", str(wiki.root)]) == 0
        assert not (wiki.root / "weekly").exists() and capsys.readouterr().out == ""

    def test_cli_writes_and_reports(self, repo, capsys):
        capsys.readouterr()
        assert main(["weekly", "--root", str(repo.root), "--format", "json"]) == 0
        result = json.loads(capsys.readouterr().out)
        assert "weekly/timeline.md" in result["written"] and result["timeline"] == "weekly/timeline.md"
