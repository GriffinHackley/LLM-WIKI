"""``wiki hook``: the command an agent's hooks run, reading the event the agent sends on stdin.

For now it can only record what arrives (``--log``), to learn each agent's real tool names
and payload fields before anything depends on them.

A hook must never get in the agent's way: Copilot denies a tool call when its pre-tool hook
exits with an error, so everything here fails quietly and the command always exits 0.
"""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
from typing import Any, Callable

AGENTS = ("claude-code", "copilot")
LOG_FILE = "hook-log.jsonl"  # in the wiki's .cache/ folder
MAX_TEXT = 300  # characters of each string kept in the log: enough to name a tool or a file, not a file's content


def run(event: str, agent: str | None, stdin_text: str, *, log: bool, find_root: Callable[[], Path | None]) -> None:
    """Handle one hook call. Never raises: a hook that fails must not block the agent."""
    if not log:
        return
    try:
        root = find_root()
        if root is None:
            return
        record_call(root / ".cache" / LOG_FILE, event, agent, stdin_text)
    except Exception:  # noqa: BLE001 - a hook must never fail the agent's tool call
        return


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
