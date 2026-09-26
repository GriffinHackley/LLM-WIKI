"""Relationship types derived from page structure, and how to derive them."""

VOCABULARY_VERSION = 2

# Edge types, most specific first. When one page reaches a target by several
# routes, the earliest type in this tuple wins.
EDGE_TYPES = (
    "rests-on",
    "supports",
    "sourced-by",
    "involves",
    "located-at",
    "hosted",
    "associated-with",
    "mentions",
    "appears-in",
    "synthesizes",
    "transcribes",
    "quotes",
    "draws-on",
    "links-to",
)

# Label shown for an edge when viewed from its target page.
INVERSE_LABELS = {
    "rests-on": "premise-of",
    "supports": "supported-by",
    "sourced-by": "source-for",
    "involves": "participant-in",
    "located-at": "location-of",
    "hosted": "held-at",
    "associated-with": "associated-with",
    "mentions": "mentioned-in",
    "appears-in": "features",
    "synthesizes": "synthesized-in",
    "transcribes": "text-of",
    "quotes": "quoted-in",
    "draws-on": "drawn-on-by",
    "links-to": "linked-from",
}

# Section headings (lowercase) whose links carry a specific type, by page type.
# "*" applies to every page type.
SECTION_EDGES: dict[str, dict[str, str]] = {
    "document": {"entities mentioned": "mentions", "claims supported": "supports"},
    "person": {"relationships": "associated-with", "people associated": "associated-with",
               "appearances in sources": "appears-in"},
    "organization": {"people associated": "associated-with", "relationships": "associated-with",
                     "appearances in sources": "appears-in"},
    "place": {"people associated": "associated-with", "events here": "hosted",
              "appearances in sources": "appears-in"},
    "event": {"participants": "involves", "location": "located-at", "sources": "appears-in"},
    "claim": {"sources": "sourced-by", "rests on": "rests-on", "supports": "supports"},
    "topic": {"key pages": "synthesizes"},
}

# Sections where bare claim IDs (EF-049) count as links to claim pages.
CLAIM_ID_SECTIONS = {"claims supported", "rests on", "supports"}

MAX_SUMMARY_LENGTH = 300
MAX_REASON_LENGTH = 160


def specificity(edge_type: str) -> int:
    return EDGE_TYPES.index(edge_type) if edge_type in EDGE_TYPES else len(EDGE_TYPES)
