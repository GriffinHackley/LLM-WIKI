"""Deterministic relationship and summary checks. Never writes anything."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from wiki_cli import block
from wiki_cli.model import ERROR, WARNING, Issue
from wiki_cli.pages import Page, PageFile, is_bundle
from wiki_cli.relations import is_local
from wiki_cli.sync import block_slugs
from wiki_cli.vocabulary import (
    CONTRADICTORY_PAIRS,
    FALLBACK_TYPE,
    MAX_INCOMING,
    MAX_OUTGOING,
    MAX_REASON_LENGTH,
    MAX_SUMMARY_LENGTH,
)


@dataclass
class SlugIndex:
    exact: set[str] = field(default_factory=set)
    folded: dict[str, list[PageFile]] = field(default_factory=lambda: defaultdict(list))

    @classmethod
    def build(cls, files: list[PageFile]) -> "SlugIndex":
        index = cls()
        for page_file in files:
            index.exact.add(page_file.slug)
            index.folded[page_file.slug.casefold()].append(page_file)
        return index


def check_page(
    page: Page,
    slugs: SlugIndex,
    local_space: str | None,
    *,
    require_summary: bool = False,
) -> list[Issue]:
    issues = list(page.issues)
    if page.text is None or page.data is None:
        if not issues:
            issues.append(Issue(ERROR, "missing-frontmatter", "page has no frontmatter"))
        return _locate(issues, page)

    if is_bundle(page.file.rel):
        issues.append(Issue(WARNING, "bundle-page", "bundle pages (x/index.md) cannot be resolved by Obsidian links"))

    collisions = [other.rel for other in slugs.folded.get(page.slug.casefold(), []) if other.rel != page.file.rel]
    if collisions:
        issues.append(Issue(ERROR, "duplicate-slug", f"slug collides with {', '.join(sorted(collisions))}"))

    issues.extend(_relation_issues(page, slugs, local_space))
    issues.extend(_summary_issues(page, require_summary))
    issues.extend(_block_issues(page, local_space))
    return _locate(issues, page)


def _relation_issues(page: Page, slugs: SlugIndex, local_space: str | None) -> list[Issue]:
    issues: list[Issue] = []
    declared = [relation for relation in page.relations if not relation.derived]
    seen: set[tuple[str, str]] = set()
    types_by_target: dict[str, list[str]] = defaultdict(list)

    for relation in page.relations:
        where = relation.index
        label = f"relations[{where}]" if where is not None else "superseded_by"
        local = is_local(relation, local_space)
        if local and relation.slug == page.slug:
            issues.append(Issue(ERROR, "self-relation", f"{label} targets the page itself", relation=where))
            continue
        if local and relation.slug not in slugs.exact:
            hint = ""
            matches = slugs.folded.get(relation.slug.casefold())
            if matches:
                hint = f" (did you mean '{matches[0].slug}'?)"
            issues.append(Issue(ERROR, "missing-target", f"{label} target '{relation.slug}' does not exist{hint}", relation=where))
        if relation.derived:
            continue
        key = (relation.target, relation.type)
        if key in seen:
            issues.append(Issue(ERROR, "duplicate-relation", f"{label} duplicates an earlier {relation.type} relation to {relation.target}", relation=where))
            continue
        seen.add(key)
        types_by_target[relation.target].append(relation.type)
        if len(relation.reason) > MAX_REASON_LENGTH:
            issues.append(Issue(WARNING, "long-reason", f"{label} reason exceeds {MAX_REASON_LENGTH} characters", relation=where))

    for target, types in sorted(types_by_target.items()):
        if len(types) < 2:
            continue
        type_set = set(types)
        contradictions = [pair for pair in CONTRADICTORY_PAIRS if pair <= type_set]
        if contradictions:
            names = " and ".join(sorted(contradictions[0]))
            issues.append(Issue(ERROR, "conflicting-relations", f"{target} is declared as both {names}"))
        else:
            issues.append(Issue(WARNING, "multiple-types", f"{target} has multiple relation types: {', '.join(sorted(type_set))}"))

    if not declared:
        issues.append(Issue(WARNING, "no-relations", "page has no relations"))
    else:
        fallback = sum(1 for relation in declared if relation.type == FALLBACK_TYPE)
        if len(declared) >= 3 and fallback * 2 > len(declared):
            issues.append(Issue(WARNING, "related-to-overuse", f"{fallback} of {len(declared)} relations use '{FALLBACK_TYPE}'"))
        if len(declared) > MAX_OUTGOING:
            issues.append(Issue(WARNING, "high-outgoing", f"page declares {len(declared)} relations (more than {MAX_OUTGOING})"))
    return issues


def _summary_issues(page: Page, require_summary: bool) -> list[Issue]:
    summary = page.summary
    if summary is None:
        severity = ERROR if require_summary else WARNING
        return [Issue(severity, "missing-summary", "page has no summary")]
    if len(summary) > MAX_SUMMARY_LENGTH:
        return [Issue(WARNING, "long-summary", f"summary exceeds {MAX_SUMMARY_LENGTH} characters")]
    return []


def _block_issues(page: Page, local_space: str | None) -> list[Issue]:
    body = page.body
    try:
        found = block.find(body)
    except block.BlockError as exc:
        return [Issue(ERROR, exc.code, str(exc))]
    expected = block_slugs(page, local_space)
    if found is None:
        return [Issue(ERROR, "missing-block", "generated link block is missing; run 'wiki rel sync'")] if expected else []
    if not block.has_only_links(found):
        return [Issue(ERROR, "unexpected-block-content", "generated link block contains manual content")]
    if not expected:
        return [Issue(ERROR, "stale-block", "generated link block exists but page has no local relations; run 'wiki rel sync'")]
    if body[found.start:found.end] != block.render(expected, page.newline):
        return [Issue(ERROR, "stale-block", "generated link block is out of date; run 'wiki rel sync'")]
    return []


def check_corpus(pages: list[Page], slugs: SlugIndex, local_space: str | None) -> list[Issue]:
    """Per-page checks plus checks that need the whole wiki."""
    issues: list[Issue] = []
    for page in pages:
        issues.extend(check_page(page, slugs, local_space))

    incoming: Counter[str] = Counter()
    declared: set[tuple[str, str]] = set()
    for page in pages:
        for relation in page.relations:
            if relation.derived or not is_local(relation, local_space):
                continue
            incoming[relation.slug] += 1
            declared.add((page.slug, relation.slug))

    by_slug = {page.slug: page for page in pages}
    for slug, count in sorted(incoming.items()):
        if count > MAX_INCOMING and slug in by_slug:
            issues.append(_locate_one(Issue(WARNING, "high-incoming", f"{count} pages relate to this page (more than {MAX_INCOMING})"), by_slug[slug]))
    for source, target in sorted(declared):
        if source < target and (target, source) in declared:
            issues.append(_locate_one(Issue(WARNING, "reciprocal-relation", f"relations are declared in both directions with '{target}'; keep one"), by_slug[source]))
    return issues


def compare_cache(pages: list[Page], snapshot: dict[str, tuple[str, set]], local_space: str | None) -> list[Issue]:
    """Report cache rows that disagree with canonical frontmatter."""
    issues: list[Issue] = []
    canonical = {page.file.rel: page for page in pages if page.data is not None}
    for rel, page in canonical.items():
        cached = snapshot.get(rel)
        expected = {(relation.target, relation.type, relation.reason) for relation in page.relations}
        if cached is None:
            issues.append(_locate_one(Issue(ERROR, "cache-mismatch", "page is missing from the cache"), page))
        elif cached[0] != page.content_hash or cached[1] != expected:
            issues.append(_locate_one(Issue(ERROR, "cache-mismatch", "cached page or relations differ from frontmatter"), page))
    for rel in sorted(set(snapshot) - set(canonical)):
        issues.append(Issue(ERROR, "cache-mismatch", "cache holds a page that no longer exists", path=rel))
    return issues


def _locate(issues: list[Issue], page: Page) -> list[Issue]:
    return [_locate_one(issue, page) for issue in issues]


def _locate_one(issue: Issue, page: Page) -> Issue:
    return Issue(issue.severity, issue.code, issue.message, path=page.file.rel, slug=page.slug, relation=issue.relation)
