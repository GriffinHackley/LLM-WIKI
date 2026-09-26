import json
import re
from pathlib import Path

import pytest

from wiki_cli.relations import parse_relations, parse_superseded_by, parse_uri
from wiki_cli.vocabulary import INVERSE_LABELS, RELATION_TYPES

SCHEMA = json.loads((Path(__file__).parents[1] / "schemas" / "relations.schema.json").read_text(encoding="utf-8"))


def codes(raw):
    return [issue.code for issue in parse_relations(raw)[1]]


def entry(**fields):
    base = {"target": "wiki://sp/tms/save", "type": "depends-on", "reason": "Saves data."}
    base.update(fields)
    return {key: value for key, value in base.items() if value is not ...}


def test_valid_relation():
    relations, issues = parse_relations([entry()])
    assert issues == []
    assert relations[0].space == "sp"
    assert relations[0].slug == "tms/save"
    assert relations[0].index == 0


def test_absent_relations():
    assert parse_relations(None) == ([], [])
    assert parse_relations([]) == ([], [])


@pytest.mark.parametrize("raw, code", [
    ("not a list", "invalid-relations"),
    (["not a mapping"], "invalid-relation"),
    ([entry(target=...)], "missing-field"),
    ([entry(type=...)], "missing-field"),
    ([entry(reason="   ")], "missing-field"),
    ([entry(reason=5)], "invalid-field"),
    ([entry(extra="x")], "unknown-field"),
    ([entry(type="blocks")], "unsupported-type"),
    ([entry(target="tms/save")], "noncanonical-target"),
    ([entry(target="[[tms/save]]")], "noncanonical-target"),
    ([entry(target="wiki://sp/tms/save.md")], "noncanonical-target"),
    ([entry(target="wiki://sp/tms/../save")], "noncanonical-target"),
    ([entry(target="wiki://sp/tms//save")], "noncanonical-target"),
    ([entry(target=" wiki://sp/tms/save")], "noncanonical-target"),
])
def test_structural_errors(raw, code):
    assert code in codes(raw)


def test_invalid_entries_are_dropped_but_valid_ones_kept():
    relations, issues = parse_relations([entry(type="bogus"), entry(target="wiki://sp/other")])
    assert [relation.slug for relation in relations] == ["other"]
    assert issues[0].relation == 0


@pytest.mark.parametrize("uri, expected", [
    ("wiki://sp-wiki/tms-save", ("sp-wiki", "tms-save")),
    ("wiki://notes/concepts/attention", ("notes", "concepts/attention")),
    ("wiki://sp/a b", None),
    ("wiki:///slug", None),
    ("wiki://sp/", None),
    ("http://sp/slug", None),
])
def test_parse_uri(uri, expected):
    assert parse_uri(uri) == expected


def test_superseded_by_bare_slug_is_local():
    relations, issues = parse_superseded_by("tms/new-page", "sp")
    assert issues == []
    assert relations[0].derived
    assert relations[0].target == "wiki://sp/tms/new-page"
    assert relations[0].type == "superseded-by"


def test_superseded_by_invalid_is_warning():
    _, issues = parse_superseded_by(["a"], "sp")
    assert issues[0].severity == "warning"


def test_schema_enum_matches_vocabulary():
    assert tuple(SCHEMA["items"]["properties"]["type"]["enum"]) == RELATION_TYPES


def test_every_type_has_an_inverse():
    assert set(RELATION_TYPES) <= set(INVERSE_LABELS)


@pytest.mark.parametrize("uri", [
    "wiki://sp/tms/save", "wiki://sp-wiki/a", "wiki://x/a/b/c", "wiki://sp/a b", "wiki://sp/a//b", "wiki://sp/[[a]]",
])
def test_schema_pattern_agrees_with_parser(uri):
    pattern = SCHEMA["items"]["properties"]["target"]["pattern"]
    # The schema cannot express the .md and dot-segment rules; the parser is stricter there.
    assert bool(re.search(pattern, uri)) == (parse_uri(uri) is not None)
