"""Print one version's section of CHANGELOG.md, for its GitHub Release.

    python .github/release_notes.py 0.3.0            # the notes
    python .github/release_notes.py 0.3.0 --title    # the release title

A section starts at a heading `## <version> (<date>)`, optionally followed by
`: <subtitle>`, and runs to the next `## ` heading. Exits 1, saying so, when the
changelog has no dated section for the version: the publish workflow checks this
before anything is published.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parent.parent / "CHANGELOG.md"
HEADING = r"^## {version} \((\d{{4}}-\d{{2}}-\d{{2}})\)(?:: (.+))?$"


def section(version: str, text: str) -> tuple[str, str]:
    """The title and the notes of ``version``'s section."""
    heading = re.search(HEADING.format(version=re.escape(version)), text, flags=re.MULTILINE)
    if not heading:
        raise LookupError(f"CHANGELOG.md has no section '## {version} (YYYY-MM-DD)'; "
                          "rename '## Unreleased' to it before tagging")
    date, subtitle = heading.groups()
    rest = text[heading.end():]
    following = re.search(r"^## ", rest, flags=re.MULTILINE)
    notes = rest[:following.start()] if following else rest
    title = f"{version}: {subtitle}" if subtitle else version
    return f"{title} ({date})", notes.strip() + "\n"


def main(argv: list[str]) -> int:
    if not argv or argv[0].startswith("-"):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    try:
        title, notes = section(argv[0].removeprefix("v"), CHANGELOG.read_text(encoding="utf-8"))
    except LookupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(title + "\n" if "--title" in argv[1:] else notes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
