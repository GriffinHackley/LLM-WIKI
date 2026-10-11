"""The docs agree with the command: every command and option is documented in
docs/commands.md, and every `wiki ...` command line in the docs uses ones that exist."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pytest

from wiki_cli.cli import build_parser

ROOT = Path(__file__).resolve().parent.parent
COMMANDS_DOC = ROOT / "docs" / "commands.md"
if not COMMANDS_DOC.is_file():  # the sdist ships the tests without the docs
    pytest.skip("docs/ is not here", allow_module_level=True)
DOCS = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]
# Documented once, under "Options most commands take".
COMMON = {"--root", "--cache", "--format", "--embed-model", "--reranker", "-h", "--help"}


def subparsers(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    return {}


def commands(parser=None, path=()) -> dict[tuple[str, ...], argparse.ArgumentParser]:
    """Every runnable command, by its words after `wiki`: ("nav", "start"), ("search",)."""
    parser = parser or build_parser()
    found = {}
    for name, sub in subparsers(parser).items():
        children = commands(sub, (*path, name))
        found.update(children or {(*path, name): sub})
    return found


def options(parser: argparse.ArgumentParser) -> set[str]:
    return {flag for action in parser._actions for flag in action.option_strings}


def sections() -> dict[tuple[str, ...], str]:
    """docs/commands.md's sections, by the command each one's `### `wiki ...`` heading names."""
    text = COMMANDS_DOC.read_text(encoding="utf-8")
    parts = re.split(r"^### `wiki ([^`]+)`\s*$", text, flags=re.MULTILINE)
    return {tuple(name.split()): re.split(r"^##? ", body, flags=re.MULTILINE)[0]
            for name, body in zip(parts[1::2], parts[2::2])}


@pytest.mark.parametrize("path", sorted(commands()), ids=" ".join)
def test_every_command_and_option_is_documented(path):
    parser = commands()[path]
    section = sections().get(path)
    assert section is not None, f"docs/commands.md has no ### `wiki {' '.join(path)}` section"
    for action in parser._actions:
        if action.help == argparse.SUPPRESS:
            continue
        if action.option_strings:
            if not set(action.option_strings) & COMMON:
                assert any(f"`{flag}`" in section for flag in action.option_strings), \
                    f"wiki {' '.join(path)}: {action.option_strings[-1]} is not documented"
        elif not isinstance(action, argparse._SubParsersAction):
            name = action.metavar or action.dest
            assert f"`{name}`" in section, f"wiki {' '.join(path)}: {name} is not documented"


def test_documented_commands_exist():
    assert set(sections()) <= set(commands()), set(sections()) - set(commands())


def command_lines():
    """(file, line) for each `wiki ...` command in a shell block or an inline code span."""
    for doc in DOCS:
        text = doc.read_text(encoding="utf-8")
        for block in re.findall(r"```bash\n(.*?)```", text, flags=re.DOTALL):
            for line in block.splitlines():
                if line.startswith("wiki "):
                    yield doc.name, line
        for span in re.findall(r"`(wiki [^`]+)`", text):
            yield doc.name, span


def test_command_lines_in_the_docs_use_real_commands_and_flags():
    parser, problems = build_parser(), []
    for doc, line in command_lines():
        words = line.split("#")[0].replace("\\", " ").split()[1:]
        if words[:1] == ["="]:
            continue  # a config line: wiki = "<path>"
        current, path = parser, []
        while words and words[0] in subparsers(current):
            current = subparsers(current)[words[0]]
            path.append(words.pop(0))
        if subparsers(current) and not path:
            if words and not words[0].startswith(("-", "<", "[")) and words[0] not in ("...",):
                problems.append(f"{doc}: unknown command in `{line}`")
            continue
        known = options(current)
        for word in words:
            flag = word.split("=")[0]
            if flag.startswith("-") and flag not in known and re.fullmatch(r"--?[a-z][a-z-]*", flag):
                problems.append(f"{doc}: `wiki {' '.join(path)}` has no {flag} (in `{line}`)")
    assert not problems, "\n".join(problems)
