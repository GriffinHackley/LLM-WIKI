"""Relationship types: two built-ins plus whatever the wiki's `[[relations]]` rules define.

With no rules, every link is a generic `links-to` edge whose reason is the sentence
around it. Rules in `.wiki-cli.toml` give links under a heading, or values of a
frontmatter field, a specific type:

    [[relations]]
    heading = "Entities mentioned"   # or a list of headings
    page_type = "document"           # optional, or a list
    type = "mentions"
    inverse = "mentioned-in"         # label shown from the target page

    [[relations]]
    field = "sources"                # frontmatter field holding slugs or [[links]]
    type = "draws-on"
    inverse = "drawn-on-by"
    reason = "Listed in sources."    # optional fixed reason

When a page reaches one target by several routes, the most specific type wins:
heading-rule types in the order their first rule appears, then block embeds, then
types that only come from frontmatter fields, then plain links.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

EMBEDS = "embeds"
LINKS_TO = "links-to"
REFERS_TO_CODE = "refers-to-code"  # a `code:` link to a file in the code repo a code wiki describes
BUILTIN_INVERSES = {EMBEDS: "embedded-in", REFERS_TO_CODE: "referred-to-by", LINKS_TO: "linked-from"}

MAX_REASON_LENGTH = 160
_TYPE_NAME = re.compile(r"[a-z][a-z0-9-]*")


class RuleError(Exception):
    pass


@dataclass(frozen=True)
class RelationRule:
    type: str
    inverse: str
    headings: tuple[str, ...] = ()  # lowercased
    field: str | None = None
    page_types: tuple[str, ...] = ()  # lowercased; empty = any page type
    reason: str | None = None

    def applies_to(self, page_type: str | None) -> bool:
        return not self.page_types or (page_type or "") in self.page_types


def parse_rules(raw: object) -> tuple[RelationRule, ...]:
    """Validate the `[[relations]]` array from `.wiki-cli.toml`."""
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise RuleError("[[relations]] must be an array of tables")
    rules: list[RelationRule] = []
    inverses: dict[str, str] = dict(BUILTIN_INVERSES)
    for index, item in enumerate(raw, start=1):
        where = f"relations rule {index}"
        if not isinstance(item, dict):
            raise RuleError(f"{where} must be a table")
        unknown = set(item) - {"heading", "field", "page_type", "type", "inverse", "reason"}
        if unknown:
            raise RuleError(f"{where}: unknown key(s) {', '.join(sorted(unknown))}")
        edge_type, inverse = item.get("type"), item.get("inverse")
        if not isinstance(edge_type, str) or not _TYPE_NAME.fullmatch(edge_type):
            raise RuleError(f"{where}: 'type' must be lowercase words joined by hyphens")
        if edge_type in BUILTIN_INVERSES:
            raise RuleError(f"{where}: '{edge_type}' is built in")
        if not isinstance(inverse, str) or not _TYPE_NAME.fullmatch(inverse):
            raise RuleError(f"{where}: 'inverse' must be lowercase words joined by hyphens")
        if inverses.setdefault(edge_type, inverse) != inverse:
            raise RuleError(f"{where}: '{edge_type}' already has inverse '{inverses[edge_type]}'")
        headings = _strings(item.get("heading"), f"{where}: 'heading'")
        field = item.get("field")
        if field is not None and (not isinstance(field, str) or not field.strip()):
            raise RuleError(f"{where}: 'field' must be a frontmatter key")
        if bool(headings) == bool(field):
            raise RuleError(f"{where}: give exactly one of 'heading' or 'field'")
        reason = item.get("reason")
        if reason is not None and not isinstance(reason, str):
            raise RuleError(f"{where}: 'reason' must be a string")
        rules.append(RelationRule(
            type=edge_type,
            inverse=inverse,
            headings=tuple(h.strip().lower() for h in headings),
            field=field.strip() if field else None,
            page_types=tuple(t.strip().lower() for t in _strings(item.get("page_type"), f"{where}: 'page_type'")),
            reason=reason,
        ))
    return tuple(rules)


def _strings(value: object, label: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return list(value)
    raise RuleError(f"{label} must be a string or a list of strings")


class Vocabulary:
    """Edge types in precedence order, with inverse labels, for one wiki's rules."""

    def __init__(self, rules: tuple[RelationRule, ...] = ()):
        self.rules = rules
        self.inverses: dict[str, str] = {}
        for rule in rules:
            self.inverses.setdefault(rule.type, rule.inverse)
        self.inverses.update(BUILTIN_INVERSES)
        # Precedence: types from heading rules (in file order), then block embeds, then types
        # that only come from frontmatter fields (usually generic "listed in" metadata), then
        # plain links.
        heading_types = [rule.type for rule in rules if rule.headings]
        field_only = [rule.type for rule in rules if rule.field and rule.type not in heading_types]
        self.types = tuple(dict.fromkeys(heading_types + [EMBEDS] + field_only + [REFERS_TO_CODE, LINKS_TO]))
        self.field_rules = tuple(rule for rule in rules if rule.field)

    def heading_type(self, page_type: str | None, heading: str) -> str | None:
        """The type for a link under ``heading`` (lowercased) on a page of ``page_type``."""
        for rule in self.rules:
            if heading in rule.headings and rule.applies_to(page_type):
                return rule.type
        return None

    def specificity(self, edge_type: str) -> int:
        return self.types.index(edge_type) if edge_type in self.types else len(self.types)

    def inverse(self, edge_type: str) -> str:
        return self.inverses.get(edge_type, edge_type)

    def rank_label(self, label: str) -> int:
        """Precedence of a type shown either forwards or as its inverse."""
        for edge_type in self.types:
            if label in (edge_type, self.inverses[edge_type]):
                return self.specificity(edge_type)
        return len(self.types)

    def fingerprint(self) -> str:
        return repr((self.rules, self.types))
