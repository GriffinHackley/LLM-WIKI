"""Deterministic page checks. Never writes anything."""

from __future__ import annotations

from wiki_cli import links
from wiki_cli.edges import derive
from wiki_cli.model import ERROR, WARNING, Issue
from wiki_cli.pages import PAGE, Page, Resolver

# Page types expected to open with a summary section.
SUMMARIZED_TYPES = {"person", "organization", "place", "event", "document", "topic", "claim"}
MAX_LISTED = 5


def check_page(page: Page, resolver: Resolver) -> list[Issue]:
    if page.file.kind != PAGE:
        return []
    issues = list(page.issues)
    if page.text is None or issues:
        return _locate(issues, page)
    if page.data is None:
        issues.append(Issue(ERROR, "missing-frontmatter", "page has no YAML frontmatter"))
        return _locate(issues, page)

    for field in ("title", "type"):
        if not isinstance(page.data.get(field), str) or not page.data[field].strip():
            issues.append(Issue(WARNING, "missing-field", f"frontmatter has no '{field}'"))

    if page.page_type in SUMMARIZED_TYPES:
        _, fallback = page.summary()
        if fallback:
            issues.append(Issue(WARNING, "missing-summary",
                                "no Summary / What this is / Claim section; search shows the first paragraph"))

    ambiguous: set[str] = set()
    unresolved: set[str] = set()
    for link in links.extract_links(page.body):
        if resolver.ambiguous(link.target):
            ambiguous.add(link.target)
        elif resolver.resolve(link.target) is None and not resolver.is_other_file(link.target):
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
