"""Workflow guides: the steps an agent follows to ingest, query or audit a wiki.

Guides ship with the command, so they always match it. A wiki picks them up in this
order: its own guides folder (`[guides] dir`), the variant for its preset, the generic
guide. Placeholders fill in what depends on the wiki: its page types and its rules.
"""

from __future__ import annotations

import re
from pathlib import Path

from wiki_cli.config import CONFIG_FILENAME, Settings

GUIDES_DIR = Path(__file__).parent / "guides"
_NAME = re.compile(r"^[a-z][a-z0-9-]*$")

RULES = ("Throughout, apply this wiki's own rules: the \"Your rules\" section of `AGENTS.md`, and any "
         "other instructions the wiki gives you. Where they conflict with this guide, they win.")


class GuideError(Exception):
    pass


def _folders(settings: Settings | None) -> list[Path]:
    folders = []
    if settings is not None and settings.guides_dir:
        folders.append(settings.root / settings.guides_dir)
    if settings is not None and settings.preset and _NAME.match(settings.preset):
        folders.append(GUIDES_DIR / settings.preset)
    folders.append(GUIDES_DIR)
    return folders


def available(settings: Settings | None) -> list[tuple[str, str]]:
    """``(name, title)`` of every guide the wiki can use, sorted by name."""
    found: dict[str, str] = {}
    for folder in _folders(settings):
        if folder.is_dir():
            for path in sorted(folder.glob("*.md")):
                found.setdefault(path.stem, _title(path))
    return sorted(found.items())


def render(name: str, settings: Settings | None) -> str:
    if not _NAME.match(name):
        raise GuideError(f"no guide '{name}'")
    for folder in _folders(settings):
        path = folder / f"{name}.md"
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            break
    else:
        names = ", ".join(guide for guide, _ in available(settings))
        raise GuideError(f"no guide '{name}'; available: {names}")
    text = text.replace("{{types}}", types_text(settings)).replace("{{rules}}", RULES)
    return text.rstrip() + "\n"


def types_text(settings: Settings | None) -> str:
    if settings is None or not settings.types:
        return ("This wiki declares no page types. Follow the types its existing pages use "
                "(`wiki list` shows them).")
    lines = [f"Page types in this wiki (declared in `{CONFIG_FILENAME}` under `[types]`):", ""]
    for page_type in settings.types:
        where = []
        if page_type.folder:
            where.append(f"pages in `{page_type.folder}/`")
        if page_type.template:
            where.append(f"template `{page_type.template}`")
        detail = f" ({'; '.join(where)})" if where else ""
        lines.append(f"- `{page_type.name}`: {page_type.description or 'no description'}{detail}")
    return "\n".join(lines)


def _title(path: Path) -> str:
    first = path.read_text(encoding="utf-8").lstrip().splitlines()[0] if path.stat().st_size else ""
    return first.lstrip("# ").strip()
