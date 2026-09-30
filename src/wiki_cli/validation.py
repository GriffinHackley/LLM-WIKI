"""Deterministic page checks. Never writes anything.

What is checked follows the wiki's `[check]` settings: frontmatter is only required
when `require_frontmatter = true`, and a summary is only expected on the page types
listed in `summary_types` ("*" for all).
"""

from __future__ import annotations

import re

from wiki_cli import links
from wiki_cli.config import PageType
from wiki_cli.edges import _link_value, derive
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

    page_type = next((declared_type for declared_type in settings.types if declared_type.name == page.page_type),
                     None) if settings is not None else None
    if page_type is not None:
        issues.extend(_required_parts(page, page_type))
    issues.extend(_uncited_sources(page, resolver))

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
        if link.is_code:
            continue  # a file in the code repo, checked against it by codebase.check_pages
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


def _required_parts(page: Page, page_type: PageType) -> list[Issue]:
    """The sections and frontmatter fields its type's `[types]` entry says every page has."""
    issues = []
    data = page.data or {}
    template = f" (see {page_type.template})" if page_type.template else ""
    missing_fields = [key for key in page_type.fields if key not in data]
    if missing_fields:
        issues.append(Issue(WARNING, "missing-field", f"frontmatter has no {_quoted(missing_fields)}; every "
                                                      f"{page_type.name} page has {_it(missing_fields)}{template}"))
    headings = {section for line, section, in_code in links.iter_lines(page.body)
                if not in_code and line.lstrip().startswith("#")}
    missing_sections = [name for name in page_type.sections if name.lower() not in headings]
    if missing_sections:
        issues.append(Issue(WARNING, "missing-section", f"no {_quoted(missing_sections, '## ')} section"
                                                        f"{'s' if len(missing_sections) > 1 else ''}; every "
                                                        f"{page_type.name} page has {_it(missing_sections)}{template}"))
    for key, allowed in page_type.values:
        value = data.get(key)
        given = value if isinstance(value, list) else [] if value is None else [value]
        folded = {item.casefold() for item in allowed}
        wrong = [str(item) for item in given if str(item).strip().casefold() not in folded]
        if wrong:
            issues.append(Issue(WARNING, "bad-value", f"'{key}' is {_quoted(wrong)}; in a {page_type.name} page "
                                                      f"it is one of {', '.join(allowed)}"))
    return issues


def _uncited_sources(page: Page, resolver: Resolver) -> list[Issue]:
    """Sources a page lists in `sources:` but never cites in its text."""
    listed = (page.data or {}).get("sources")
    if not isinstance(listed, list) or not listed:
        return []
    linked = set()
    for link in links.extract_links(page.body, page.file.rel):
        if not link.is_code:
            linked.add((resolver.resolve_path if link.is_path else resolver.resolve)(link.target) or link.target)
    uncited = []
    for item in listed:
        if not isinstance(item, str) or not item.strip():
            continue
        target = _link_value(item)
        if (resolver.resolve(target) or target) in linked:
            continue
        # a wiki's own citation format may name the source in plain text: "(senate-report-2024, p. 4)"
        name = target.rsplit("/", 1)[-1]
        if re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", page.body, re.IGNORECASE):
            continue
        uncited.append(target)
    if not uncited:
        return []
    return [Issue(WARNING, "uncited-sources", f"sources: lists {_quoted(uncited, '[[', ']]')} but the text never "
                                              "cites it; cite it where its facts are used, or remove it")]


def _it(names: list[str]) -> str:
    return "it" if len(names) == 1 else "them"


def _quoted(names: list[str], before: str = "'", after: str | None = None) -> str:
    after = before if after is None else after
    after = "" if before == "## " else after
    return ", ".join(f"{before}{name}{after}" for name in names)


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
