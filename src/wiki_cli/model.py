"""Shared value types."""

from __future__ import annotations

from dataclasses import asdict, dataclass

ERROR = "error"
WARNING = "warning"


@dataclass(frozen=True)
class Issue:
    severity: str
    code: str
    message: str
    path: str | None = None
    slug: str | None = None
    relation: int | None = None

    def to_dict(self) -> dict:
        return {key: value for key, value in asdict(self).items() if value is not None}


@dataclass(frozen=True)
class Relation:
    target: str
    space: str
    slug: str
    type: str
    reason: str
    index: int | None = None  # position in the page's relations list; None when derived
    derived: bool = False  # True for edges read from other frontmatter fields
