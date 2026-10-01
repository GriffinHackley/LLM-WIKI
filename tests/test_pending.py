"""`wiki pending`: sources in raw/ that no page links to yet."""

import json
from pathlib import Path

import pytest

from wiki_cli.cli import main
from wiki_cli.config import load_settings
from wiki_cli.scaffold import scaffold
from wiki_cli.sources import pending, raw_dirs


def write(root: Path, rel: str, data: str | bytes) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
    return path


def source_page(root: Path, slug: str, body: str, front: str = "") -> None:
    write(root, f"wiki/sources/{slug}.md", f"---\ntitle: {slug}\ntype: source\n{front}---\n# {slug}\n\n{body}\n")


@pytest.fixture
def wiki(tmp_path) -> Path:
    root = tmp_path / "wiki"
    scaffold(root, preset="research")
    return root


def keys(root: Path) -> list[str]:
    return [source.key for source in pending(load_settings(root))[0]]


def test_a_new_wiki_has_nothing_pending(wiki, capsys):
    assert main(["pending", "--root", str(wiki), "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"raw_dirs": ["raw"], "pending": [], "ingested": 0}


def test_files_sharing_a_name_are_one_source(wiki):
    write(wiki, "raw/report.pdf", b"%PDF")
    write(wiki, "raw/report.txt", "text")
    write(wiki, "raw/notes/interview.html", "<p>hi</p>")
    waiting, ingested = pending(load_settings(wiki))
    assert [(s.key, s.files) for s in waiting] == [
        ("raw/notes/interview", ["raw/notes/interview.html"]),
        ("raw/report", ["raw/report.pdf", "raw/report.txt"]),
    ] and ingested == 0


@pytest.mark.parametrize("link", [
    "[[raw/report.pdf]]",  # a wikilink by path, to either file
    "[[raw/report.txt|the text]]",
    "[[raw/report]]",  # the indexed text's slug
    "[[report.pdf]]",  # a bare name, as Obsidian writes it
    "[the report](../../raw/report.pdf)",  # a Markdown link, relative to the page
    "![[raw/report.pdf#page=3]]",
])
def test_any_link_to_any_of_its_files_ingests_a_source(wiki, link):
    write(wiki, "raw/report.pdf", b"%PDF")
    write(wiki, "raw/report.txt", "text")
    write(wiki, "raw/other.pdf", b"%PDF other")
    source_page(wiki, "report-2024", f"## Original\n{link}")
    assert keys(wiki) == ["raw/other"]


@pytest.mark.parametrize("front", ["source_path: raw/report.pdf\n", 'original: "[[report.pdf]]"\n',
                                   "files: [raw/report.txt]\n"])
def test_frontmatter_naming_a_file_ingests_it(wiki, front):
    write(wiki, "raw/report.pdf", b"%PDF")
    write(wiki, "raw/report.txt", "text")
    source_page(wiki, "report-2024", "No link in the body.", front)
    assert keys(wiki) == []


def test_a_link_to_a_similar_name_does_not_count(wiki):
    write(wiki, "raw/report.pdf", b"%PDF")
    source_page(wiki, "report-2024", "[[raw/report-2.pdf]] and [[report]] the page slug is not a file.")
    # a bare [[report]] names the source by its shared name, which is how Obsidian links a .txt
    assert keys(wiki) == []
    source_page(wiki, "report-2024", "[[raw/report-2.pdf]] only.")
    assert keys(wiki) == ["raw/report"]


def test_code_links_and_urls_are_not_sources(wiki):
    write(wiki, "raw/app.py", "print()")
    source_page(wiki, "x", "[code](code:raw/app.py)", 'url: "https://example.com/raw/app.py"\n')
    assert keys(wiki) == ["raw/app"]


def test_duplicates_of_ingested_or_listed_files_are_flagged(wiki):
    write(wiki, "raw/report.pdf", b"same bytes")
    write(wiki, "raw/report-copy.pdf", b"same bytes")
    write(wiki, "raw/later.pdf", b"new bytes")
    write(wiki, "raw/later-again.pdf", b"new bytes")
    source_page(wiki, "report-2024", "[[raw/report.pdf]]")
    waiting, ingested = pending(load_settings(wiki))
    assert ingested == 1
    assert {(s.key, s.duplicate_of) for s in waiting} == {
        ("raw/later", None), ("raw/later-again", "raw/later.pdf"), ("raw/report-copy", "raw/report.pdf")}


def test_readmes_dotfiles_and_ignored_files_are_not_sources(wiki):
    write(wiki, "raw/README.md", "What goes here.")
    write(wiki, "raw/SOURCES.md", "Manifest.")
    write(wiki, "raw/sub/README.txt", "Notes.")
    write(wiki, "raw/report.pdf", b"%PDF")
    assert keys(wiki) == ["raw/report", "raw/SOURCES"]  # raw/.gitkeep is hidden
    config = wiki / ".wiki-cli.toml"
    config.write_text(config.read_text(encoding="utf-8") + '\n[pending]\nignore = ["raw/SOURCES.md"]\n',
                      encoding="utf-8")
    assert keys(wiki) == ["raw/report"]


def test_raw_folders_come_from_the_raw_patterns(tmp_path):
    root = tmp_path / "vault"
    write(root, ".wiki-cli.toml", 'raw = ["sources/text/**/*.txt", "**/*.log", "archive/*.txt"]\n')
    (root / "sources/text").mkdir(parents=True)
    assert raw_dirs(load_settings(root)) == ["archive", "sources/text"]
    write(root, "raw/x.pdf", b"%PDF")
    assert raw_dirs(load_settings(root)) == ["archive", "raw", "sources/text"]


def test_text_output(wiki, capsys):
    write(wiki, "raw/report.pdf", b"%PDF")
    write(wiki, "raw/report.txt", "text")
    assert main(["pending", "--root", str(wiki)]) == 0
    out = capsys.readouterr().out
    assert out == "Sources not ingested yet (1)\n  raw/report.pdf, raw/report.txt\n1 pending, 0 ingested, in raw/\n"
