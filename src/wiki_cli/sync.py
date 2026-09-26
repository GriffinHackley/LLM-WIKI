"""Synchronize a page's generated link block with its canonical relations."""

from __future__ import annotations

from dataclasses import dataclass, field

from wiki_cli import block
from wiki_cli.model import ERROR, Issue
from wiki_cli.pages import Page, atomic_write
from wiki_cli.relations import is_local


@dataclass
class SyncResult:
    page: Page
    changed: bool = False
    written: bool = False
    issues: list[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(issue.severity == ERROR for issue in self.issues)


def block_slugs(page: Page, local_space: str | None) -> list[str]:
    """Sorted, deduplicated local targets declared in ``relations``."""
    return sorted({
        relation.slug
        for relation in page.relations
        if not relation.derived and is_local(relation, local_space)
    })


def sync_page(page: Page, local_space: str | None, *, dry_run: bool = False) -> SyncResult:
    """Render the block for ``page`` and write it if it changed.

    Refuses to write when the page has structural errors; missing target pages
    are left to ``check`` so a page can be written before its targets exist.
    """
    result = SyncResult(page)
    if page.text is None or page.data is None:
        result.issues = [issue for issue in page.issues if issue.severity == ERROR] or [
            Issue(ERROR, "missing-frontmatter", "page has no frontmatter")
        ]
        return result
    errors = [issue for issue in page.issues if issue.severity == ERROR]
    if errors:
        result.issues = errors
        return result

    try:
        updated = block.apply(page.text, page.body_offset, block_slugs(page, local_space), page.newline)
    except block.BlockError as exc:
        result.issues = [Issue(ERROR, exc.code, str(exc))]
        return result

    result.changed = updated != page.text
    if result.changed and not dry_run:
        atomic_write(page.file.path, updated, page.bom)
        result.written = True
    return result
