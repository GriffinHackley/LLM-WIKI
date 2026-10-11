"""The publish workflow's release notes: one version's section of CHANGELOG.md."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / ".github" / "release_notes.py"
spec = importlib.util.spec_from_file_location("release_notes", SCRIPT)
release_notes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_notes)

CHANGELOG = """# Changelog

## Unreleased

- Not yet.

## 1.1.0 (2026-11-02)

- Faster search.

## 1.0.0 (2026-10-11): first release

The first one.

- Everything.
"""


def test_a_dated_section_is_the_notes_up_to_the_next_heading():
    assert release_notes.section("1.1.0", CHANGELOG) == ("1.1.0 (2026-11-02)", "- Faster search.\n")


def test_a_subtitle_goes_in_the_title_and_the_last_section_runs_to_the_end():
    title, notes = release_notes.section("1.0.0", CHANGELOG)
    assert title == "1.0.0: first release (2026-10-11)"
    assert notes == "The first one.\n\n- Everything.\n"


@pytest.mark.parametrize("version", ["1.2.0", "1.1", "Unreleased"])
def test_a_version_without_a_dated_section_is_refused(version):
    with pytest.raises(LookupError, match="rename '## Unreleased'"):
        release_notes.section(version, CHANGELOG)


def test_the_changelog_dates_every_released_version():
    text = release_notes.CHANGELOG.read_text(encoding="utf-8")
    assert release_notes.section("0.3.0", text)[0] == "0.3.0: first release on PyPI (2026-10-11)"
