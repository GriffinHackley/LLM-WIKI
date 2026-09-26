"""``wiki`` command-line entry point.

Exit codes: 0 success, 1 validation failures, 2 usage or runtime errors.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from wiki_cli import __version__
from wiki_cli.cache import Cache, CacheUnavailable
from wiki_cli.config import ConfigError, Settings, load_settings
from wiki_cli.model import ERROR, WARNING, Issue
from wiki_cli.pages import Page, PageNotFound, discover, load, resolve
from wiki_cli.sync import sync_page
from wiki_cli.validation import SlugIndex, check_corpus, check_page, compare_cache
from wiki_cli.vocabulary import DERIVED_TYPES, INVERSE_LABELS, RELATION_TYPES, VOCABULARY_VERSION

EXIT_OK, EXIT_INVALID, EXIT_ERROR = 0, 1, 2


class UsageError(Exception):
    pass


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "handler"):
        parser.print_help()
        return EXIT_ERROR
    try:
        settings = load_settings(args.wiki_root, args.space, args.cache)
        return args.handler(args, settings)
    except (ConfigError, PageNotFound, CacheUnavailable, UsageError) as exc:
        print(f"wiki: {exc}", file=sys.stderr)
        return EXIT_ERROR


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--wiki-root", help="wiki content directory (default: $LLM_WIKI_ROOT or ~/llm-wiki/wiki)")
    common.add_argument("--space", help="local wiki space (default: $LLM_WIKI_SPACE or wiki.toml name)")
    common.add_argument("--cache", help="cache database path (default: <repo>/.cache/wiki.sqlite3)")
    common.add_argument("--format", choices=("text", "json"), default="text")

    parser = argparse.ArgumentParser(prog="wiki", description="Read-side tooling for llm-wiki.")
    parser.add_argument("--version", action="version", version=f"wiki {__version__}")
    commands = parser.add_subparsers(title="commands", metavar="<command>")

    rel = commands.add_parser("rel", help="typed page relations").add_subparsers(title="rel commands", metavar="<command>")
    sync = rel.add_parser("sync", parents=[common], help="regenerate the managed link block from frontmatter")
    _add_target(sync)
    sync.add_argument("--dry-run", action="store_true", help="report changes without writing")
    sync.set_defaults(handler=cmd_sync)

    neighbors = rel.add_parser("neighbors", parents=[common], help="list a page's related pages (routing metadata only)")
    neighbors.add_argument("slug")
    neighbors.add_argument("--incoming", action="store_true")
    neighbors.add_argument("--outgoing", action="store_true")
    neighbors.add_argument("--relation", choices=RELATION_TYPES + DERIVED_TYPES)
    neighbors.add_argument("--limit", type=_positive_int)
    neighbors.set_defaults(handler=cmd_neighbors)

    check = commands.add_parser("check", parents=[common], help="validate relations, summaries, and link blocks")
    _add_target(check)
    check.add_argument("--verify-cache", action="store_true", help="also verify the cache matches frontmatter (with --all)")
    check.add_argument("--strict", action="store_true", help="fail on warnings too")
    check.add_argument("--no-warnings", action="store_true", help="hide warnings")
    check.add_argument("--require-summary", action="store_true", help="treat a missing summary as an error")
    check.set_defaults(handler=cmd_check)

    index = commands.add_parser("index", help="manage the derived cache").add_subparsers(title="index commands", metavar="<command>")
    for name, handler, help_text in (
        ("refresh", cmd_index_refresh, "index new, changed, moved, and deleted pages"),
        ("rebuild", cmd_index_rebuild, "delete and recreate the cache"),
        ("status", cmd_index_status, "report cache counts and staleness"),
    ):
        index.add_parser(name, parents=[common], help=help_text).set_defaults(handler=handler)

    vocab = commands.add_parser("vocab", parents=[common], help="list relation types")
    vocab.set_defaults(handler=cmd_vocab)
    return parser


def _add_target(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("target", nargs="?", help="slug, wiki:// URI, or .md path")
    group.add_argument("--all", action="store_true", help="every page in the wiki")


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


# -- rel sync ----------------------------------------------------------------

def cmd_sync(args: argparse.Namespace, settings: Settings) -> int:
    if args.all:
        pages = [page for page in _load_all(settings) if page.has_frontmatter]
    else:
        pages = [load(resolve(args.target, settings), settings.space)]

    updated: list[str] = []
    unchanged = 0
    issues: list[Issue] = []
    for page in pages:
        result = sync_page(page, settings.space, dry_run=args.dry_run)
        issues.extend(_with_page(issue, page) for issue in result.issues)
        if result.changed:
            updated.append(page.file.rel)
        elif result.ok:
            unchanged += 1

    warning = None
    if updated and not args.dry_run:
        warning = _update_cache(settings, pages if not args.all else None)

    if args.format == "json":
        payload = {"updated": updated, "unchanged": unchanged}
        if args.dry_run:
            payload["dry_run"] = True
        if issues:
            payload["issues"] = [issue.to_dict() for issue in issues]
        if warning:
            payload["cache_warning"] = warning
        _print_json(payload)
    else:
        verb = "would update" if args.dry_run else "updated"
        for rel in updated:
            print(f"{verb} {rel}")
        _print_issues(issues)
        if warning:
            print(f"warning: {warning}", file=sys.stderr)
        print(f"{len(updated)} {verb}, {unchanged} unchanged, {sum(1 for i in issues if i.severity == ERROR)} failed")
    return EXIT_INVALID if any(issue.severity == ERROR for issue in issues) else EXIT_OK


def _update_cache(settings: Settings, pages: list[Page] | None) -> str | None:
    """Refresh the cache after writes; a cache failure never fails a sync."""
    try:
        with Cache(settings) as cache:
            if pages is None or cache.needs_full_refresh:
                cache.refresh()
            else:
                for page in pages:
                    cache.refresh_file(page.file)
    except Exception as exc:  # noqa: BLE001 - the cache is disposable
        return f"cache not updated ({exc}); run 'wiki index refresh'"
    return None


# -- rel neighbors -----------------------------------------------------------

def cmd_neighbors(args: argparse.Namespace, settings: Settings) -> int:
    both = not args.incoming and not args.outgoing
    with Cache(settings) as cache:
        if not cache.ensure_fresh(args.slug):
            raise PageNotFound(f"no page with slug '{args.slug}'")
        results = cache.neighbors(
            args.slug,
            outgoing=both or args.outgoing,
            incoming=both or args.incoming,
            relation_type=args.relation,
            limit=args.limit,
        )
    if args.format == "json":
        _print_json({"page": args.slug, "neighbors": results})
        return EXIT_OK
    if not results:
        print(f"{args.slug}: no related pages")
    for entry in results:
        if entry["direction"] == "outgoing":
            flag = " [external]" if entry.get("external") else " [unresolved]" if entry.get("unresolved") else ""
            print(f"-> {entry['type']:<15} {entry['slug']}{flag}  {entry['reason']}")
        else:
            print(f"<- {entry['inverse']:<15} {entry['slug']}  {entry['reason']}")
    return EXIT_OK


# -- check -------------------------------------------------------------------

def cmd_check(args: argparse.Namespace, settings: Settings) -> int:
    if args.all:
        pages = [page for page in _load_all(settings) if page.has_frontmatter or not settings.skip_no_frontmatter]
        slugs = SlugIndex.build([page.file for page in pages])
        issues = check_corpus(pages, slugs, settings.space)
        if args.require_summary:
            issues = [_escalate_summary(issue) for issue in issues]
        issues.extend(_cache_issues(pages, settings, verify=args.verify_cache))
        checked = len(pages)
    else:
        if args.verify_cache:
            raise UsageError("--verify-cache requires --all")
        slugs = SlugIndex.build(discover(settings))
        page = load(resolve(args.target, settings), settings.space)
        issues = check_page(page, slugs, settings.space, require_summary=args.require_summary)
        issues.extend(_cache_issues([page], settings, verify=False))
        checked = 1

    issues.sort(key=lambda issue: (issue.path or "", issue.severity != ERROR, issue.code, issue.relation or -1))
    errors = sum(1 for issue in issues if issue.severity == ERROR)
    warnings = len(issues) - errors
    shown = [issue for issue in issues if not (args.no_warnings and issue.severity == WARNING)]
    failed = errors > 0 or (args.strict and warnings > 0)

    if args.format == "json":
        _print_json({
            "ok": not failed,
            "pages": checked,
            "errors": errors,
            "warnings": warnings,
            "issues": [issue.to_dict() for issue in shown],
        })
    else:
        _print_issues(shown)
        print(f"{checked} pages checked: {errors} errors, {warnings} warnings")
    return EXIT_INVALID if failed else EXIT_OK


def _escalate_summary(issue: Issue) -> Issue:
    if issue.code != "missing-summary":
        return issue
    return Issue(ERROR, issue.code, issue.message, issue.path, issue.slug, issue.relation)


def _cache_issues(pages: list[Page], settings: Settings, *, verify: bool) -> list[Issue]:
    """Cache-derived checks: stale summaries always, full verification on request."""
    try:
        cache = Cache(settings, readonly=True)
    except CacheUnavailable as exc:
        return [Issue(ERROR, "cache-mismatch", str(exc))] if verify else []
    with cache:
        issues: list[Issue] = []
        if verify:
            issues.extend(compare_cache(pages, cache.snapshot(), settings.space))
        stale = cache.stale_summaries()
        for page in pages:
            if stale.get(page.file.rel) == page.content_hash:
                issues.append(Issue(WARNING, "summary-stale", "body changed but summary did not",
                                    path=page.file.rel, slug=page.slug))
        return issues


# -- index -------------------------------------------------------------------

def cmd_index_refresh(args: argparse.Namespace, settings: Settings) -> int:
    with Cache(settings) as cache:
        stats = cache.refresh()
    return _print_stats(args, stats.to_dict())


def cmd_index_rebuild(args: argparse.Namespace, settings: Settings) -> int:
    with Cache(settings) as cache:
        stats = cache.rebuild()
    return _print_stats(args, stats.to_dict())


def cmd_index_status(args: argparse.Namespace, settings: Settings) -> int:
    with Cache(settings) as cache:
        status = cache.status()
    return _print_stats(args, status)


def _print_stats(args: argparse.Namespace, values: dict) -> int:
    if args.format == "json":
        _print_json(values)
    else:
        print(", ".join(f"{key.replace('_', ' ')}: {value}" for key, value in values.items()))
    return EXIT_OK


# -- vocab -------------------------------------------------------------------

def cmd_vocab(args: argparse.Namespace, settings: Settings) -> int:
    types = [{"type": name, "inverse": INVERSE_LABELS[name]} for name in RELATION_TYPES]
    derived = [{"type": name, "inverse": INVERSE_LABELS[name], "from": "superseded_by"} for name in DERIVED_TYPES]
    if args.format == "json":
        _print_json({"version": VOCABULARY_VERSION, "types": types, "derived": derived})
    else:
        for entry in types:
            print(f"{entry['type']:<15} inverse: {entry['inverse']}")
        for entry in derived:
            print(f"{entry['type']:<15} inverse: {entry['inverse']}  (read from superseded_by)")
    return EXIT_OK


# -- helpers -----------------------------------------------------------------

def _load_all(settings: Settings) -> list[Page]:
    return [load(page_file, settings.space) for page_file in discover(settings)]


def _with_page(issue: Issue, page: Page) -> Issue:
    return Issue(issue.severity, issue.code, issue.message, page.file.rel, page.slug, issue.relation)


def _print_issues(issues: list[Issue]) -> None:
    for issue in issues:
        location = issue.path or "-"
        print(f"{location}: {issue.severity} [{issue.code}] {issue.message}")


def _print_json(payload: dict) -> None:
    print(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
