"""Discover page files, derive slugs, load pages, and write them atomically."""

from __future__ import annotations

import codecs
import hashlib
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from wiki_cli import block, frontmatter
from wiki_cli.config import Settings
from wiki_cli.model import ERROR, Issue, Relation
from wiki_cli.relations import parse_relations, parse_superseded_by

SKIPPED_DIRS = {"node_modules"}
PLACEHOLDER_LENGTH = 200


@dataclass(frozen=True)
class PageFile:
    path: Path
    rel: str  # posix path relative to the wiki root
    slug: str


@dataclass
class Page:
    file: PageFile
    content_hash: str
    text: str | None
    bom: bool
    newline: str
    has_frontmatter: bool
    data: dict | None
    body_offset: int
    relations: list[Relation] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)

    @property
    def slug(self) -> str:
        return self.file.slug

    @property
    def body(self) -> str:
        return "" if self.text is None else self.text[self.body_offset:]

    @property
    def title(self) -> str | None:
        return _string_field(self.data, "title")

    @property
    def page_type(self) -> str | None:
        return _string_field(self.data, "type")

    @property
    def summary(self) -> str | None:
        value = _string_field(self.data, "summary")
        return value.strip() if value and value.strip() else None

    @property
    def body_hash(self) -> str:
        return content_hash(block.strip(self.body).encode("utf-8"))

    def placeholder_summary(self) -> str | None:
        return placeholder_summary(self.body)


class PageNotFound(Exception):
    pass


def slug_for(rel: str) -> str:
    """Apply llm-wiki's rule: path without extension; ``x/index.md`` is slug ``x``."""
    path = PurePosixPath(rel)
    stem = path.with_suffix("").as_posix()
    if path.name.lower() == "index.md" and path.parent.as_posix() != ".":
        return path.parent.as_posix()
    return stem


def is_bundle(rel: str) -> bool:
    path = PurePosixPath(rel)
    return path.name.lower() == "index.md" and path.parent.as_posix() != "."


def is_excluded(slug: str, patterns: tuple[str, ...]) -> bool:
    """Approximate gitignore-style matching of ``ingest.exclude`` globs against slugs."""
    path = PurePosixPath(slug)
    for pattern in patterns:
        cleaned = pattern.strip().strip("/")
        if not cleaned:
            continue
        if "/" in cleaned:
            if path.full_match(cleaned) or path.full_match(f"{cleaned}/**"):
                return True
        elif any(PurePosixPath(part).full_match(cleaned) for part in path.parts):
            return True
    return False


def discover(settings: Settings) -> list[PageFile]:
    """Walk the wiki root for ``*.md`` files by name only, sorted by relative path."""
    return [page_file for page_file, _ in scan(settings)]


def scan(settings: Settings) -> list[tuple[PageFile, os.DirEntry]]:
    """Like ``discover`` but keeps each ``DirEntry``, whose ``stat()`` is free on Windows."""
    found: list[tuple[PageFile, os.DirEntry]] = []
    stack = [(settings.wiki_root, "")]
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
            elif entry.is_file() and entry.name.lower().endswith(".md"):
                rel = prefix + entry.name
                slug = slug_for(rel)
                if not is_excluded(slug, settings.exclude):
                    found.append((PageFile(Path(entry.path), rel, slug), entry))
    found.sort(key=lambda item: item[0].rel)
    return found


def resolve(target: str, settings: Settings) -> PageFile:
    """Resolve a slug, ``wiki://`` URI, or ``.md`` path to a page file under the wiki root."""
    from wiki_cli.relations import parse_uri

    root = settings.wiki_root
    if target.lower().endswith(".md"):
        candidate = Path(target)
        if not candidate.is_absolute():
            candidate = candidate if candidate.exists() else root / candidate
        candidate = candidate.resolve()
        try:
            rel = candidate.relative_to(root).as_posix()
        except ValueError:
            raise PageNotFound(f"{target} is outside the wiki root {root}") from None
        if not candidate.is_file():
            raise PageNotFound(f"no such page file: {candidate}")
        return PageFile(candidate, rel, slug_for(rel))

    parsed = parse_uri(target)
    slug = parsed[1] if parsed else target.strip("/")
    for rel in (f"{slug}.md", f"{slug}/index.md"):
        candidate = root / rel
        if candidate.is_file():
            # resolve() returns the on-disk case, which matters on case-insensitive filesystems.
            actual = candidate.resolve()
            actual_rel = actual.relative_to(root).as_posix()
            return PageFile(actual, actual_rel, slug_for(actual_rel))
    raise PageNotFound(f"no page with slug '{slug}'")


def load(page_file: PageFile, local_space: str | None) -> Page:
    return parse(page_file, page_file.path.read_bytes(), local_space)


def parse(page_file: PageFile, data: bytes, local_space: str | None) -> Page:
    digest = content_hash(data)
    bom = data.startswith(codecs.BOM_UTF8)
    try:
        text = data[len(codecs.BOM_UTF8):].decode("utf-8") if bom else data.decode("utf-8")
    except UnicodeDecodeError:
        return Page(page_file, digest, None, bom, "\n", False, None, 0,
                    issues=[Issue(ERROR, "invalid-encoding", "page is not valid UTF-8")])

    newline = detect_newline(text)
    try:
        parsed = frontmatter.parse(text)
    except frontmatter.FrontmatterError as exc:
        return Page(page_file, digest, text, bom, newline, True, None, 0,
                    issues=[Issue(ERROR, "invalid-frontmatter", str(exc))])

    page = Page(page_file, digest, text, bom, newline, parsed.data is not None, parsed.data, parsed.body_offset)
    if parsed.data is not None:
        relations, issues = parse_relations(parsed.data.get("relations"))
        derived, derived_issues = parse_superseded_by(parsed.data.get("superseded_by"), local_space)
        page.relations = relations + derived
        page.issues = issues + derived_issues
    return page


def detect_newline(text: str) -> str:
    index = text.find("\n")
    return "\r\n" if index > 0 and text[index - 1] == "\r" else "\n"


def encode(text: str, bom: bool) -> bytes:
    data = text.encode("utf-8")
    return codecs.BOM_UTF8 + data if bom else data


def atomic_write(path: Path, text: str, bom: bool) -> None:
    """Write through a temp file in the same folder, then atomically rename."""
    handle, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as temp:
            temp.write(encode(text, bom))
            temp.flush()
            os.fsync(temp.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def placeholder_summary(body: str) -> str | None:
    """First prose paragraph of the body, collapsed and truncated."""
    paragraph: list[str] = []
    in_fence = False
    for line in block.strip(body).splitlines():
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
        if stripped.startswith(("#", "<!--", "|", "---")) and not paragraph:
            continue
        paragraph.append(stripped)
    if not paragraph:
        return None
    text = " ".join(paragraph)
    text = re.sub(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > PLACEHOLDER_LENGTH:
        text = text[:PLACEHOLDER_LENGTH].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return text


def _string_field(data: dict | None, key: str) -> str | None:
    if not data:
        return None
    value = data.get(key)
    return value if isinstance(value, str) else None


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
