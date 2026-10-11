"""``wiki hook``: the command an agent's hooks run, reading the event the agent sends on stdin.

What it does, for an agent the code repo's config names in `agents`:

- at session start, tells the agent where the wiki is, how to work with it, and which
  decisions wait for the user's approval; and notes which files were already changed, so
  the end-of-task commit can tell them from the task's own;
- shows the wiki's notes on a file the first time the session touches it (`wiki covering`):
  before an edit and after a read where the agent can show text before a tool runs, after
  either where it cannot;
- records each file the session edits, for the end-of-task commit;
- refuses `git commit`, and asks the user before `wiki finish`, which commits the task.

`--log` also records each call as it arrived, to learn an agent's real tool names and
payload fields.

A hook must never get in the agent's way: Copilot denies a tool call when its pre-tool hook
exits with an error, so everything here fails quietly and the command always exits 0.
"""

from __future__ import annotations

import datetime
import json
import os
import re
from pathlib import Path
from typing import Any, Callable

from wiki_cli import agents, codebase, covering, sessions
from wiki_cli.agents import AFTER_TOOL, BEFORE_TOOL, EDIT, READ, SESSION_START, SHELL, Event, Reply
from wiki_cli.cache import Cache
from wiki_cli.config import Settings

LOG_FILE = "hook-log.jsonl"  # in the wiki's .cache/ folder
MAX_TEXT = 300  # characters of each string kept in the log: enough to name a tool or a file, not a file's content

_COMMAND_START = r"(?:^|[\s;&|(`{])"
GIT_COMMIT = re.compile(_COMMAND_START + r"git(?:\.exe)?\s+(?:-[cC]\s+\S+\s+|--?[\w-]+(?:=\S+)?\s+)*commit\b")
WIKI_FINISH = re.compile(_COMMAND_START + r"wiki(?:\.exe)?\s+finish\b")

COMMIT_DENIED = ("Commits are not made during a task. When the work is done and the user has approved it, "
                 "run `wiki finish`: it commits the code, then the wiki.")
FINISH_ASK = "`wiki finish` commits this task: the code first, then the wiki pages. Approve if the work is done."


def run(event_name: str, agent_name: str | None, stdin_text: str, *, log: bool,
        load: Callable[[], Settings | None]) -> dict | None:
    """Handle one hook call; returns the reply to print as JSON, if any. Never raises: a
    hook that fails must not block the agent."""
    try:
        settings = load()
        if settings is None:
            return None
        if log:
            try:
                record_call(settings.root / ".cache" / LOG_FILE, event_name, agent_name, stdin_text)
            except OSError:
                pass
        try:
            agent = agents.get(agent_name or "")
        except agents.AgentError:
            return None
        if agent.id not in settings.agents or settings.code_repo is None:
            return None
        event = agent.read(event_name, _parse(stdin_text))
        if event is None or event.kind is None:
            return None
        reply = respond(settings, agent, event)
        return agent.reply(event.kind, reply) if reply is not None else None
    except Exception:  # noqa: BLE001 - a hook must never fail the agent's tool call
        return None


def respond(settings: Settings, agent: agents.Agent, event: Event) -> Reply | None:
    cwd = Path(event.cwd) if event.cwd else Path.cwd()
    if event.kind == SESSION_START:
        if event.session:
            _note_changed_files(settings, event.session)
        return Reply(context=session_start_text(settings, cwd))
    if event.kind == BEFORE_TOOL and event.tool == SHELL and event.command:
        if GIT_COMMIT.search(event.command):
            return Reply(decision="deny", reason=COMMIT_DENIED)
        if WIKI_FINISH.search(event.command):
            return Reply(decision="ask", reason=FINISH_ASK)
        return None
    if event.kind == AFTER_TOOL and event.tool == EDIT and event.session:
        _record_edits(settings, event.session, event.paths, cwd)
    if event.tool in (READ, EDIT) and event.paths and _shows_notes(agent, event):
        results = covering.lookup(settings, list(event.paths), cwd=cwd, session=event.session)
        notes = covering.text(results, settings.root, cwd, quiet=True)
        return Reply(context=notes) if notes else None
    return None


def _shows_notes(agent: agents.Agent, event: Event) -> bool:
    """Notes are best before an edit and after a read; an agent that cannot show text
    before a tool runs gets them after either."""
    if agent.context_before_tool:
        return (event.kind, event.tool) in ((BEFORE_TOOL, EDIT), (AFTER_TOOL, READ))
    return event.kind == AFTER_TOOL


def session_start_text(settings: Settings, cwd: Path) -> str:
    lines = [
        f"This code has an LLM-maintained wiki at {covering.display_path(settings.root, cwd)} "
        "(`wiki` commands find it from here). Work through `wiki guide work`: it says how to "
        "learn the area from the wiki, plan, do the work, and keep the wiki current. The wiki's "
        "notes on a file are shown the first time you open it.",
        "Never run `git commit` yourself: at the end of a task, once the user approves it, "
        "`wiki finish` commits the code and then the wiki.",
    ]
    proposed = proposed_decisions(settings)
    if proposed:
        lines.append("Decisions waiting for the user's approval (status: proposed):")
        lines += [f"- {slug}: {summary}" if summary else f"- {slug}" for slug, summary in proposed]
    return "\n".join(lines)


def proposed_decisions(settings: Settings) -> list[tuple[str, str | None]]:
    with Cache(settings) as cache:
        cache.refresh()
        return cache.conn.execute(
            "SELECT pages.slug, pages.summary FROM page_values JOIN pages ON pages.path = page_values.path "
            "WHERE page_values.field = 'status' AND lower(page_values.value) = 'proposed' "
            "AND pages.page_type = 'decision' ORDER BY pages.slug").fetchall()


def _note_changed_files(settings: Settings, session: str) -> None:
    """The code repo's changed files when the session started, once: session start can
    fire twice, and a resumed session keeps what it noted first."""
    data = sessions.load(settings.root, session)
    if "changed_at_start" in data:
        return
    try:
        status = codebase.git(settings.code_repo, "status", "--porcelain", "--untracked-files=all")
    except codebase.CodeRepoError:
        return
    data["changed_at_start"] = sorted({line[3:].strip('"') for line in status.splitlines() if len(line) > 3})
    data.setdefault("started", datetime.datetime.now().astimezone().isoformat(timespec="seconds"))
    sessions.save(settings.root, session, data)


def _record_edits(settings: Settings, session: str, paths: tuple[str, ...], cwd: Path) -> None:
    data = sessions.load(settings.root, session)
    edited = list(data.get("edited", []))
    known = {item.lower() for item in edited}
    for given in paths:
        path = Path(given) if Path(given).is_absolute() else cwd / given
        full = (path.resolve() if path.exists() else Path(os.path.normpath(path))).as_posix()
        if full.lower() not in known:
            edited.append(full)
            known.add(full.lower())
    if edited != data.get("edited"):
        data["edited"] = edited
        sessions.save(settings.root, session, data)


def _parse(stdin_text: str) -> Any:
    try:
        return json.loads(stdin_text) if stdin_text.strip() else None
    except json.JSONDecodeError:
        return None


def record_call(path: Path, event: str, agent: str | None, stdin_text: str) -> None:
    try:
        payload: Any = json.loads(stdin_text) if stdin_text.strip() else None
    except json.JSONDecodeError:
        payload = {"unparsed": stdin_text}
    entry = {
        "time": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "event": event,
        "agent": agent,
        "cwd": os.getcwd(),
        "payload": shorten(payload),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(entry, ensure_ascii=False) + "\n")


def shorten(value: Any) -> Any:
    """The payload with every long string cut short, so the log keeps names and paths but
    not the file contents a tool read or wrote."""
    if isinstance(value, str):
        if len(value) <= MAX_TEXT:
            return value
        return value[:MAX_TEXT] + f"... ({len(value)} characters)"
    if isinstance(value, dict):
        return {key: shorten(item) for key, item in value.items()}
    if isinstance(value, list):
        return [shorten(item) for item in value]
    return value
