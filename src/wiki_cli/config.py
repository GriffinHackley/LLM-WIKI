"""Locate the wiki root and read the optional ``.wiki-cli.toml``.

Every setting has a default that works on a plain folder of Markdown files, so the
config file is only needed to narrow what is indexed or to describe the wiki's
conventions (relation rules, summary headings, page types).
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from wiki_cli.models import DEFAULT_EMBED_MODEL, DEFAULT_RERANKER
from wiki_cli.vocabulary import RelationRule, RuleError, Vocabulary, parse_rules

CONFIG_FILENAME = ".wiki-cli.toml"
CACHE_FILENAME = "wiki.sqlite3"
DEFAULT_PAGES = ("**/*.md",)
DEFAULT_RAW = ("raw/**/*.txt",)
DEFAULT_MODELS_DIR = Path.home() / ".cache" / "wiki-cli" / "models"  # shared by every wiki
DEFAULT_SUMMARY_FIELDS = ("summary", "description")
DEFAULT_SUMMARY_HEADINGS = ("summary",)
_TOP_LEVEL = {"pages", "exclude", "raw", "embed_model", "reranker", "relations", "summary", "page_type",
              "check", "suggest"}


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    root: Path
    cache_path: Path
    pages: tuple[str, ...] = DEFAULT_PAGES
    exclude: tuple[str, ...] = ()
    raw: tuple[str, ...] = DEFAULT_RAW
    embed_model: str = DEFAULT_EMBED_MODEL
    reranker: str = DEFAULT_RERANKER
    models_dir: Path | None = None
    relations: tuple[RelationRule, ...] = ()
    summary_fields: tuple[str, ...] = DEFAULT_SUMMARY_FIELDS
    summary_headings: tuple[str, ...] = DEFAULT_SUMMARY_HEADINGS  # lowercased
    page_type_field: str = "type"
    type_folders: tuple[tuple[str, str], ...] = ()  # (folder prefix, page type), longest first
    summary_types: tuple[str, ...] = ()  # page types expected to have a summary; "*" = all
    require_frontmatter: bool = False
    named_types: tuple[str, ...] = ()  # page types `suggest` matches by name; empty = all
    vocabulary: Vocabulary = field(default_factory=Vocabulary, compare=False)

    def page_type_for(self, rel: str, data: dict | None) -> str | None:
        value = (data or {}).get(self.page_type_field)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
        for folder, page_type in self.type_folders:
            if rel.startswith(folder + "/"):
                return page_type
        return None

    def fingerprint(self) -> str:
        """Settings that change derived edges or page types; a change re-derives them."""
        return repr((self.vocabulary.fingerprint(), self.page_type_field, self.type_folders))

    def summary_fingerprint(self) -> str:
        return repr((self.summary_fields, self.summary_headings))


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
    """Root from ``--root``, then ``WIKI_ROOT``, then the nearest ``.wiki-cli.toml`` upward,
    then the current folder."""
    root_value = root or os.environ.get("WIKI_ROOT")
    if root_value:
        resolved = Path(root_value).expanduser().resolve()
    else:
        cwd = Path.cwd().resolve()
        resolved = find_root(cwd) or cwd
    if not resolved.is_dir():
        raise ConfigError(f"wiki root does not exist: {resolved}")

    config = _read_config(Path(os.environ.get("WIKI_CONFIG") or resolved / CONFIG_FILENAME))
    unknown = set(config) - _TOP_LEVEL
    if unknown:
        raise ConfigError(f"{CONFIG_FILENAME}: unknown setting(s) {', '.join(sorted(unknown))}")
    cache_path = Path(cache or os.environ.get("WIKI_CACHE") or resolved / ".cache" / CACHE_FILENAME)
    cache_path = cache_path.expanduser().resolve()
    models_dir = Path(os.environ.get("WIKI_MODELS_DIR") or DEFAULT_MODELS_DIR).expanduser()

    try:
        rules = parse_rules(config.get("relations"))
    except RuleError as exc:
        raise ConfigError(f"{CONFIG_FILENAME}: {exc}") from exc
    summary = _table(config, "summary")
    page_type = _table(config, "page_type")
    check = _table(config, "check")
    suggest = _table(config, "suggest")
    folders = page_type.get("folders", {})
    if not isinstance(folders, dict) or not all(isinstance(v, str) for v in folders.values()):
        raise ConfigError(f"{CONFIG_FILENAME}: [page_type] folders must map folder paths to type names")
    type_folders = tuple(sorted(((k.strip("/"), v.strip().lower()) for k, v in folders.items()),
                                key=lambda pair: -len(pair[0])))

    return Settings(
        root=resolved,
        cache_path=cache_path,
        pages=_patterns(config, "pages", DEFAULT_PAGES),
        exclude=_patterns(config, "exclude", ()),
        raw=_patterns(config, "raw", DEFAULT_RAW),
        embed_model=embed_model or os.environ.get("WIKI_EMBED_MODEL") or config.get("embed_model") or DEFAULT_EMBED_MODEL,
        reranker=reranker or os.environ.get("WIKI_RERANKER") or config.get("reranker") or DEFAULT_RERANKER,
        models_dir=models_dir,
        relations=rules,
        summary_fields=_patterns(summary, "fields", DEFAULT_SUMMARY_FIELDS, "[summary] fields"),
        summary_headings=tuple(h.lower() for h in _patterns(summary, "headings", DEFAULT_SUMMARY_HEADINGS,
                                                               "[summary] headings")),
        page_type_field=str(page_type.get("field", "type")),
        type_folders=type_folders,
        summary_types=tuple(t.lower() for t in _patterns(check, "summary_types", (), "[check] summary_types")),
        require_frontmatter=bool(check.get("require_frontmatter", False)),
        named_types=tuple(t.lower() for t in _patterns(suggest, "named_types", (), "[suggest] named_types")),
        vocabulary=Vocabulary(rules),
    )


def _table(config: dict, key: str) -> dict:
    value = config.get(key, {})
    if not isinstance(value, dict):
        raise ConfigError(f"{CONFIG_FILENAME}: [{key}] must be a table")
    return value


def _patterns(config: dict, key: str, default: tuple[str, ...], label: str | None = None) -> tuple[str, ...]:
    value = config.get(key, list(default))
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"{CONFIG_FILENAME}: '{label or key}' must be a list of strings")
    return tuple(value)


def _read_config(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
