"""Locate and parse YAML frontmatter without ever reserializing it."""

from __future__ import annotations

import re
from dataclasses import dataclass

import yaml

_OPENING = re.compile(r"---[ \t]*\r?\n")
_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)  # libyaml when available


class FrontmatterError(Exception):
    pass


@dataclass(frozen=True)
class Frontmatter:
    data: dict | None  # None when the text has no frontmatter block
    body_offset: int  # index in the text where the body starts


def split(text: str) -> tuple[str, int] | None:
    """Return ``(yaml_text, body_offset)``, or None when there is no frontmatter.

    Raises FrontmatterError when an opening delimiter has no closing one.
    """
    opening = _OPENING.match(text)
    if not opening:
        return None
    position = opening.end()
    while True:
        newline = text.find("\n", position)
        line_end = len(text) if newline == -1 else newline + 1
        line = text[position:line_end].rstrip("\r\n").rstrip(" \t")
        if line in ("---", "..."):
            return text[opening.end():position], line_end
        if newline == -1:
            raise FrontmatterError("frontmatter has no closing '---' line")
        position = line_end


def parse(text: str) -> Frontmatter:
    """Parse frontmatter into a mapping. Raises FrontmatterError on invalid YAML."""
    parts = split(text)
    if parts is None:
        return Frontmatter(data=None, body_offset=0)
    yaml_text, body_offset = parts
    try:
        data = yaml.load(yaml_text, Loader=_LOADER)
    except yaml.YAMLError as exc:
        raise FrontmatterError(f"invalid YAML: {_first_line(exc)}") from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise FrontmatterError("frontmatter must be a mapping")
    return Frontmatter(data=data, body_offset=body_offset)


def _first_line(exc: Exception) -> str:
    return str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
