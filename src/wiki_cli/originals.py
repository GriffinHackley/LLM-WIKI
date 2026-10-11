"""Originals: the user's own writing (`[originals]`), which the agent reads and never changes.

Each original's frontmatter is only what the user's Obsidian templates fill in (`kind:`
and its dates). `wiki check --all` reports an original missing one of those fields, or
with a kind the config does not list: a file made outside Obsidian gets no template. It
only reports; the files are the user's to fix.

Unlike sources in `raw/`, originals change after they are ingested. Ingest records the
original's hash on its page (`original_hash:`); `wiki pending` lists the original again
once the hash no longer matches. The hash leaves out the date lines of the frontmatter
(the `fields` other than `kind`), so a timestamp bump from Obsidian alone is no change.
"""

from __future__ import annotations

import hashlib
import re

from wiki_cli import frontmatter
from wiki_cli.config import Settings
from wiki_cli.model import WARNING, Issue
from wiki_cli.pages import RAW, scan_vault


HASH_FIELD = "original_hash"  # on an original's page: the original's content_hash when it was ingested
ORIGINAL_FIELD = "original"  # on an original's page: the original it describes
HASH_LENGTH = 16


def content_hash(settings: Settings, rel: str) -> str:
    """The original's hash without its date lines, with line endings made uniform."""
    text, _, _ = read(settings, rel)
    if text is None:
        return ""
    text = text.replace("\r\n", "\n")
    dates = [key for key in settings.originals.fields if key != "kind"] if settings.originals else []
    opening = frontmatter.OPENING.match(text) if dates else None
    try:
        parts = frontmatter.split(text) if opening else None
    except frontmatter.FrontmatterError:
        parts = None
    if parts is not None:
        yaml_text = parts[0]
        pattern = re.compile(rf"^(?:{'|'.join(re.escape(key) for key in dates)})[ \t]*:.*\n?", re.MULTILINE)
        text = text[:opening.end()] + pattern.sub("", yaml_text) + text[opening.end() + len(yaml_text):]
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:HASH_LENGTH]


def files(settings: Settings, scanned=None) -> list[str]:
    """The originals, as paths from the wiki root, sorted."""
    if settings.originals is None:
        return []
    scanned = scan_vault(settings)[0] if scanned is None else scanned
    return sorted(page_file.rel for page_file, _ in scanned
                  if page_file.kind == RAW and settings.originals.contains(page_file.rel))


def read(settings: Settings, rel: str) -> tuple[str | None, dict | None, str | None]:
    """An original's text, its frontmatter (None when it has none), and why it could not be
    read or parsed, if it could not."""
    try:
        text = (settings.root / rel).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return None, None, f"cannot read it: {exc}"
    try:
        return text, frontmatter.parse(text).data, None
    except frontmatter.FrontmatterError as exc:
        return text, None, str(exc)


def check(settings: Settings, scanned=None) -> list[Issue]:
    if settings.originals is None:
        return []
    found = []
    for rel in files(settings, scanned):
        found.extend(_check_one(settings, rel))
    return found


def _check_one(settings: Settings, rel: str) -> list[Issue]:
    originals = settings.originals
    _, data, problem = read(settings, rel)
    if problem:
        return [Issue(WARNING, "original-unreadable", f"original's frontmatter: {problem}", path=rel)]
    data = data or {}
    issues = []
    missing = [key for key in originals.fields if data.get(key) in (None, "")]
    if missing:
        issues.append(Issue(WARNING, "original-missing-field",
                            f"original has no {', '.join(repr(key) for key in missing)} in its frontmatter: "
                            "its template was not applied (made outside Obsidian?). The file is the user's to fix",
                            path=rel))
    kind = data.get("kind")
    if "kind" in originals.fields and kind not in (None, "") and str(kind).strip().lower() not in originals.kinds:
        issues.append(Issue(WARNING, "original-bad-kind",
                            f"original's kind is '{kind}'; it is one of {', '.join(originals.kinds)}", path=rel))
    return issues
