"""Derive typed, reasoned edges from a page's sections and frontmatter."""

from __future__ import annotations

import re
from dataclasses import dataclass

from wiki_cli import links
from wiki_cli.pages import RAW, Page, Resolver
from wiki_cli.vocabulary import CLAIM_ID_SECTIONS, MAX_REASON_LENGTH, SECTION_EDGES, specificity

_LIST_PREFIX = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_LEADING_SEPARATOR = re.compile(r"^[\s—–:;,.-]+")
_LEADING_LINKS = re.compile(r"^(?:\s*!?\[\[[^\[\]\n]+?\]\]\s*(?:[,;&/]|\band\b)?\s*)+")


@dataclass(frozen=True)
class Edge:
    target: str  # resolved slug, or the normalized link text when unresolved
    type: str
    reason: str
    resolved: bool


def derive(page: Page, resolver: Resolver) -> list[Edge]:
    """All outgoing edges of ``page``, one per target (the most specific type wins)."""
    if page.file.kind == RAW or page.text is None:
        return []
    page_type = page.page_type or ""
    section_types = {**SECTION_EDGES.get("*", {}), **SECTION_EDGES.get(page_type, {})}
    candidates: list[tuple[str, str, str]] = []  # (target text, type, reason)

    data = page.data or {}
    for source in _string_list(data.get("sources")):
        candidates.append((source, "draws-on", "Listed in sources."))
    for premise in _string_list(data.get("rests_on")):
        candidates.append((premise, "rests-on", "Premise of this argument."))
    source_path = data.get("source_path")
    if isinstance(source_path, str) and source_path.strip():
        stem = source_path.strip().rsplit("/", 1)[-1].rsplit(".", 1)[0]
        candidates.append((f"raw/{stem}", "transcribes", "Extracted text of this document."))

    for link in links.extract_links(page.body):
        if link.embed and link.anchor and link.anchor.startswith("^"):
            candidates.append((link.target, "quotes", f"Embeds quote {link.anchor[1:]}."))
            continue
        edge_type = section_types.get(link.section, "links-to")
        candidates.append((link.target, edge_type, _reason(link, edge_type)))

    for claim_id, section, line in links.claim_ids(page.body, CLAIM_ID_SECTIONS):
        edge_type = section_types.get(section, "links-to")
        candidates.append((claim_id, edge_type, _list_reason(line, claim_id) or f"Listed under {section.capitalize()}."))

    best: dict[str, Edge] = {}
    for target_text, edge_type, reason in candidates:
        resolved = resolver.resolve(target_text)
        if resolved is None and resolver.is_other_file(target_text):
            continue  # an attachment or unindexed note: it exists, but it is not a page
        key = resolved or links.normalize(target_text)
        if not key or key == page.slug:
            continue
        edge = Edge(key, edge_type, _clip(reason), resolved is not None)
        current = best.get(key)
        if current is None or specificity(edge_type) < specificity(current.type):
            best[key] = edge
    return sorted(best.values(), key=lambda edge: (specificity(edge.type), edge.target))


def _reason(link: links.Link, edge_type: str) -> str:
    listed = _list_reason(link.line, link.target, link.display)
    if listed:
        return listed
    if edge_type != "links-to":
        return f"Listed under {link.section.capitalize()}." if link.section else "Linked."
    fragment = _fragment(link)
    heading = link.section.capitalize() if link.section else "Body"
    return f"{heading}: {fragment}" if fragment else f"Linked under {heading}."


def _list_reason(line: str, target: str, display: str | None = None) -> str:
    """For a list item, the line with the link itself removed: its role or description."""
    if not _LIST_PREFIX.match(line):
        return ""
    text = _LIST_PREFIX.sub("", line)

    def drop_own_link(match: re.Match) -> str:
        own = links.split_target(match.group(0).lstrip("!")[2:-2])[0] == target
        return "" if own else match.group(0)

    if "[[" in text:
        # A leading run of links is the item's subject list ("[[a]], [[b]] — role"); drop it whole.
        text = _LEADING_LINKS.sub("", text)
        text = re.sub(r"!?\[\[[^\[\]\n]+?\]\]", drop_own_link, text, count=0)
    else:
        text = text.replace(target, "", 1)
    # Nothing left but other links and separators: the line carries no description.
    if not re.sub(r"!?\[\[[^\[\]\n]+?\]\]|[\s—–:;,.\-]", "", text):
        return ""
    return _LEADING_SEPARATOR.sub("", links.plain(text))


def _fragment(link: links.Link) -> str:
    """The sentence around the link, in plain text."""
    text = links.plain(_LIST_PREFIX.sub("", link.line))
    label = link.display or link.target.rsplit("/", 1)[-1]
    position = text.find(label)
    if position < 0:
        return text[:MAX_REASON_LENGTH]
    start = max(text.rfind(". ", 0, position) + 2, 0) if text.rfind(". ", 0, position) >= 0 else 0
    end = text.find(". ", position)
    return text[start:end + 1 if end >= 0 else len(text)].strip()


def _clip(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= MAX_REASON_LENGTH:
        return text
    return text[:MAX_REASON_LENGTH].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def _string_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(item) for item in value if isinstance(item, (str, int)) and str(item).strip()]
    return []
