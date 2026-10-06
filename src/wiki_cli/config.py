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
              "check", "suggest", "search", "preset", "types", "guides", "code", "pending", "weekly", "records"}
_CODE_KEYS = {"repo", "origin"}
_WEEKLY_KEYS = {"folder", "template", "sections", "group_by"}
WEEKLY_SECTIONS = ("summary", "activity", "pages", "work", "sources", "questions", "health", "code")
WEEKLY_GROUPS = ("type", "module")
REDIRECT_KEY = "wiki"  # a config holding only this key says "the wiki for this folder is over there"
_TYPE_KEYS = {"description", "folder", "template"}
_TYPE_LISTS = {"sections", "fields"}
_TYPE_VALUES = "values"
_TYPE_HUB = "hub"
_TYPE_RECORD = "record"
_RECORDS_KEYS = {"recheck_days", "final"}
_GUIDES_KEYS = {"dir", "parts"}
DEFAULT_RECHECK_DAYS = 30
# Statuses after which a record rarely changes: `wiki stale` stops asking for a recheck.
DEFAULT_FINAL = ("done", "closed", "resolved", "merged", "released", "cancelled", "canceled", "rejected",
                 "won't do", "wont do", "won't fix", "wontfix", "duplicate")
DEFAULT_PARTS = "guides/parts"


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class PageType:
    """A page type the wiki declares in `[types.<name>]`: what it is, where its pages live,
    and the template new pages start from. `sections` and `fields` are what `wiki check`
    expects every page of the type to have: headings, and frontmatter keys. `values` limits
    frontmatter fields to the values listed for them. A `hub` type's pages each stand for
    an idea other pages gather around (a module, a concept): `wiki clusters` counts a
    cluster that links to one as covered. A `record` type's pages each describe an item
    that lives and changes in another system (a ticket in a tracker): they cite it by `key`
    and `url`, and `synced` says which version of it they reflect."""
    name: str
    description: str = ""
    folder: str | None = None
    template: str | None = None
    sections: tuple[str, ...] = ()
    fields: tuple[str, ...] = ()
    values: tuple[tuple[str, tuple[str, ...]], ...] = ()  # (field, allowed values)
    hub: bool = False
    record: bool = False


@dataclass(frozen=True)
class Weekly:
    """`[weekly]`: where weekly notes go, the template they start from, which generated
    sections they show, and what "where the work went" groups changed pages by."""
    folder: str = "weekly"
    template: str = "templates/weekly.md"
    sections: tuple[str, ...] = WEEKLY_SECTIONS
    group_by: str = "type"


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
    guides_parts: str = DEFAULT_PARTS  # folder of the wiki's guide parts, filling the guides' {{part:...}} slots
    records_recheck_days: int = DEFAULT_RECHECK_DAYS  # a record synced longer ago is due for a recheck
    records_final: tuple[str, ...] = DEFAULT_FINAL  # statuses (lowercased) of records that no longer change
    code_repo: Path | None = None  # the code a code wiki describes ([code] repo, or $WIKI_CODE_REPO)
    code_origin: str | None = None  # the code repo's origin URL, to tell a wrong pointer
    pending_ignore: tuple[str, ...] = ()  # files under raw/ that are not sources (`wiki pending` skips them)
    weekly: Weekly | None = None  # weekly notes, when [weekly] turns them on
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
    guides = _table(config, "guides")
    if set(guides) - _GUIDES_KEYS:
        raise ConfigError(f"{CONFIG_FILENAME}: [guides]: unknown key(s) {', '.join(sorted(set(guides) - _GUIDES_KEYS))}")
    guides_dir, parts = guides.get("dir"), guides.get("parts")
    for key, value in (("dir", guides_dir), ("parts", parts)):
        if value is not None and (not isinstance(value, str) or not value.strip("/ ")):
            raise ConfigError(f"{CONFIG_FILENAME}: [guides] {key} must be a folder path")
    if parts is None:
        parts = f"{guides_dir.strip('/')}/parts" if guides_dir else DEFAULT_PARTS
    recheck_days, final = _records(config)
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
        guides_parts=parts.strip("/ "),
        records_recheck_days=recheck_days,
        records_final=final,
        code_repo=code_repo,
        code_origin=code.get("origin") or None,
        pending_ignore=_patterns(_table(config, "pending"), "ignore", (), "[pending] ignore"),
        weekly=_weekly(config),
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


def _records(config: dict) -> tuple[int, tuple[str, ...]]:
    table = _table(config, "records")
    unknown = set(table) - _RECORDS_KEYS
    if unknown:
        raise ConfigError(f"{CONFIG_FILENAME}: [records]: unknown key(s) {', '.join(sorted(unknown))}")
    days = table.get("recheck_days", DEFAULT_RECHECK_DAYS)
    if isinstance(days, bool) or not isinstance(days, int) or days < 1:
        raise ConfigError(f"{CONFIG_FILENAME}: [records] recheck_days must be a whole number of days, 1 or more")
    final = tuple(status.strip().lower() for status in _patterns(table, "final", DEFAULT_FINAL, "[records] final"))
    return days, final


def _weekly(config: dict) -> Weekly | None:
    if "weekly" not in config:
        return None
    table = _table(config, "weekly")
    unknown = set(table) - _WEEKLY_KEYS
    if unknown:
        raise ConfigError(f"{CONFIG_FILENAME}: [weekly]: unknown key(s) {', '.join(sorted(unknown))}")
    for key in ("folder", "template", "group_by"):
        if key in table and (not isinstance(table[key], str) or not table[key].strip()):
            raise ConfigError(f"{CONFIG_FILENAME}: [weekly] {key} must be a non-empty string")
    sections = tuple(name.strip().lower() for name in _patterns(table, "sections", WEEKLY_SECTIONS,
                                                                 "[weekly] sections"))
    wrong = [name for name in sections if name not in WEEKLY_SECTIONS]
    if wrong:
        raise ConfigError(f"{CONFIG_FILENAME}: [weekly] sections: no section {', '.join(wrong)}; "
                          f"choose from {', '.join(WEEKLY_SECTIONS)}")
    group_by = table.get("group_by", "type").strip().lower()
    if group_by not in WEEKLY_GROUPS:
        raise ConfigError(f"{CONFIG_FILENAME}: [weekly] group_by must be {' or '.join(WEEKLY_GROUPS)}")
    return Weekly(folder=table.get("folder", "weekly").strip().strip("/"),
                  template=table.get("template", "templates/weekly.md").strip(), sections=sections,
                  group_by=group_by)


def _types(table: dict) -> tuple[PageType, ...]:
    found = []
    for name, entry in table.items():
        where = f"{CONFIG_FILENAME}: [types.{name}]"
        if not isinstance(entry, dict):
            raise ConfigError(f"{where} must be a table")
        unknown = set(entry) - _TYPE_KEYS - _TYPE_LISTS - {_TYPE_VALUES, _TYPE_HUB, _TYPE_RECORD}
        if unknown:
            raise ConfigError(f"{where}: unknown key(s) {', '.join(sorted(unknown))}")
        if not all(isinstance(entry[key], str) for key in _TYPE_KEYS & set(entry)):
            raise ConfigError(f"{where}: {', '.join(sorted(_TYPE_KEYS))} must be strings")
        for flag in (_TYPE_HUB, _TYPE_RECORD):
            if not isinstance(entry.get(flag, False), bool):
                raise ConfigError(f"{where}: {flag} must be true or false")
        lists = {key: _patterns(entry, key, (), f"[types.{name}] {key}") for key in _TYPE_LISTS}
        values = _table(entry, _TYPE_VALUES, f"types.{name}.{_TYPE_VALUES}")
        allowed = tuple((key.strip(), tuple(item.strip() for item in _patterns(
            values, key, (), f"[types.{name}.{_TYPE_VALUES}] {key}") if item.strip())) for key in values)
        folder = entry.get("folder", "").strip().strip("/")
        found.append(PageType(name.strip().lower(), entry.get("description", "").strip(), folder or None,
                              entry.get("template", "").strip() or None,
                              tuple(item.strip() for item in lists["sections"] if item.strip()),
                              tuple(item.strip() for item in lists["fields"] if item.strip()), allowed,
                              entry.get(_TYPE_HUB, False), entry.get(_TYPE_RECORD, False)))
    return tuple(found)


def _table(config: dict, key: str, label: str | None = None) -> dict:
    value = config.get(key, {})
    if not isinstance(value, dict):
        raise ConfigError(f"{CONFIG_FILENAME}: [{label or key}] must be a table")
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
