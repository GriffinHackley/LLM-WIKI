"""Groups: the parts of a wiki its pages say they belong to, for `--group` on `wiki
clusters` and `wiki map`.

A page belongs to a group when one of the frontmatter fields `[groups] fields` names
(`tags` by default) holds the group's name. Pages that every group shares (a person, a
place) rarely carry the field, so a group also takes in every page its tagged pages link
to or are linked from: one step through the relations graph.

`--ungrouped` looks at the pages that name no group (a loose idea and what grew from it).
With `[groups] exclusive = true`, the groups are separate worlds (books in the writing
preset): `wiki check` warns when a page links a page whose groups share none of its own.
"""

from __future__ import annotations

from wiki_cli.cache import Cache
from wiki_cli.config import Settings
from wiki_cli.edges import derive
from wiki_cli.model import WARNING, Issue
from wiki_cli.pages import PAGE, Page, Resolver, load, scan_vault

UNGROUPED = "(ungrouped)"  # the label --ungrouped gives clusters and the map


class GroupError(Exception):
    pass


def tagged(cache: Cache) -> dict[str, set[str]]:
    """Each group name (lowercased, without a leading '#') and the pages that name it."""
    settings = cache.settings
    found: dict[str, set[str]] = {}
    for page_file, _ in scan_vault(settings)[0]:
        if page_file.kind != PAGE:
            continue
        for name in names(load(page_file, settings), settings):
            found.setdefault(name, set()).add(page_file.slug)
    return found


def names(page: Page, settings: Settings) -> set[str]:
    """The groups a page's group fields name, lowercased, without a leading '#'."""
    data = page.data or {}
    found = set()
    for field in settings.group_fields:
        value = data.get(field)
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, (str, int)) and str(item).strip().lstrip("#").strip():
                found.add(str(item).strip().lstrip("#").strip().casefold())
    return found


def ungrouped(cache: Cache) -> set[str]:
    """The pages that name no group. Unlike a group, it takes in no neighbours: a loose
    page's links into a book are that book's pages."""
    grouped = set().union(*tagged(cache).values())
    return {slug for (slug,) in cache.conn.execute("SELECT slug FROM pages WHERE kind = 'page'")} - grouped


def cross_links(pages: list[Page], resolver: Resolver, settings: Settings) -> list[Issue]:
    """With `[groups] exclusive`, links between pages whose groups share none (a page in no
    group may link anything, and be linked by anything)."""
    if not settings.groups_exclusive:
        return []
    by_slug = {page.slug: names(page, settings) for page in pages if page.file.kind == PAGE}
    issues = []
    for page in pages:
        own = by_slug.get(page.slug)
        if not own:
            continue
        crossing = sorted({edge.target for edge in derive(page, resolver)
                           if edge.resolved and by_slug.get(edge.target) and not by_slug[edge.target] & own})
        for target in crossing:
            issues.append(Issue(WARNING, "cross-group-link",
                                f"links [[{target}]], of {_listed(by_slug[target])}, from a page of {_listed(own)}: "
                                "the groups are separate ([groups] exclusive); remove the link, or name both "
                                "groups on the page that belongs to both", path=page.file.rel, slug=page.slug))
    return issues


def _listed(groups: set[str]) -> str:
    return ", ".join(sorted(groups))


def members(cache: Cache, name: str) -> set[str]:
    """The pages in a group: those whose group fields name it, and every page they link to
    or are linked from. An unknown name is an error that lists the groups there are."""
    groups = tagged(cache)
    key = name.strip().lstrip("#").strip().casefold()
    if key not in groups:
        fields = ", ".join(cache.settings.group_fields)
        known = ", ".join(sorted(groups)) or "none"
        raise GroupError(f"no page names the group '{name}' in its {fields} frontmatter; groups: {known} "
                         "([groups] fields in .wiki-cli.toml says which fields name them)")
    seeds = groups[key]
    pages = {slug for (slug,) in cache.conn.execute("SELECT slug FROM pages WHERE kind = 'page'")}
    found = set(seeds) & pages
    for source, target in cache.conn.execute(
            "SELECT source_slug, target_slug FROM relations WHERE resolved = 1"):
        if source in seeds and target in pages:
            found.add(target)
        elif target in seeds and source in pages:
            found.add(source)
    return found
