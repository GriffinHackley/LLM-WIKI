from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest
import yaml

from wiki_cli.config import load_settings

CONFIG = """\
pages = ["wiki/**/*.md", "dossiers/*/claims.md"]
exclude = ["wiki/index.md"]
raw = ["raw/*.txt"]

""" + (Path(__file__).parent / "politics_rules.toml").read_text(encoding="utf-8")

FOLDERS = {"person": "people", "organization": "organizations", "place": "places", "event": "events",
           "document": "documents", "topic": "topics", "claim": "claims"}


class Wiki:
    """A temporary Obsidian wiki laid out like the Politics repo."""

    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True)
        (root / ".wiki-cli.toml").write_text(CONFIG, encoding="utf-8")

    @property
    def cache_path(self) -> Path:
        return self.root / ".cache" / "wiki.sqlite3"

    def settings(self, **overrides):
        return load_settings(overrides.pop("root", self.root), overrides.pop("cache", self.cache_path))

    def write(self, rel: str, meta: dict | None = None, body: str = "", *, raw: str | None = None,
              newline: str = "\n") -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if raw is None:
            front = "---\n" + yaml.safe_dump(meta, sort_keys=False) + "---\n" if meta is not None else ""
            raw = front + textwrap.dedent(body)
        path.write_bytes(raw.replace("\n", newline).encode("utf-8"))
        return path

    def page(self, page_type: str, slug: str, sections: dict[str, str] | None = None, *,
             summary: str | None = "default", **meta) -> Path:
        """Write ``wiki/<folder>/<slug>.md`` with frontmatter and ``## `` sections."""
        front = {"title": meta.pop("title", slug), "type": page_type, **meta}
        parts = [f"# {front['title']}", ""]
        if summary is not None:
            heading = {"document": "What this is", "event": "What happened", "claim": "Claim"}.get(page_type, "Summary")
            parts += [f"## {heading}", f"About {slug}." if summary == "default" else summary, ""]
        for heading, text in (sections or {}).items():
            parts += [f"## {heading}", textwrap.dedent(text).strip(), ""]
        return self.write(f"wiki/{FOLDERS.get(page_type, page_type)}/{slug}.md", front, "\n".join(parts))

    def raw_text(self, name: str, text: str) -> Path:
        return self.write(f"raw/{name}", raw=text)


def bump(path: Path) -> None:
    """Force a distinct mtime so change detection does not depend on clock resolution."""
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))


@pytest.fixture(autouse=True)
def fake_models(monkeypatch):
    """Tests never load real models; the fakes are deterministic and instant."""
    monkeypatch.setenv("WIKI_EMBED_MODEL", "fake:hash")
    monkeypatch.setenv("WIKI_RERANKER", "fake:overlap")
    for name in ("WIKI_ROOT", "WIKI_CONFIG", "WIKI_CACHE", "WIKI_MODELS_DIR"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def wiki(tmp_path: Path) -> Wiki:
    return Wiki(tmp_path / "politics")
