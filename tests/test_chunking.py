from wiki_cli import frontmatter
from wiki_cli.chunking import CODE_BLOCK_LIMIT, MAX_CHARS, chunk_page


def chunks_of(body: str, summary: str | None = "Summary."):
    text = "---\ntitle: T\n---\n" + body
    return text, chunk_page(text, frontmatter.parse(text).body_offset, summary)


def test_summary_is_chunk_zero():
    _, chunks = chunks_of("Intro.\n")
    assert (chunks[0].ordinal, chunks[0].heading_path, chunks[0].text) == (0, "", "Summary.")


def test_splits_by_heading_with_paths():
    _, chunks = chunks_of(
        "Intro paragraph.\n\n# Setup\n\nSetup text.\n\n## Install\n\nInstall text.\n\n# Usage\n\nUsage text.\n")
    assert [(c.heading_path, c.text.splitlines()[-1]) for c in chunks[1:]] == [
        ("", "Intro paragraph."),
        ("Setup", "Setup text."),
        ("Setup > Install", "Install text."),
        ("Usage", "Usage text."),
    ]


def test_heading_without_content_is_skipped():
    _, chunks = chunks_of("# Parent\n\n## Child\n\nChild text.\n")
    assert [c.heading_path for c in chunks[1:]] == ["Parent > Child"]


def test_headings_inside_code_fences_are_ignored():
    _, chunks = chunks_of("# Real\n\n```bash\n# not a heading\necho hi\n```\n")
    assert [c.heading_path for c in chunks[1:]] == ["Real"]
    assert "# not a heading" in chunks[1].text


def test_offsets_point_into_page_text():
    text, chunks = chunks_of("# A\n\nAlpha text.\n\n# B\n\nBeta text.\n")
    for chunk in chunks[1:]:
        assert text[chunk.start:chunk.end].strip() == chunk.text


def test_long_section_is_split_with_size_cap():
    paragraphs = "\n\n".join(f"Paragraph {i} " + "word " * 60 for i in range(20))
    _, chunks = chunks_of(f"# Long\n\n{paragraphs}\n")
    body = chunks[1:]
    assert len(body) > 1
    assert all(len(c.text) <= MAX_CHARS for c in body)
    assert all(c.heading_path == "Long" for c in body)
    joined = " ".join(c.text for c in body)
    assert all(f"Paragraph {i} " in joined for i in range(20))


def test_code_block_kept_whole_when_moderately_large():
    code = "\n".join(f"line_{i} = {i}" for i in range(150))
    assert MAX_CHARS < len(code) < CODE_BLOCK_LIMIT
    _, chunks = chunks_of(f"# Code\n\nIntro.\n\n```python\n{code}\n```\n")
    assert any("line_0 = 0" in c.text and "line_149 = 149" in c.text for c in chunks)


def test_huge_single_line_is_split():
    _, chunks = chunks_of("x" * (MAX_CHARS * 5) + "\n")
    assert len(chunks) > 2
    assert all(len(c.text) <= MAX_CHARS * 2 for c in chunks)


def test_heading_links_are_cleaned():
    _, chunks = chunks_of("# See [[tms/save|Save]] and [docs](http://x)\n\nText.\n")
    assert chunks[1].heading_path == "See Save and docs"


def test_missing_summary_gives_empty_chunk_zero():
    _, chunks = chunks_of("Text.\n", summary=None)
    assert chunks[0].text == ""
