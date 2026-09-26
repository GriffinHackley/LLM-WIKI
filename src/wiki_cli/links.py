"""Parse links (Obsidian ``[[wikilinks]]`` and Markdown ``[text](path)``) and their sections."""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from urllib.parse import unquote

_WIKILINK = re.compile(r"(!?)\[\[([^\[\]\n]+?)\]\]")
_MARKDOWN_LINK = re.compile(r"(!?)\[([^\[\]\n]*)\]\(\s*(<[^<>\n]+>|[^()\s]+)(?:\s+\"[^\"\n]*\")?\s*\)")
WIKILINK = _WIKILINK  # groups: 1 "!" for an embed, 2 the inner "target#anchor|display"
_ANY_LINK = re.compile(_WIKILINK.pattern + "|" + _MARKDOWN_LINK.pattern)
_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:")
_HEADING = re.compile(r"(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*")
_FENCE = re.compile(r"(```|~~~)")
_INLINE_CODE = re.compile(r"`[^`\n]*`")
_RULE = re.compile(r"\s*(?:-{3,}|\*{3,}|_{3,})\s*")
_BLOCK_ID = re.compile(r"(?<!\S)\^[A-Za-z0-9-]+(?=\s|$)")
_LIST_ITEM = re.compile(r"\s*(?:[-*+]|\d+[.)])\s+")


@dataclass(frozen=True)
class Link:
    target: str  # normalized: forward slashes, no ".md", no "#..."; Markdown links are root-relative paths
    display: str | None
    anchor: str | None  # heading or ^block after "#"
    embed: bool
    section: str  # lowercase heading of the nearest enclosing section ("" before any)
    line: str  # the full source line
    raw: str = ""  # the link exactly as written
    is_path: bool = False  # a Markdown link: target is a path from the wiki root, never a bare name
    context: str = ""  # the whole paragraph, list item or table row, with wrapped lines joined


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


def markdown_target(href: str, source_rel: str | None) -> tuple[str, str | None] | None:
    """Resolve a Markdown link's href to ``(root-relative target, anchor)``; None if external."""
    href = href.strip()
    if href.startswith("<") and href.endswith(">"):
        href = href[1:-1]
    if not href or href.startswith("#") or _SCHEME.match(href):
        return None  # same-page anchor, or an external URL (http:, mailto:, obsidian:, ...)
    href, _, anchor = href.partition("#")
    path = unquote(href).replace("\\", "/")
    if path.startswith("/"):
        joined = path.lstrip("/")
    else:
        base = posixpath.dirname(source_rel) if source_rel else ""
        joined = posixpath.join(base, path)
    joined = posixpath.normpath(joined)
    if joined.startswith("..") or joined in (".", ""):
        return None  # points outside the wiki
    return normalize(joined), (unquote(anchor).strip() or None)


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


def _with_context(body: str):
    """``iter_lines`` plus each line's block: its paragraph, list item or table row, with
    hard-wrapped continuation lines joined, so a sentence split across lines stays whole."""
    rows = list(iter_lines(body))
    blocks: list[list[str]] = []
    block_of: list[int | None] = []
    current: int | None = None
    for line, _, in_code in rows:
        if in_code or not line.strip() or _HEADING.fullmatch(line) or _RULE.fullmatch(line):
            current = None
            block_of.append(None)
            continue
        table_row = line.lstrip().startswith("|")
        if current is None or table_row or _LIST_ITEM.match(line):
            blocks.append([line.rstrip()])
            current = len(blocks) - 1
        else:
            blocks[current].append(line.strip())
        block_of.append(current)
        if table_row:
            current = None
    for (line, section, in_code), block in zip(rows, block_of):
        yield line, section, in_code, line if block is None else " ".join(blocks[block])


def extract_links(body: str, source_rel: str | None = None) -> list[Link]:
    """Links outside code, in order. ``source_rel`` resolves relative Markdown links."""
    found: list[Link] = []
    for line, section, in_code, context in _with_context(body):
        if in_code:
            continue
        searchable = _INLINE_CODE.sub(lambda match: " " * len(match.group(0)), line)
        for match in _ANY_LINK.finditer(searchable):
            raw = line[match.start():match.end()]
            if match.group(2) is not None:  # [[wikilink]]
                target, anchor, display = split_target(match.group(2))
                if target:
                    found.append(Link(target, display, anchor, bool(match.group(1)), section, line, raw,
                                      context=context))
                continue
            resolved = markdown_target(match.group(5), source_rel)
            if resolved:
                target, anchor = resolved
                display = match.group(4).strip() or None
                found.append(Link(target, display, anchor, bool(match.group(3)), section, line, raw, is_path=True,
                                  context=context))
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


def strip_links(text: str) -> str:
    """Remove every link, keeping nothing of it."""
    return _ANY_LINK.sub(" ", text)


def plain(text: str) -> str:
    """Strip link and emphasis markup for display: ``[[a|B]]`` -> ``B``, ``[B](a.md)`` -> ``B``."""
    text = _WIKILINK.sub(lambda match: _display(match.group(2)), text)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[*_`]+", "", text)
    text = _BLOCK_ID.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def _display(inner: str) -> str:
    target, anchor, display = split_target(inner)
    return display or target.rsplit("/", 1)[-1]
