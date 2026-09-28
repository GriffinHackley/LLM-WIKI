"""Split a page into search chunks by heading, with a size cap.

Chunk 0 is always the page summary. Offsets index into the full page text, so a
chunk can later be read back as a section.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_CHARS = 1600  # about 400 tokens
OVERLAP_CHARS = 300  # a short trailing paragraph is repeated at the start of the next piece
CODE_BLOCK_LIMIT = MAX_CHARS * 2  # a fenced block up to this size is never split

_HEADING = re.compile(r"(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*")
_FENCE = re.compile(r"(```|~~~)")


@dataclass(frozen=True)
class Chunk:
    ordinal: int
    heading_path: str
    start: int  # offset in the full page text
    end: int
    text: str


def chunk_page(text: str, body_offset: int, summary: str | None) -> list[Chunk]:
    body = text[body_offset:]
    chunks = [Chunk(0, "", body_offset, body_offset, summary or "")]
    for heading_path, start, end in _sections(body):
        for piece_start, piece_end in _pieces(body, start, end):
            piece = body[piece_start:piece_end].strip()
            if piece:
                chunks.append(Chunk(len(chunks), heading_path, body_offset + piece_start,
                                    body_offset + piece_end, piece))
    return chunks


def _lines(body: str):
    """Yield ``(start, end, line, in_fence_before)`` for each line."""
    position = 0
    in_fence = False
    while position < len(body):
        newline = body.find("\n", position)
        end = len(body) if newline == -1 else newline + 1
        line = body[position:end].rstrip("\r\n")
        yield position, end, line, in_fence
        if _FENCE.match(line.lstrip()):
            in_fence = not in_fence
        position = end


def _sections(body: str) -> list[tuple[str, int, int]]:
    """``(heading_path, start, end)`` spans; each section starts at its heading line."""
    sections: list[tuple[str, int, int]] = []
    stack: list[tuple[int, str]] = []
    current_path, current_start = "", 0
    for start, _end, line, in_fence in _lines(body):
        match = None if in_fence else _HEADING.fullmatch(line)
        if not match:
            continue
        if start > current_start and _has_content(body[current_start:start], current_path):
            sections.append((current_path, current_start, start))
        level = len(match.group(1))
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, _clean_heading(match.group(2))))
        current_path = " > ".join(title for _, title in stack)
        current_start = start
    if _has_content(body[current_start:], current_path):
        sections.append((current_path, current_start, len(body)))
    return sections


def _has_content(section: str, heading_path: str) -> bool:
    """True when a section has text beyond its own heading line."""
    lines = [line for line in section.splitlines() if line.strip()]
    if heading_path and lines and _HEADING.fullmatch(lines[0].strip()):
        lines = lines[1:]
    return bool(lines)


def _clean_heading(title: str) -> str:
    title = re.sub(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", lambda m: m.group(2) or m.group(1), title)
    title = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", title)
    return title.replace(">", "-").strip()


def _pieces(body: str, start: int, end: int) -> list[tuple[int, int]]:
    """Split one section into pieces no longer than MAX_CHARS where possible."""
    if len(body[start:end].strip()) <= MAX_CHARS:
        return [(start, end)]
    pieces: list[tuple[int, int]] = []
    current: list[tuple[int, int]] = []
    for block_start, block_end, is_code in _blocks(body, start, end):
        size = block_end - block_start
        if size > MAX_CHARS and not (is_code and size <= CODE_BLOCK_LIMIT):
            if current:
                pieces.append((current[0][0], current[-1][1]))
                current = []
            pieces.extend(_hard_split(body, block_start, block_end))
            continue
        if current and block_end - current[0][0] > MAX_CHARS:
            pieces.append((current[0][0], current[-1][1]))
            last = current[-1]
            current = [last] if last[1] - last[0] <= OVERLAP_CHARS else []
        current.append((block_start, block_end))
    if current:
        pieces.append((current[0][0], current[-1][1]))
    return pieces


def _blocks(body: str, start: int, end: int) -> list[tuple[int, int, bool]]:
    """Paragraphs and fenced code blocks within ``[start, end)``: ``(start, end, is_code)``."""
    blocks: list[tuple[int, int, bool]] = []
    block_start: int | None = None
    fence_start: int | None = None
    for line_start, line_end, line, in_fence in _lines(body[:end]):
        if line_start < start:
            continue
        opens_or_closes = bool(_FENCE.match(line.lstrip()))
        if fence_start is not None:
            if opens_or_closes:
                blocks.append((fence_start, line_end, True))
                fence_start = None
            continue
        if opens_or_closes and not in_fence:
            if block_start is not None:
                blocks.append((block_start, line_start, False))
                block_start = None
            fence_start = line_start
            continue
        if not line.strip():
            if block_start is not None:
                blocks.append((block_start, line_start, False))
                block_start = None
        elif block_start is None:
            block_start = line_start
    if fence_start is not None:
        blocks.append((fence_start, end, True))
    if block_start is not None:
        blocks.append((block_start, end, False))
    return blocks


def _hard_split(body: str, start: int, end: int) -> list[tuple[int, int]]:
    """Split an oversized block on line boundaries."""
    pieces: list[tuple[int, int]] = []
    piece_start = start
    for line_start, line_end, _line, _ in _lines(body[:end]):
        if line_start < start:
            continue
        if line_end - piece_start > MAX_CHARS and line_start > piece_start:
            pieces.append((piece_start, line_start))
            piece_start = line_start
    pieces.append((piece_start, end))
    # A single line longer than the cap is split by characters as a last resort.
    result: list[tuple[int, int]] = []
    for piece_start, piece_end in pieces:
        while piece_end - piece_start > MAX_CHARS * 2:
            result.append((piece_start, piece_start + MAX_CHARS))
            piece_start += MAX_CHARS
        result.append((piece_start, piece_end))
    return result
