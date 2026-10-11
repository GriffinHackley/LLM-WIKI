"""What the tool remembers about one agent session, in the wiki's `.cache/sessions/`: the
files whose wiki notes it was already shown. Keyed by the session id the agent's hooks send.

Disposable like the rest of the cache: losing it only means notes are shown again.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

FOLDER = "sessions"
KEEP_DAYS = 14  # a session file not touched for this long is removed when another is written


def path_for(root: Path, session: str) -> Path:
    """The session's file. Ids that are not plain names are hashed, so no id can reach
    outside the folder."""
    name = session if re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", session) and session.strip(".") else \
        hashlib.sha256(session.encode("utf-8")).hexdigest()[:32]
    return root / ".cache" / FOLDER / f"{name}.json"


def load(root: Path, session: str) -> dict:
    try:
        data = json.loads(path_for(root, session).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save(root: Path, session: str, data: dict) -> None:
    path = path_for(root, session)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        _prune(path.parent)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    temporary.replace(path)


def _prune(folder: Path) -> None:
    cutoff = time.time() - KEEP_DAYS * 86400
    for old in folder.glob("*.json"):
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink()
        except OSError:
            pass
