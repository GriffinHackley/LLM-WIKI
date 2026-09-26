"""Render and replace the generated compatibility link block.

Everything between the markers is owned by tooling and replaceable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

BLOCK_VERSION = "v1"
START_MARKER = f"<!-- wiki-relations:{BLOCK_VERSION}:start -->"
END_MARKER = f"<!-- wiki-relations:{BLOCK_VERSION}:end -->"

_MARKER = re.compile(r"^<!-- wiki-relations:(v\d+):(start|end) -->[ \t]*(?=\r?\n|\Z)", re.MULTILINE)
_LINK_LINE = re.compile(r"\[\[[^\[\]\r\n]+\]\]")


class BlockError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Block:
    start: int  # offset of the start marker within the body
    end: int  # offset just past the end marker
    inner: str  # text between the marker lines


def render(slugs: list[str], newline: str) -> str:
    """Render the block for already sorted, deduplicated slugs (no trailing newline)."""
    lines = [START_MARKER, *(f"[[{slug}]]" for slug in slugs), END_MARKER]
    return newline.join(lines)


def find(body: str) -> Block | None:
    """Locate the managed block. Raises BlockError when markers are malformed."""
    markers = list(_MARKER.finditer(body))
    if not markers:
        return None
    for marker in markers:
        if marker.group(1) != BLOCK_VERSION:
            raise BlockError("unsupported-block-version", f"unsupported block version {marker.group(1)}")
    kinds = [marker.group(2) for marker in markers]
    if kinds != ["start", "end"]:
        raise BlockError("malformed-block", "expected exactly one start marker followed by one end marker")
    start, end = markers
    inner = body[start.end():end.start()]
    inner = re.sub(r"\A\r?\n", "", inner)
    inner = re.sub(r"\r?\n\Z", "", inner)
    return Block(start=start.start(), end=end.end(), inner=inner)


def has_only_links(block: Block) -> bool:
    """True when every nonblank line inside the block is a single wikilink."""
    lines = [line.strip() for line in block.inner.splitlines()]
    return all(_LINK_LINE.fullmatch(line) for line in lines if line)


def apply(text: str, body_offset: int, slugs: list[str], newline: str) -> str:
    """Return ``text`` with the block replaced, appended, or removed to match ``slugs``."""
    head, body = text[:body_offset], text[body_offset:]
    block = find(body)
    ends_with_newline = text.endswith("\n")

    if slugs:
        rendered = render(slugs, newline)
        if block:
            body = body[:block.start] + rendered + body[block.end:]
        else:
            stripped = body.rstrip("\r\n")
            if stripped:
                body = stripped + newline + newline + rendered + (newline if ends_with_newline else "")
            else:
                separator = "" if not head or head.endswith("\n") else newline
                body = separator + rendered + newline
    elif block:
        before = body[:block.start].rstrip("\r\n")
        after = body[block.end:]
        if not after.strip("\r\n"):
            body = before + (newline if before and ends_with_newline else "")
        else:
            after = after.lstrip("\r\n")
            body = before + newline + newline + after if before else after

    return head + body


def strip(body: str) -> str:
    """Return the body without the managed block, for hashing page content."""
    try:
        block = find(body)
    except BlockError:
        return body
    if not block:
        return body
    return body[:block.start].rstrip("\r\n") + body[block.end:]
