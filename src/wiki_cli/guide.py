"""Workflow guides: the steps an agent follows to ingest, query or audit a wiki.

Guides ship with the command, so they always match it. A wiki picks them up in this
order: its own guides folder (`[guides] dir`), the variant for its preset, the generic
guide. Placeholders fill in what depends on the wiki: its page types and its rules.
Text between `{{#code}}` and `{{/code}}` is kept only in a wiki with a code repo, and
between `{{^code}}` and `{{/code}}` only in one without.
"""

from __future__ import annotations

import re
from pathlib import Path

from wiki_cli.config import CONFIG_FILENAME, Settings

GUIDES_DIR = Path(__file__).parent / "guides"
_NAME = re.compile(r"^[a-z][a-z0-9-]*$")
# A block on lines of its own takes its line breaks with it; an inline one only itself.
_CODE_BLOCK = re.compile(r"^\{\{([#^])code\}\}\n(.*?)^\{\{/code\}\}\n|\{\{([#^])code\}\}(.*?)\{\{/code\}\}",
                         re.DOTALL | re.MULTILINE)

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
    has_code = settings is not None and (settings.code_repo is not None or settings.preset == "code")
    def keep(match: re.Match) -> str:
        wanted = (match.group(1) or match.group(3)) == "#"  # {{#code}}: with a code repo; {{^code}}: without
        return (match.group(2) or match.group(4) or "") if wanted == has_code else ""

    text = _CODE_BLOCK.sub(keep, text)
    text = text.replace("{{types}}", types_text(settings)).replace("{{rules}}", RULES)
    if "{{code_repo}}" in text:
        code_repo = settings.code_repo if settings is not None else None
        text = text.replace("{{code_repo}}", code_repo.as_posix() if code_repo else
                            "(no code repo configured: set [code] repo in .wiki-cli.toml, or WIKI_CODE_REPO)")
    title, _, rest = text.partition("\n")
    return f"{title}\n\n{preamble(name)}{rest}".rstrip() + "\n"


def preamble(name: str) -> str:
    """Said first in every guide, because an agent may take `wiki guide <name>` for the
    command that does the work and run it again and again."""
    return (f"> **You carry out these steps yourself.** `wiki guide {name}` only prints them; no `wiki`\n"
            f"> command does this workflow for you (there is no `wiki {name}`). Use your own tools to\n"
            "> read files, write files and run commands, and run a `wiki` command only where a step\n"
            "> names one. You now have the whole guide: don't run `wiki guide` again. Start at step 1.\n")


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
        for key, allowed in page_type.values:
            where.append(f"`{key}:` one of {', '.join(allowed)}")
        detail = f" ({'; '.join(where)})" if where else ""
        lines.append(f"- `{page_type.name}`: {page_type.description or 'no description'}{detail}")
    return "\n".join(lines)


def _title(path: Path) -> str:
    first = path.read_text(encoding="utf-8").lstrip().splitlines()[0] if path.stat().st_size else ""
    return first.lstrip("# ").strip()
