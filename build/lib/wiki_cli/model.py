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
