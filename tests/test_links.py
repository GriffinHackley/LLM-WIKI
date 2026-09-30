import pytest

from wiki_cli import links


@pytest.mark.parametrize("inner, expected", [
    ("charles-babbage", ("charles-babbage", None, None)),
    ("doc-slug|Short cite", ("doc-slug", None, "Short cite")),
    ("doc-slug\\|Cite in table", ("doc-slug", None, "Cite in table")),
    ("doc-slug#^q-no-list", ("doc-slug", "^q-no-list", None)),
    ("notebooks/difference-engine/claims|N claims", ("notebooks/difference-engine/claims", None, "N claims")),
    ("page.md#Heading|Label", ("page", "Heading", "Label")),
    ("folder\\sub\\page", ("folder/sub/page", None, None)),
])
def test_split_target(inner, expected):
    assert links.split_target(inner) == expected


def test_extract_links_tracks_sections_and_embeds():
    body = (
        "Intro [[a]].\n\n"
        "## Entities mentioned\n"
        "- [[charles-babbage]] — Inventor\n"
        "- [[doc|Cite]]; ![[doc2#^q-x]]\n"
        "\n---\n"
        "Footer [[ledger]]\n"
    )
    found = [(link.target, link.section, link.embed, link.anchor) for link in links.extract_links(body)]
    assert found == [
        ("a", "", False, None),
        ("charles-babbage", "entities mentioned", False, None),
        ("doc", "entities mentioned", False, None),
        ("doc2", "entities mentioned", True, "^q-x"),
        ("ledger", "", False, None),  # a horizontal rule ends the section
    ]


def test_links_in_code_are_ignored():
    body = "```\n[[in-fence]]\n## Not a heading\n```\nUse `[[in-inline-code]]` but [[real]].\n"
    assert [link.target for link in links.extract_links(body)] == ["real"]


def test_section_text_uses_first_matching_section_in_priority_order():
    body = "# T\n\n## What this is\nA memo.\n\n## Summary\nThe real summary\ncontinues.\n\nSecond para.\n"
    assert links.section_text(body, ("summary", "what this is")) == "The real summary continues."
    assert links.section_text(body, ("claim",)) is None


def test_plain_strips_markup():
    assert links.plain("See **[[doc|Cite]]** and [x](http://y) <span>z</span> ^q-id") == "See Cite and x z"


def test_plain_keeps_underscores_inside_words():
    assert links.plain("`huggingface_hub`, an _emphasised_ word, __bold__") == "huggingface_hub, an emphasised word, bold"
