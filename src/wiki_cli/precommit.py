"""What the pre-commit hook checks, run by `wiki precommit` so the steps update with the command.

The hook in `.githooks/pre-commit` only calls this: sources in the raw folders are never
edited, renamed or deleted; last week's note is written (with `[weekly]`); `wiki check
--all` reports no errors; and the wiki's own `[hooks] pre_commit` commands pass.
"""

from __future__ import annotations

import os
import subprocess

from wiki_cli.config import HookCommand, Settings
from wiki_cli.pages import matches
from wiki_cli.sources import ALWAYS_IGNORED, raw_dirs


class HookError(Exception):
    pass


def staged(settings: Settings, diff_filter: str) -> list[str]:
    """Staged files under the wiki root, as paths from it, limited to ``diff_filter`` changes."""
    try:
        done = subprocess.run(["git", "-C", str(settings.root), "-c", "core.quotePath=false", "diff", "--cached",
                               "--name-only", "--relative", f"--diff-filter={diff_filter}"],
                              capture_output=True)
    except OSError as exc:
        raise HookError(f"cannot run git: {exc}") from exc
    if done.returncode != 0:
        raise HookError(done.stderr.decode("utf-8", "replace").strip() or "git diff --cached failed")
    return [line for line in done.stdout.decode("utf-8", "replace").splitlines() if line]


def changed_sources(settings: Settings) -> list[str]:
    """Staged edits, renames and deletions of sources: files in the raw folders other than
    README files and those `[pending] ignore` lists (a manifest updated as sources arrive)."""
    folders = raw_dirs(settings)
    return [rel for rel in staged(settings, "MDR")
            if any(rel.startswith(folder + "/") for folder in folders)
            and rel.rsplit("/", 1)[-1] not in ALWAYS_IGNORED
            and not matches(rel, settings.pending_ignore)]


def planned(settings: Settings) -> list[tuple[HookCommand, list[str]]]:
    """The `[hooks] pre_commit` commands to run, each with the staged files it is given: a
    command with `files` runs only when a staged (added or changed) file matches them."""
    found = []
    changed = None
    for command in settings.pre_commit:
        if not command.files:
            found.append((command, []))
            continue
        if changed is None:
            changed = staged(settings, "ACMR")
        files = [rel for rel in changed if matches(rel, command.files)]
        if files:
            found.append((command, files))
    return found


def run(settings: Settings, command: HookCommand, files: list[str]) -> subprocess.CompletedProcess:
    """Run a command with the system shell from the wiki root, its files appended as arguments."""
    line = " ".join([command.run, *(_quote(rel) for rel in files)])
    if os.name == "nt":  # /d: skip the user's cmd AutoRun, which can fail and fail the command with it
        line, shell = f'"{os.environ.get("COMSPEC", "cmd.exe")}" /d /s /c "{line}"', False
    else:
        shell = True
    return subprocess.run(line, shell=shell, cwd=settings.root, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def _quote(rel: str) -> str:
    return f'"{rel}"' if any(char in rel for char in " &|;<>()'^%!$`") else rel
