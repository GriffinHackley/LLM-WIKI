"""Groups: the parts of a wiki its pages say they belong to, for `--group` on `wiki
clusters` and `wiki map`.

A page belongs to a group when one of the frontmatter fields `[groups] fields` names
(`tags` by default) holds the group's name. Pages that every group shares (a person, a
place) rarely carry the field, so a group also takes in every page its tagged pages link
to or are linked from: one step through the relations graph.
"""

from __future__ import annotations

from wiki_cli.cache import Cache
from wiki_cli.pages import PAGE, load, scan_vault


class GroupError(Exception):
    pass


def tagged(cache: Cache) -> dict[str, set[str]]:
    """Each group name (lowercased, without a leading '#') and the pages that name it."""
    settings = cache.settings
    found: dict[str, set[str]] = {}
    for page_file, _ in scan_vault(settings)[0]:
        if page_file.kind != PAGE:
            continue
        data = load(page_file, settings).data or {}
        for field in settings.group_fields:
            value = data.get(field)
            for item in value if isinstance(value, list) else [value]:
                if isinstance(item, (str, int)) and str(item).strip().lstrip("#").strip():
                    found.setdefault(str(item).strip().lstrip("#").strip().casefold(), set()).add(page_file.slug)
    return found


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
