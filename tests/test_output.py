"""Text output: color never changes the layout, and piped output is plain."""

import re

import pytest

from wiki_cli import output
from wiki_cli.cli import main

ANSI = re.compile(r"\033\[[0-9;]*m")


@pytest.fixture
def pages(wiki):
    wiki.page("person", "charles-babbage", {"Relationships": "- [[ada-lovelace]] — tutored her\n- [[ghost]] — absent"})
    wiki.page("person", "ada-lovelace", summary="A mathematician whose notes describe the first published "
                                                "algorithm for the analytical engine, written in 1843.")
    return wiki


def text(capsys, *args):
    capsys.readouterr()
    main([*args])
    captured = capsys.readouterr()
    return captured.out + captured.err


@pytest.mark.parametrize("args", [("neighbors", "charles-babbage"), ("list",), ("check", "--all"), ("unwritten",),
                                  ("vocab",), ("orphans",)])
def test_color_does_not_change_the_layout(pages, capsys, monkeypatch, args):
    root = ["--root", str(pages.root)]
    plain = text(capsys, *args, *root)
    monkeypatch.setattr(output, "_color", lambda stream: True)
    colored = text(capsys, *args, *root)
    assert ANSI.search(colored)
    assert ANSI.sub("", colored) == plain


def test_piped_output_is_not_wrapped(pages, capsys, monkeypatch):
    monkeypatch.delenv("COLUMNS", raising=False)
    out = text(capsys, "list", "--root", str(pages.root))
    assert "written in 1843." in [line for line in out.splitlines() if "ada-lovelace" in line][0]
    monkeypatch.setenv("COLUMNS", "60")
    out = text(capsys, "list", "--root", str(pages.root))
    assert all(len(line) <= 60 for line in out.splitlines())
    assert "  ada-lovelace     " in out  # continuation lines stay under the summary column


def test_rows_align_and_wrap(monkeypatch):
    monkeypatch.setenv("COLUMNS", "60")  # the narrowest width wrapped to
    lines = output.rows([("a", "short"), ("longer-key", "a much longer text that has to wrap onto more than one line, "
                                                       "under its own column")])
    assert lines[0] == "  a           short"
    assert all(line.startswith(" " * 14) for line in lines[2:]) and all(len(line) <= 60 for line in lines)
