"""The coding agents the wiki knows: one row each, saying where the agent's hooks live, what
it calls its events and tools, and how to read what its hooks send and write a reply.

Everything else in the tool works with the agent-neutral `Event` and `Reply`, so adding an
agent is adding a row here. Agents are named by hook format, not by product: Copilot in
VS Code and the Copilot CLI share one format, and Claude Code in a terminal or in VS Code's
extension share another.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


class AgentError(Exception):
    pass


# Agent-neutral event kinds.
SESSION_START = "session-start"
BEFORE_TOOL = "before-tool"
AFTER_TOOL = "after-tool"

# Agent-neutral tool kinds.
READ = "read"
EDIT = "edit"
SHELL = "shell"


@dataclass(frozen=True)
class Event:
    """One hook call, in the agent's terms translated to the tool's."""
    kind: str | None  # SESSION_START, BEFORE_TOOL, AFTER_TOOL, or None for an event the tool ignores
    session: str | None = None
    cwd: str | None = None
    source: str | None = None  # how a session started: startup, resume, ...
    tool: str | None = None  # READ, EDIT, SHELL, or None for any other tool
    tool_name: str | None = None  # the agent's own name for the tool
    paths: tuple[str, ...] = ()  # the files a read or edit tool names, as the agent gave them
    command: str | None = None  # a shell tool's command line


@dataclass(frozen=True)
class Reply:
    """What a hook answers. `context` is text the model is shown; `decision` ("deny" or
    "ask") stops a tool call, or asks the user first, for the given `reason`."""
    context: str | None = None
    decision: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class Agent:
    id: str
    name: str
    aliases: tuple[str, ...] = ()  # older names `wiki new --agent` still accepts
    hook_file: str | None = None  # where its hooks live, from the top of the repo it works in; None: no hooks
    events: tuple[tuple[str, str], ...] = ()  # (event kind, the agent's event name)
    tools: tuple[tuple[str, str], ...] = ()  # (the agent's tool name, tool kind)
    context_before_tool: bool = False  # whether a before-tool hook can show the model text
    read_event: Callable[[str, dict], Event] | None = None  # (agent's event name, payload) -> Event
    write_reply: Callable[[str, Reply], dict | None] | None = None  # (event kind, reply) -> stdout JSON
    accepts: Callable[[dict], bool] | None = None  # is the payload in this agent's format?

    def event_name(self, kind: str) -> str | None:
        return dict(self.events).get(kind)

    def tool_kind(self, tool_name: str | None) -> str | None:
        return dict(self.tools).get(tool_name or "")

    def read(self, event_name: str, payload: Any) -> Event | None:
        """The payload as an `Event`, or None when it is not in this agent's format (another
        agent reading this agent's hook file) or this agent has no hooks."""
        if self.read_event is None or not isinstance(payload, dict):
            return None
        if self.accepts is not None and not self.accepts(payload):
            return None
        return self.read_event(event_name, payload)

    def reply(self, kind: str, reply: Reply) -> dict | None:
        if self.write_reply is None:
            return None
        return self.write_reply(kind, reply)


# --- Claude Code ------------------------------------------------------------------------

_CLAUDE_EVENTS = ((SESSION_START, "SessionStart"), (BEFORE_TOOL, "PreToolUse"), (AFTER_TOOL, "PostToolUse"))
_CLAUDE_TOOLS = (("Read", READ), ("Edit", EDIT), ("MultiEdit", EDIT), ("Write", EDIT), ("NotebookEdit", EDIT),
                 ("Bash", SHELL), ("PowerShell", SHELL))


def _claude_read(event_name: str, payload: dict) -> Event:
    kind = {name: kind for kind, name in _CLAUDE_EVENTS}.get(payload.get("hook_event_name") or event_name)
    tool_name = _text(payload.get("tool_name"))
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    paths = tuple(value for key in ("file_path", "notebook_path") if (value := _text(tool_input.get(key))))
    return Event(kind=kind, session=_text(payload.get("session_id")), cwd=_text(payload.get("cwd")),
                 source=_text(payload.get("source")), tool=dict(_CLAUDE_TOOLS).get(tool_name or ""),
                 tool_name=tool_name, paths=paths, command=_text(tool_input.get("command")))


def _claude_reply(kind: str, reply: Reply) -> dict | None:
    name = dict(_CLAUDE_EVENTS).get(kind)
    if name is None:
        return None
    output: dict[str, Any] = {"hookEventName": name}
    if reply.decision and kind == BEFORE_TOOL:
        output["permissionDecision"] = reply.decision
        output["permissionDecisionReason"] = reply.reason or ""
    if reply.context:
        output["additionalContext"] = reply.context
    return {"hookSpecificOutput": output} if len(output) > 1 else None


def _claude_accepts(payload: dict) -> bool:
    # Copilot also runs the hooks in .claude/settings.json, sending its "VS Code format":
    # snake_case like Claude Code's, but with a `timestamp`, which Claude Code does not send.
    return "timestamp" not in payload


CLAUDE_CODE = Agent(
    id="claude-code", name="Claude Code", aliases=("claude",), hook_file=".claude/settings.json",
    events=_CLAUDE_EVENTS, tools=_CLAUDE_TOOLS, context_before_tool=True,
    read_event=_claude_read, write_reply=_claude_reply, accepts=_claude_accepts)


# --- GitHub Copilot (VS Code's Copilot agent, the Copilot CLI, the cloud agent) -----------
# Hooks configured with camelCase event names get camelCase payloads and Copilot's own
# tool names. A before-tool hook can only allow, deny or ask: it cannot show the model text.

_COPILOT_EVENTS = ((SESSION_START, "sessionStart"), (BEFORE_TOOL, "preToolUse"), (AFTER_TOOL, "postToolUse"))
_COPILOT_TOOLS = (("view", READ), ("edit", EDIT), ("create", EDIT), ("str_replace_editor", EDIT),
                  ("apply_patch", EDIT), ("bash", SHELL), ("powershell", SHELL))
_PATCH_FILE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+?)\s*$", re.MULTILINE)


def _copilot_read(event_name: str, payload: dict) -> Event:
    kind = {name: kind for kind, name in _COPILOT_EVENTS}.get(event_name)
    tool_name = _text(payload.get("toolName"))
    args = payload.get("toolArgs")
    if isinstance(args, str):  # documented as unknown: sometimes the arguments' JSON text
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {"input": args}
    args = args if isinstance(args, dict) else {}
    paths = [value for key in ("path", "file_path", "filePath") if (value := _text(args.get(key)))]
    if tool_name == "apply_patch":
        patch = _text(args.get("input")) or _text(args.get("patch")) or ""
        paths += _PATCH_FILE.findall(patch)
    return Event(kind=kind, session=_text(payload.get("sessionId")), cwd=_text(payload.get("cwd")),
                 source=_text(payload.get("source")), tool=dict(_COPILOT_TOOLS).get(tool_name or ""),
                 tool_name=tool_name, paths=tuple(dict.fromkeys(paths)), command=_text(args.get("command")))


def _copilot_reply(kind: str, reply: Reply) -> dict | None:
    if kind == BEFORE_TOOL:
        if not reply.decision:
            return None
        return {"permissionDecision": reply.decision, "permissionDecisionReason": reply.reason or ""}
    if kind in (SESSION_START, AFTER_TOOL) and reply.context:
        return {"additionalContext": reply.context}
    return None


def _copilot_accepts(payload: dict) -> bool:
    return "hook_event_name" not in payload  # the snake_case "VS Code format" is read as Claude Code's


COPILOT = Agent(
    id="copilot", name="GitHub Copilot", hook_file=".github/hooks/wiki.json",
    events=_COPILOT_EVENTS, tools=_COPILOT_TOOLS, context_before_tool=False,
    read_event=_copilot_read, write_reply=_copilot_reply, accepts=_copilot_accepts)


# --- OpenCode: slash commands only, no hooks yet ------------------------------------------

OPENCODE = Agent(id="opencode", name="OpenCode")


AGENTS = (CLAUDE_CODE, COPILOT, OPENCODE)
IDS = tuple(agent.id for agent in AGENTS)
NAMES = tuple(name for agent in AGENTS for name in (agent.id, *agent.aliases))  # what --agent accepts


def get(name: str) -> Agent:
    """The agent with this id or alias."""
    for agent in AGENTS:
        if name == agent.id or name in agent.aliases:
            return agent
    raise AgentError(f"no agent '{name}': choose {', '.join(IDS)}")


def resolve(names: object, where: str = "agents") -> tuple[str, ...]:
    """A list of agent names as their ids, in order and without repeats."""
    if isinstance(names, str):
        names = [names]
    if not isinstance(names, (list, tuple)) or not all(isinstance(name, str) for name in names):
        raise AgentError(f"{where} must be a list of agent names: {', '.join(IDS)}")
    return tuple(dict.fromkeys(get(name).id for name in names))


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
