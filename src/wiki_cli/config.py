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
DERIVATION_VERSION = "6"  # bump when edge or reason extraction changes: edges are re-derived
DEFAULT_PAGES = ("**/*.md",)
DEFAULT_RAW = ("raw/**/*.txt",)
DEFAULT_MODELS_DIR = Path.home() / ".cache" / "wiki-cli" / "models"  # shared by every wiki
DEFAULT_SUMMARY_FIELDS = ("summary", "description")
DEFAULT_SUMMARY_HEADINGS = ("summary",)
DEFAULT_SEARCH_RESULTS = 3
MAX_SEARCH_RESULTS = 20  # search reranks 20 passages (search.RERANK_K), so it never returns more pages
_TOP_LEVEL = {"pages", "exclude", "raw", "embed_model", "reranker", "relations", "summary", "page_type",
              "check", "suggest", "search", "preset", "types", "guides", "code"}
_CODE_KEYS = {"repo", "origin"}
REDIRECT_KEY = "wiki"  # a config holding only this key says "the wiki for this folder is over there"
_TYPE_KEYS = {"description", "folder", "template"}


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class PageType:
    """A page type the wiki declares in `[types.<name>]`: what it is, where its pages live,
    and the template new pages start from."""
    name: str
    description: str = ""
    folder: str | None = None
    template: str | None = None


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
    search_results: int = DEFAULT_SEARCH_RESULTS  # pages returned by search, nav start and nav search
    preset: str | None = None  # the preset `wiki new` made the wiki from; picks guide variants
    types: tuple[PageType, ...] = ()  # declared page types; empty = any type is fine
    guides_dir: str | None = None  # folder of the wiki's own guides, overriding the built-in ones
    code_repo: Path | None = None  # the code a code wiki describes ([code] repo, or $WIKI_CODE_REPO)
    code_origin: str | None = None  # the code repo's origin URL, to tell a wrong pointer
    redirected_from: Path | None = field(default=None, compare=False)  # a code repo that pointed here
    vocabulary: Vocabulary = field(default_factory=Vocabulary, compare=False)
    root_note: str | None = field(default=None, compare=False)  # set when no config chose the root

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
        return repr((DERIVATION_VERSION, self.vocabulary.fingerprint(), self.page_type_field, self.type_folders))

    def summary_fingerprint(self) -> str:
        return repr((self.summary_fields, self.summary_headings))


def find_root(start: Path) -> Path | None:
    for directory in (start, *start.parents):
        if (directory / CONFIG_FILENAME).is_file():
            return directory
    return None


def implicit_root(cwd: Path) -> tuple[Path, str]:
    """Root when no config or ``--root`` names one: the enclosing git repo, else ``cwd``.
    The home folder and drive roots are refused; indexing them is never intended."""
    root, how = cwd, "the current folder"
    for directory in (cwd, *cwd.parents):
        if (directory / ".git").exists():
            root, how = directory, "the git repository"
            break
    if root == Path.home().resolve() or root == Path(root.anchor):
        raise ConfigError(f"no {CONFIG_FILENAME} found and {root} is not a wiki folder; "
                          "cd into the wiki, pass --root, or add a config (see 'wiki init')")
    return root, f"no {CONFIG_FILENAME} found; using {how} {root} as the wiki root"


def load_settings(
    root: str | os.PathLike | None = None,
    cache: str | os.PathLike | None = None,
    embed_model: str | None = None,
    reranker: str | None = None,
) -> Settings:
    """Root from ``--root``, then ``WIKI_ROOT``, then the nearest ``.wiki-cli.toml`` upward,
    then the enclosing git repository, then the current folder (never the home folder)."""
    root_value = root or os.environ.get("WIKI_ROOT")
    root_note = None
    if root_value:
        resolved = Path(root_value).expanduser().resolve()
    else:
        cwd = Path.cwd().resolve()
        resolved = find_root(cwd)
        if resolved is None:
            resolved, root_note = implicit_root(cwd)
    if not resolved.is_dir():
        raise ConfigError(f"wiki root does not exist: {resolved}")

    config = _read_config(Path(os.environ.get("WIKI_CONFIG") or resolved / CONFIG_FILENAME))
    redirected_from = None
    if REDIRECT_KEY in config:
        resolved, redirected_from, config = _follow_redirect(resolved, config), resolved, None
        config = _read_config(resolved / CONFIG_FILENAME)
        if REDIRECT_KEY in config:
            raise ConfigError(f"{resolved / CONFIG_FILENAME} redirects again; a redirect must name the wiki itself")
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
    results = _table(config, "search").get("results", DEFAULT_SEARCH_RESULTS)
    if isinstance(results, bool) or not isinstance(results, int) or not 1 <= results <= MAX_SEARCH_RESULTS:
        raise ConfigError(f"{CONFIG_FILENAME}: [search] results must be a whole number from 1 to {MAX_SEARCH_RESULTS}")
    folders = page_type.get("folders", {})
    if not isinstance(folders, dict) or not all(isinstance(v, str) for v in folders.values()):
        raise ConfigError(f"{CONFIG_FILENAME}: [page_type] folders must map folder paths to type names")
    types = _types(_table(config, "types"))
    by_folder = {k.strip("/"): v.strip().lower() for k, v in folders.items()}
    by_folder.update({page_type.folder: page_type.name for page_type in types if page_type.folder})
    type_folders = tuple(sorted(by_folder.items(), key=lambda pair: -len(pair[0])))
    preset = config.get("preset")
    if preset is not None and not isinstance(preset, str):
        raise ConfigError(f"{CONFIG_FILENAME}: 'preset' must be a string")
    guides_dir = _table(config, "guides").get("dir")
    if guides_dir is not None and not isinstance(guides_dir, str):
        raise ConfigError(f"{CONFIG_FILENAME}: [guides] dir must be a folder path")
    code = _table(config, "code")
    if set(code) - _CODE_KEYS or not all(isinstance(value, str) for value in code.values()):
        raise ConfigError(f"{CONFIG_FILENAME}: [code] takes 'repo' (a folder path) and 'origin' (a URL)")
    code_value = os.environ.get("WIKI_CODE_REPO") or code.get("repo")
    code_repo = (resolved / Path(code_value).expanduser()).resolve() if code_value else None

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
        search_results=results,
        preset=preset,
        types=types,
        guides_dir=guides_dir.strip("/") if guides_dir else None,
        code_repo=code_repo,
        code_origin=code.get("origin") or None,
        redirected_from=redirected_from,
        vocabulary=Vocabulary(rules),
        root_note=root_note,
    )


def _follow_redirect(folder: Path, config: dict) -> Path:
    """A code repo's `.wiki-cli.toml` holding only `wiki = "<path>"` points at its wiki."""
    if set(config) != {REDIRECT_KEY} or not isinstance(config[REDIRECT_KEY], str):
        raise ConfigError(f"{folder / CONFIG_FILENAME}: '{REDIRECT_KEY}' must be the only setting, "
                          "a path to the wiki")
    target = (folder / Path(config[REDIRECT_KEY]).expanduser()).resolve()
    if not (target / CONFIG_FILENAME).is_file():
        raise ConfigError(f"{folder / CONFIG_FILENAME} points at {target}, which is not a wiki "
                          f"(no {CONFIG_FILENAME} there)")
    return target


def _types(table: dict) -> tuple[PageType, ...]:
    found = []
    for name, entry in table.items():
        where = f"{CONFIG_FILENAME}: [types.{name}]"
        if not isinstance(entry, dict):
            raise ConfigError(f"{where} must be a table")
        unknown = set(entry) - _TYPE_KEYS
        if unknown:
            raise ConfigError(f"{where}: unknown key(s) {', '.join(sorted(unknown))}")
        if not all(isinstance(value, str) for value in entry.values()):
            raise ConfigError(f"{where}: {', '.join(sorted(_TYPE_KEYS))} must be strings")
        folder = entry.get("folder", "").strip().strip("/")
        found.append(PageType(name.strip().lower(), entry.get("description", "").strip(), folder or None,
                              entry.get("template", "").strip() or None))
    return tuple(found)


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
