"""``wiki`` command-line entry point.

Exit codes: 0 success, 1 validation failures, 2 usage or runtime errors.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from wiki_cli import __version__, evaluate, init
from wiki_cli.cache import Cache, CacheUnavailable
from wiki_cli.config import CONFIG_FILENAME, ConfigError, Settings, load_settings
from wiki_cli.model import ERROR, WARNING, Issue
from wiki_cli.models import (
    EMBEDDING_MODELS,
    NO_RERANKER,
    RERANKERS,
    ModelUnavailable,
    download,
    load_embedder,
    load_reranker,
)
from wiki_cli.nav import DEFAULT_MAX_PAGES, NavError, Navigator
from wiki_cli.pages import PageNotFound, Resolver, load, resolve, scan_vault
from wiki_cli.search import search
from wiki_cli.suggest import suggest
from wiki_cli.validation import check_corpus, check_page, compare_cache

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
        settings = load_settings(args.root, args.cache,
                                 getattr(args, "embed_model", None), getattr(args, "reranker", None))
        return args.handler(args, settings)
    except (ConfigError, PageNotFound, CacheUnavailable, UsageError, ModelUnavailable, evaluate.EvalError) as exc:
        print(f"wiki: {exc}", file=sys.stderr)
        return EXIT_ERROR


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", help="wiki root (default: $WIKI_ROOT, else the nearest .wiki-cli.toml upward)")
    common.add_argument("--cache", help="cache database path (default: <root>/.cache/wiki.sqlite3)")
    common.add_argument("--format", choices=("text", "json"), default="text")

    models = argparse.ArgumentParser(add_help=False)
    models.add_argument("--embed-model", help="embedding model (default: $WIKI_EMBED_MODEL or .wiki-cli.toml)")
    models.add_argument("--reranker", help="reranker model or 'none' (default: $WIKI_RERANKER or .wiki-cli.toml)")

    parser = argparse.ArgumentParser(prog="wiki", description="Search and navigation tools for an Obsidian LLM wiki.")
    parser.add_argument("--version", action="version", version=f"wiki {__version__}")
    commands = parser.add_subparsers(title="commands", metavar="<command>")

    search_parser = commands.add_parser("search", parents=[common, models], help="find the best pages for a question")
    search_parser.add_argument("question")
    search_parser.add_argument("--limit", type=_positive_int, default=3)
    search_parser.add_argument("--include-raw", action="store_true", help="also search raw source text")
    search_parser.add_argument("--keyword-only", action="store_true", help="skip vector search and reranking")
    search_parser.set_defaults(handler=cmd_search)

    nav = commands.add_parser("nav", help="guided traversal: search, read sections, follow relations").add_subparsers(
        title="nav commands", metavar="<command>")
    nav_start = nav.add_parser("start", parents=[common, models], help="search and open a session")
    nav_start.add_argument("question")
    nav_start.add_argument("--max-pages", type=_positive_int, default=DEFAULT_MAX_PAGES)
    nav_start.add_argument("--limit", type=_positive_int, default=3)
    nav_start.set_defaults(handler=cmd_nav_start)
    nav_read = nav.add_parser("read", parents=[common, models], help="read the best section of a page")
    nav_read.add_argument("session")
    nav_read.add_argument("slug")
    nav_read.add_argument("--why", required=True, help="a few sentences: the open question and why this page")
    part = nav_read.add_mutually_exclusive_group()
    part.add_argument("--section", help="read this section instead of the best match")
    part.add_argument("--full", action="store_true", help="read the whole page")
    nav_read.set_defaults(handler=cmd_nav_read)
    nav_candidates = nav.add_parser("candidates", parents=[common, models], help="next pages to consider")
    nav_candidates.add_argument("session")
    nav_candidates.add_argument("--limit", type=_positive_int, default=8)
    nav_candidates.set_defaults(handler=cmd_nav_candidates)
    nav_search = nav.add_parser("search", parents=[common, models], help="search again for an open question")
    nav_search.add_argument("session")
    nav_search.add_argument("question")
    nav_search.add_argument("--limit", type=_positive_int, default=3)
    nav_search.set_defaults(handler=cmd_nav_search)
    nav_end = nav.add_parser("end", parents=[common, models], help="close the session, recording cited pages")
    nav_end.add_argument("session")
    nav_end.add_argument("--cited", default="", help="comma-separated slugs the answer cites")
    nav_end.set_defaults(handler=cmd_nav_end)
    nav_log = nav.add_parser("log", parents=[common, models], help="show a session's steps")
    nav_log.add_argument("session")
    nav_log.set_defaults(handler=cmd_nav_log)

    suggest_parser = commands.add_parser("suggest", parents=[common],
                                         help="pages a page names but does not link, shares sources with, or resembles")
    suggest_parser.add_argument("slug")
    suggest_parser.add_argument("--limit", type=_positive_int, default=10)
    suggest_parser.set_defaults(handler=cmd_suggest)

    unwritten = commands.add_parser("unwritten", parents=[common], help="link targets with no page, most-linked first")
    unwritten.add_argument("--limit", type=_positive_int, default=50)
    unwritten.set_defaults(handler=cmd_unwritten)

    orphans = commands.add_parser("orphans", parents=[common], help="pages nothing relates to")
    orphans.set_defaults(handler=cmd_orphans)

    neighbors = commands.add_parser("neighbors", parents=[common], help="a page's typed relations (no page bodies)")
    neighbors.add_argument("slug")
    neighbors.add_argument("--incoming", action="store_true")
    neighbors.add_argument("--outgoing", action="store_true")
    neighbors.add_argument("--relation", help="only this relation type (see 'wiki vocab')")
    neighbors.add_argument("--limit", type=_positive_int)
    neighbors.set_defaults(handler=cmd_neighbors)

    check = commands.add_parser("check", parents=[common], help="check pages for problems the tools rely on")
    group = check.add_mutually_exclusive_group(required=True)
    group.add_argument("target", nargs="?", help="slug or path")
    group.add_argument("--all", action="store_true", help="every indexed page")
    check.add_argument("--verify-cache", action="store_true", help="also verify the cache matches the files (with --all)")
    check.add_argument("--strict", action="store_true", help="fail on warnings too")
    check.add_argument("--no-warnings", action="store_true", help="hide warnings")
    check.set_defaults(handler=cmd_check)

    index = commands.add_parser("index", help="manage the derived cache").add_subparsers(title="index commands", metavar="<command>")
    for name, handler, help_text in (
        ("refresh", cmd_index_refresh, "index new, changed, moved, and deleted files, then embed them"),
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
    eval_run.add_argument("questions", nargs="?", help="question file (default: <root>/eval/questions.yaml)")
    eval_run.add_argument("--split", choices=evaluate.SPLITS + ("all",), default="test")
    eval_run.add_argument("--keyword-only", action="store_true")
    eval_run.set_defaults(handler=cmd_eval_run)

    vocab = commands.add_parser("vocab", parents=[common], help="list relation types")
    vocab.set_defaults(handler=cmd_vocab)

    init_parser = commands.add_parser("init", parents=[common],
                                      help=f"survey the wiki and draft a starter {CONFIG_FILENAME}")
    init_parser.add_argument("--write", action="store_true", help=f"create {CONFIG_FILENAME} (never overwrites)")
    init_parser.set_defaults(handler=cmd_init)
    return parser


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


# -- search ------------------------------------------------------------------

def cmd_search(args: argparse.Namespace, settings: Settings) -> int:
    with Cache(settings) as cache:
        cache.refresh()  # cheap when nothing changed; new pages become keyword-searchable
        embedder = None if args.keyword_only else load_embedder(settings.embed_model, settings.models_dir)
        reranker = None if args.keyword_only else load_reranker(settings.reranker, settings.models_dir)
        result = search(cache.conn, args.question, embedder=embedder, reranker=reranker,
                        embed_model=cache.embed_model, limit=args.limit, include_raw=args.include_raw)
        pending = cache.pending_embeddings() if embedder else 0
    notes = list(result.notes)
    if pending and "vector" in result.modes:
        notes.append(f"{pending} files are not embedded yet; run 'wiki index refresh'")

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
            print(f"   {hit.summary}")
    for note in notes:
        print(f"note: {note}", file=sys.stderr)
    return EXIT_OK


# -- nav ---------------------------------------------------------------------

def _navigate(args: argparse.Namespace, settings: Settings, step) -> int:
    """Run one navigation step. A refused step exits 1 with a stable error code."""
    with Cache(settings) as cache:
        navigator = Navigator(cache, embedder=load_embedder(settings.embed_model, settings.models_dir),
                              reranker=load_reranker(settings.reranker, settings.models_dir))
        try:
            result = step(navigator)
        except NavError as exc:
            if args.format == "json":
                _print_json({"error": exc.code, "message": str(exc)})
            else:
                print(f"wiki: {exc}", file=sys.stderr)
            return EXIT_INVALID
    if args.format == "json":
        _print_json(result)
    else:
        _print_nav_text(result)
    return EXIT_OK


def cmd_nav_start(args, settings):
    return _navigate(args, settings, lambda nav: nav.start(args.question, max_pages=args.max_pages, limit=args.limit))


def cmd_nav_read(args, settings):
    return _navigate(args, settings, lambda nav: nav.read(args.session, args.slug, args.why,
                                                          section=args.section, full=args.full))


def cmd_nav_candidates(args, settings):
    return _navigate(args, settings, lambda nav: nav.candidates(args.session, limit=args.limit))


def cmd_nav_search(args, settings):
    return _navigate(args, settings, lambda nav: nav.requery(args.session, args.question, limit=args.limit))


def cmd_nav_end(args, settings):
    cited = [slug.strip() for slug in args.cited.split(",") if slug.strip()]
    return _navigate(args, settings, lambda nav: nav.end(args.session, cited))


def cmd_nav_log(args, settings):
    return _navigate(args, settings, lambda nav: nav.log(args.session))


def _print_nav_text(result: dict) -> None:
    if "content" in result:
        print(f"== {result['slug']} § {result['section']}  ({result['pages_left']} pages left)")
        print(result["content"])
        print(f"-- sections: {'; '.join(result['sections'])}")
        return
    if "results" in result:
        print(f"session {result['session']}")
        for number, hit in enumerate(result["results"], start=1):
            section = f"  § {hit['section']}" if hit.get("section") else ""
            print(f"{number}. {hit['slug']}{section}")
            if hit.get("summary"):
                print(f"   {hit['summary']}")
        return
    if "linked" in result:
        for group in ("linked", "similar", "earlier"):
            for item in result[group]:
                detail = item.get("relation") or group
                reason = f" — {item['reason']}" if item.get("reason") else ""
                print(f"[{detail}] {item['slug']}{reason}")
                if item.get("summary"):
                    print(f"   {item['summary']}")
        if result.get("more_linked"):
            print(f"(+{result['more_linked']} more linked pages)")
        return
    print(json.dumps(result, ensure_ascii=False, indent=1))


# -- suggest / unwritten / orphans ---------------------------------------------

def cmd_suggest(args: argparse.Namespace, settings: Settings) -> int:
    with Cache(settings) as cache:
        if not cache.ensure_fresh(args.slug):
            raise PageNotFound(f"no indexed page '{args.slug}'")
        results = suggest(cache, args.slug, limit=args.limit)
    if args.format == "json":
        _print_json({"page": args.slug, "suggestions": results})
    else:
        if not results:
            print(f"{args.slug}: no suggestions")
        for item in results:
            print(f"{item['slug']}: {' '.join(item['reasons'])}")
    return EXIT_OK


def cmd_unwritten(args: argparse.Namespace, settings: Settings) -> int:
    with Cache(settings) as cache:
        cache.refresh()
        rows = cache.conn.execute(
            """SELECT target_slug, COUNT(DISTINCT source_slug), GROUP_CONCAT(DISTINCT source_slug)
               FROM relations WHERE resolved = 0 GROUP BY target_slug
               ORDER BY COUNT(DISTINCT source_slug) DESC, target_slug LIMIT ?""", (args.limit,)).fetchall()
    items = [{"target": target, "linked_from": sorted(sources.split(","))} for target, _, sources in rows]
    if args.format == "json":
        _print_json({"unwritten": items})
    else:
        for item in items:
            print(f"{item['target']} ({len(item['linked_from'])}): {', '.join(item['linked_from'])}")
    return EXIT_OK


def cmd_orphans(args: argparse.Namespace, settings: Settings) -> int:
    with Cache(settings) as cache:
        cache.refresh()
        rows = cache.conn.execute(
            """SELECT p.slug, p.path FROM pages p WHERE p.kind = 'page' AND NOT EXISTS (
                   SELECT 1 FROM relations r WHERE r.target_slug = p.slug AND r.resolved = 1
                   AND r.source_slug != p.slug AND r.relation_type != 'transcribes')
               ORDER BY p.path""").fetchall()
    if args.format == "json":
        _print_json({"orphans": [{"slug": slug, "path": path} for slug, path in rows]})
    else:
        for slug, path in rows:
            print(f"{slug}  ({path})")
    return EXIT_OK


# -- neighbors ---------------------------------------------------------------

def cmd_neighbors(args: argparse.Namespace, settings: Settings) -> int:
    if args.relation and args.relation not in settings.vocabulary.types:
        raise UsageError(f"unknown relation '{args.relation}'; see 'wiki vocab'")
    both = not args.incoming and not args.outgoing
    with Cache(settings) as cache:
        if not cache.ensure_fresh(args.slug):
            raise PageNotFound(f"no indexed page '{args.slug}'")
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
        arrow = "->" if entry["direction"] == "outgoing" else "<-"
        flag = " [not written]" if entry.get("unresolved") else ""
        print(f"{arrow} {entry['type']:<16} {entry['slug']}{flag}  {entry['reason']}")
    return EXIT_OK


# -- check -------------------------------------------------------------------

def cmd_check(args: argparse.Namespace, settings: Settings) -> int:
    scanned, others = scan_vault(settings)
    files = [page_file for page_file, _ in scanned]
    resolver = Resolver([(page_file.slug, page_file.rel) for page_file in files], others)
    if args.all:
        pages = [load(page_file, settings) for page_file in files]
        issues = check_corpus(pages, resolver)
        issues.extend(_cache_issues(pages, settings, resolver, verify=args.verify_cache))
        checked = sum(1 for page in pages if page.file.kind == "page")
    else:
        if args.verify_cache:
            raise UsageError("--verify-cache requires --all")
        page = load(resolve(args.target, settings), settings)
        issues = check_page(page, resolver)
        issues.extend(_cache_issues([page], settings, resolver, verify=False))
        checked = 1

    issues.sort(key=lambda issue: (issue.path or "", issue.severity != ERROR, issue.code, issue.message))
    errors = sum(1 for issue in issues if issue.severity == ERROR)
    warnings = len(issues) - errors
    shown = [issue for issue in issues if not (args.no_warnings and issue.severity == WARNING)]
    failed = errors > 0 or (args.strict and warnings > 0)

    if args.format == "json":
        _print_json({"ok": not failed, "pages": checked, "errors": errors, "warnings": warnings,
                     "issues": [issue.to_dict() for issue in shown]})
    else:
        for issue in shown:
            print(f"{issue.path or '-'}: {issue.severity} [{issue.code}] {issue.message}")
        print(f"{checked} pages checked: {errors} errors, {warnings} warnings")
    return EXIT_INVALID if failed else EXIT_OK


def _cache_issues(pages, settings: Settings, resolver: Resolver, *, verify: bool) -> list[Issue]:
    """Cache-derived checks: stale summaries always, full verification on request."""
    try:
        cache = Cache(settings, readonly=True)
    except CacheUnavailable as exc:
        return [Issue(ERROR, "cache-mismatch", str(exc))] if verify else []
    with cache:
        issues: list[Issue] = []
        if verify:
            issues.extend(compare_cache(pages, cache.snapshot(), resolver))
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


def cmd_index_status(args: argparse.Namespace, settings: Settings) -> int:
    with Cache(settings) as cache:
        return _print_stats(args, cache.status())


def _embed(cache: Cache, settings: Settings, *, verbose: bool) -> dict:
    """Embed pending files. A missing model skips embedding instead of failing."""
    if cache.pending_embeddings() == 0 and cache.embed_model == settings.embed_model:
        return {"embedded": 0}
    try:
        embedder = load_embedder(settings.embed_model, settings.models_dir)
        embedded = cache.embed_pending(embedder, _progress(verbose))
    except ModelUnavailable as exc:
        return {"embedded": 0, "embedding_skipped": str(exc)}
    finally:
        if verbose:
            print(file=sys.stderr)
    return {"embedded": embedded}


def _progress(verbose: bool):
    def report(done: int, total: int) -> None:
        if verbose:
            print(f"\rembedding {done}/{total} files", end="", file=sys.stderr, flush=True)
    return report


def _print_stats(args: argparse.Namespace, values: dict) -> int:
    if args.format == "json":
        _print_json(values)
    else:
        print(", ".join(f"{key.replace('_', ' ')}: {value}" for key, value in values.items()))
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
    path = Path(args.questions) if args.questions else settings.root / "eval" / "questions.yaml"
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
                cache.embed_pending(embedder, _progress(args.format == "text"))
                if args.format == "text":
                    print(file=sys.stderr)
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


# -- vocab -------------------------------------------------------------------

def cmd_vocab(args: argparse.Namespace, settings: Settings) -> int:
    vocabulary = settings.vocabulary
    types = []
    for name in vocabulary.types:
        entry = {"type": name, "inverse": vocabulary.inverse(name)}
        sources = [f"heading '{h}'" for rule in vocabulary.rules if rule.type == name for h in rule.headings]
        sources += [f"field '{rule.field}'" for rule in vocabulary.rules if rule.type == name and rule.field]
        entry["from"] = sources or (["block embeds"] if name == "embeds" else ["any other link"])
        types.append(entry)
    if args.format == "json":
        _print_json({"types": types})
    else:
        for entry in types:
            print(f"{entry['type']:<18} inverse: {entry['inverse']:<18} from: {'; '.join(entry['from'])}")
    return EXIT_OK


def cmd_init(args: argparse.Namespace, settings: Settings) -> int:
    result = init.survey(settings)
    draft = init.render(result)
    target = settings.root / CONFIG_FILENAME
    if args.format == "json":
        _print_json({**result, "config": draft})
        return EXIT_OK
    if args.write:
        if target.exists():
            raise UsageError(f"{target} already exists; not overwriting (run without --write to print a draft)")
        target.write_text(draft, encoding="utf-8")
        print(f"wrote {target}", file=sys.stderr)
    else:
        print(draft, end="")
    return EXIT_OK


def _print_json(payload: dict) -> None:
    print(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
