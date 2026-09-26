"""Controlled relationship vocabulary.

This module is the single source of truth. ``schemas/relations.schema.json``
duplicates the enum; a test asserts the two stay in sync.
"""

VOCABULARY_VERSION = 1

# Types an author may declare in a page's ``relations`` list.
RELATION_TYPES = (
    "depends-on",
    "implements",
    "implemented-by",
    "used-by",
    "configures",
    "tested-by",
    "documents",
    "related-to",
)

# Types derived from other llm-wiki frontmatter fields, never declared in ``relations``.
SUPERSEDED_BY = "superseded-by"
DERIVED_TYPES = (SUPERSEDED_BY,)

# Label shown for an edge when viewed from its target page.
INVERSE_LABELS = {
    "depends-on": "used-by",
    "implements": "implemented-by",
    "implemented-by": "implements",
    "used-by": "depends-on",
    "configures": "configured-by",
    "tested-by": "tests",
    "documents": "documented-by",
    "related-to": "related-to",
    SUPERSEDED_BY: "supersedes",
}

# Pairs that contradict each other when declared from one page to the same target.
CONTRADICTORY_PAIRS = (
    frozenset({"implements", "implemented-by"}),
    frozenset({"depends-on", "used-by"}),
)

FALLBACK_TYPE = "related-to"
RELATION_FIELDS = ("target", "type", "reason")

# Lint thresholds; exceeding them produces warnings, not errors.
MAX_REASON_LENGTH = 160
MAX_SUMMARY_LENGTH = 250
MAX_OUTGOING = 12
MAX_INCOMING = 50
