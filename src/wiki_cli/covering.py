"""`wiki covering <path>`: the wiki's notes on one code file, for an agent about to work on it.

The pages whose `covers:` match the file, most specific first, and the gotchas and
decisions that affect those pages. Run by an agent's hook on every file it touches, so it
reads only the cache (no page is parsed) and asks git only whether the file changed since
each page was checked.

Paths are matched without regard to case: agents name the same file `c:\\Code\\a.py` and
`C:\\code\\A.py` on Windows.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from wiki_cli import codebase, sessions
from wiki_cli.cache import Cache
from wiki_cli.config import Settings

# Page types whose pages are notes on the pages they affect (the code preset's `## Affects`).
NOTE_TYPES = ("gotcha", "decision")
# Decisions in these states no longer apply.
CLOSED_STATUSES = frozenset({"superseded", "rejected", "deprecated", "withdrawn"})


@dataclass
class Covering:
    path: str  # the file, relative to the code repo, or as given when it is outside it
    pages: list[dict] = field(default_factory=list)
    notes: list[dict] = field(default_factory=list)
    shown_before: bool = False  # already shown in this session: the notes are left out
    outside: str | None = None  # why the file has no notes to look up (outside the code repo, a wiki page)

    def to_dict(self) -> dict:
        return {key: value for key, value in self.__dict__.items() if value not in (None, False)}


def relative_path(given: str, code_repo: Path, wiki_root: Path, cwd: Path) -> tuple[str, str | None]:
    """``(path relative to the code repo, None)``, or ``(given, why it has no notes)``."""
    path = Path(given)
    if not path.is_absolute():
        path = cwd / path
    path = path.resolve() if path.exists() else Path(os.path.normpath(path))  # resolve: the real casing
    for folder, why in ((wiki_root, "a wiki page"), (code_repo, None)):
        rel = _inside(path, folder)
        if rel is not None:
            return (given, why) if why else (rel, None)
    return given, "outside the code repo"


def _inside(path: Path, folder: Path) -> str | None:
    """``path`` relative to ``folder`` as a forward-slash path, comparing without regard to
    case, or None when it is not inside."""
    full, top = str(path), os.path.normpath(folder).rstrip("\\/") + os.sep
    if not full.lower().startswith(top.lower()):
        return None
    return PurePosixPath(*Path(full[len(top):]).parts).as_posix()


def matches(rel: str, pattern: str) -> bool:
    pattern = pattern.strip().lstrip("/")
    return bool(pattern) and PurePosixPath(rel.lower()).full_match(pattern.lower())


def lookup(settings: Settings, given: list[str], *, cwd: Path, session: str | None = None) -> list[Covering]:
    code_repo = settings.code_repo
    if code_repo is None:
        raise codebase.CodeRepoError("this wiki names no code repo, so no page covers code; "
                                     "add [code] repo to .wiki-cli.toml")
    seen = set(sessions.load(settings.root, session).get("shown", [])) if session else set()
    results = []
    with Cache(settings) as cache:
        cache.refresh()
        covers = cache.values("covers")
        verified = {slug: value for slug, _, value in cache.values("verified")}
        status = {slug: value.lower() for slug, _, value in cache.values("status")}
        info = {slug: (page_type, summary, path) for slug, page_type, summary, path in cache.conn.execute(
            "SELECT slug, page_type, summary, path FROM pages WHERE kind = 'page'")}
        for item in given:
            rel, outside = relative_path(item, code_repo, settings.root, cwd)
            result = Covering(rel, outside=outside)
            results.append(result)
            if outside:
                continue
            if rel.lower() in seen:
                result.shown_before = True
                continue
            seen.add(rel.lower())
            found = _covering_pages(rel, covers)
            result.pages = [_entry(slug, info, status) | {"matched": pattern} for slug, pattern in found
                            if slug in info and info[slug][0] not in NOTE_TYPES]
            notes = {slug: [] for slug, _ in found if slug in info and info[slug][0] in NOTE_TYPES}
            for page in result.pages:
                for (source,) in cache.conn.execute(
                        "SELECT DISTINCT source_slug FROM relations WHERE target_slug = ? AND resolved = 1",
                        (page["slug"],)):
                    if source in info and info[source][0] in NOTE_TYPES:
                        notes.setdefault(source, []).append(page["slug"])
            result.notes = [_entry(slug, info, status) | ({"via": via} if via else {})
                            for slug, via in sorted(notes.items(), key=lambda pair: (info[pair[0]][0], pair[0]))
                            if status.get(slug) not in CLOSED_STATUSES]
            _mark_changed(code_repo, rel, result.pages, verified)
    if session:
        data = sessions.load(settings.root, session)
        data["shown"] = sorted(seen)
        try:
            sessions.save(settings.root, session, data)
        except OSError:
            pass
    return results


def _covering_pages(rel: str, covers: list[tuple[str, str, str]]) -> list[tuple[str, str]]:
    """``(slug, the pattern that matched)``, most specific first: a page naming the file
    itself, then the longest pattern."""
    best: dict[str, str] = {}
    for slug, _, pattern in covers:
        if matches(rel, pattern) and len(pattern) > len(best.get(slug, "")):
            best[slug] = pattern
    return sorted(best.items(), key=lambda pair: (_has_wildcard(pair[1]), -len(pair[1].replace("*", "")), pair[0]))


def _has_wildcard(pattern: str) -> bool:
    return any(char in pattern for char in "*?[")


def _entry(slug: str, info: dict, status: dict) -> dict:
    page_type, summary, path = info[slug]
    entry = {"slug": slug, "type": page_type, "summary": summary, "path": path}
    if slug in status:
        entry["status"] = status[slug]
    return entry


def _mark_changed(code_repo: Path, rel: str, pages: list[dict], verified: dict[str, str]) -> None:
    """Flag pages checked against a commit the file has changed since, or that have
    uncommitted changes to the file. One git call per distinct commit; git errors are ignored."""
    commits = {verified[page["slug"]] for page in pages if page["slug"] in verified}
    if not commits:
        return
    spec = [f":(literal,icase){rel}"]
    try:
        dirty = bool(codebase.git(code_repo, "status", "--porcelain", "--", *spec).strip())
    except codebase.CodeRepoError:
        return
    changed_since = {}
    for commit in commits:
        try:
            changed_since[commit] = dirty or bool(
                codebase.git(code_repo, "diff", "--name-only", f"{commit}..HEAD", "--", *spec).strip())
        except codebase.CodeRepoError:
            changed_since[commit] = False
    for page in pages:
        if changed_since.get(verified.get(page["slug"], "")):
            page["changed"] = True


def text(results: list[Covering], wiki_root: Path, cwd: Path, *, quiet: bool = False) -> str:
    """The notes as plain text, for a person or an agent, with page paths relative to
    ``cwd``. ``quiet`` leaves out the files with nothing to show (what a hook wants)."""
    blocks = []
    for result in results:
        if result.outside or result.shown_before or not (result.pages or result.notes):
            if not quiet:
                why = (result.outside or ("shown earlier in this session" if result.shown_before
                                          else "no wiki page covers it"))
                blocks.append(f"{result.path}: {why}.")
            continue
        lines = [f"Wiki notes on {result.path} (read the pages before changing it):"]
        for entry in result.pages + result.notes:
            label = entry["type"] or "page"
            if entry.get("status") and entry["type"] == "decision":
                label += f", {entry['status']}"
            if entry.get("changed"):
                label += ", the file changed since this page was checked"
            lines.append(f"- {entry['slug']} ({label}): {display_path(wiki_root / entry['path'], cwd)}")
            if entry.get("summary"):
                lines.append(f"  {entry['summary']}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def display_path(path: Path, cwd: Path) -> str:
    try:
        return Path(os.path.relpath(path, cwd)).as_posix()
    except ValueError:  # another drive on Windows
        return path.as_posix()
