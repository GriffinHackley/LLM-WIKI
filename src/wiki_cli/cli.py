"""``wiki`` command-line entry point.

Exit codes: 0 success, 1 validation failures, 2 usage or runtime errors.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import textwrap
from pathlib import Path
from typing import Sequence

from wiki_cli import (
    __version__,
    audit,
    clusters,
    codebase,
    evaluate,
    guide,
    init,
    output,
    records,
    scaffold,
    sources,
    weekly,
)
from wiki_cli.cache import Cache, CacheUnavailable
from wiki_cli.config import CONFIG_FILENAME, ConfigError, Settings, load_settings
from wiki_cli.model import ERROR, WARNING, Issue
from wiki_cli.models import (
    EMBEDDING_MODELS,
    is_downloaded,
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
WORKFLOWS = ("ingest", "query", "lint", "sync")  # guides an agent may mistake for commands


class UsageError(Exception):
    pass


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in WORKFLOWS:
        _print_error(f"there is no '{argv[0]}' command. `wiki guide {argv[0]}` prints the steps of that "
                     "workflow for you, the agent, to carry out yourself with your own tools.")
        return EXIT_ERROR
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "handler"):
        parser.print_help()
        return EXIT_ERROR
    try:
        if args.handler is cmd_new:
            return cmd_new(args)
        if args.handler is cmd_guide:
            return cmd_guide(args, _optional_settings(args))
        settings = load_settings(args.root, args.cache,
                                 getattr(args, "embed_model", None), getattr(args, "reranker", None))
        if settings.root_note:
            output.note(settings.root_note)
        return args.handler(args, settings)
    except (ConfigError, PageNotFound, CacheUnavailable, UsageError, ModelUnavailable, evaluate.EvalError,
            guide.GuideError, scaffold.ScaffoldError, weekly.WeeklyError) as exc:
        _print_error(str(exc))
        return EXIT_ERROR


def _print_error(message: str) -> None:
    prefix = output.style("wiki:", "red", "bold", stream=sys.stderr)
    print(output.wrap(message, prefix + " ", "  ", stream=sys.stderr), file=sys.stderr)


COMMAND_ORDER = ("new", "init", "guide", "search", "nav", "list", "neighbors", "suggest", "check", "unwritten",
                 "orphans", "clusters", "pending", "stale", "weekly", "index", "models", "vocab", "eval")


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", help="wiki root (default: $WIKI_ROOT, else the nearest .wiki-cli.toml upward, "
                        "else the enclosing git repository, else the current folder)")
    common.add_argument("--cache", help="cache database path (default: <root>/.cache/wiki.sqlite3)")
    common.add_argument("--format", choices=("text", "json"), default="text")

    models = argparse.ArgumentParser(add_help=False)
    models.add_argument("--embed-model", help="embedding model (default: $WIKI_EMBED_MODEL or .wiki-cli.toml)")
    models.add_argument("--reranker", help="reranker model or 'none' (default: $WIKI_RERANKER or .wiki-cli.toml)")

    parser = argparse.ArgumentParser(
        prog="wiki",
        description="Start and run an LLM-maintained wiki with any agent: presets, workflow guides, search, typed "
                    "relations and guided navigation.",
        epilog="Run 'wiki <command> --help' for a command's options. New here? 'wiki new my-wiki', or "
               "'wiki new .' in a folder of notes you already keep.")
    parser.add_argument("--version", action="version", version=f"wiki {__version__}")
    commands = parser.add_subparsers(title="commands", metavar="<command>")

    search_parser = commands.add_parser("search", parents=[common, models], help="find the best pages for a question")
    search_parser.add_argument("question")
    search_parser.add_argument("--limit", type=_positive_int, help="pages to return (default: [search] results in .wiki-cli.toml, else 3)")
    search_parser.add_argument("--include-raw", action="store_true", help="also search raw source text")
    search_parser.add_argument("--keyword-only", action="store_true", help="skip vector search and reranking")
    search_parser.set_defaults(handler=cmd_search)

    nav = commands.add_parser("nav", help="guided traversal: search, read sections, follow relations").add_subparsers(
        title="nav commands", metavar="<command>")
    nav_start = nav.add_parser("start", parents=[common, models], help="search and open a session")
    nav_start.add_argument("question")
    nav_start.add_argument("--max-pages", type=_positive_int, default=DEFAULT_MAX_PAGES)
    nav_start.add_argument("--limit", type=_positive_int, help="pages to return (default: [search] results in .wiki-cli.toml, else 3)")
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
    nav_search.add_argument("--limit", type=_positive_int, help="pages to return (default: [search] results in .wiki-cli.toml, else 3)")
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
    clusters_parser = commands.add_parser(
        "clusters", parents=[common],
        help="groups of densely linked pages that no hub page (a module, a concept) covers")
    clusters_parser.add_argument("--all", action="store_true", help="also list clusters a hub page covers")
    clusters_parser.add_argument("--min-size", type=_positive_int, default=clusters.MIN_CLUSTER,
                                 help=f"smallest cluster listed (default {clusters.MIN_CLUSTER})")
    clusters_parser.set_defaults(handler=cmd_clusters)

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
    check.add_argument("--ingested", action="store_true",
                       help="for a source page: check the whole ingest (original linked, pages cite it, committed)")
    check.add_argument("--summary-ok", action="store_true",
                       help="record that the page's summary still fits its body (clears summary-stale)")
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

    new = commands.add_parser("new", help="create a wiki from a preset, or add what is missing (never overwrites)")
    new.add_argument("folder", nargs="?", default=".", help="the wiki's folder (default: the current folder)")
    new.add_argument("--preset", default=scaffold.DEFAULT_PRESET,
                     help=f"{' or '.join(scaffold.presets())}, or a preset folder (default: {scaffold.DEFAULT_PRESET})")
    new.add_argument("--agent", choices=scaffold.AGENTS, action="append",
                     help="also write this agent's adapter files (slash commands for the workflows); repeatable")
    new.add_argument("--code", help="code preset: the top folder of the code's git repository (required)")
    new.add_argument("--git-hook", action="store_true",
                     help="install a pre-commit hook: no edits to raw/, and 'wiki check --all' must pass")
    new.add_argument("--format", choices=("text", "json"), default="text")
    new.set_defaults(handler=cmd_new)

    guide_parser = commands.add_parser("guide", parents=[common], help="print a workflow's steps for an agent")
    guide_parser.add_argument("name", nargs="?", help="the workflow (omit to list them)")
    guide_parser.add_argument("extra", nargs="*", help=argparse.SUPPRESS)
    guide_parser.add_argument("--parts", action="store_true",
                              help="list the guides' part slots, and which ones this wiki fills with its own text")
    guide_parser.set_defaults(handler=cmd_guide)

    stale = commands.add_parser("stale", parents=[common],
                                help="pages whose code changed since they were verified, and records due for a "
                                     "recheck")
    stale.set_defaults(handler=cmd_stale)

    pending_parser = commands.add_parser("pending", parents=[common],
                                         help="sources in raw/ that no page links to yet")
    pending_parser.set_defaults(handler=cmd_pending)

    weekly_parser = commands.add_parser("weekly", parents=[common],
                                        help="write a note for each finished week of work on the wiki, and a timeline")
    weekly_parser.add_argument("--week", help="only this week (2026-W40), rewriting its generated part; "
                                              "'current' shows this week so far without writing it")
    weekly_parser.add_argument("--hook", action="store_true",
                               help="for the pre-commit hook: stage what it writes, and do nothing when [weekly] "
                                    "is not in the config")
    weekly_parser.set_defaults(handler=cmd_weekly)

    list_parser = commands.add_parser("list", parents=[common], help="every page, with its type and summary")
    list_parser.add_argument("--type", dest="page_type", help="only pages of this type")
    list_parser.set_defaults(handler=cmd_list)

    vocab = commands.add_parser("vocab", parents=[common], help="list relation types")
    vocab.set_defaults(handler=cmd_vocab)

    init_parser = commands.add_parser("init", parents=[common],
                                      help=f"survey the wiki and draft a starter {CONFIG_FILENAME}")
    init_parser.add_argument("--write", action="store_true", help=f"create {CONFIG_FILENAME} (never overwrites)")
    init_parser.add_argument("--preset", help=f"also adopt a preset's page types, relation rules and weekly notes "
                                              f"({' or '.join(scaffold.presets())}, or a preset folder); with a "
                                              f"{CONFIG_FILENAME} already there, print only those to merge in")
    init_parser.set_defaults(handler=cmd_init)
    # Listed by task in --help: setting up, finding, checking, then the cache and models.
    commands._choices_actions.sort(key=lambda action: COMMAND_ORDER.index(action.dest))
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
                        embed_model=cache.embed_model, limit=args.limit or settings.search_results,
                        include_raw=args.include_raw)
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
        print("No results.")
    for number, hit in enumerate(result.hits, start=1):
        _print_hit(number, hit.slug, hit.section, hit.summary, score=hit.score)
    for note in notes:
        output.note(note)
    return EXIT_OK


def _print_hit(number: int, slug: str, section: str | None, summary: str | None, *,
               score: float | None = None) -> None:
    where = f"  {output.dim('§ ' + section)}" if section else ""
    points = f"  {output.dim(f'({score:.3f})')}" if score is not None else ""
    print(f"{number}. {output.bold(slug)}{where}{points}")
    if summary:
        print(output.wrap(summary, "   "))


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
                _print_error(str(exc))
            return EXIT_INVALID
    if args.format == "json":
        _print_json(result)
    else:
        _print_nav_text(result)
    return EXIT_OK


def cmd_nav_start(args, settings):
    return _navigate(args, settings, lambda nav: nav.start(args.question, max_pages=args.max_pages,
                                                               limit=args.limit or settings.search_results))


def cmd_nav_read(args, settings):
    return _navigate(args, settings, lambda nav: nav.read(args.session, args.slug, args.why,
                                                          section=args.section, full=args.full))


def cmd_nav_candidates(args, settings):
    return _navigate(args, settings, lambda nav: nav.candidates(args.session, limit=args.limit))


def cmd_nav_search(args, settings):
    return _navigate(args, settings, lambda nav: nav.requery(args.session, args.question,
                                                                 limit=args.limit or settings.search_results))


def cmd_nav_end(args, settings):
    cited = [slug.strip() for slug in args.cited.split(",") if slug.strip()]
    return _navigate(args, settings, lambda nav: nav.end(args.session, cited))


def cmd_nav_log(args, settings):
    return _navigate(args, settings, lambda nav: nav.log(args.session))


def _print_nav_text(result: dict) -> None:
    session = result.get("session", "")
    if "content" in result:  # nav read
        left = output.plural(result["pages_left"], "page")
        print(f"{output.bold(result['slug'])}  {output.dim('§ ' + result['section'])}  {output.dim(f'({left} left)')}")
        print()
        print(result["content"].rstrip())
        print()
        print(output.wrap(" · ".join(result["sections"]), output.dim("Sections: ")))
        return
    if "results" in result:  # nav start, nav search
        limit = f": up to {output.plural(result['max_pages'], 'page')}" if result.get("max_pages") else ""
        print(f"{output.heading('Session ' + session)}{limit}")
        for number, hit in enumerate(result["results"], start=1):
            _print_hit(number, hit["slug"], hit.get("section"), hit.get("summary"))
        if result["results"]:
            print(output.dim(f"Next: wiki nav read {session} <slug> --why \"<the open question>\""))
        return
    if "linked" in result:  # nav candidates
        groups = (("linked", "Linked from " + result.get("from", "this page")), ("similar", "Similar"),
                  ("earlier", "Seen earlier"))
        shown = False
        for key, title in groups:
            items = result[key]
            if not items:
                continue
            print(("\n" if shown else "") + output.heading(title, len(items)))
            shown = True
            for item in items:
                tags = output.dim(" · ".join(tag for tag in (item.get("relation"), item.get("type")) if tag))
                print(f"  {output.bold(item['slug'])}" + (f"  {tags}" if tags else ""))
                if item.get("reason"):
                    print(output.wrap(item["reason"], "    "))
                if item.get("summary"):
                    print(output.dim(output.wrap(item["summary"], "    ")))
        if result.get("more_linked"):
            print(output.dim(f"  and {result['more_linked']} more linked pages (raise --limit to see them)"))
        if not shown:
            print("No pages to go to from here.")
        if "pages_left" in result:
            print(output.dim(f"\n{output.plural(result['pages_left'], 'page')} left to read"))
        return
    if "pages_read" in result:  # nav end
        print(output.heading(f"Session {session} ended"))
        for label, slugs in (("Read", result["pages_read"]), ("Cited", result["cited"])):
            print(output.wrap(", ".join(slugs) or "nothing", f"  {label + ':':<7}"))
        for key, label in (("cited_without_reading", "cited without reading"), ("unknown", "unknown pages")):
            if result.get(key):
                print(f"{output.style('warning:', 'yellow')} {label}: {', '.join(result[key])}")
        return
    if "events" in result:  # nav log
        state = " (ended)" if result["ended"] else ""
        print(output.heading(f"Session {session}{state}"))
        print(output.wrap(result["question"], "  "))
        items = []
        for event in result["events"]:
            detail = "; ".join(f"{key}: {', '.join(map(str, value)) if isinstance(value, list) else value}"
                               for key, value in event.items() if key not in ("kind", "slug"))
            items.append((event["kind"], event.get("slug", ""), detail))
        for line in output.rows(items, styles=((), ("bold",), ("dim",))):
            print(line)
        return
    print(json.dumps(result, ensure_ascii=False, indent=1))


# -- new / guide / list ----------------------------------------------------------

def cmd_new(args: argparse.Namespace) -> int:
    result = scaffold.scaffold(Path(args.folder), args.preset, args.agent, git_hook=args.git_hook,
                               code=Path(args.code) if args.code else None)
    if args.format == "json":
        _print_json(result)
        return EXIT_OK
    _print_new(result)
    return EXIT_OK


# What each file `wiki new` writes is for, shown beside it.
_NEW_FILES = {
    "AGENTS.md": "how agents work on the wiki",
    "CLAUDE.md": "points Claude Code at AGENTS.md",
    ".claude/settings.json": "lets Claude Code run wiki; blocks edits in raw/",
    ".github/copilot-instructions.md": "points GitHub Copilot at the agent instructions",
    ".gitignore": "keeps the cache out of git",
    ".gitattributes": "keeps the hook's line endings",
    ".githooks/pre-commit": "checks the wiki before each commit",
    "raw/.gitkeep": "sources go here",
    "wiki/open-questions.md": "contradictions and gaps to chase",
}


def _print_new(result: dict) -> None:
    """`wiki new` for a person: what it did, a list per kind of change with what each
    file is for, the lines to add by hand, and the next steps."""
    established, adopted, preset = result["established"], result["adopted"], result["preset"]
    if adopted:
        print(f"{output.bold('Existing wiki:')} {result['root']} ({output.plural(adopted, 'page')})")
        print(f"Added the {preset} workflows. Your pages are unchanged.")
    elif established:
        print(f"{output.bold('Wiki:')} {result['root']}")
        print(f"Added what was missing for the {preset} workflows.")
    else:
        print(f"{output.bold(f'New {preset} wiki:')} {result['root']}")
    if result["git_init"]:
        print("Started a git repository.")

    descriptions = dict(_NEW_FILES)
    descriptions[CONFIG_FILENAME] = ("drafted from your pages: review it" if adopted else "settings: page types, "
                                     "relations, search")
    _print_files("Created", result["created"], descriptions)
    _print_files("Kept (already there)", result["kept"], {})
    if result["skipped"]:
        print("\n" + output.wrap(f"Not added: the preset's {len(result['skipped'])} starter pages and templates "
                                 "(your wiki keeps its own)."))

    notes = list(result["notes"])
    if result.get("preset_types"):
        notes.append(f"The {preset} preset's page types ({', '.join(result['preset_types'])}) are not in "
                     f"{CONFIG_FILENAME}. 'wiki init --preset {preset}' prints them, with its relation rules, "
                     "to merge in if you want them.")
    if notes:
        print("\n" + output.heading("Notes"))
        for note in notes:
            print(output.wrap(note[:1].upper() + note[1:], "  - ", "    "))

    for item in result["add"]:
        print("\n" + output.heading(f"Add to {item['file']}:"))
        _print_block(item["text"])
    if result.get("code_setup"):
        print("\n" + output.heading(f"In the code repo ({result['code_repo']}):"))
        for item in result["code_setup"]:
            print("\n" + output.wrap(f"Add to {item['file']} ({item['why']}):", "  "))
            _print_block(item["text"], indent="      ")

    steps = []
    if adopted:
        steps.append((f"Review {CONFIG_FILENAME}", "which files are pages, summaries, relation rules"))
    if result["add"]:
        steps.append(("Add the lines above", "to the files named"))
    if result["models_missing"]:
        steps.append(("wiki models download", "once per machine, about 0.2 GB; search is keyword-only until then"))
    if established:
        steps.append(("wiki index refresh", "index and embed the pages, about 2 minutes per 500 on CPU"))
        steps.append(("wiki check --all", "see what the checks find in the pages as they are"))
    if result.get("code_repo"):
        steps.append(("Add the code repo lines above", "so the agent working on the code keeps the wiki"))
        steps.append(("Ask your agent", "from the code repo, to document a module ('wiki guide ingest') and to sync "
                                        "the wiki after code changes ('wiki guide sync')"))
    else:
        steps.append(("Ask your agent", "to ingest a source you put in raw/, or to answer a question from the "
                                        "wiki; 'wiki guide' lists the workflows"))
    print("\n" + output.heading("Next"))
    for line in output.rows([(f"{number}. {step}", detail) for number, (step, detail) in enumerate(steps, start=1)],
                            max_key=40):
        print(line)


def _print_files(heading: str, paths: list[str], descriptions: dict[str, str]) -> None:
    """Files under a heading, one per line with what each is for; a folder of several
    files (the skills, the templates) is one line naming them."""
    if not paths:
        return
    items, groups = [], {}
    for rel in paths:
        folder, name = rel.rsplit("/", 1) if "/" in rel else ("", rel)
        if name == "SKILL.md" and "/" in folder:  # .claude/skills/wiki-ingest/SKILL.md
            folder, name = folder.rsplit("/", 1)
        groups.setdefault(folder, []).append((rel, name))
    for folder, entries in groups.items():
        if folder and len(entries) >= 3:
            kind = next((label for end, label in (("skills", "workflow skills"), ("commands", "slash commands"),
                                                  ("templates", "page templates")) if folder.endswith(end)), "")
            names = ", ".join(name.removesuffix(".md") for _, name in entries)
            items.append((f"{folder}/", f"{kind}: {names}" if kind else names))
        else:
            items += [(rel, descriptions.get(rel, "")) for rel, _ in entries]
    print("\n" + output.heading(heading))
    for line in output.rows(items, max_key=34, styles=((), ("dim",))):
        print(line)


def _print_block(text: str, indent: str = "    ") -> None:
    """Text to paste, as it must be pasted: indented, never rewrapped."""
    for line in text.rstrip().splitlines():
        print(f"{indent}{line}".rstrip())


def _optional_settings(args: argparse.Namespace) -> Settings | None:
    """The wiki's settings when there is a wiki here; guides still print without one."""
    try:
        settings = load_settings(args.root, args.cache)
    except ConfigError:
        return None
    return settings if settings.root_note is None else None


def cmd_guide(args: argparse.Namespace, settings: Settings | None) -> int:
    if args.extra:
        raise UsageError(f"`wiki guide {args.name}` takes no other arguments: it prints steps for you to carry "
                         f"out yourself. Follow them, starting at step 1, with {' '.join(args.extra)}.")
    if args.parts:
        if args.name:
            raise UsageError("--parts lists the parts of every guide: wiki guide --parts")
        return _print_parts(args, settings)
    if args.name is None:
        guides = guide.available(settings)
        if args.format == "json":
            _print_json({"guides": [{"name": name, "title": title} for name, title in guides]})
        else:
            print(output.heading("Workflows") + output.dim("  (print one: wiki guide <name>)"))
            for line in output.rows([(name, title.split(": ", 1)[-1][:1].upper() + title.split(": ", 1)[-1][1:])
                                     for name, title in guides], styles=(("bold",),)):
                print(line)
        return EXIT_OK
    text = guide.render(args.name, settings)
    if args.format == "json":
        _print_json({"guide": args.name, "text": text})
    else:
        print(text, end="")
    return EXIT_OK


def _print_parts(args: argparse.Namespace, settings: Settings | None) -> int:
    found = guide.parts(settings)
    if args.format == "json":
        _print_json({"parts_dir": settings.guides_parts if settings else None, "parts": found})
        return EXIT_OK
    folder = settings.guides_parts if settings else "guides/parts"
    print(output.heading("Guide parts", len([part for part in found if part["source"] != "unused"])))
    print(output.dim(output.wrap(f"A wiki fills a part with its own text in {folder}/<name>.md; the guides "
                                 "print it at that step.")))
    for part in found:
        state = {"wiki": f"this wiki's: {part.get('path')}", "default": "built-in default", "empty": "empty",
                 "unused": f"no guide uses it: {part.get('path')}"}[part["source"]]
        print(f"\n{output.bold(part['name'])}  {output.dim('in ' + ', '.join(part['guides']))}" if part["guides"]
              else f"\n{output.bold(part['name'])}")
        print(f"  {output.style(state, 'yellow') if part['source'] == 'unused' else state}")
        if part["text"]:
            print(output.dim(output.wrap(" ".join(part["text"].split()), "    ")))
    return EXIT_OK


def cmd_stale(args: argparse.Namespace, settings: Settings) -> int:
    due = records.due(settings)
    code = None
    if settings.code_repo is not None or not records.record_types(settings):
        try:
            code = _stale_code(settings)
        except codebase.CodeRepoError as exc:
            raise UsageError(str(exc)) from exc
    if args.format == "json":
        payload = dict(code or {})
        if records.record_types(settings):
            payload["records"] = due
        _print_json(payload)
        return EXIT_OK
    if code is not None:
        _print_stale_code(code)
    if records.record_types(settings):
        print(("\n" if code is not None else "") + output.heading("Records to recheck", len(due))
              + output.dim(f"  never synced, or synced over {settings.records_recheck_days} days ago and not final"))
        if not due:
            print("  None: every record page is synced recently enough, or closed.")
        for entry in due:
            label = " · ".join(part for part in (entry["key"], entry["status"]) if part)
            print(f"  {output.bold(entry['slug'])}" + (f"  {output.dim(label)}" if label else "")
                  + f"  {output.style(entry['reason'], 'yellow')}")
            if entry["url"]:
                print(output.dim(f"    {entry['url']}"))
    return EXIT_OK


def _stale_code(settings: Settings) -> dict:
    repo = codebase.repo(settings)
    head = codebase.head(repo)
    scanned, _ = scan_vault(settings)
    stale, unverified = [], []
    for page_file, _ in scanned:
        if page_file.kind != "page":
            continue
        page = load(page_file, settings)
        globs = codebase.covers(page)
        if not globs:
            continue
        verified = codebase.verified(page)
        if not verified:
            unverified.append(page.slug)
            continue
        result = codebase.staleness(repo, page.slug, page_file.rel, globs, verified)
        if result.changed or result.uncommitted or result.problem:
            stale.append(result)
    return {"code_repo": str(repo), "head": head, "stale": [item.to_dict() for item in stale],
            "unverified": unverified}


def _print_stale_code(code: dict) -> None:
    print(f"{output.bold('Code repo')} {output.dim('at ' + code['head'][:12])}")
    print(f"  {code['code_repo']}")
    stale, unverified = code["stale"], code["unverified"]
    if not stale and not unverified:
        print("Every page with covers: is up to date.")
    if stale:
        print("\n" + output.heading("Code changed since verified", len(stale)))
    for item in stale:
        print(f"  {output.bold(item['slug'])}  {output.dim('verified at ' + item['verified'][:12])}")
        details = [(output.style("problem", "yellow"), item["problem"])] if item.get("problem") else []
        details += [("changed", ", ".join(item["changed"]))] if item.get("changed") else []
        details += [("uncommitted", ", ".join(item["uncommitted"]))] if item.get("uncommitted") else []
        for label, text in details:
            print(output.wrap(text, f"    {label}: "))
    if unverified:
        print("\n" + output.heading("Never verified", len(unverified)))
        print(output.wrap(", ".join(unverified), "  "))


def cmd_pending(args: argparse.Namespace, settings: Settings) -> int:
    waiting, ingested = sources.pending(settings)
    folders = sources.raw_dirs(settings)
    if args.format == "json":
        _print_json({"raw_dirs": folders, "pending": [source.to_dict() for source in waiting], "ingested": ingested})
        return EXIT_OK
    if not folders:
        print("No raw/ folder yet: sources go in raw/.")
        return EXIT_OK
    where = ", ".join(f + "/" for f in folders)
    if waiting:
        print(output.heading("Sources not ingested yet", len(waiting)))
        items = [(", ".join(source.files), f"same content as {source.duplicate_of}" if source.duplicate_of else "")
                 for source in waiting]
        for line in output.rows(items, max_key=60, styles=(("bold",), ("yellow",))):
            print(line)
        print(output.dim(f"{len(waiting)} pending, {ingested} ingested, in {where}"))
    else:
        print(f"Nothing pending: all {output.plural(ingested, 'source')} in {where} are ingested.")
    return EXIT_OK


def cmd_weekly(args: argparse.Namespace, settings: Settings) -> int:
    if args.hook and (settings.weekly is None or args.week):
        return EXIT_OK  # the hook runs in every wiki; notes are only for those that turn them on
    result = weekly.run(settings, week=args.week)
    if args.hook:
        weekly.stage(settings, result["written"])
    if args.format == "json":
        _print_json(result)
    elif result["preview"] is not None:
        print(result["preview"], end="")
    elif not args.hook:
        if result["written"]:
            print(output.heading("Wrote", len(result["written"])))
            for rel in result["written"]:
                print(f"  {rel}")
        else:
            print("Weekly notes are up to date.")
    return EXIT_OK


def cmd_list(args: argparse.Namespace, settings: Settings) -> int:
    with Cache(settings) as cache:
        cache.refresh()
        query = ("SELECT slug, COALESCE(title, slug), page_type, summary, path FROM pages WHERE kind = 'page'"
                 + (" AND page_type = ?" if args.page_type else "") + " ORDER BY COALESCE(page_type, '~'), slug")
        rows = cache.conn.execute(query, (args.page_type.lower(),) if args.page_type else ()).fetchall()
    pages = [{"slug": slug, "title": title, "type": page_type, "summary": summary, "path": path}
             for slug, title, page_type, summary, path in rows]
    if args.format == "json":
        _print_json({"pages": pages})
        return EXIT_OK
    groups: dict = {}
    for page in pages:
        groups.setdefault(page["type"], []).append(page)
    for number, (page_type, members) in enumerate(groups.items()):
        print(("\n" if number else "") + output.heading(page_type or "no type", len(members)))
        items = []
        for page in members:
            title = page["title"] if page["title"] != page["slug"] else ""
            text = " — ".join(part for part in (title, page["summary"] or "") if part)
            items.append((page["slug"], text))
        for line in output.rows(items, max_key=28, styles=(("bold",),)):
            print(line)
    if not pages:
        print("No pages" + (f" of type '{args.page_type}'." if args.page_type else "."))
    return EXIT_OK


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
            print(f"No suggestions for {args.slug}.")
        else:
            print(output.heading(f"Suggestions for {args.slug}", len(results)))
        for item in results:
            print(f"  {output.bold(item['slug'])}" + (f"  {output.dim(item['type'])}" if item.get("type") else ""))
            for reason in item["reasons"]:
                print(output.wrap(reason, "    "))
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
    elif not items:
        print("Every link has a page.")
    else:
        print(output.heading("Linked but not written", len(items)) + output.dim("  most linked first"))
        rows = [(item["target"], f"{output.plural(len(item['linked_from']), 'page')}: {', '.join(item['linked_from'])}")
                for item in items]
        for line in output.rows(rows, max_key=32, styles=(("bold",),)):
            print(line)
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
    elif not rows:
        print("No orphans: every page is linked from another.")
    else:
        print(output.heading("Pages nothing links to", len(rows)))
        for line in output.rows([(slug, path) for slug, path in rows], styles=(("bold",), ("dim",))):
            print(line)
    return EXIT_OK


def cmd_clusters(args: argparse.Namespace, settings: Settings) -> int:
    with Cache(settings) as cache:
        cache.refresh()
        result = clusters.clusters(cache, min_size=args.min_size, include_covered=args.all)
    if args.format == "json":
        _print_json(result)
        return EXIT_OK
    if result.get("too_small"):
        print(f"{output.plural(result['pages'], 'page')}: too few for clusters to mean anything "
              f"(it takes {result['too_small']}).")
        return EXIT_OK
    hubs = result["hub_types"]
    shown = [cluster for cluster in result["clusters"] if cluster["covered_by"] is None]
    if hubs:
        print(output.heading(f"Clusters of {args.min_size}+ pages no hub page covers", len(shown)))
        more = "1 more cluster is" if result["covered"] == 1 else f"{result['covered']} more clusters are"
        print(output.dim(output.wrap(f"Hub types: {', '.join(hubs)}. {more} covered"
                                     + (" (listed last)." if args.all else " (--all lists them)."))))
    else:
        print(output.heading(f"Clusters of {args.min_size}+ pages", len(shown)))
        print(output.dim(output.wrap("No hub types are declared: judge whether each cluster's most linked page is "
                                     "about what its pages share.")))
    if not result["clusters"]:
        print("None.")
    for number, cluster in enumerate(result["clusters"], start=1):
        covered = cluster["covered_by"]
        top = covered or cluster["most_linked"]
        size = output.plural(cluster["size"], "page")
        print(f"\n{number}. {output.bold(size)}" + (output.dim("  covered") if covered else ""))
        details = []
        if top:
            details.append(("covered by" if covered else "most linked",
                            f"{top['slug']} ({top['type'] or 'no type'}), linked with {top['linked_from']} of them"))
        names = cluster["pages"]
        details.append(("pages", ", ".join(names[:12]) + (f" and {len(names) - 12} more" if len(names) > 12 else "")))
        if cluster["terms"]:
            details.append(("terms", ", ".join(cluster["terms"])))
        if cluster["relations"]:
            details.append(("relations", ", ".join(f"{kind} {count}" for kind, count in cluster["relations"].items())))
        for line in output.rows(details, indent="   ", styles=(("dim",),)):
            print(line)
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
        print(f"No pages relate to {args.slug}.")
        return EXIT_OK
    print(output.heading(f"Relations of {args.slug}", len(results))
          + output.dim(f"  -> {args.slug} links to it, <- it links to {args.slug}"))
    items = []
    for entry in results:
        arrow = "->" if entry["direction"] == "outgoing" else "<-"
        flag = " (not written)" if entry.get("unresolved") else ""
        items.append((f"{arrow} {entry['type']}", entry["slug"] + flag, entry["reason"]))
    for line in output.rows(items, styles=(("dim",), ("bold",))):
        print(line)
    return EXIT_OK


# -- check -------------------------------------------------------------------

def cmd_check(args: argparse.Namespace, settings: Settings) -> int:
    if args.all and args.summary_ok:
        raise UsageError("--summary-ok takes one page, after you have re-read its summary")
    if args.ingested and (args.all or args.summary_ok or args.verify_cache):
        raise UsageError("--ingested takes one source page: wiki check <source-slug> --ingested")
    scanned, others = scan_vault(settings)
    files = [page_file for page_file, _ in scanned]
    resolver = Resolver([(page_file.slug, page_file.rel) for page_file in files], others)
    if args.ingested:
        issues, checked = audit.ingested(settings, args.target)
    elif args.all:
        pages = [load(page_file, settings) for page_file in files]
        issues = check_corpus(pages, resolver)
        issues.extend(_cache_issues(pages, settings, resolver, verify=args.verify_cache))
        issues.extend(codebase.check_pages(pages, settings))
        checked = sum(1 for page in pages if page.file.kind == "page")
    else:
        if args.verify_cache:
            raise UsageError("--verify-cache requires --all")
        page = load(resolve(args.target, settings), settings)
        if args.summary_ok:
            with Cache(settings) as cache:  # only the cache records this; the page is not touched
                cache.ensure_fresh(page.slug)
                cache.conn.execute("UPDATE pages SET summary_body_hash = body_hash WHERE path = ? "
                                   "AND summary_body_hash IS NOT NULL", (page.file.rel,))
                cache.conn.commit()
        issues = check_page(page, resolver)
        issues.extend(_cache_issues([page], settings, resolver, verify=False))
        issues.extend(codebase.check_pages([page], settings))
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
        _print_issues(shown)
        counts = f"{output.plural(errors, 'error')}, {output.plural(warnings, 'warning')}"
        if not errors and not warnings:
            counts = output.style("no problems", "green")
        elif errors:
            counts = output.style(counts, "red")
        print(("\n" if shown else "") + f"{output.plural(checked, 'page')} checked: {counts}")
    return EXIT_INVALID if failed else EXIT_OK


def _print_issues(issues: list[Issue]) -> None:
    """Issues grouped by file, each with its severity and code."""
    groups: dict = {}
    for issue in issues:
        groups.setdefault(issue.path or "(the wiki)", []).append(issue)
    code_width = max((len(issue.code) for issue in issues), default=0)
    for number, (path, items) in enumerate(groups.items()):
        print(("\n" if number else "") + output.bold(path))
        for issue in items:
            severity = output.style(f"{issue.severity:<7}", "red" if issue.severity == ERROR else "yellow")
            lead = f"  {severity}  {output.dim(f'{issue.code:<{code_width}}')}  "
            print(output.wrap(issue.message, lead))


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
        status = cache.status()
    if args.format == "json":
        _print_json(status)
        return EXIT_OK
    items = [
        ("pages", str(status["pages"])),
        ("raw text files", str(status["raw"])),
        ("relations", f"{status['relations']} ({status['unresolved']} to pages not written yet)"),
        ("sections", str(status["chunks"])),
        ("out of date", output.plural(status["stale"], "file") + ("  run 'wiki index refresh'" if status["stale"] else "")),
        ("not embedded", output.plural(status["pending_embedding"], "file")
         + ("  run 'wiki index refresh'" if status["pending_embedding"] else "")),
    ]
    if status.get("embed_model"):
        items.append(("embedding model", status["embed_model"]))
    items.append(("cache", f"{settings.cache_path} (schema {status['version']})"))
    print(output.heading("Index"))
    for line in output.rows(items, styles=(("dim",),)):
        print(line)
    return EXIT_OK


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
            print(f"\rEmbedding {done}/{total} files", end="", file=sys.stderr, flush=True)
    return report


def _print_stats(args: argparse.Namespace, values: dict) -> int:
    """`index refresh` and `rebuild`: what changed, in one line."""
    if args.format == "json":
        _print_json(values)
        return EXIT_OK
    labels = (("added", "added"), ("changed", "changed"), ("touched", "touched (metadata only)"),
              ("removed", "removed"), ("moved", "moved"), ("embedded", "embedded"))
    done = [f"{values[key]} {label}" for key, label in labels if values.get(key)]
    unchanged = values.get("unchanged", 0)
    if done:
        print(f"{output.bold('Index updated:')} {', '.join(done)}" + output.dim(f"  ({unchanged} unchanged)"))
    else:
        print(f"Index is up to date ({output.plural(unchanged, 'file')}).")
    if values.get("embedding_skipped"):
        output.note(f"embedding skipped: {values['embedding_skipped']}")
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
        for title, names, configured, is_reranker in (
                ("Embedding models", payload["embedding"], settings.embed_model, False),
                ("Rerankers", payload["reranker"], settings.reranker, True)):
            print(output.heading(title))
            items = []
            for name in names:
                state = []
                if name == configured:
                    state.append("in use")
                if name == NO_RERANKER:
                    state.append("skip reranking")
                elif is_downloaded(name, settings.models_dir, reranker=is_reranker):
                    state.append("downloaded")
                items.append(("*" if name == configured else " ", name, ", ".join(state)))
            for line in output.rows(items, indent=" ", max_key=40, styles=((), ("bold",), ("dim",))):
                print(line)
            print()
        print(f"{output.dim('Models folder:')} {settings.models_dir}")
    return EXIT_OK


def cmd_models_download(args: argparse.Namespace, settings: Settings) -> int:
    for name, is_reranker in ((settings.embed_model, False), (settings.reranker, True)):
        if args.format == "text":
            print(f"Downloading {name}...", file=sys.stderr)
        download(name, settings.models_dir, reranker=is_reranker)
    if args.format == "json":
        _print_json({"downloaded": [settings.embed_model, settings.reranker], "models_dir": str(settings.models_dir)})
    else:
        print(output.wrap(f"{settings.embed_model} and {settings.reranker}, in {settings.models_dir}",
                          output.bold("Downloaded: ")))
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
        print(output.heading("Search evaluation"))
        items = [(key.replace("_", " "), str(value)) for key, value in summary.items() if key != "misses"]
        for line in output.rows(items, styles=(("dim",),)):
            print(line)
        if summary["misses"]:
            print("\n" + output.heading("Misses", len(summary["misses"])) + output.dim("  and the pages search found"))
            for line in output.rows([(str(miss["id"]), ", ".join(miss["top"]) or "-") for miss in summary["misses"]],
                                    styles=(("bold",),)):
                print(line)
    return EXIT_OK


# -- vocab -------------------------------------------------------------------

def cmd_vocab(args: argparse.Namespace, settings: Settings) -> int:
    vocabulary = settings.vocabulary
    types = []
    for name in vocabulary.types:
        entry = {"type": name, "inverse": vocabulary.inverse(name)}
        sources = [f"heading '{h}'" for rule in vocabulary.rules if rule.type == name for h in rule.headings]
        sources += [f"field '{rule.field}'" for rule in vocabulary.rules if rule.type == name and rule.field]
        entry["from"] = sources or [{"embeds": "block embeds", "refers-to-code": "code: links"}.get(name, "any other link")]
        types.append(entry)
    if args.format == "json":
        _print_json({"types": types})
    else:
        print(output.heading("Relation types", len(types)))
        items = [("type", "seen from the target", "comes from")]
        items += [(entry["type"], entry["inverse"], "; ".join(entry["from"])) for entry in types]
        lines = output.rows(items, styles=(("bold",), ()))
        print(output.dim(lines[0].replace("\033[1m", "").replace("\033[0m", "")))
        for line in lines[1:]:
            print(line)
    return EXIT_OK


def cmd_init(args: argparse.Namespace, settings: Settings) -> int:
    preset = scaffold.preset_files(args.preset) if args.preset else None
    target = settings.root / CONFIG_FILENAME
    if preset is not None and target.exists():
        # The wiki has a config: only the preset's tables it does not declare yet, to merge in.
        if args.write:
            raise UsageError(f"{target} already exists; run without --write and merge what it prints")
        lines = init.preset_lines(preset, _page_folder(settings), declared_types={t.name for t in settings.types},
                                  declared_relations={rule.type for rule in settings.relations},
                                  weekly=settings.weekly is not None)
        text = "\n".join(lines).rstrip() + "\n"
        if args.format == "json":
            _print_json({"preset": preset[0], "config": text})
        else:
            print(f"# Add what fits to {target}:\n")
            print(text, end="")
        return EXIT_OK
    result = init.survey(settings)
    result["adopt_tables"] = preset is not None
    draft = init.render(result, preset=preset)
    if args.format == "json":
        _print_json({**result, "config": draft})
        return EXIT_OK
    if args.write:
        if target.exists():
            raise UsageError(f"{target} already exists; not overwriting (run without --write to print a draft)")
        target.write_text(draft, encoding="utf-8")
        print(f"Wrote {target}. Review it, then run 'wiki index refresh'.", file=sys.stderr)
    else:
        print(draft, end="")
    return EXIT_OK


def _page_folder(settings: Settings) -> str | None:
    """The folder a wiki's pages live in, when its `pages` names exactly one."""
    if len(settings.pages) == 1:
        match = re.fullmatch(r"([^*?\[\]]+)/\*\*/\*\.md", settings.pages[0])
        if match:
            return match.group(1)
    return None


def _print_json(payload: dict) -> None:
    print(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
