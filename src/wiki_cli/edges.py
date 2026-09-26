"""Derive typed, reasoned edges from a page's links, using the wiki's relation rules.

- A link under a heading named by a rule gets that rule's type; the rest of the list
  line becomes its reason.
- A frontmatter field named by a rule links to each value (slugs or ``[[links]]``).
- Any other frontmatter value written as ``[[link]]`` is a plain link (Obsidian treats it
  as one).
- An embed of a block (``![[page#^id]]``) is ``embeds``; every other link is
  ``links-to``, with the surrounding sentence as its reason.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from wiki_cli import links
from wiki_cli.pages import RAW, Page, Resolver
from wiki_cli.vocabulary import EMBEDS, LINKS_TO, MAX_REASON_LENGTH, Vocabulary

_LIST_PREFIX = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_LEADING_SEPARATOR = re.compile(r"^[\s—–:;,.-]+")
_TRAILING_SEPARATOR = re.compile(r"[—–:;,-]\s*$")
_LINK_TEXT = r"(?:!?\[\[[^\[\]\n]+?\]\]|!?\[[^\[\]\n]*\]\([^()\n]*\))"
_LEADING_LINKS = re.compile(rf"^(?:\s*{_LINK_TEXT}\s*(?:[,;&/]|\band\b)?\s*)+")
_ONLY_LINKS = re.compile(rf"{_LINK_TEXT}|[\s—–:;,.\-]")
_WIKILINK_VALUE = re.compile(r"^\s*\[\[([^\[\]\n]+?)\]\]\s*$")


@dataclass(frozen=True)
class Edge:
    target: str  # resolved slug, or the normalized link text when unresolved
    type: str
    reason: str
    resolved: bool


@dataclass(frozen=True)
class _Candidate:
    target: str
    type: str
    reason: str
    is_path: bool = False


def derive(page: Page, resolver: Resolver, vocabulary: Vocabulary | None = None) -> list[Edge]:
    """All outgoing edges of ``page``, one per target (the most specific type wins)."""
    if page.file.kind == RAW or page.text is None:
        return []
    vocabulary = vocabulary or (page.settings.vocabulary if page.settings else Vocabulary())
    page_type = page.page_type
    candidates: list[_Candidate] = []

    data = page.data or {}
    ruled_fields = set()
    for rule in vocabulary.field_rules:
        if not rule.applies_to(page_type) or rule.field not in data:
            continue
        ruled_fields.add(rule.field)
        for value in _values(data[rule.field]):
            candidates.append(_Candidate(_link_value(value), rule.type, rule.reason or f"Listed in {rule.field}."))
    for key, value in data.items():
        if key in ruled_fields:
            continue
        for item in _values(value):
            match = _WIKILINK_VALUE.match(item)
            if match:
                target = links.split_target(match.group(1))[0]
                candidates.append(_Candidate(target, LINKS_TO, f"Frontmatter: {key}."))

    for link in links.extract_links(page.body, page.file.rel):
        if link.embed and link.anchor and link.anchor.startswith("^"):
            candidates.append(_Candidate(link.target, EMBEDS, f"Embeds block {link.anchor[1:]}.", link.is_path))
            continue
        edge_type = vocabulary.heading_type(page_type, link.section) or LINKS_TO
        candidates.append(_Candidate(link.target, edge_type, _reason(link, edge_type), link.is_path))

    best: dict[str, Edge] = {}
    for candidate in candidates:
        resolved = (resolver.resolve_path if candidate.is_path else resolver.resolve)(candidate.target)
        if resolved is None and resolver.is_other_file(candidate.target):
            continue  # an attachment or unindexed note: it exists, but it is not a page
        key = resolved or links.normalize(candidate.target)
        if not key or key == page.slug:
            continue
        edge = Edge(key, candidate.type, _clip(candidate.reason), resolved is not None)
        current = best.get(key)
        if current is None or vocabulary.specificity(edge.type) < vocabulary.specificity(current.type):
            best[key] = edge
    return sorted(best.values(), key=lambda edge: (vocabulary.specificity(edge.type), edge.target))


def _values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(item) for item in value if isinstance(item, (str, int)) and str(item).strip()]
    return []


def _link_value(value: str) -> str:
    """A field value is a slug, or a ``[[link]]`` to one."""
    match = _WIKILINK_VALUE.match(value)
    return links.split_target(match.group(1))[0] if match else links.normalize(value)


def _reason(link: links.Link, edge_type: str) -> str:
    listed = _list_reason(link)
    if listed:
        return listed
    if edge_type != LINKS_TO:
        return f"Listed under {link.section.capitalize()}." if link.section else "Linked."
    heading = link.section.capitalize() if link.section else "Body"
    fragment = _fragment(link, max(MAX_REASON_LENGTH - len(heading) - 3, 60))
    return f"{heading}: {fragment}" if fragment else f"Linked under {heading}."


def _list_reason(link: links.Link) -> str:
    """For a list item, its role or description: the item without its leading links."""
    context = link.context or link.line
    if not _LIST_PREFIX.match(context):
        return ""
    text = _LIST_PREFIX.sub("", context)
    # A leading run of links is the item's subject list ("[[a]], [[b]] — role"); drop it whole.
    # A link inside the description ("Demo fixed on the [[engine]].") stays, as its name.
    text = _LEADING_LINKS.sub("", text)
    trimmed = text.rstrip(" .")
    if link.raw and trimmed.endswith(link.raw) and _TRAILING_SEPARATOR.search(trimmed[:-len(link.raw)]):
        text = trimmed[:-len(link.raw)]  # a link after a separator ("Co-lead: [[a]]") is the subject too
    if not _ONLY_LINKS.sub("", text):
        return ""  # nothing left but other links and separators: no description
    return _LEADING_SEPARATOR.sub("", links.plain(text)).rstrip(" —–:;,-")


def _fragment(link: links.Link, budget: int) -> str:
    """The sentence around the link, in plain text, showing the link within ``budget`` characters."""
    source = _LIST_PREFIX.sub("", link.context or link.line)
    text = links.plain(source)
    # Locate the link itself, not its name: the name can occur earlier (inside a longer slug).
    at = source.find(link.raw) if link.raw else -1
    if at >= 0:
        position = len(links.plain(source[:at]))
    else:
        position = text.find(link.display or link.target.rsplit("/", 1)[-1])
    if position < 0:
        return text[:MAX_REASON_LENGTH]
    before = text.rfind(". ", 0, position + 1)  # + 1: the link may start right after ". "
    start = before + 2 if before >= 0 else 0
    end = text.find(". ", position)
    lead = ""
    link_end = position + len(links.plain(link.raw)) if link.raw else position
    if link_end - start > budget:
        # The clipped reason would stop before the link: keep as much as fits before it instead.
        cut = link_end - budget + 2  # room for the ellipsis
        start = text.find(" ", cut, position) + 1 or position
        lead = "…"
    return lead + text[start:end + 1 if end >= 0 else len(text)].strip()


def _clip(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= MAX_REASON_LENGTH:
        return text
    return text[:MAX_REASON_LENGTH].rsplit(" ", 1)[0].rstrip(",;:") + "…"
