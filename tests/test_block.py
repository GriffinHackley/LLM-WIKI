import pytest

from wiki_cli import block, frontmatter
from wiki_cli.block import END_MARKER, START_MARKER


def apply(text: str, slugs: list[str], newline: str = "\n") -> str:
    offset = frontmatter.parse(text).body_offset
    return block.apply(text, offset, slugs, newline)


PAGE = "---\ntitle: A\n---\n# A\n\nProse.\n"


def test_appends_block_after_blank_line():
    result = apply(PAGE, ["b", "tms/c"])
    assert result == PAGE + f"\n{START_MARKER}\n[[b]]\n[[tms/c]]\n{END_MARKER}\n"


def test_apply_is_idempotent():
    once = apply(PAGE, ["b"])
    assert apply(once, ["b"]) == once


def test_replaces_existing_block_in_place():
    middle = PAGE + f"\n{START_MARKER}\n[[old]]\n{END_MARKER}\n\nTrailing prose.\n"
    result = apply(middle, ["new"])
    assert "[[old]]" not in result
    assert result.endswith(f"{START_MARKER}\n[[new]]\n{END_MARKER}\n\nTrailing prose.\n")


def test_removing_last_relation_restores_original():
    assert apply(apply(PAGE, ["b"]), []) == PAGE


def test_removes_block_from_middle_of_body():
    middle = PAGE + f"\n{START_MARKER}\n[[old]]\n{END_MARKER}\n\nTrailing prose.\n"
    assert apply(middle, []) == PAGE + "\nTrailing prose.\n"


def test_no_block_and_no_slugs_is_unchanged():
    assert apply(PAGE, []) == PAGE


def test_preserves_missing_final_newline():
    text = PAGE.rstrip("\n")
    result = apply(text, ["b"])
    assert not result.endswith("\n")
    assert apply(result, ["b"]) == result
    assert apply(result, []) == text


def test_crlf_newlines_preserved():
    text = PAGE.replace("\n", "\r\n")
    result = apply(text, ["b"], "\r\n")
    assert "\n" not in result.replace("\r\n", "")
    assert result.endswith(f"\r\n{START_MARKER}\r\n[[b]]\r\n{END_MARKER}\r\n")
    assert apply(result, ["b"], "\r\n") == result


def test_empty_body_gets_block():
    text = "---\ntitle: A\n---\n"
    result = apply(text, ["b"])
    assert result == text + f"{START_MARKER}\n[[b]]\n{END_MARKER}\n"
    assert apply(result, []) == text


def test_frontmatter_without_trailing_newline():
    text = "---\ntitle: A\n---"
    result = apply(text, ["b"])
    assert result == text + f"\n{START_MARKER}\n[[b]]\n{END_MARKER}\n"


@pytest.mark.parametrize("body, code", [
    (f"{START_MARKER}\n[[a]]\n", "malformed-block"),
    (f"{END_MARKER}\n", "malformed-block"),
    (f"{START_MARKER}\n{END_MARKER}\n{START_MARKER}\n{END_MARKER}\n", "malformed-block"),
    ("<!-- wiki-relations:v2:start -->\n<!-- wiki-relations:v2:end -->\n", "unsupported-block-version"),
])
def test_malformed_markers_raise(body, code):
    with pytest.raises(block.BlockError) as info:
        block.find(body)
    assert info.value.code == code


def test_marker_must_be_on_its_own_line():
    assert block.find(f"inline {START_MARKER} text\n") is None


def test_has_only_links_detects_manual_content():
    found = block.find(f"{START_MARKER}\n[[a]]\nmanual note\n{END_MARKER}\n")
    assert not block.has_only_links(found)
    assert block.has_only_links(block.find(f"{START_MARKER}\n[[a]]\n{END_MARKER}\n"))


def test_strip_removes_block_for_hashing():
    body = f"Prose.\n\n{START_MARKER}\n[[a]]\n{END_MARKER}\n"
    assert block.strip(body) == "Prose.\n"


class TestFrontmatter:
    def test_no_frontmatter(self):
        assert frontmatter.parse("# Just a heading\n").data is None

    def test_empty_frontmatter_is_empty_mapping(self):
        parsed = frontmatter.parse("---\n---\nbody")
        assert parsed.data == {}
        assert parsed.body_offset == len("---\n---\n")

    def test_dots_close_frontmatter(self):
        assert frontmatter.parse("---\na: 1\n...\nbody").data == {"a": 1}

    @pytest.mark.parametrize("text", ["---\na: 1\n", "---\na: [1\n---\n", "---\n- a\n---\n"])
    def test_invalid(self, text):
        with pytest.raises(frontmatter.FrontmatterError):
            frontmatter.parse(text)

    def test_crlf(self):
        parsed = frontmatter.parse("---\r\na: 1\r\n---\r\nbody")
        assert parsed.data == {"a": 1}
        assert "---\r\na: 1\r\n---\r\nbody"[parsed.body_offset:] == "body"
