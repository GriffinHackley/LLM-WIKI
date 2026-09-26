"""Parse and structurally validate relation declarations."""

from __future__ import annotations

import re

from wiki_cli.model import ERROR, WARNING, Issue, Relation
from wiki_cli.vocabulary import RELATION_FIELDS, RELATION_TYPES, SUPERSEDED_BY

_SPACE = r"[A-Za-z0-9][A-Za-z0-9._-]*"
_SEGMENT = r"[^/\s\[\]|#^\\]+"
_URI = re.compile(rf"wiki://(?P<space>{_SPACE})/(?P<slug>{_SEGMENT}(?:/{_SEGMENT})*)")
_SLUG = re.compile(rf"{_SEGMENT}(?:/{_SEGMENT})*")


def parse_uri(value: str) -> tuple[str, str] | None:
    """Return ``(space, slug)`` for a canonical ``wiki://`` URI, else None."""
    match = _URI.fullmatch(value)
    if not match or not is_valid_slug(match.group("slug")):
        return None
    return match.group("space"), match.group("slug")


def is_valid_slug(slug: str) -> bool:
    if not _SLUG.fullmatch(slug) or slug.lower().endswith(".md"):
        return False
    return all(segment not in (".", "..") for segment in slug.split("/"))


def make_uri(space: str | None, slug: str) -> str:
    return f"wiki://{space}/{slug}" if space else slug


def parse_relations(raw: object) -> tuple[list[Relation], list[Issue]]:
    """Parse a ``relations`` value. Invalid entries produce issues and are dropped."""
    if raw is None:
        return [], []
    if not isinstance(raw, list):
        return [], [Issue(ERROR, "invalid-relations", "'relations' must be a list")]

    relations: list[Relation] = []
    issues: list[Issue] = []
    for index, item in enumerate(raw):
        entry_issues: list[Issue] = []

        def fail(code: str, message: str) -> None:
            entry_issues.append(Issue(ERROR, code, message, relation=index))

        if not isinstance(item, dict):
            fail("invalid-relation", f"relations[{index}] must be a mapping")
            issues.extend(entry_issues)
            continue

        for key in sorted(set(map(str, item)) - set(RELATION_FIELDS)):
            fail("unknown-field", f"relations[{index}] has unknown field '{key}'")
        for field in RELATION_FIELDS:
            value = item.get(field)
            if value is None or (isinstance(value, str) and not value.strip()):
                fail("missing-field", f"relations[{index}] is missing '{field}'")
            elif not isinstance(value, str):
                fail("invalid-field", f"relations[{index}].{field} must be a string")

        target, relation_type, reason = (item.get(field) for field in RELATION_FIELDS)
        parsed = None
        if isinstance(relation_type, str) and relation_type.strip() and relation_type not in RELATION_TYPES:
            fail("unsupported-type", f"relations[{index}] has unsupported type '{relation_type}'")
        if isinstance(target, str) and target.strip():
            parsed = parse_uri(target)
            if parsed is None:
                fail("noncanonical-target", f"relations[{index}] target '{target}' is not a canonical wiki://<space>/<slug> URI")

        if entry_issues:
            issues.extend(entry_issues)
            continue
        space, slug = parsed
        relations.append(Relation(target, space, slug, relation_type, reason.strip(), index=index))
    return relations, issues


def parse_superseded_by(raw: object, local_space: str | None) -> tuple[list[Relation], list[Issue]]:
    """Read llm-wiki's native ``superseded_by`` field as a derived relation."""
    if raw is None or raw == "":
        return [], []
    if not isinstance(raw, str):
        return [], [Issue(WARNING, "invalid-superseded-by", "'superseded_by' must be a slug or wiki:// URI")]
    parsed = parse_uri(raw)
    if parsed is None:
        if not is_valid_slug(raw):
            return [], [Issue(WARNING, "invalid-superseded-by", f"'superseded_by' value '{raw}' is not a valid slug")]
        parsed = (local_space or "", raw)
    space, slug = parsed
    relation = Relation(
        target=make_uri(space, slug),
        space=space,
        slug=slug,
        type=SUPERSEDED_BY,
        reason="Replaces this page.",
        derived=True,
    )
    return [relation], []


def is_local(relation: Relation, local_space: str | None) -> bool:
    return local_space is None or relation.space in ("", local_space)
