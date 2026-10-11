"""``wiki hook``: the command an agent's hooks run, reading the event the agent sends on stdin.

What it does, for an agent the code repo's config names in `agents`:

- at session start, tells the agent where the wiki is, how to work with it, which task is
  open, and which decisions wait for the user's approval;
- shows the wiki's notes on a file the first time the session touches it (`wiki covering`):
  before an edit and after a read where the agent can show text before a tool runs, after
  either where it cannot;
- refuses edits to the code and the wiki unless this session holds the open task, and
  records each file the task edits, for the end-of-task commit (tasks.py);
- refuses `git commit`, and asks the user before `wiki task start|takeover|cancel` and
  `wiki finish`, showing what `wiki finish` will commit.

In every wiki whose config names the agent, code repo or not, it refuses an edit to an
original (the user's writing, `[originals]`) or to a source already in a raw folder. That
is all it does in a wiki with no code repo.

`--log` also records each call as it arrived, to learn an agent's real tool names and
payload fields.

A hook must never get in the agent's way by failing: Copilot denies a tool call when its
pre-tool hook exits with an error, so everything here fails quietly and the command always
exits 0. It refuses a tool call only by its reply.
"""

from __future__ import annotations

import datetime
import json
import os
import re
from pathlib import Path
from typing import Any, Callable

from wiki_cli import agents, covering, tasks
from wiki_cli.sources import raw_dirs
from wiki_cli.agents import AFTER_TOOL, BEFORE_TOOL, EDIT, READ, SESSION_START, SHELL, Event, Reply
from wiki_cli.cache import Cache
from wiki_cli.config import Settings

LOG_FILE = "hook-log.jsonl"  # in the wiki's .cache/ folder
MAX_TEXT = 300  # characters of each string kept in the log: enough to name a tool or a file, not a file's content

_COMMAND_START = r"(?:^|[\s;&|(`{])"
GIT_COMMIT = re.compile(_COMMAND_START + r"git(?:\.exe)?\s+(?:-[cC]\s+\S+\s+|--?[\w-]+(?:=\S+)?\s+)*commit\b")
WIKI_FINISH = re.compile(_COMMAND_START + r"wiki(?:\.exe)?\s+finish\b")
WIKI_TASK = re.compile(_COMMAND_START + r"wiki(?:\.exe)?\s+task\s+(start|takeover|cancel)\b")

COMMIT_DENIED = ("Commits are not made during a task. When the work is done and the user has approved it, "
                 "run `wiki finish`: it commits the code, then the wiki.")
ORIGINAL_DENIED = ("{rel} is the user's own writing: read it, never change, move or delete it. What the wiki "
                   "knows about it goes on its wiki page.")
SOURCE_DENIED = "{rel} is a source in a raw folder: sources are never edited. Notes about it go on its source page."
TASK_ASK = {
    "start": "Opens the task, so this session may change the code and the wiki. Approve if you approved its plan.",
    "takeover": "Moves the open task to this session, which then holds it. Approve if this session carries it on.",
    "cancel": "Closes the open task without committing anything; its changes stay in the working tree.",
}


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
        if agent.id not in settings.agents:
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
    if event.kind == BEFORE_TOOL and event.tool == EDIT:
        refusals = [why for given in event.paths if (why := raw_refusal(settings, _absolute(given, cwd)))]
        if refusals:
            return Reply(decision="deny", reason=refusals[0])
    if settings.code_repo is None:
        return None
    if event.kind == SESSION_START:
        return Reply(context=session_start_text(settings, cwd, event.session))
    if event.kind == BEFORE_TOOL and event.tool == SHELL and event.command:
        return _shell(settings, event)
    paths = [_absolute(given, cwd) for given in event.paths]
    if event.kind == BEFORE_TOOL and event.tool == EDIT:
        refusals = [why for path in paths if (why := tasks.edit_refusal(settings, event.session, path))]
        if refusals:
            return Reply(decision="deny", reason=refusals[0])
    if event.kind == AFTER_TOOL and event.tool == EDIT:
        for path in paths:
            tasks.record_edit(settings, event.session, path)
    if event.tool in (READ, EDIT) and paths and _shows_notes(agent, event):
        results = covering.lookup(settings, [str(path) for path in paths], cwd=cwd, session=event.session)
        notes = covering.text(results, settings.root, cwd, quiet=True)
        return Reply(context=notes) if notes else None
    return None


def raw_refusal(settings: Settings, path: Path) -> str | None:
    """Why an edit to ``path`` is refused: it is an original, or a source already saved in
    a raw folder. A new file in a raw folder (a source being saved) may be written."""
    try:
        rel = path.resolve().relative_to(settings.root.resolve()).as_posix()
    except ValueError:
        return None
    if settings.originals is not None and settings.originals.contains(rel):
        return ORIGINAL_DENIED.format(rel=rel)
    if path.exists() and any(rel.startswith(folder + "/") for folder in raw_dirs(settings)):
        return SOURCE_DENIED.format(rel=rel)
    return None


def _shell(settings: Settings, event: Event) -> Reply | None:
    command = event.command or ""
    if GIT_COMMIT.search(command):
        return Reply(decision="deny", reason=COMMIT_DENIED)
    if WIKI_FINISH.search(command):
        try:
            text = tasks.plan_text(tasks.plan(settings))
        except tasks.TaskError as exc:
            text = str(exc)
        return Reply(decision="ask", reason="`wiki finish` commits the task's files, the code then the wiki.\n"
                                            + text)
    match = WIKI_TASK.search(command)
    if match:
        if event.session and match.group(1) in ("start", "takeover"):
            tasks.hand_off(settings, event.session, match.group(1))
        return Reply(decision="ask", reason=TASK_ASK[match.group(1)])
    return None


def _absolute(given: str, cwd: Path) -> Path:
    path = Path(given) if Path(given).is_absolute() else cwd / given
    return path.resolve() if path.exists() else Path(os.path.normpath(path))


def _shows_notes(agent: agents.Agent, event: Event) -> bool:
    """Notes are best before an edit and after a read; an agent that cannot show text
    before a tool runs gets them after either."""
    if agent.context_before_tool:
        return (event.kind, event.tool) in ((BEFORE_TOOL, EDIT), (AFTER_TOOL, READ))
    return event.kind == AFTER_TOOL


def session_start_text(settings: Settings, cwd: Path, session: str | None = None) -> str:
    lines = [
        f"This code has an LLM-maintained wiki at {covering.display_path(settings.root, cwd)} "
        "(`wiki` commands find it from here). Work through `wiki guide work`: it says how to "
        "learn the area from the wiki, plan, do the work, and keep the wiki current. The wiki's "
        "notes on a file are shown the first time you open it.",
        "Never run `git commit` yourself: at the end of a task, once the user approves it, "
        "`wiki finish` commits the code and then the wiki.",
    ]
    task = tasks.load(settings)
    if task is None:
        lines.append("No task is open: files in the code repo and the wiki can't be changed until the user "
                     "approves a plan and you run `wiki task start`.")
    elif session is not None and task.session == session:
        lines.append(f"This session holds the open task {tasks.describe(task)}.")
    else:
        lines.append(f"Another session holds the open task {tasks.describe(task)}: edits here are refused "
                     "until it is finished, or this session takes it over (`wiki task takeover`).")
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
