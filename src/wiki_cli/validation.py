"""Deterministic page checks. Never writes anything.

What is checked follows the wiki's `[check]` settings: frontmatter is only required
when `require_frontmatter = true`, and a summary is only expected on the page types
listed in `summary_types` ("*" for all).
"""

from __future__ import annotations

from wiki_cli import links
from wiki_cli.edges import derive
from wiki_cli.model import ERROR, WARNING, Issue
from wiki_cli.pages import PAGE, Page, Resolver

MAX_LISTED = 5


def check_page(page: Page, resolver: Resolver) -> list[Issue]:
    if page.file.kind != PAGE:
        return []
    settings = page.settings
    issues = list(page.issues)
    if page.text is None or issues:
        return _locate(issues, page)
    if page.data is None and settings is not None and settings.require_frontmatter:
        issues.append(Issue(ERROR, "missing-frontmatter", "page has no YAML frontmatter"))
        return _locate(issues, page)
    if page.data is not None and settings is not None and settings.require_frontmatter:
        for key in ("title", settings.page_type_field):
            if not isinstance(page.data.get(key), str) or not page.data[key].strip():
                issues.append(Issue(WARNING, "missing-field", f"frontmatter has no '{key}'"))

    declared = {page_type.name for page_type in settings.types} if settings is not None else set()
    if declared and page.page_type and page.page_type not in declared:
        issues.append(Issue(WARNING, "unknown-type",
                            f"type '{page.page_type}' is not one of the types in [types]: {', '.join(sorted(declared))}"))

    summary_types = settings.summary_types if settings is not None else ()
    if "*" in summary_types or (page.page_type and page.page_type in summary_types):
        _, fallback = page.summary()
        if fallback:
            headings = ", ".join(settings.summary_headings) if settings is not None else "summary"
            issues.append(Issue(WARNING, "missing-summary",
                                f"no summary field or section ({headings}); search shows the first paragraph"))

    ambiguous: set[str] = set()
    unresolved: set[str] = set()
    for link in links.extract_links(page.body, page.file.rel):
        if link.is_path:
            found = resolver.resolve_path(link.target)
        elif resolver.ambiguous(link.target):
            ambiguous.add(link.target)
            continue
        else:
            found = resolver.resolve(link.target)
        if found is None and not resolver.is_other_file(link.target):
            unresolved.add(link.target)
    for target in sorted(ambiguous):
        issues.append(Issue(WARNING, "ambiguous-link", f"[[{target}]] matches several files; link by path"))
    if unresolved:
        names = sorted(unresolved)
        listed = ", ".join(names[:MAX_LISTED]) + (f" and {len(names) - MAX_LISTED} more" if len(names) > MAX_LISTED else "")
        issues.append(Issue(WARNING, "unwritten-links", f"{len(names)} links to pages not written yet: {listed}"))
    return _locate(issues, page)


def check_corpus(pages: list[Page], resolver: Resolver) -> list[Issue]:
    issues: list[Issue] = []
    for page in pages:
        issues.extend(check_page(page, resolver))
    return issues


def compare_cache(pages: list[Page], snapshot: dict[str, tuple[str, set]], resolver: Resolver) -> list[Issue]:
    """Report cache rows that disagree with the files."""
    issues: list[Issue] = []
    current = {page.file.rel: page for page in pages}
    for rel, page in current.items():
        cached = snapshot.get(rel)
        expected = {(edge.target, edge.type) for edge in derive(page, resolver)}
        if cached is None:
            issues.append(_locate_one(Issue(ERROR, "cache-mismatch", "file is missing from the cache"), page))
        elif cached[0] != page.content_hash or cached[1] != expected:
            issues.append(_locate_one(Issue(ERROR, "cache-mismatch", "cached content or relations differ from the file"), page))
    for rel in sorted(set(snapshot) - set(current)):
        issues.append(Issue(ERROR, "cache-mismatch", "cache holds a file that no longer exists", path=rel))
    return issues


def _locate(issues: list[Issue], page: Page) -> list[Issue]:
    return [_locate_one(issue, page) for issue in issues]


def _locate_one(issue: Issue, page: Page) -> Issue:
    return Issue(issue.severity, issue.code, issue.message, path=page.file.rel, slug=page.slug)
