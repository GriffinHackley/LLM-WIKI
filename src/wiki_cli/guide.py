"""Workflow guides: the steps an agent follows to ingest, query or audit a wiki.

Guides ship with the command, so they always match it. A wiki picks them up in this
order: its own guides folder (`[guides] dir`), the variant for its preset, the generic
guide. Placeholders fill in what depends on the wiki: its page types and its rules.
Text between `{{#code}}` and `{{/code}}` is kept only in a wiki with a code repo, and
between `{{^code}}` and `{{/code}}` only in one without; `{{#records}}` likewise for a
wiki with a record type.

A `{{part:<name>}}` slot is filled with the wiki's own text for that step, from
`<[guides] parts>/<name>.md` (`guides/parts/` by default), else with the built-in
default in `guides/parts/`, else with nothing. Parts let a wiki say how it does one step
(how to fetch a ticket, what to run before committing) while the rest of the guide
keeps up with the command.
"""

from __future__ import annotations

import re
from pathlib import Path

from wiki_cli.config import CONFIG_FILENAME, Settings

GUIDES_DIR = Path(__file__).parent / "guides"
PARTS_DIR = GUIDES_DIR / "parts"
_NAME = re.compile(r"^[a-z][a-z0-9-]*$")
# A block on lines of its own takes its line breaks with it; an inline one only itself.
_BLOCK = re.compile(r"^\{\{([#^])(code|records)\}\}\n(.*?)^\{\{/\2\}\}\n"
                    r"|\{\{([#^])(code|records)\}\}(.*?)\{\{/\5\}\}", re.DOTALL | re.MULTILINE)
# A part on a line of its own is indented like that line, and takes the line away when empty.
_PART = re.compile(r"^([ \t]*)\{\{part:([a-z][a-z0-9-]*)\}\}[ \t]*\n|\{\{part:([a-z][a-z0-9-]*)\}\}",
                   re.MULTILINE)
_PART_NAME = re.compile(r"\{\{part:([a-z][a-z0-9-]*)\}\}")

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
    text = _source(name, settings)
    text = _PART.sub(lambda match: _fill_part(match, settings), text)
    text = text.replace("{{types}}", types_text(settings)).replace("{{rules}}", RULES)
    if "{{code_repo}}" in text:
        code_repo = settings.code_repo if settings is not None else None
        text = text.replace("{{code_repo}}", code_repo.as_posix() if code_repo else
                            "(no code repo configured: set [code] repo in .wiki-cli.toml, or WIKI_CODE_REPO)")
    title, _, rest = text.partition("\n")
    return f"{title}\n\n{preamble(name)}{rest}".rstrip() + "\n"


def _source(name: str, settings: Settings | None) -> str:
    """The guide's text for this wiki, with its conditional blocks resolved."""
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
    flags = {"code": settings is not None and (settings.code_repo is not None or settings.preset == "code"),
             "records": settings is not None and any(page_type.record for page_type in settings.types)}

    def keep(match: re.Match) -> str:
        sign, flag, body = (match.group(1), match.group(2), match.group(3)) if match.group(1) else \
            (match.group(4), match.group(5), match.group(6))
        return body if (sign == "#") == flags[flag] else ""

    previous = None
    while previous != text:  # blocks may nest: a records block inside a code block
        previous, text = text, _BLOCK.sub(keep, text)
    return text


def part_text(name: str, settings: Settings | None) -> tuple[str, str]:
    """A part's text and where it came from: ``wiki`` (the wiki's own file),
    ``default`` (the built-in one) or ``empty``."""
    if settings is not None:
        own = settings.root / settings.guides_parts / f"{name}.md"
        if own.is_file():
            return own.read_text(encoding="utf-8").strip("\n").rstrip(), "wiki"
    default = PARTS_DIR / f"{name}.md"
    if default.is_file():
        return default.read_text(encoding="utf-8").strip("\n").rstrip(), "default"
    return "", "empty"


def _fill_part(match: re.Match, settings: Settings | None) -> str:
    if match.group(2) is None:  # inline
        return " ".join(part_text(match.group(3), settings)[0].split())
    indent, (text, _) = match.group(1), part_text(match.group(2), settings)
    if not text:
        return ""
    return "\n".join(indent + line if line.strip() else "" for line in text.splitlines()) + "\n"


def parts(settings: Settings | None) -> list[dict]:
    """Every part slot in the wiki's guides: its name, the guides that use it, and where
    its text comes from; then the wiki's own part files no guide uses (a misspelled name)."""
    used: dict[str, list[str]] = {}
    for guide_name, _ in available(settings):
        for name in _PART_NAME.findall(_source(guide_name, settings)):
            if guide_name not in used.setdefault(name, []):
                used[name].append(guide_name)
    result = []
    for name in sorted(used):
        text, source = part_text(name, settings)
        entry = {"name": name, "guides": used[name], "source": source, "text": text}
        if source == "wiki":
            entry["path"] = f"{settings.guides_parts}/{name}.md"
        result.append(entry)
    folder = settings.root / settings.guides_parts if settings is not None else None
    if folder is not None and folder.is_dir():
        for path in sorted(folder.glob("*.md")):
            if path.stem not in used:
                result.append({"name": path.stem, "guides": [], "source": "unused",
                               "path": f"{settings.guides_parts}/{path.name}", "text": ""})
    return result


def preamble(name: str) -> str:
    """Said first in every guide, because an agent may take `wiki guide <name>` for the
    command that does the work and run it again and again."""
    return (f"> **You carry out these steps yourself.** `wiki guide {name}` only prints them; no `wiki`\n"
            f"> command does this workflow for you (there is no `wiki {name}`). Use your own tools to\n"
            "> read files, write files and run commands, and run a `wiki` command only where a step\n"
            "> names one. You now have the whole guide: don't run `wiki guide` again. Start at step 1.\n")


def types_text(settings: Settings | None) -> str:
    if settings is None or not settings.types:
        return ("This wiki declares no page types. Follow its existing pages: `wiki list` shows their "
                "types and summaries. Before writing a new page, open two or three pages like it and match "
                "their folder, frontmatter and headings.")
    lines = [f"Page types in this wiki (declared in `{CONFIG_FILENAME}` under `[types]`):", ""]
    for page_type in settings.types:
        where = []
        if page_type.folder:
            where.append(f"pages in `{page_type.folder}/`")
        if page_type.template:
            where.append(f"template `{page_type.template}`")
        for key, allowed in page_type.values:
            where.append(f"`{key}:` one of {', '.join(allowed)}")
        if page_type.hub:
            where.append("a hub: other pages gather around it")
        if page_type.record:
            where.append("a record: names its item by `key:` and `url:`, and `synced:` its last-updated time")
        detail = f" ({'; '.join(where)})" if where else ""
        lines.append(f"- `{page_type.name}`: {page_type.description or 'no description'}{detail}")
    return "\n".join(lines)


def _title(path: Path) -> str:
    first = path.read_text(encoding="utf-8").lstrip().splitlines()[0] if path.stat().st_size else ""
    return first.lstrip("# ").strip()
