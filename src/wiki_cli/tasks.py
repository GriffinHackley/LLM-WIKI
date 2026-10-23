"""One task at a time on the code, and the end-of-task commit (`wiki task`, `wiki finish`).

A task is opened by `wiki task start` once the user approved its plan, held by one agent
session, and closed by `wiki finish` (or `wiki task cancel`). While it is open, the agent
hooks refuse edits to the code and the wiki from any other session, and record each file
the holding session edits; with no task open they refuse every such edit. Starting,
taking over and finishing a task each ask the user first, through the hook.

`wiki task start` takes a snapshot: the hash of every file already changed in the code repo
and the wiki. `wiki finish` commits every file changed since then, by the agent's edit
tools, a shell command or by hand, and leaves out (listing them) the changes that were
already there and have not moved: in the code repo first, then in the wiki, after setting
the `verified:` of each page the task checked (`"pending"`) to the new code commit. It
refuses while a page covering a changed file is still unchecked, or a changed page has
errors.

The task lives in the wiki's `.cache/task.json`: like the rest of the cache it is local to
this checkout and never committed.
"""

from __future__ import annotations

import datetime
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from wiki_cli import codebase
from wiki_cli.config import Settings

TASK_FILE = "task.json"
HANDOFF_FILE = "handoff.json"  # the session a hook saw asking to run `wiki task start|takeover`
HANDOFF_SECONDS = 600  # the user may take a while to confirm
PENDING = codebase.PENDING  # a page's `verified:` while the task that checked it is not yet committed
_VERIFIED_LINE = re.compile(r"""^(verified:[ \t]*)(["']?)pending\2(?=[ \t]*\r?$)""", re.MULTILINE | re.IGNORECASE)


class TaskError(Exception):
    pass


@dataclass
class Task:
    title: str
    session: str | None
    started: str
    # per repository ("code", and "wiki" when it is its own), the files already changed at the start and
    # their hashes (None: not known); a repository missing here had no snapshot taken
    before: dict[str, dict[str, str | None]] = field(default_factory=dict)
    edited: list[str] = field(default_factory=list)  # absolute paths the task's sessions edited

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def _cache(settings: Settings) -> Path:
    return settings.root / ".cache"


def load(settings: Settings) -> Task | None:
    try:
        data = json.loads((_cache(settings) / TASK_FILE).read_text(encoding="utf-8"))
        if "changed_before" in data and "before" not in data:  # a task started before snapshots: names only
            data["before"] = {"code": dict.fromkeys(data["changed_before"])}
        return Task(**{key: data[key] for key in Task.__dataclass_fields__ if key in data})
    except (OSError, ValueError, TypeError, KeyError):
        return None


def save(settings: Settings, task: Task) -> None:
    path = _cache(settings) / TASK_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(task.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
    temporary.replace(path)


def close(settings: Settings) -> None:
    (_cache(settings) / TASK_FILE).unlink(missing_ok=True)


def describe(task: Task) -> str:
    return f'"{task.title}" (started {task.started}' + (f", session {task.session})" if task.session else ")")


# -- handing a session id from the hook to the command -------------------------------------

def hand_off(settings: Settings, session: str, command: str) -> None:
    path = _cache(settings) / HANDOFF_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"session": session, "command": command, "time": time.time()}), encoding="utf-8")


def handed_session(settings: Settings, command: str) -> str | None:
    """The session whose agent asked to run ``command``, as its hook recorded it, if recent."""
    path = _cache(settings) / HANDOFF_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if data.get("command") != command or time.time() - float(data.get("time", 0)) > HANDOFF_SECONDS:
        return None
    path.unlink(missing_ok=True)
    return data.get("session") or None


# -- start, take over, cancel --------------------------------------------------------------

def start(settings: Settings, title: str, session: str | None) -> Task:
    if not title.strip():
        raise TaskError("name the task: wiki task start \"<a short title>\"")
    if session is None:
        raise TaskError("no agent session asked for this: an agent runs `wiki task start` (its hook "
                        "names the session), or pass --session <id>")
    current = load(settings)
    if current is not None:
        if current.session == session:
            raise TaskError(f"this session already holds the task {describe(current)}")
        raise TaskError(f"a task is already open: {describe(current)}. Finish it (`wiki finish`) or cancel it "
                        "(`wiki task cancel`) first; to carry it on in this session, `wiki task takeover`")
    task = Task(title=title.strip(), session=session, started=_now(),
                before={key: codebase.file_hashes(repo, codebase.changed_files(repo))
                        for key, repo in _repos(settings).items()})
    save(settings, task)
    return task


def takeover(settings: Settings, session: str | None) -> Task:
    if session is None:
        raise TaskError("no agent session asked for this: an agent runs `wiki task takeover` (its hook "
                        "names the session), or pass --session <id>")
    task = load(settings)
    if task is None:
        raise TaskError("no task is open: start one with `wiki task start \"<title>\"`")
    task.session = session
    save(settings, task)
    return task


def cancel(settings: Settings) -> Task:
    task = load(settings)
    if task is None:
        raise TaskError("no task is open")
    close(settings)
    return task


# -- what the hooks ask ---------------------------------------------------------------------

def edit_refusal(settings: Settings, session: str | None, path: Path) -> str | None:
    """Why an edit to ``path`` by ``session`` is refused, or None when it may go ahead.
    Only files in the code repo and the wiki are guarded."""
    if not _guarded(settings, path):
        return None
    task = load(settings)
    if task is None:
        return ("No task is open, so files in the code repo and the wiki can't be changed yet. Follow "
                "`wiki guide work`: get the plan approved by the user, then run `wiki task start \"<title>\"`.")
    if session is not None and task.session != session:
        return (f"Another session holds the open task {describe(task)}. Only one task runs at a time. If that "
                "session has ended and this one carries the task on, run `wiki task takeover` (the user "
                "confirms it); otherwise finish or cancel that task first.")
    return None


def record_edit(settings: Settings, session: str | None, path: Path) -> None:
    task = load(settings)
    if task is None or (session is not None and task.session != session) or not _guarded(settings, path):
        return
    full = path.as_posix()
    if full.lower() not in {item.lower() for item in task.edited}:
        task.edited.append(full)
        save(settings, task)


def _guarded(settings: Settings, path: Path) -> bool:
    return any(_inside(path, folder) is not None
               for folder in (settings.root, settings.code_repo) if folder is not None)


def _inside(path: Path, folder: Path) -> str | None:
    full, top = str(path), str(folder).rstrip("\\/")
    if full.lower() == top.lower():
        return None
    for separator in ("\\", "/"):
        if full.lower().startswith((top + separator).lower()):
            return Path(full[len(top) + 1:]).as_posix()
    return None


# -- finish ---------------------------------------------------------------------------------

@dataclass
class Plan:
    task: Task
    code: list[str] = field(default_factory=list)  # code repo files to commit, relative to it
    wiki: list[str] = field(default_factory=list)  # wiki files to commit, relative to the wiki's repo
    notes: dict[str, list[str]] = field(default_factory=dict)  # "code:<file>" or "wiki:<file>": how it got in
    pending: list[str] = field(default_factory=list)  # wiki pages whose verified: is "pending"
    unchanged: list[str] = field(default_factory=list)  # edited, but nothing to commit
    other: list[str] = field(default_factory=list)  # changed in the code repo before the task, not since: left out
    other_wiki: list[str] = field(default_factory=list)  # the same in the wiki
    problems: list[str] = field(default_factory=list)  # why it cannot finish yet

    def to_dict(self) -> dict:
        data = {key: value for key, value in self.__dict__.items() if key != "task"}
        data["task"] = self.task.to_dict()
        return data


UNRECORDED = "changed by a command or by hand"
EARLIER = "already changed when the task started: those changes go in too"
INCLUDED = "added with --include"


def plan(settings: Settings, include: list[str] | None = None, exclude: list[str] | None = None) -> Plan:
    """What `wiki finish` would commit: every file changed since the task's snapshot, or
    edited by its sessions, in the code repo and the wiki, and every pending page."""
    task = load(settings)
    if task is None:
        raise TaskError("no task is open: `wiki finish` commits an open task's files")
    repos = _repos(settings)
    wiki_repo = codebase.top_level(settings.root)
    result = Plan(task)
    taken: dict[str, tuple[str, str]] = {}  # lower-case full path -> ("code" or "wiki", name in its repo)
    left: dict[str, tuple[str, str]] = {}  # the same, for changes left out
    for key, repo in repos.items():
        changed = codebase.changed_files(repo)
        hashes = codebase.file_hashes(repo, changed)
        before = task.before.get(key)
        before = None if before is None else {name.lower(): value for name, value in before.items()}
        recorded = {rel.lower(): rel for edited in task.edited if (rel := _inside(Path(edited), repo))}
        lowered = {name.lower() for name in changed}
        result.unchanged += sorted(rel for lower, rel in recorded.items() if lower not in lowered)
        for name in sorted(changed):
            full = repo / name
            bucket = "wiki" if key == "wiki" or _inside(full, settings.root) is not None else "code"
            earlier = before is None or name.lower() in before  # no snapshot: everything counts as earlier
            moved = not earlier or (before[name.lower()] is not None and before[name.lower()] != hashes[name])
            notes = ([] if name.lower() in recorded else [UNRECORDED]) + ([EARLIER] if earlier else [])
            if moved or name.lower() in recorded:
                taken[_key(full)] = (bucket, name)
                result.notes[f"{bucket}:{name}"] = notes
            else:
                left[_key(full)] = (bucket, name)
    result.pending = _pending_pages(settings)
    pending = {_key(settings.root / rel) for rel in result.pending}
    for rel in result.pending:
        wiki_rel = _inside(settings.root / rel, wiki_repo) if wiki_repo is not None else None
        if wiki_rel:
            taken.setdefault(_key(settings.root / rel), ("wiki", wiki_rel))
            left.pop(_key(settings.root / rel), None)
    for given in include or []:
        found = _given(given, repos, left) or _given(given, repos, taken)
        if found is None:
            result.problems.append(f"--include {given}: no change to it in the code repo or the wiki")
        elif found in left:
            taken[found] = left.pop(found)
            result.notes[":".join(taken[found])] = [INCLUDED]
    for given in exclude or []:
        found = _given(given, repos, taken)
        if found is None:
            result.problems.append(f"--exclude {given}: not among the files to commit")
        elif found in pending:
            result.problems.append(f"--exclude {given}: its verified: is \"pending\", so it goes in; set it back "
                                   "to the commit it was checked against to leave it out")
        else:
            left[found] = taken.pop(found)
    for bucket, name in taken.values():
        (result.wiki if bucket == "wiki" else result.code).append(name)
    for bucket, name in left.values():
        (result.other_wiki if bucket == "wiki" else result.other).append(name)
    result.notes = {name: notes for name, notes in result.notes.items()
                    if notes and name.split(":", 1)[1] in (result.code if name.startswith("code:") else result.wiki)}
    for names in (result.code, result.wiki, result.other, result.other_wiki):
        names.sort()
    if not result.code and not result.wiki:
        result.problems.append("the task changed no file in the code repo or the wiki: nothing to commit "
                               "(`wiki task cancel` closes it)")
    result.problems += _unchecked_pages(settings, result.code)
    return result


def _repos(settings: Settings) -> dict[str, Path]:
    """The repositories a task commits to: "code", and "wiki" unless the wiki is a folder in
    the code repo."""
    code_repo = _code_repo(settings).resolve()
    repos = {"code": code_repo}
    wiki_repo = codebase.top_level(settings.root)
    if wiki_repo is not None and _key(wiki_repo) != _key(code_repo):
        repos["wiki"] = wiki_repo
    return repos


def _key(path: Path) -> str:
    return path.resolve().as_posix().lower()


def _given(given: str, repos: dict[str, Path], pool: dict[str, tuple[str, str]]) -> str | None:
    """The key in ``pool`` of the file an --include or --exclude path names: absolute,
    relative to the current folder, or relative to the top of the code repo or the wiki's."""
    path = Path(given)
    for candidate in [path] if path.is_absolute() else [Path.cwd() / path, *(repo / path for repo in repos.values())]:
        if _key(candidate) in pool:
            return _key(candidate)
    return None


def _pending_pages(settings: Settings) -> list[str]:
    from wiki_cli.cache import Cache
    with Cache(settings) as cache:
        cache.refresh()
        return sorted(path for _, path, value in cache.values("verified") if value.lower() == PENDING)


def _unchecked_pages(settings: Settings, code_files: list[str]) -> list[str]:
    """A page covering a file the task changed must have been checked in it (`verified:
    "pending"`): otherwise it would be committed as describing code it was not checked against."""
    from wiki_cli.cache import Cache
    from wiki_cli.covering import matches
    with Cache(settings) as cache:
        verified = {slug: value for slug, _, value in cache.values("verified")}
        covers = cache.values("covers")
    unchecked: dict[str, list[str]] = {}
    for slug, _, pattern in covers:
        if slug not in verified or verified[slug].lower() == PENDING:
            continue
        for name in code_files:
            if matches(name, pattern) and name not in unchecked.setdefault(slug, []):
                unchecked[slug].append(name)
    return [f"[[{slug}]] covers {', '.join(files)}, which the task changed: check the page against the change "
            f"and set its verified: to \"pending\"" for slug, files in sorted(unchecked.items()) if files]


def plan_text(result: Plan) -> str:
    lines = [f"Task {describe(result.task)}"]
    for title, bucket, names in (
            ("Code to commit", "code", result.code), ("Wiki files to commit", "wiki", result.wiki),
            ("Left out, changed in the code repo before the task and not since (--include adds one)", None,
             result.other),
            ("Left out, changed in the wiki before the task and not since (--include adds one)", None,
             result.other_wiki)):
        if names:
            lines.append(f"{title}:")
            for name in names:
                notes = result.notes.get(f"{bucket}:{name}") if bucket else None
                lines.append(f"  {name}" + (f"  ({'; '.join(notes)})" if notes else ""))
    if result.problems:
        lines.append("Not ready to finish:")
        lines += [f"  {problem}" for problem in result.problems]
    return "\n".join(lines)


def finish(settings: Settings, message: str, include: list[str] | None = None,
           exclude: list[str] | None = None) -> dict:
    if not message.strip():
        raise TaskError("give the commit message the user approved: wiki finish -m \"<message>\"")
    result = plan(settings, include, exclude)
    if result.problems:
        raise TaskError("not ready to finish:\n  " + "\n  ".join(result.problems))
    errors = _page_errors(settings, result)
    if errors:
        raise TaskError("wiki pages have errors (`wiki check <page>`):\n  " + "\n  ".join(errors))
    code_repo = _code_repo(settings)
    code_commit = None
    if result.code:
        code_commit = _commit(code_repo, result.code, message)
    verified_at = code_commit or codebase.head(code_repo)
    for rel in result.pending:
        _set_verified(settings.root / rel, verified_at)
    wiki_commit = None
    if result.wiki:
        wiki_repo = codebase.top_level(settings.root)
        body = message.rstrip() + (f"\n\nCode: {code_commit}" if code_commit else "")
        wiki_commit = _commit(wiki_repo, result.wiki, body)
    close(settings)
    return {"task": result.task.title, "code_commit": code_commit, "wiki_commit": wiki_commit,
            "code": result.code, "wiki": result.wiki, "verified": result.pending, "left_out": result.other,
            "left_out_wiki": result.other_wiki}


def _page_errors(settings: Settings, result: Plan) -> list[str]:
    """Errors (not warnings) `wiki check` finds on the pages the task changed or checked."""
    from wiki_cli.model import ERROR
    from wiki_cli.pages import Resolver, load as load_page, scan_vault
    from wiki_cli.validation import check_page
    wiki_repo = codebase.top_level(settings.root)
    wanted = set(result.pending) | {rel for name in result.wiki if wiki_repo is not None
                                    and (rel := _inside(wiki_repo / name, settings.root)) and rel.endswith(".md")}
    scanned, others = scan_vault(settings)
    files = [page_file for page_file, _ in scanned]
    resolver = Resolver([(page_file.slug, page_file.rel) for page_file in files], others)
    errors = []
    for page_file in files:
        if page_file.rel in wanted:
            errors += [f"{page_file.rel}: {issue.message}" for issue in check_page(load_page(page_file, settings),
                                                                                    resolver)
                       if issue.severity == ERROR]
    return errors


def _commit(repo: Path, files: list[str], message: str) -> str:
    # a file removed with `git rm` is in neither the working tree nor the index, and `git add` refuses it
    indexed = set(codebase.git(repo, "ls-files", "-z", "--", *files).split("\0"))
    to_add = [name for name in files if name in indexed or (repo / name).exists()]
    if to_add:
        codebase.git(repo, "add", "-A", "--", *to_add)
    codebase.git(repo, "commit", "-q", "-m", message, "--", *files)
    return codebase.head(repo)


def _set_verified(path: Path, commit: str) -> None:
    data = path.read_bytes().decode("utf-8")
    end = data.find("\n---", 3)
    front, rest = (data[:end], data[end:]) if data.startswith("---") and end != -1 else ("", data)
    if front:
        path.write_bytes((_VERIFIED_LINE.sub(lambda match: f'{match.group(1)}"{commit}"', front, count=1)
                          + rest).encode("utf-8"))


def _code_repo(settings: Settings) -> Path:
    if settings.code_repo is None:
        raise TaskError("this wiki names no code repo: tasks are for work on the code")
    return settings.code_repo


def _now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")
