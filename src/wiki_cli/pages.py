"""Discover indexed files, assign Obsidian slugs, and load pages."""

from __future__ import annotations

import codecs
import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable

from wiki_cli import frontmatter, links
from wiki_cli.config import Settings
from wiki_cli.model import ERROR, Issue
from wiki_cli.vocabulary import MAX_SUMMARY_LENGTH

PAGE = "page"
RAW = "raw"
SKIPPED_DIRS = {"node_modules", "__pycache__"}
SUMMARY_SECTIONS = ("summary", "what this is", "what happened", "claim", "question this page answers", "synthesis")


@dataclass(frozen=True)
class PageFile:
    path: Path
    rel: str  # posix path relative to the wiki root
    slug: str
    kind: str = PAGE  # PAGE or RAW


@dataclass
class Page:
    file: PageFile
    content_hash: str
    text: str | None
    data: dict | None  # frontmatter; None when absent or invalid
    body_offset: int
    issues: list[Issue] = field(default_factory=list)

    @property
    def slug(self) -> str:
        return self.file.slug

    @property
    def body(self) -> str:
        return "" if self.text is None else self.text[self.body_offset:]

    @property
    def title(self) -> str:
        value = _string(self.data, "title")
        if value:
            return value.strip()
        heading = re.search(r"^#[ \t]+(.+?)[ \t]*$", self.body, re.MULTILINE)
        return heading.group(1) if heading else self.slug.rsplit("/", 1)[-1]

    @property
    def page_type(self) -> str | None:
        if self.file.kind == RAW:
            return "raw"
        value = _string(self.data, "type")
        return value.strip().lower() if value else None

    def summary(self) -> tuple[str | None, bool]:
        """``(summary, is_fallback)``: a summary section's first paragraph, else the first prose paragraph."""
        if self.text is None:
            return None, False
        if self.file.kind == RAW:
            return _truncate(first_paragraph(self.body)), True
        found = links.section_text(self.body, SUMMARY_SECTIONS)
        if found:
            return _truncate(links.plain(found)), False
        return _truncate(first_paragraph(self.body)), True

    @property
    def body_hash(self) -> str:
        """Hash of the body without its summary text, to detect a stale summary."""
        summary, _ = self.summary()
        body = self.body.replace(summary, "") if summary else self.body
        return content_hash(body.encode("utf-8"))


class PageNotFound(Exception):
    pass


def matches(rel: str, patterns: tuple[str, ...]) -> bool:
    path = PurePosixPath(rel)
    return any(path.full_match(pattern) for pattern in patterns)


def scan(settings: Settings) -> list[tuple[PageFile, os.DirEntry]]:
    """Indexed files with their ``DirEntry`` (whose ``stat()`` is free on Windows), sorted by path."""
    return scan_vault(settings)[0]


def vault_files(settings: Settings) -> list[str]:
    """Files in the vault that are not indexed (attachments, unindexed notes), as relative paths."""
    return scan_vault(settings)[1]


def scan_vault(settings: Settings) -> tuple[list[tuple[PageFile, os.DirEntry]], list[str]]:
    """Walk the vault once: indexed files, and every other (non-hidden) file."""
    found: list[tuple[str, str, os.DirEntry]] = []
    others: list[str] = []
    stack = [(settings.root, "")]
    while stack:
        directory, prefix = stack.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError:
            continue
        for entry in entries:
            if entry.name.startswith("."):
                continue
            if entry.is_dir(follow_symlinks=False):
                if entry.name not in SKIPPED_DIRS:
                    stack.append((Path(entry.path), f"{prefix}{entry.name}/"))
                continue
            rel = prefix + entry.name
            if matches(rel, settings.exclude):
                others.append(rel)
            elif rel.lower().endswith(".md") and matches(rel, settings.pages):
                found.append((rel, PAGE, entry))
            elif matches(rel, settings.raw):
                found.append((rel, RAW, entry))
            else:
                others.append(rel)
    found.sort(key=lambda item: item[0])
    slugs = assign_slugs([(rel, kind) for rel, kind, _ in found])
    files = [(PageFile(Path(entry.path), rel, slugs[rel], kind), entry) for rel, kind, entry in found]
    return files, sorted(others)


def discover(settings: Settings) -> list[PageFile]:
    return [page_file for page_file, _ in scan(settings)]


def assign_slugs(files: list[tuple[str, str]]) -> dict[str, str]:
    """Obsidian naming: the bare file name when unique, else the path without extension.

    Raw files are ``raw/<stem>`` so they never collide with page slugs.
    """
    stems: dict[str, int] = {}
    for rel, kind in files:
        if kind == PAGE:
            stem = _stem(rel).casefold()
            stems[stem] = stems.get(stem, 0) + 1
    slugs = {}
    for rel, kind in files:
        if kind == RAW:
            slugs[rel] = f"raw/{_stem(rel)}"
        elif stems[_stem(rel).casefold()] == 1:
            slugs[rel] = _stem(rel)
        else:
            slugs[rel] = rel[:-3]
    return slugs


def _stem(rel: str) -> str:
    name = rel.rsplit("/", 1)[-1]
    return name.rsplit(".", 1)[0] if "." in name else name


class Resolver:
    """Resolve link targets to slugs the way Obsidian does."""

    def __init__(self, slugs: list[tuple[str, str]],
                 others: Iterable[str] | Callable[[], Iterable[str]] | None = None):
        """``slugs``: indexed ``(slug, rel)`` pairs. ``others``: every other file in the vault
        (or a callable returning them, evaluated only if needed): links to these exist in
        Obsidian but are not pages."""
        self._others_source = others
        self._other_paths: set[str] | None = None
        self._other_names: set[str] = set()
        self.slugs = {slug for slug, _ in slugs}
        self.by_path = {rel.rsplit(".", 1)[0].casefold(): slug for slug, rel in slugs}
        self.by_stem: dict[str, list[str]] = {}
        self.raw_by_stem: dict[str, str] = {}
        self.by_fold: dict[str, str] = {}
        for slug, rel in slugs:
            if slug.startswith("raw/") and not rel.lower().endswith(".md"):
                # Raw text is reached by extension ("[[x.txt]]") or path, never by bare name,
                # so it does not collide with the document page of the same name.
                self.raw_by_stem[_stem(rel).casefold()] = slug
            else:
                self.by_stem.setdefault(_stem(rel).casefold(), []).append(slug)
            self.by_fold[slug.casefold()] = slug

    def resolve(self, target: str) -> str | None:
        target = links.normalize(target)
        if target in self.slugs:
            return target
        folded = target.casefold()
        if folded in self.by_fold:
            return self.by_fold[folded]
        if "/" in target:
            if folded in self.by_path:
                return self.by_path[folded]
            # Obsidian also accepts a trailing partial path.
            suffix = [slug for path, slug in self.by_path.items() if path.endswith("/" + folded)]
            return suffix[0] if len(suffix) == 1 else None
        candidates = self.by_stem.get(folded, [])
        if len(candidates) == 1:
            return candidates[0]
        # Links to non-Markdown files carry their extension ("[[report.txt]]"); indexed raw
        # text is keyed without it.
        stem, dot, extension = target.rpartition(".")
        if dot and stem and "/" not in extension and extension.lower() != "md":
            return self.raw_by_stem.get(stem.rsplit("/", 1)[-1].casefold()) or self.resolve(stem)
        return None

    def ambiguous(self, target: str) -> bool:
        target = links.normalize(target)
        return "/" not in target and len(self.by_stem.get(target.casefold(), [])) > 1

    def is_other_file(self, target: str) -> bool:
        """True when the link points at a vault file that is not indexed (a PDF, an image,
        an unindexed note): it exists, so it is not a page waiting to be written."""
        if self._other_paths is None:
            source = self._others_source() if callable(self._others_source) else (self._others_source or ())
            self._other_paths = set()
            for rel in source:
                key = rel[:-3] if rel.lower().endswith(".md") else rel
                self._other_paths.add(key.casefold())
                self._other_names.add(key.rsplit("/", 1)[-1].casefold())
        folded = links.normalize(target).casefold()
        if folded in self._other_paths:
            return True
        if "/" not in folded:
            return folded in self._other_names
        return any(path.endswith("/" + folded) for path in self._other_paths)


def resolve(target: str, settings: Settings) -> PageFile:
    """Resolve a slug or a path to an indexed file."""
    files = discover(settings)
    by_slug = {page_file.slug: page_file for page_file in files}
    if target in by_slug:
        return by_slug[target]
    candidate = Path(target)
    if not candidate.is_absolute():
        candidate = candidate if candidate.exists() else settings.root / candidate
    candidate = candidate.resolve()
    for page_file in files:
        if page_file.path.resolve() == candidate:
            return page_file
    resolved = Resolver([(page_file.slug, page_file.rel) for page_file in files]).resolve(target)
    if resolved:
        return by_slug[resolved]
    raise PageNotFound(f"no indexed page '{target}'")


def load(page_file: PageFile) -> Page:
    return parse(page_file, page_file.path.read_bytes())


def parse(page_file: PageFile, data: bytes) -> Page:
    digest = content_hash(data)
    if data.startswith(codecs.BOM_UTF8):
        data = data[len(codecs.BOM_UTF8):]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="replace")
        if page_file.kind == PAGE:
            return Page(page_file, digest, text, None, 0,
                        issues=[Issue(ERROR, "invalid-encoding", "page is not valid UTF-8")])
    if page_file.kind == RAW:
        return Page(page_file, digest, text, None, 0)
    try:
        parsed = frontmatter.parse(text)
    except frontmatter.FrontmatterError as exc:
        # Unparseable YAML between intact "---" delimiters is still frontmatter, not body:
        # keep it out of the body so fixing the YAML doesn't look like a body edit.
        try:
            parts = frontmatter.split(text)
        except frontmatter.FrontmatterError:
            parts = None
        body_offset = parts[1] if parts else 0
        return Page(page_file, digest, text, None, body_offset, issues=[Issue(ERROR, "invalid-frontmatter", str(exc))])
    return Page(page_file, digest, text, parsed.data, parsed.body_offset)


def first_paragraph(body: str) -> str | None:
    paragraph: list[str] = []
    in_fence = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
            if paragraph:
                break
            continue
        if in_fence:
            continue
        if not stripped:
            if paragraph:
                break
            continue
        if not paragraph and (stripped.startswith(("#", "<!--", "|", "---", "**", ">")) or stripped.startswith("- ")):
            continue
        paragraph.append(stripped)
    return links.plain(" ".join(paragraph)) if paragraph else None


def _truncate(text: str | None) -> str | None:
    if not text:
        return None
    if len(text) <= MAX_SUMMARY_LENGTH:
        return text
    return text[:MAX_SUMMARY_LENGTH].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def _string(data: dict | None, key: str) -> str | None:
    value = data.get(key) if data else None
    return value if isinstance(value, str) else None


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
