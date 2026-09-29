"""`wiki check <source> --ingested`: has an ingest done everything the guide asks?

The guide's end state as checks an agent can run and fix until clean: the source page
passes `wiki check`, links its file in `raw/`, names what the source discusses, is cited by
the pages it touched (which pass `wiki check` too), and everything is committed.
"""

from __future__ import annotations

import subprocess

from wiki_cli.cache import Cache
from wiki_cli.config import Settings
from wiki_cli.model import ERROR, WARNING, Issue
from wiki_cli.pages import Resolver, load, matches, resolve, scan_vault
from wiki_cli.sources import _RawIndex, raw_dirs
from wiki_cli.validation import check_page
from wiki_cli.vocabulary import REFERS_TO_CODE

OPEN_QUESTIONS = "open-questions"  # where the presets keep questions; linking it is not discussing anything
TOUCHED_CODES = {"missing-section", "missing-field", "uncited-sources"}  # beside errors, what a touched page must fix
MAX_LISTED = 5


def ingested(settings: Settings, target: str) -> tuple[list[Issue], int]:
    """Problems with the ingest of the source page ``target``, and how many pages were checked."""
    scanned, others = scan_vault(settings)
    resolver = Resolver([(page_file.slug, page_file.rel) for page_file, _ in scanned], others)
    page = load(resolve(target, settings), settings)
    issues = check_page(page, resolver)

    index = _RawIndex(settings, scanned, others)
    if not index.linked(page):
        issues.append(_on(page, ERROR, "no-original", "links no file in raw/: link the file this source was "
                                                        "ingested from, for example [[raw/report.pdf]] (step 3)"))

    raw_keys = {key.casefold() for key in index.by_path}
    with Cache(settings) as cache:
        cache.refresh()
        outgoing = [target for target, relation in cache.conn.execute(
            "SELECT target_slug, relation_type FROM relations WHERE source_path = ?", (page.file.rel,))
            if relation != REFERS_TO_CODE and target.casefold() not in raw_keys and not _is_questions(target)]
        typed = {slug: page_type for slug, page_type in cache.conn.execute(
            "SELECT slug, page_type FROM pages WHERE kind = 'page'")}
        citing = sorted({(source, path) for source, path in cache.conn.execute(
            "SELECT source_slug, source_path FROM relations WHERE target_slug = ? AND resolved = 1 "
            "AND source_path != ?", (page.slug, page.file.rel)) if not _is_questions(source)
            and (typed.get(source) or not settings.types)})
        stale = cache.stale_summaries()

    if not outgoing:
        issues.append(_on(page, ERROR, "names-nothing", "links no wiki page: list what the source discusses, "
                                                          "one line each, - [[slug]] - its role (steps 3 and 5)"))
    if not citing:
        issues.append(_on(page, ERROR, "not-cited", "no page cites this source: work what it says into the pages it "
                                                      "discusses, citing it (steps 4 to 7). If it adds nothing to "
                                                      "any page, say so in your report"))
    for slug, path in citing:
        touched = load(resolve(path, settings), settings)
        issues.extend(issue for issue in check_page(touched, resolver)
                      if issue.severity == ERROR or issue.code in TOUCHED_CODES)
        if stale.get(path) == touched.content_hash:
            issues.append(_on(touched, WARNING, "summary-stale", "body changed but summary did not: revise it, or "
                                                                  f"run wiki check {slug} --summary-ok"))

    changed = _uncommitted(settings)
    if changed:
        listed = ", ".join(changed[:MAX_LISTED]) + (f" and {len(changed) - MAX_LISTED} more"
                                                    if len(changed) > MAX_LISTED else "")
        issues.append(Issue(ERROR, "uncommitted", f"{len(changed)} wiki files not committed: {listed}. Commit "
                                                  "them: git add <each file>, then git commit -m \"Ingest <source>: "
                                                  "...\" (step 10)"))
    return issues, 1 + len(citing)


def _is_questions(slug: str) -> bool:
    return slug.rsplit("/", 1)[-1].casefold() == OPEN_QUESTIONS


def _on(page, severity: str, code: str, message: str) -> Issue:
    return Issue(severity, code, message, path=page.file.rel, slug=page.slug)


def _uncommitted(settings: Settings) -> list[str]:
    """Changed and new pages and sources in the wiki's git repository (none when it is not
    one). Other files, such as an editor's settings, are the user's to commit."""
    try:
        done = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all", "--", "."],
                              cwd=settings.root, capture_output=True, text=True, encoding="utf-8")
    except OSError:
        return []
    if done.returncode != 0:
        return []
    folders = raw_dirs(settings)
    changed = [line[3:].strip().strip('"').split(" -> ")[-1] for line in done.stdout.splitlines() if line.strip()]
    return [rel for rel in changed if (rel.endswith(".md") and matches(rel, settings.pages))
            or any(rel.startswith(folder + "/") for folder in folders)]
