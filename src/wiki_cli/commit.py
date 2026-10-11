"""`wiki commit "<message>"`: commit the wiki's changes, never the user's writing.

It commits every change under the wiki root (new, changed and deleted files) except those
in the originals folder: the user's writing in progress is theirs to commit (Obsidian Git
does it), and an ingest commit never sweeps it in. Sources newly saved in `raw/` are
committed with the ingest that read them; the pre-commit hook still refuses an edited one.

Another tool committing at the same moment (Obsidian Git on a timer) holds git's index
lock: the command waits and tries again. A failing pre-commit hook is reported, never
skipped. In a code wiki with a task open, `wiki finish` commits the task instead.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field

from wiki_cli import tasks
from wiki_cli.config import Settings

LOCK_TRIES = 10  # attempts while another git process holds the index lock
LOCK_WAIT = 1.0  # seconds between them


class CommitError(Exception):
    pass


@dataclass
class Result:
    commit: str | None = None  # None when there was nothing to commit
    files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"commit": self.commit, "files": self.files}


def commit(settings: Settings, message: str, *, wait: float = LOCK_WAIT) -> Result:
    if not message.strip():
        raise CommitError("the commit message is empty: say what changed, e.g. \"ingest: the-salt-road/02-storm\"")
    if settings.code_repo is not None and tasks.load(settings) is not None:
        raise CommitError("a task is open: `wiki finish` commits it, the code then the wiki")
    if _git(settings, "rev-parse", "--is-inside-work-tree", wait=wait).stdout.strip() != "true":
        raise CommitError(f"{settings.root} is not in a git repository")
    specs = pathspecs(settings)
    _git(settings, "add", "-A", "--", *specs, wait=wait)
    files = _git(settings, "-c", "core.quotePath=false", "diff", "--cached", "--name-only", "--relative", "--",
                 *specs, wait=wait).stdout.splitlines()
    if not files:
        return Result()
    _git(settings, "commit", "-q", "-m", message, "--", *specs, wait=wait)
    return Result(_git(settings, "rev-parse", "HEAD", wait=wait).stdout.strip(), files)


def pathspecs(settings: Settings) -> list[str]:
    """The wiki root, less the originals folder."""
    specs = ["."]
    if settings.originals is not None:
        specs.append(f":(exclude){settings.originals.folder}")
    return specs


def _git(settings: Settings, *args: str, wait: float) -> subprocess.CompletedProcess:
    for attempt in range(LOCK_TRIES):
        try:
            done = subprocess.run(["git", "-C", str(settings.root), *args], capture_output=True, text=True,
                                  encoding="utf-8", errors="replace")
        except OSError as exc:
            raise CommitError(f"cannot run git: {exc}") from exc
        if done.returncode == 0:
            return done
        error = (done.stderr.strip() or done.stdout.strip() or f"git {args[0]} failed")
        if "index.lock" not in error or attempt == LOCK_TRIES - 1:
            raise CommitError(error)
        time.sleep(wait)
    raise CommitError("git's index stayed locked")  # not reached: the last attempt raises above
