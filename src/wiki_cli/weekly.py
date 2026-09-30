"""`wiki weekly`: a note for each week of work on the wiki, and a timeline across the weeks.

Everything a note shows comes from the wiki's git history and from the wiki as it stood
at the week's last commit, so a note can be written any time after its week ends and
comes out the same. A commit belongs to the ISO week (Monday to Sunday) of its author
date, in the author's own time zone. Notes live in their own folder, outside the wiki's
pages: never indexed, never in the relations graph.

The command writes only notes and the timeline. In a note it owns the frontmatter keys
it sets and the block between the markers; everything else in the note is the reader's
and survives a rerun.
"""

from __future__ import annotations

import datetime as dt
import io
import math
import re
import subprocess
import tarfile
import tempfile
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import yaml

from wiki_cli import codebase, frontmatter, sources
from wiki_cli.cache import Cache
from wiki_cli.config import Settings
from wiki_cli.model import ERROR
from wiki_cli.pages import Resolver, load, matches, scan_vault
from wiki_cli.validation import check_corpus
from wiki_cli.vocabulary import LINKS_TO, REFERS_TO_CODE

START, END = "<!-- wiki:weekly start -->", "<!-- wiki:weekly end -->"
TIMELINE = "timeline.md"
BAR_WIDTH = 20
MAX_LISTED = 15
MAX_SUMMARY = 120
HEATMAP_GROUPS = 8
NO_MODULE, UNTYPED = "(no module)", "(untyped)"
_WEEK = re.compile(r"^(\d{4})-W(\d{1,2})$")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_EIGHTHS = " ▏▎▍▌▋▊▉"
_SHADES = "·░▒▓█"  # none, then quarters of the busiest cell
HEALTH = (("errors", "Check errors"), ("warnings", "Check warnings"), ("unwritten", "Unwritten links"),
          ("orphans", "Orphan pages"))
DEFAULT_TEMPLATE = f"""---
week: {{{{week}}}}
start: {{{{start}}}}
end: {{{{end}}}}
---

# {{{{dates}}}}

{START}
{END}

## Highlights
What mattered this week, in a few lines: decisions made, problems found, what is next.
"""


class WeeklyError(Exception):
    pass


# -- weeks -------------------------------------------------------------------

@dataclass(frozen=True, order=True)
class Week:
    year: int
    number: int

    @classmethod
    def of(cls, day: dt.date) -> Week:
        iso = day.isocalendar()
        return cls(iso.year, iso.week)

    @classmethod
    def parse(cls, text: str) -> Week:
        match = _WEEK.match(text.strip())
        if match:
            try:
                dt.date.fromisocalendar(int(match.group(1)), int(match.group(2)), 1)  # a week the year has
                return cls(int(match.group(1)), int(match.group(2)))
            except ValueError:
                pass
        raise WeeklyError(f"'{text}' is not a week; write it as 2026-W40, or 'current'")

    @property
    def start(self) -> dt.date:
        return dt.date.fromisocalendar(self.year, self.number, 1)

    @property
    def end(self) -> dt.date:
        return self.start + dt.timedelta(days=6)

    @property
    def name(self) -> str:
        return f"{self.year}-W{self.number:02d}"

    @property
    def dates(self) -> str:
        return date_range(self.start, self.end)

    def next(self) -> Week:
        return Week.of(self.start + dt.timedelta(days=7))

    def previous(self) -> Week:
        return Week.of(self.start - dt.timedelta(days=7))


def date_range(start: dt.date, end: dt.date) -> str:
    """`28 Sep - 4 Oct 2026`; `1 - 7 Jun 2026`; `29 Dec 2025 - 4 Jan 2026`."""
    if start.year != end.year:
        return f"{_day(start)} {start.year} - {_day(end)} {end.year}"
    if start.month != end.month:
        return f"{_day(start)} - {_day(end)} {end.year}"
    return f"{start.day} - {_day(end)} {end.year}"


def _day(day: dt.date) -> str:
    return f"{day.day} {_MONTHS[day.month - 1]}"


# -- history -----------------------------------------------------------------

@dataclass
class Commit:
    sha: str
    day: dt.date  # the author date, in the author's own time zone
    changes: list[tuple[str, str, str | None]]  # (status letter, path, new path of a rename or copy)


def _git(root: Path, *args: str, binary: bool = False):
    try:
        done = subprocess.run(["git", "-C", str(root), "-c", "core.quotePath=false", *args], capture_output=True)
    except OSError as exc:
        raise WeeklyError(f"cannot run git: {exc}") from exc
    if done.returncode != 0:
        raise WeeklyError(done.stderr.decode("utf-8", "replace").strip() or f"git {' '.join(args)} failed")
    return done.stdout if binary else done.stdout.decode("utf-8", "replace")


def history(root: Path) -> list[Commit]:
    """Every commit touching the wiki's folder, oldest first, with what it changed."""
    try:
        _git(root, "rev-parse", "--verify", "-q", "HEAD")
    except WeeklyError:
        return []  # no commits yet
    out = _git(root, "log", "--reverse", "-M", "--name-status", "--relative", "--format=%x00%H %aI", "--", ".")
    commits = []
    for block in out.split("\x00")[1:]:
        lines = block.strip("\n").splitlines()
        sha, when = lines[0].split(" ", 1)
        changes = []
        for line in lines[1:]:
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            status = parts[0][:1]
            changes.append((status, parts[1], parts[2] if status in "RC" and len(parts) > 2 else None))
        commits.append(Commit(sha, dt.datetime.fromisoformat(when).date(), changes))
    return commits


class _Kinds:
    """What a path in the wiki is: a page, a source in raw/, a weekly note, or other."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.weekly = f"{settings.weekly.folder}/"
        self.raw_dirs = [f"{folder}/" for folder in sources.raw_dirs(settings)]

    def __call__(self, rel: str) -> str:
        if rel.startswith(self.weekly):
            return "weekly"
        if matches(rel, self.settings.exclude):
            return "other"
        if rel.lower().endswith(".md") and matches(rel, self.settings.pages):
            return "page"
        if matches(rel, self.settings.raw) or rel.startswith(tuple(self.raw_dirs)):
            return "raw"
        return "other"


@dataclass
class Changes:
    """One week's commits and their net effect on pages and sources."""
    commits: list[Commit] = field(default_factory=list)
    added: dict[str, None] = field(default_factory=dict)  # dicts keep the order things happened
    updated: dict[str, None] = field(default_factory=dict)
    deleted: dict[str, None] = field(default_factory=dict)
    renamed: dict[str, str] = field(default_factory=dict)  # original path -> current path
    raw_added: dict[str, None] = field(default_factory=dict)

    def page(self, status: str, path: str, new: str | None = None) -> None:
        if status == "C":
            status, path = "A", new
        if status == "A":
            if path in self.deleted:
                del self.deleted[path]
                self.updated[path] = None
            else:
                self.added[path] = None
        elif status in "MT":
            if path not in self.added:
                self.updated[path] = None
        elif status == "D":
            if path in self.added:
                del self.added[path]
                return
            self.updated.pop(path, None)
            original = self._original(path)
            if original is not None:
                del self.renamed[original]
                path = original
            self.deleted[path] = None
        elif status == "R":
            if path in self.added:
                del self.added[path]
                self.added[new] = None
                return
            original = self._original(path)
            if original is not None:
                del self.renamed[original]
            else:
                original = path
            if original != new:
                self.renamed[original] = new
            if path in self.updated:
                del self.updated[path]
                self.updated[new] = None

    def _original(self, path: str) -> str | None:
        return next((old for old, new in self.renamed.items() if new == path), None)

    @property
    def touched(self) -> list[str]:
        """Pages as they are at the week's end that the week added, changed or renamed."""
        return list(dict.fromkeys([*self.added, *self.updated, *self.renamed.values()]))


def by_week(commits: list[Commit], kind) -> tuple[dict[Week, Changes], dict[Week, int]]:
    """Each week's changes, and the index in ``commits`` of its first commit."""
    weeks: dict[Week, Changes] = {}
    first: dict[Week, int] = {}
    for index, commit in enumerate(commits):
        if all(kind(path) == "weekly" and (new is None or kind(new) == "weekly")
               for _, path, new in commit.changes):
            continue  # only weekly notes: not work on the wiki
        week = Week.of(commit.day)
        changes = weeks.setdefault(week, Changes())
        first.setdefault(week, index)
        changes.commits.append(commit)
        for status, path, new in commit.changes:
            old_kind, new_kind = kind(path), kind(new) if new else None
            if status == "R" and old_kind != new_kind:  # moved into or out of the pages
                if old_kind == "page":
                    changes.page("D", path)
                if new_kind == "page":
                    changes.page("A", new)
                if new_kind == "raw":
                    changes.raw_added[new] = None
                continue
            if (new_kind or old_kind) == "page":
                changes.page(status, path, new)
            elif (new_kind or old_kind) == "raw":
                target = new or path
                if PurePosixPath(target).name.startswith("."):
                    continue  # raw/.gitkeep and the like are not sources
                if status in "AC":
                    changes.raw_added[target] = None
                elif status == "D":
                    changes.raw_added.pop(path, None)
                elif status == "R" and path in changes.raw_added:
                    del changes.raw_added[path]
                    changes.raw_added[target] = None
    return weeks, first


# -- the wiki at one commit ----------------------------------------------------

class Snapshot:
    """The wiki as it stood at one commit, indexed in a temporary folder (no embeddings)."""

    def __init__(self, settings: Settings, root: Path, prefix: str, sha: str):
        self._folder = tempfile.TemporaryDirectory(prefix="wiki-weekly-", ignore_cleanup_errors=True)
        tree = Path(self._folder.name)
        data = _git(root, "archive", "--format=tar", f"{sha}:{prefix}" if prefix else sha, binary=True)
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            archive.extractall(tree, filter="data")
        self.settings = replace(settings, root=tree, cache_path=tree / ".cache" / "wiki.sqlite3", root_note=None)
        self.cache = Cache(self.settings)
        self.cache.refresh()

    def close(self) -> None:
        self.cache.close()
        self._folder.cleanup()

    def __enter__(self) -> Snapshot:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def info(self, rel: str) -> dict | None:
        row = self.cache.conn.execute("SELECT slug, page_type, summary FROM pages WHERE path = ?", (rel,)).fetchone()
        return {"slug": row[0], "type": row[1], "summary": row[2]} if row else None

    def data(self, rel: str) -> dict:
        try:
            return frontmatter.parse((self.settings.root / rel).read_text(encoding="utf-8")).data or {}
        except (OSError, frontmatter.FrontmatterError):
            return {}

    def groups(self, rel: str, group_by: str) -> list[str]:
        info = self.info(rel)
        if info is None:
            return []
        if group_by == "type":
            return [info["type"] or UNTYPED]
        if info["type"] == "module":
            return [info["slug"]]
        found, frontier, seen = set(), [info["slug"]], {info["slug"]}
        for _ in range(2):  # a gotcha affects a file, which is part of a module
            reached = []
            for slug in frontier:
                for target, target_type in self.cache.conn.execute(
                        """SELECT DISTINCT r.target_slug, p.page_type FROM relations r
                           JOIN pages p ON p.slug = r.target_slug
                           WHERE r.source_slug = ? AND r.resolved = 1 AND r.relation_type NOT IN (?, ?)""",
                        (slug, LINKS_TO, REFERS_TO_CODE)):
                    if target_type == "module":
                        found.add(target)
                    elif target not in seen:
                        seen.add(target)
                        reached.append(target)
            if found:
                break
            frontier = reached
        return sorted(found) or [NO_MODULE]

    def slug_of(self, name: str) -> str | None:
        row = self.cache.conn.execute("SELECT path FROM pages WHERE slug = ? AND kind = 'page'", (name,)).fetchone()
        return row[0] if row else None

    def health(self) -> dict[str, int]:
        scanned, others = scan_vault(self.settings)
        files = [page_file for page_file, _ in scanned]
        resolver = Resolver([(page_file.slug, page_file.rel) for page_file in files], others)
        issues = check_corpus([load(page_file, self.settings) for page_file in files], resolver)
        errors = sum(1 for issue in issues if issue.severity == ERROR)
        conn = self.cache.conn
        return {
            "errors": errors,
            "warnings": len(issues) - errors,
            "unwritten": conn.execute("SELECT COUNT(DISTINCT target_slug) FROM relations WHERE resolved = 0").fetchone()[0],
            "orphans": conn.execute(
                """SELECT COUNT(*) FROM pages p WHERE p.kind = 'page' AND NOT EXISTS (
                       SELECT 1 FROM relations r WHERE r.target_slug = p.slug AND r.resolved = 1
                       AND r.source_slug != p.slug AND r.relation_type != 'transcribes')""").fetchone()[0],
        }

    def pending(self) -> int:
        return len(sources.pending(self.settings)[0])

    def covering(self) -> list[tuple[str, str, list[str]]]:
        """``(slug, path, covers globs)`` of every page that says what code it covers."""
        found = []
        for slug, rel in self.cache.conn.execute("SELECT slug, path FROM pages WHERE kind = 'page' ORDER BY path"):
            globs = codebase.covers(SimpleNamespace(data=self.data(rel)))
            if globs:
                found.append((slug, rel, globs))
        return found


# -- the note ------------------------------------------------------------------

@dataclass
class Note:
    week: Week
    data: dict  # the frontmatter keys the command owns
    block: str  # the generated block, without the markers


class _Builder:
    """Builds the notes for one run, sharing the history and the code repo's log."""

    def __init__(self, settings: Settings, root: Path):
        self.settings = settings
        self.config = settings.weekly
        self.root = root
        self.prefix = _git(root, "rev-parse", "--show-prefix").strip()
        self.commits = history(root)
        self.kind = _Kinds(settings)
        self.weeks, self.first = by_week(self.commits, self.kind)
        self._code_commits: list[tuple[dt.date, list[str]]] | None = None

    def build(self, week: Week, previous: dict | None) -> Note:
        """``previous``: the latest earlier note that recorded health, for the changes."""
        changes = self.weeks.get(week, Changes())
        data = {"week": week.name, "start": week.start, "end": week.end, "commits": len(changes.commits),
                "added": len(changes.added), "updated": len(changes.updated), "deleted": len(changes.deleted),
                "renamed": len(changes.renamed)}
        lines = [self._links(week), ""]
        if not changes.commits:
            data["work"] = {}
            lines += ["## Summary", "No changes this week."]
            return Note(week, data, "\n".join(lines))
        with Snapshot(self.settings, self.root, self.prefix, changes.commits[-1].sha) as snapshot:
            counts = Counter()
            for rel in changes.touched:
                counts.update(snapshot.groups(rel, self.config.group_by))
            work = dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))  # busiest first
            data["work"] = work
            health = snapshot.health() if "health" in self.config.sections else None
            if health is not None:
                data["health"] = health
            base = self._base(week)
            for section in self.config.sections:
                lines += getattr(self, f"_{section}")(week, changes, snapshot, work, base, health, previous)
        return Note(week, data, "\n".join(lines).rstrip())

    def _base(self, week: Week) -> str | None:
        """The last commit before the week's first one: the wiki as the week found it."""
        index = self.first[week]
        return self.commits[index - 1].sha if index else None

    def _links(self, week: Week) -> str:
        previous, following = week.previous(), week.next()
        parts = []
        if self.first and previous >= min(self.first):
            parts.append(f"Previous: [[{previous.name}|{previous.dates}]]")
        parts.append(f"Next: [[{following.name}|{following.dates}]]")
        return " | ".join(parts)

    # Each section returns its lines, starting with its heading and ending with a blank line.

    def _summary(self, week, changes, snapshot, work, base, health, previous):
        counts = [f"{len(changes.added)} added", f"{len(changes.updated)} updated"]
        counts += [f"{len(value)} {label}" for value, label in ((changes.deleted, "deleted"),
                                                              (changes.renamed, "renamed")) if value]
        commits = len(changes.commits)
        text = f"{commits} commit{'s' if commits != 1 else ''}; pages: {', '.join(counts)}."
        busiest = [name for name in work if name not in (NO_MODULE, UNTYPED)]
        if busiest:
            count = work[busiest[0]]
            text += f" Most work in {self._group(busiest[0])} ({count} page{'s' if count != 1 else ''})."
        return ["## Summary", text, ""]

    def _activity(self, week, changes, snapshot, work, base, health, previous):
        per_day = Counter((commit.day - week.start).days for commit in changes.commits)
        top = max(per_day.values())
        rows = [f"{_DAYS[day]} {_day(week.start + dt.timedelta(days=day)):<6}  "
                f"{bar(per_day[day], top):<{BAR_WIDTH}}  {per_day[day]}".rstrip() for day in range(7)]
        return ["## Activity", "Commits per day.", "", "```text", *rows, "```", ""]

    def _pages(self, week, changes, snapshot, work, base, health, previous):
        lines = ["## Pages"]
        if not (changes.added or changes.updated or changes.deleted or changes.renamed):
            return lines + ["No pages changed.", ""]
        if changes.added:
            lines += ["", "### Added"]
            for rel in list(changes.added)[:MAX_LISTED]:
                info = snapshot.info(rel) or {"slug": _stem(rel), "type": None, "summary": None}
                kind = f" ({info['type']})" if info["type"] else ""
                summary = f": {_short(info['summary'])}" if info["summary"] else ""
                lines.append(f"- [[{info['slug']}]]{kind}{summary}")
            lines += _more(changes.added)
        if changes.updated:
            lines += ["", "### Updated"]
            by_type: dict[str, list[str]] = {}
            for rel in changes.updated:
                info = snapshot.info(rel) or {"slug": _stem(rel), "type": None}
                by_type.setdefault(info["type"] or UNTYPED, []).append(f"[[{info['slug']}]]")
            for page_type, links in sorted(by_type.items()):
                shown = ", ".join(links[:MAX_LISTED]) + (f" and {len(links) - MAX_LISTED} more"
                                                          if len(links) > MAX_LISTED else "")
                lines.append(f"- {page_type}: {shown}")
        if changes.renamed:
            lines += ["", "### Renamed"]
            for old, new in list(changes.renamed.items())[:MAX_LISTED]:
                info = snapshot.info(new) or {"slug": _stem(new)}
                lines.append(f"- {_stem(old)} -> [[{info['slug']}]]")
            lines += _more(changes.renamed)
        if changes.deleted:
            lines += ["", "### Deleted", *[f"- {_stem(rel)}" for rel in list(changes.deleted)[:MAX_LISTED]]]
            lines += _more(changes.deleted)
        return lines + [""]

    def _work(self, week, changes, snapshot, work, base, health, previous):
        lines = ["## Where the work went"]
        if not work:
            return lines + ["No pages changed.", ""]
        top = max(work.values())
        lines += ["", f"| {'Module' if self.config.group_by == 'module' else 'Type'} | Pages | |", "|---|--:|---|"]
        for name, count in work.items():
            lines.append(f"| {self._group(name)} | {count} | {bar(count, top)} |")
        return lines + [""]

    def _sources(self, week, changes, snapshot, work, base, health, previous):
        lines = ["## Sources"]
        added = list(changes.raw_added)
        if added:
            shown = ", ".join(f"`{rel}`" for rel in added[:MAX_LISTED])
            more = f" and {len(added) - MAX_LISTED} more" if len(added) > MAX_LISTED else ""
            lines.append(f"- Added to raw/: {shown}{more}")
        else:
            lines.append("- None added to raw/.")
        lines.append(f"- Not yet ingested at the week's end (`wiki pending`): {snapshot.pending()}")
        return lines + [""]

    def _questions(self, week, changes, snapshot, work, base, health, previous):
        lines = ["## Open questions"]
        rel = snapshot.slug_of("open-questions")
        if rel is None or rel not in changes.touched:
            return lines + ["None added or resolved.", ""]
        before = _bullets(self._show(base, rel)) if base else []
        after = _bullets((snapshot.settings.root / rel).read_text(encoding="utf-8"))
        added = [line for line in after if line not in before]
        resolved = [line for line in before if line not in after]
        if not added and not resolved:
            return lines + ["None added or resolved.", ""]
        for label, items in (("Added", added), ("Resolved", resolved)):
            if items:
                lines += ["", f"### {label}", *[f"- {_short(item)}" for item in items[:MAX_LISTED]], *_more(items)]
        return lines + [""]

    def _health(self, week, changes, snapshot, work, base, health, previous):
        before = (previous or {}).get("health") or {}
        lines = ["## Health", "At the week's end.", "", "| | Count | Change |", "|---|--:|--:|"]
        for key, label in HEALTH:
            change = health[key] - before[key] if key in before else None
            shown = "-" if change is None else (f"{change:+d}" if change else "0")
            lines.append(f"| {label} | {health[key]} | {shown} |")
        return lines + [""]

    def _code(self, week, changes, snapshot, work, base, health, previous):
        repo = self._code_repo()
        if repo is None:
            return []
        files = sorted({path for day, paths in self._code_log(repo) if Week.of(day) == week for path in paths})
        commits = sum(1 for day, _ in self._code_log(repo) if Week.of(day) == week)
        lines = ["## Code", f"{commits} code commit{'s' if commits != 1 else ''}, {len(files)} files changed."]
        if not files:
            return lines + [""]
        areas = Counter(_area(path) for path in files)
        lines.append("Areas: " + ", ".join(f"`{area}` ({count})" for area, count in areas.most_common(MAX_LISTED)))
        touched = set(changes.touched)
        verified = []
        for rel in changes.touched:
            now = codebase.verified(SimpleNamespace(data=snapshot.data(rel)))
            original = next((old for old, new in changes.renamed.items() if new == rel), rel)
            then = _verified_in(self._show(base, original)) if base and rel not in changes.added else None
            if now and now != then:
                verified.append((snapshot.info(rel) or {"slug": _stem(rel)})["slug"])
        if verified:
            lines.append("Verified against the code this week: " + ", ".join(f"[[{slug}]]" for slug in verified))
        behind, uncovered = Counter(), Counter()
        pages = snapshot.covering()
        for path in files:
            covering = [(slug, rel) for slug, rel, globs in pages
                        if any(PurePosixPath(path).full_match(glob.strip().lstrip("/")) for glob in globs)]
            if not covering:
                uncovered[_area(path)] += 1
            elif not any(rel in touched for _, rel in covering):
                behind.update(slug for slug, _ in covering)
        if behind:
            lines.append("Code changed, page not updated: " + ", ".join(
                f"[[{slug}]] ({count} file{'s' if count != 1 else ''})" for slug, count in behind.most_common(MAX_LISTED)))
        if uncovered:
            lines.append("Changed code no page covers: " + ", ".join(
                f"`{area}` ({count})" for area, count in uncovered.most_common(MAX_LISTED)))
        return lines + [""]

    def _code_repo(self) -> Path | None:
        if self.settings.code_repo is None:
            return None
        try:
            return codebase.repo(self.settings)
        except codebase.CodeRepoError:
            return None

    def _code_log(self, repo: Path) -> list[tuple[dt.date, list[str]]]:
        if self._code_commits is None:
            out = _git(repo, "log", "--format=%x00%aI", "--name-only")
            self._code_commits = []
            for block in out.split("\x00")[1:]:
                lines = [line for line in block.strip("\n").splitlines() if line.strip()]
                if lines:
                    self._code_commits.append((dt.datetime.fromisoformat(lines[0]).date(), lines[1:]))
        return self._code_commits

    def _show(self, sha: str | None, rel: str) -> str:
        if sha is None:
            return ""
        try:
            return _git(self.root, "show", f"{sha}:{self.prefix}{rel}")
        except WeeklyError:
            return ""

    def _group(self, name: str) -> str:
        return f"[[{name}]]" if self.config.group_by == "module" and not name.startswith("(") else name


def bar(value: float, top: float, width: int = BAR_WIDTH) -> str:
    """A bar of block characters, ``width`` long at ``top``; any value above 0 shows."""
    if value <= 0 or top <= 0:
        return ""
    eighths = max(1, round(value / top * width * 8))
    full, rest = divmod(eighths, 8)
    return "█" * full + (_EIGHTHS[rest] if rest else "")


def _more(items) -> list[str]:
    """An "and N more" line for a list shown only up to MAX_LISTED items."""
    return [f"- and {len(items) - MAX_LISTED} more"] if len(items) > MAX_LISTED else []


def _stem(rel: str) -> str:
    return PurePosixPath(rel).stem


def _short(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= MAX_SUMMARY else text[:MAX_SUMMARY - 1].rstrip() + "…"


def _area(path: str) -> str:
    folders = PurePosixPath(path).parts[:-1]
    return "/".join(folders[:2]) or "(top level)"


def _bullets(text: str) -> list[str]:
    return [line.strip()[2:].strip() for line in text.splitlines() if line.strip().startswith("- ")]


def _verified_in(text: str) -> str | None:
    try:
        data = frontmatter.parse(text).data or {}
    except frontmatter.FrontmatterError:
        return None
    return codebase.verified(SimpleNamespace(data=data))


# -- files -------------------------------------------------------------------

def _template(settings: Settings) -> str:
    path = settings.root / settings.weekly.template
    return path.read_text(encoding="utf-8") if path.is_file() else DEFAULT_TEMPLATE


def render(note: Note, existing: str | None, template: str) -> str:
    """The note's text: ``existing`` (or the filled template) with the command's frontmatter
    keys and generated block replaced. Everything else is kept as it was."""
    if existing is None:
        text = template
        for key, value in (("week", note.week.name), ("start", note.week.start.isoformat()),
                           ("end", note.week.end.isoformat()), ("dates", note.week.dates)):
            text = text.replace(f"{{{{{key}}}}}", value)
    else:
        text = existing
    try:
        parts = frontmatter.split(text)
    except frontmatter.FrontmatterError:
        parts = None
    data, body = {}, text
    if parts is not None:
        data = yaml.safe_load(parts[0]) or {}
        body = text[parts[1]:]
        if not isinstance(data, dict):
            data = {}
    data.update(note.data)
    block = f"{START}\n{note.block}\n{END}"
    if START in body and END in body.split(START, 1)[1]:
        before, rest = body.split(START, 1)
        body = before + block + rest.split(END, 1)[1]
    else:  # a template without the markers: the block goes under the title
        heading = re.search(r"^# .*$", body, re.MULTILINE)
        at = heading.end() if heading else 0
        body = body[:at] + ("\n\n" if heading else "") + block + "\n" + body[at:]
    dumped = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)
    return f"---\n{dumped}---\n\n{body.lstrip(chr(10))}"


def read_note(path: Path) -> dict | None:
    try:
        return frontmatter.parse(path.read_text(encoding="utf-8")).data
    except (OSError, frontmatter.FrontmatterError):
        return None


def timeline(folder: Path, group_by: str) -> str:
    """The timeline page: every week's numbers, newest first, then a heatmap of where the
    work went. Built only from the notes' frontmatter."""
    notes = []
    for path in folder.glob("*.md"):
        match = _WEEK.match(path.stem)
        data = read_note(path) if match else None
        if data:
            notes.append((Week.parse(path.stem), data))
    notes.sort(key=lambda item: item[0], reverse=True)
    touched = {week: sum(int(data.get(key) or 0) for key in ("added", "updated", "deleted", "renamed"))
               for week, data in notes}
    top = max(touched.values(), default=0)
    lines = ["# Weekly timeline", "",
             "Work on the wiki week by week, newest first. `wiki weekly` rewrites this page; "
             "write notes in the weekly notes instead.", "",
             "| Week | Commits | Added | Updated | Pages touched |", "|---|--:|--:|--:|---|"]
    for week, data in notes:
        pages = f"{bar(touched[week], top)} {touched[week]}".strip()
        lines.append(f"| [[{week.name}\\|{week.dates}]] | {int(data.get('commits') or 0)} | "
                     f"{int(data.get('added') or 0)} | {int(data.get('updated') or 0)} | {pages} |")
    totals = Counter()
    for _, data in notes:
        totals.update({str(name): int(count) for name, count in (data.get("work") or {}).items()})
    groups = [name for name, _ in sorted(totals.items(), key=lambda item: (-item[1], item[0]))
              if not name.startswith("(")][:HEATMAP_GROUPS]
    if groups:
        busiest = max(int((data.get("work") or {}).get(name, 0)) for _, data in notes for name in groups) or 1
        widths = [max(len(name[:12]), 3) for name in groups]
        heading = "Module" if group_by == "module" else "Type"
        lines += ["", "## Where the work went", "",
                  f"Pages touched per {heading.lower()}: {_SHADES[0]} none, then {_SHADES[1]} {_SHADES[2]} "
                  f"{_SHADES[3]} {_SHADES[4]} up to the busiest week.", "", "```text",
                  " " * 10 + "  ".join(name[:12].ljust(width) for name, width in zip(groups, widths))]
        for week, data in notes:
            work = data.get("work") or {}
            cells = []
            for name, width in zip(groups, widths):
                count = int(work.get(name, 0))
                shade = _SHADES[min(4, math.ceil(count / busiest * 4))] if count else _SHADES[0]
                cells.append(shade.ljust(width))
            lines.append(f"{week.name:<10}" + "  ".join(cells).rstrip())
        lines.append("```")
    return "\n".join(lines) + "\n"


# -- the command ---------------------------------------------------------------

def _earlier_health(folder: Path, week: Week) -> dict | None:
    """The latest note before ``week`` that recorded health: what its change is measured
    against, across weeks with no commits."""
    earlier = sorted((Week.parse(path.stem), path) for path in folder.glob("*.md")
                     if _WEEK.match(path.stem) and Week.parse(path.stem) < week) if folder.is_dir() else []
    for _, path in reversed(earlier):
        data = read_note(path)
        if data and data.get("health"):
            return data
    return None


def run(settings: Settings, *, week: str | None = None, today: dt.date | None = None) -> dict:
    """Write the missing notes (or the one ``week`` names) and the timeline. ``week`` =
    "current" renders this week so far without writing anything."""
    if settings.weekly is None:
        raise WeeklyError("weekly notes are off in this wiki: add a [weekly] table to .wiki-cli.toml "
                          "(see docs/config.md)")
    today = today or dt.date.today()
    current = Week.of(today)
    folder = settings.root / settings.weekly.folder
    builder = _Builder(settings, settings.root)
    template = _template(settings)

    if week == "current":
        note = builder.build(current, _earlier_health(folder, current))
        path = folder / f"{current.name}.md"
        return {"written": [], "timeline": None,
                "preview": render(note, path.read_text(encoding="utf-8") if path.is_file() else None, template)}
    if week is not None:
        wanted = Week.parse(week)
        if wanted >= current:
            raise WeeklyError(f"{wanted.name} has not ended yet; `wiki weekly --week current` shows it so far")
        targets = [wanted]
    elif builder.weeks:
        targets, cursor = [], min(builder.weeks)
        while cursor < current:
            if not (folder / f"{cursor.name}.md").is_file():
                targets.append(cursor)
            cursor = cursor.next()
    else:
        targets = []

    written = []
    for target in targets:
        path = folder / f"{target.name}.md"
        note = builder.build(target, _earlier_health(folder, target))
        existing = path.read_text(encoding="utf-8") if path.is_file() else None
        text = render(note, existing, template)
        if text != existing:
            folder.mkdir(parents=True, exist_ok=True)
            path.write_bytes(text.encode("utf-8"))
            written.append(path)
    timeline_path = folder / TIMELINE
    timeline_rel = None
    if folder.is_dir():
        text = timeline(folder, settings.weekly.group_by)
        if not timeline_path.is_file() or timeline_path.read_text(encoding="utf-8") != text:
            timeline_path.write_bytes(text.encode("utf-8"))
            written.append(timeline_path)
        timeline_rel = timeline_path.relative_to(settings.root).as_posix()
    return {"written": [path.relative_to(settings.root).as_posix() for path in written], "timeline": timeline_rel,
            "preview": None}


def stage(settings: Settings, paths: list[str]) -> None:
    if paths:
        _git(settings.root, "add", "--", *paths)
