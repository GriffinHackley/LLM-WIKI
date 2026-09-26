"""``wiki`` command-line entry point.

Exit codes: 0 success, 1 validation failures, 2 usage or runtime errors.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from wiki_cli import __version__, evaluate
from wiki_cli.cache import Cache, CacheUnavailable
from wiki_cli.config import ConfigError, Settings, load_settings
from wiki_cli.models import (
    EMBEDDING_MODELS,
    NO_RERANKER,
    RERANKERS,
    ModelUnavailable,
    download,
    load_embedder,
    load_reranker,
)
from wiki_cli.search import search
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
        settings = load_settings(args.wiki_root, args.space, args.cache,
                                 getattr(args, "embed_model", None), getattr(args, "reranker", None))
        return args.handler(args, settings)
    except (ConfigError, PageNotFound, CacheUnavailable, UsageError, ModelUnavailable, evaluate.EvalError) as exc:
        print(f"wiki: {exc}", file=sys.stderr)
        return EXIT_ERROR


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--wiki-root", help="wiki content directory (default: $LLM_WIKI_ROOT or ~/llm-wiki/wiki)")
    common.add_argument("--space", help="local wiki space (default: $LLM_WIKI_SPACE or wiki.toml name)")
    common.add_argument("--cache", help="cache database path (default: <repo>/.cache/wiki.sqlite3)")
    common.add_argument("--format", choices=("text", "json"), default="text")

    models = argparse.ArgumentParser(add_help=False)
    models.add_argument("--embed-model", help="embedding model (default: $WIKI_EMBED_MODEL or BAAI/bge-small-en-v1.5)")
    models.add_argument("--reranker", help="reranker model or 'none' (default: $WIKI_RERANKER or BAAI/bge-reranker-base)")

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

    search_parser = commands.add_parser("search", parents=[common, models], help="find the best pages for a question")
    search_parser.add_argument("question")
    search_parser.add_argument("--limit", type=_positive_int, default=3)
    search_parser.add_argument("--keyword-only", action="store_true", help="skip vector search and reranking")
    search_parser.set_defaults(handler=cmd_search)

    index = commands.add_parser("index", help="manage the derived cache").add_subparsers(title="index commands", metavar="<command>")
    for name, handler, help_text in (
        ("refresh", cmd_index_refresh, "index new, changed, moved, and deleted pages, then embed them"),
        ("rebuild", cmd_index_rebuild, "delete and recreate the cache"),
    ):
        sub = index.add_parser(name, parents=[common, models], help=help_text)
        sub.add_argument("--no-embed", action="store_true", help="skip embedding (keyword search still works)")
        sub.set_defaults(handler=handler)
    index.add_parser("status", parents=[common], help="report cache counts and staleness").set_defaults(handler=cmd_index_status)

    model_commands = commands.add_parser("models", help="embedding and reranker models").add_subparsers(
        title="models commands", metavar="<command>")
    model_commands.add_parser("list", parents=[common], help="list supported models").set_defaults(handler=cmd_models_list)
    fetch = model_commands.add_parser("download", parents=[common, models],
                                      help="download the configured models (the only command that downloads)")
    fetch.set_defaults(handler=cmd_models_download)

    eval_commands = commands.add_parser("eval", help="search-quality evaluation").add_subparsers(
        title="eval commands", metavar="<command>")
    eval_sample = eval_commands.add_parser("sample", parents=[common], help="pick pages to write evaluation questions about")
    eval_sample.add_argument("--single", type=int, default=40)
    eval_sample.add_argument("--multi", type=int, default=12)
    eval_sample.add_argument("--seed", type=int, default=1)
    eval_sample.set_defaults(handler=cmd_eval_sample)
    eval_run = eval_commands.add_parser("run", parents=[common, models], help="score search against a question file")
    eval_run.add_argument("questions", nargs="?", help="question file (default: <repo>/eval/questions.yaml)")
    eval_run.add_argument("--split", choices=evaluate.SPLITS + ("all",), default="test")
    eval_run.add_argument("--keyword-only", action="store_true")
    eval_run.set_defaults(handler=cmd_eval_run)

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
        stats = cache.refresh().to_dict()
        if not args.no_embed:
            stats.update(_embed(cache, settings, verbose=args.format == "text"))
    return _print_stats(args, stats)


def cmd_index_rebuild(args: argparse.Namespace, settings: Settings) -> int:
    with Cache(settings) as cache:
        stats = cache.rebuild().to_dict()
        if not args.no_embed:
            stats.update(_embed(cache, settings, verbose=args.format == "text"))
    return _print_stats(args, stats)


def _embed(cache: Cache, settings: Settings, *, verbose: bool) -> dict:
    """Embed pending pages. A missing model skips embedding instead of failing."""
    if cache.pending_embeddings() == 0 and cache.embed_model == settings.embed_model:
        return {"embedded": 0}

    def progress(done: int, total: int) -> None:
        if verbose:
            print(f"\rembedding {done}/{total} pages", end="", file=sys.stderr, flush=True)

    try:
        embedder = load_embedder(settings.embed_model, settings.models_dir)
        embedded = cache.embed_pending(embedder, progress)
    except ModelUnavailable as exc:
        return {"embedded": 0, "embedding_skipped": str(exc)}
    finally:
        if verbose:
            print(file=sys.stderr)
    return {"embedded": embedded}


# -- search ------------------------------------------------------------------

def cmd_search(args: argparse.Namespace, settings: Settings) -> int:
    with Cache(settings) as cache:
        cache.refresh()  # cheap when nothing changed; new pages become keyword-searchable
        embedder = None if args.keyword_only else load_embedder(settings.embed_model, settings.models_dir)
        reranker = None if args.keyword_only else load_reranker(settings.reranker, settings.models_dir)
        result = search(cache.conn, args.question, embedder=embedder, reranker=reranker,
                        embed_model=cache.embed_model, limit=args.limit)
        pending = cache.pending_embeddings() if embedder else 0
    notes = list(result.notes)
    if pending and "vector" in result.modes:
        notes.append(f"{pending} pages are not embedded yet; run 'wiki index refresh'")

    if args.format == "json":
        payload = {"results": [hit.to_dict() for hit in result.hits]}
        if notes:
            payload["notes"] = notes
        _print_json(payload)
        return EXIT_OK
    if not result.hits:
        print("no results")
    for number, hit in enumerate(result.hits, start=1):
        section = f"  § {hit.section}" if hit.section else ""
        print(f"{number}. {hit.slug}{section}  ({hit.score:.3f})")
        if hit.summary:
            print(f"   {hit.summary}{' [placeholder]' if hit.placeholder else ''}")
    for note in notes:
        print(f"note: {note}", file=sys.stderr)
    return EXIT_OK


# -- models ------------------------------------------------------------------

def cmd_models_list(args: argparse.Namespace, settings: Settings) -> int:
    payload = {
        "embedding": list(EMBEDDING_MODELS),
        "reranker": [*RERANKERS, NO_RERANKER],
        "configured": {"embed_model": settings.embed_model, "reranker": settings.reranker},
        "models_dir": str(settings.models_dir),
    }
    if args.format == "json":
        _print_json(payload)
    else:
        print("embedding models: " + ", ".join(payload["embedding"]))
        print("rerankers: " + ", ".join(payload["reranker"]))
        print(f"configured: {settings.embed_model} + {settings.reranker}")
        print(f"models folder: {settings.models_dir}")
    return EXIT_OK


def cmd_models_download(args: argparse.Namespace, settings: Settings) -> int:
    for name, is_reranker in ((settings.embed_model, False), (settings.reranker, True)):
        if args.format == "text":
            print(f"downloading {name} to {settings.models_dir}", file=sys.stderr)
        download(name, settings.models_dir, reranker=is_reranker)
    if args.format == "json":
        _print_json({"downloaded": [settings.embed_model, settings.reranker], "models_dir": str(settings.models_dir)})
    return EXIT_OK


# -- eval --------------------------------------------------------------------

def cmd_eval_sample(args: argparse.Namespace, settings: Settings) -> int:
    with Cache(settings) as cache:
        cache.refresh()
        _print_json(evaluate.sample(cache, single=args.single, multi=args.multi, seed=args.seed))
    return EXIT_OK


def cmd_eval_run(args: argparse.Namespace, settings: Settings) -> int:
    path = Path(args.questions) if args.questions else settings.repo_root / "eval" / "questions.yaml"
    questions = evaluate.load_questions(path)
    if args.split != "all":
        questions = [question for question in questions if question.split == args.split]
    if not questions:
        raise UsageError(f"no questions in split '{args.split}'")

    embed_model = None if args.keyword_only else settings.embed_model
    eval_settings = Settings(**{**settings.__dict__, "cache_path": evaluate.eval_cache_path(settings, embed_model)})
    with Cache(eval_settings) as cache:
        cache.refresh()
        embedder = reranker = None
        if not args.keyword_only:
            embedder = load_embedder(settings.embed_model, settings.models_dir)
            reranker = load_reranker(settings.reranker, settings.models_dir)
            if cache.pending_embeddings() or cache.embed_model != embedder.name:
                _embed_or_fail(cache, embedder, verbose=args.format == "text")
        summary = evaluate.run(cache, questions, embedder=embedder, reranker=reranker)

    summary = {"embed_model": embed_model or "none", "reranker": "none" if args.keyword_only else settings.reranker,
               "split": args.split, **summary}
    if args.format == "json":
        _print_json(summary)
    else:
        for key, value in summary.items():
            if key != "misses":
                print(f"{key}: {value}")
        for miss in summary["misses"]:
            print(f"miss {miss['id']}: top {', '.join(miss['top']) or '-'}")
    return EXIT_OK


def _embed_or_fail(cache: Cache, embedder, *, verbose: bool) -> None:
    def progress(done: int, total: int) -> None:
        if verbose:
            print(f"\rembedding {done}/{total} pages", end="", file=sys.stderr, flush=True)
    cache.embed_pending(embedder, progress)
    if verbose:
        print(file=sys.stderr)


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
