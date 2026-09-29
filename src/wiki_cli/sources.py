"""Sources waiting to be ingested: files in `raw/` that no page links to yet.

A source can be several files: a PDF or web page and the `.txt` extracted from it, which
share a name and differ only in extension. A page that links any of them (by path or by
bare name, in its body or its frontmatter) has ingested the source.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from wiki_cli import links
from wiki_cli.config import Settings
from wiki_cli.pages import PAGE, load, matches, scan_vault

DEFAULT_RAW_DIR = "raw"
ALWAYS_IGNORED = ("README.md", "README.txt")  # a note about the folder, at any depth
_GLOB = re.compile(r"[*?\[]")
_FRONTMATTER_LINK = re.compile(r"^\[\[([^\]]+)\]\]$")


@dataclass
class Source:
    key: str  # the files' shared path without extension, e.g. raw/report-2024
    files: list[str] = field(default_factory=list)
    duplicate_of: str | None = None  # a file already ingested (or listed earlier) with the same content

    def to_dict(self) -> dict:
        return {"source": self.key, "files": self.files, "duplicate_of": self.duplicate_of}


def raw_dirs(settings: Settings) -> list[str]:
    """The folders sources live in: the fixed start of each `raw` pattern, plus `raw/`."""
    found = []
    for pattern in settings.raw:
        parts = []
        for part in pattern.split("/")[:-1]:
            if _GLOB.search(part):
                break
            parts.append(part)
        if parts:
            found.append("/".join(parts))
    if (settings.root / DEFAULT_RAW_DIR).is_dir():
        found.append(DEFAULT_RAW_DIR)
    tops = []
    for folder in sorted(set(found), key=len):
        if not any(folder == top or folder.startswith(top + "/") for top in tops):
            tops.append(folder)
    return sorted(tops)


def pending(settings: Settings) -> tuple[list[Source], int]:
    """Sources no page links to, sorted by path, and how many sources are ingested."""
    scanned, others = scan_vault(settings)
    folders = raw_dirs(settings)
    raw_files = sorted(rel for rel in [page_file.rel for page_file, _ in scanned] + others
                       if any(rel.startswith(folder + "/") for folder in folders)
                       and rel.rsplit("/", 1)[-1] not in ALWAYS_IGNORED
                       and not matches(rel, settings.pending_ignore))
    groups: dict[str, Source] = {}
    for rel in raw_files:
        key = _without_extension(rel)
        groups.setdefault(key.casefold(), Source(key)).files.append(rel)

    by_path: dict[str, str] = {}
    by_name: dict[str, set[str]] = {}
    for folded, source in groups.items():
        names = {folded.rsplit("/", 1)[-1]}
        by_path[folded] = folded
        for rel in source.files:
            by_path[rel.casefold()] = folded
            names.add(rel.rsplit("/", 1)[-1].casefold())
        for name in names:
            by_name.setdefault(name, set()).add(folded)

    ingested: set[str] = set()
    for page_file, _ in scanned:
        if page_file.kind != PAGE:
            continue
        for target in _targets(load(page_file, settings)):
            folded = target.casefold().lstrip("/")
            if folded in by_path:
                ingested.add(by_path[folded])
            elif "/" not in folded:
                ingested.update(by_name.get(folded, ()))
            else:
                ingested.update(key for path, key in by_path.items() if path.endswith("/" + folded))

    seen: dict[str, str] = {}
    for folded in sorted(ingested):
        for rel in groups[folded].files:
            seen.setdefault(_digest(settings, rel), rel)
    waiting = []
    for folded, source in sorted(groups.items(), key=lambda item: item[1].key.casefold()):
        if folded in ingested:
            continue
        for rel in source.files:
            digest = _digest(settings, rel)
            if digest in seen and source.duplicate_of is None:
                source.duplicate_of = seen[digest]
            seen.setdefault(digest, rel)
        waiting.append(source)
    return waiting, len(ingested)


def _targets(page) -> list[str]:
    """Every link target on a page, as paths from the wiki root where the link is a path,
    plus frontmatter values that name a file (``source_path: raw/x.pdf`` or ``"[[x]]"``)."""
    found = []
    for link in links.extract_links(page.body, page.file.rel):
        if not link.is_code:
            found.append(link.target)
    for value in (page.data or {}).values():
        for item in value if isinstance(value, list) else [value]:
            if not isinstance(item, str):
                continue
            item = item.strip()
            match = _FRONTMATTER_LINK.match(item)
            if match:
                found.append(links.split_target(match.group(1))[0])
            elif "/" in item and " " not in item and "://" not in item:
                found.append(links.normalize(item))
    return found


def _without_extension(rel: str) -> str:
    folder, _, name = rel.rpartition("/")
    stem = name.rsplit(".", 1)[0] if "." in name.lstrip(".") else name
    return f"{folder}/{stem}" if folder else stem


def _digest(settings: Settings, rel: str) -> str:
    digest = hashlib.sha256()
    try:
        with (settings.root / rel).open("rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
    except OSError:
        return f"unreadable:{rel}"
    return digest.hexdigest()
