"""Locate the wiki root and read ``.wiki-cli.toml``."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from wiki_cli.models import DEFAULT_EMBED_MODEL, DEFAULT_RERANKER

CONFIG_FILENAME = ".wiki-cli.toml"
CACHE_FILENAME = "wiki.sqlite3"
DEFAULT_PAGES = ("wiki/**/*.md",)


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    root: Path
    cache_path: Path
    pages: tuple[str, ...] = DEFAULT_PAGES
    exclude: tuple[str, ...] = ()
    raw: tuple[str, ...] = ()
    embed_model: str = DEFAULT_EMBED_MODEL
    reranker: str = DEFAULT_RERANKER
    models_dir: Path | None = None


def find_root(start: Path) -> Path | None:
    for directory in (start, *start.parents):
        if (directory / CONFIG_FILENAME).is_file():
            return directory
    return None


def load_settings(
    root: str | os.PathLike | None = None,
    cache: str | os.PathLike | None = None,
    embed_model: str | None = None,
    reranker: str | None = None,
) -> Settings:
    """Root from ``--root``, then ``WIKI_ROOT``, then the nearest ``.wiki-cli.toml`` upward."""
    root_value = root or os.environ.get("WIKI_ROOT")
    resolved = Path(root_value).expanduser().resolve() if root_value else find_root(Path.cwd().resolve())
    if resolved is None:
        raise ConfigError(f"no wiki root: pass --root, set WIKI_ROOT, or run inside a folder with {CONFIG_FILENAME}")
    if not resolved.is_dir():
        raise ConfigError(f"wiki root does not exist: {resolved}")

    config = _read_config(Path(os.environ.get("WIKI_CONFIG") or resolved / CONFIG_FILENAME))
    cache_path = Path(cache or os.environ.get("WIKI_CACHE") or resolved / ".cache" / CACHE_FILENAME)
    cache_path = cache_path.expanduser().resolve()
    models_dir = Path(os.environ.get("WIKI_MODELS_DIR") or cache_path.parent / "models").expanduser()

    return Settings(
        root=resolved,
        cache_path=cache_path,
        pages=_patterns(config, "pages", DEFAULT_PAGES),
        exclude=_patterns(config, "exclude", ()),
        raw=_patterns(config, "raw", ()),
        embed_model=embed_model or os.environ.get("WIKI_EMBED_MODEL") or config.get("embed_model") or DEFAULT_EMBED_MODEL,
        reranker=reranker or os.environ.get("WIKI_RERANKER") or config.get("reranker") or DEFAULT_RERANKER,
        models_dir=models_dir,
    )


def _patterns(config: dict, key: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = config.get(key, list(default))
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"{CONFIG_FILENAME}: '{key}' must be a list of glob patterns")
    return tuple(value)


def _read_config(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
