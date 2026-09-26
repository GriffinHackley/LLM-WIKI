"""Resolve the wiki root, local space, cache location, and ingest filters."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_WIKI_ROOT = Path.home() / "llm-wiki" / "wiki"
CACHE_FILENAME = "wiki.sqlite3"


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    wiki_root: Path
    repo_root: Path
    space: str | None  # None means every wiki:// target is treated as local
    cache_path: Path
    exclude: tuple[str, ...] = ()
    skip_no_frontmatter: bool = True


def load_settings(
    wiki_root: str | os.PathLike | None = None,
    space: str | None = None,
    cache: str | os.PathLike | None = None,
) -> Settings:
    """Build settings from arguments, environment, and the repo's ``wiki.toml``.

    Only documented, committed ``wiki.toml`` keys are read (``name`` and the
    ``[ingest]`` filters); llm-wiki's private configuration is never read.
    """
    root_value = wiki_root or os.environ.get("LLM_WIKI_ROOT") or DEFAULT_WIKI_ROOT
    root = Path(root_value).expanduser().resolve()
    if not root.is_dir():
        raise ConfigError(f"wiki root does not exist: {root}")
    repo_root = root.parent

    toml = _read_wiki_toml(repo_root / "wiki.toml")
    ingest = toml.get("ingest", {}) if isinstance(toml.get("ingest"), dict) else {}

    resolved_space = space or os.environ.get("LLM_WIKI_SPACE") or toml.get("name") or None
    cache_value = cache or os.environ.get("WIKI_CACHE") or repo_root / ".cache" / CACHE_FILENAME
    exclude = ingest.get("exclude", [])
    skip = ingest.get("skip_no_frontmatter", True)

    return Settings(
        wiki_root=root,
        repo_root=repo_root,
        space=str(resolved_space) if resolved_space else None,
        cache_path=Path(cache_value).expanduser().resolve(),
        exclude=tuple(str(pattern) for pattern in exclude) if isinstance(exclude, list) else (),
        skip_no_frontmatter=bool(skip),
    )


def _read_wiki_toml(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
