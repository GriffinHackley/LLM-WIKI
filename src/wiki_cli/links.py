"""Parse Obsidian links and the sections they sit in."""

from __future__ import annotations

import re
from dataclasses import dataclass

_LINK = re.compile(r"(!?)\[\[([^\[\]\n]+?)\]\]")
_HEADING = re.compile(r"(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*")
_FENCE = re.compile(r"(```|~~~)")
_INLINE_CODE = re.compile(r"`[^`\n]*`")
_CLAIM_ID = re.compile(r"\b([A-Z]{2,3}-\d{3})\b")
_RULE = re.compile(r"\s*(?:-{3,}|\*{3,}|_{3,})\s*")


@dataclass(frozen=True)
class Link:
    target: str  # page part, normalized: no ".md", forward slashes, no "#..."
    display: str | None
    anchor: str | None  # heading or ^block after "#"
    embed: bool
    section: str  # lowercase heading of the nearest enclosing section ("" before any)
    line: str  # the full source line


def split_target(inner: str) -> tuple[str, str | None, str | None]:
    """``target#anchor|display`` (with ``\\|`` in tables) -> (target, anchor, display)."""
    inner = inner.replace("\\|", "|")
    target, _, display = inner.partition("|")
    target, _, anchor = target.partition("#")
    return normalize(target), anchor.strip() or None, display.strip() or None


def normalize(target: str) -> str:
    target = target.strip().replace("\\", "/").strip("/")
    if target.lower().endswith(".md"):
        target = target[:-3]
    return target


def iter_lines(body: str):
    """Yield ``(line, section, in_code)`` with the current section heading (lowercased)."""
    section = ""
    in_fence = False
    for line in body.splitlines():
        stripped = line.lstrip()
        if _FENCE.match(stripped):
            in_fence = not in_fence
            yield line, section, True
            continue
        if not in_fence:
            heading = _HEADING.fullmatch(line)
            if heading:
                section = heading.group(2).strip().lower()
                yield line, section, False
                continue
            if _RULE.fullmatch(line):
                section = ""  # a horizontal rule ends the section (e.g. a page footer)
                yield line, section, False
                continue
        yield line, section, in_fence


def extract_links(body: str) -> list[Link]:
    links: list[Link] = []
    for line, section, in_code in iter_lines(body):
        if in_code:
            continue
        searchable = _INLINE_CODE.sub(lambda match: " " * len(match.group(0)), line)
        for match in _LINK.finditer(searchable):
            target, anchor, display = split_target(match.group(2))
            if target:
                links.append(Link(target, display, anchor, bool(match.group(1)), section, line))
    return links


def claim_ids(body: str, sections: set[str]) -> list[tuple[str, str, str]]:
    """Bare claim IDs in the given sections: ``(id, section, line)``, outside links."""
    found: list[tuple[str, str, str]] = []
    for line, section, in_code in iter_lines(body):
        if in_code or section not in sections:
            continue
        without_links = _LINK.sub(" ", line)
        for match in _CLAIM_ID.finditer(without_links):
            found.append((match.group(1), section, line))
    return found


def section_text(body: str, names: tuple[str, ...]) -> str | None:
    """First prose paragraph of the first section whose heading is in ``names``."""
    wanted = [name.lower() for name in names]
    for name in wanted:
        collecting = False
        paragraph: list[str] = []
        for line, section, in_code in iter_lines(body):
            if _HEADING.fullmatch(line) and not in_code:
                if collecting:
                    break
                collecting = section == name
                continue
            if not collecting or in_code:
                continue
            if not line.strip():
                if paragraph:
                    break
                continue
            paragraph.append(line.strip())
        if paragraph:
            return " ".join(paragraph)
    return None


def plain(text: str) -> str:
    """Strip link and emphasis markup for display: ``[[a|B]]`` -> ``B``."""
    text = _LINK.sub(lambda match: _display(match.group(2)), text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[*_`]+", "", text)
    text = re.sub(r"\^q-[\w-]+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _display(inner: str) -> str:
    target, anchor, display = split_target(inner)
    return display or target.rsplit("/", 1)[-1]
