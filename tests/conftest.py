from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from wiki_cli.config import load_settings


class Wiki:
    """A temporary llm-wiki repository: ``<repo>/wiki.toml`` and ``<repo>/wiki/``."""

    def __init__(self, repo: Path, space: str = "sp"):
        self.repo = repo
        self.root = repo / "wiki"
        self.root.mkdir(parents=True)
        (repo / "wiki.toml").write_text(f'name = "{space}"\n', encoding="utf-8")
        self.space = space

    @property
    def cache_path(self) -> Path:
        return self.repo / ".cache" / "wiki.sqlite3"

    def settings(self, **overrides):
        return load_settings(overrides.pop("wiki_root", self.root), overrides.pop("space", None),
                             overrides.pop("cache", self.cache_path))

    def uri(self, slug: str) -> str:
        return f"wiki://{self.space}/{slug}"

    def write(self, slug: str, meta: dict | None = None, body: str = "Body text.\n", *, raw: str | None = None,
              newline: str = "\n") -> Path:
        path = self.root / f"{slug}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        if raw is None:
            meta = {"title": slug, "summary": f"About {slug}."} if meta is None else meta
            raw = "---\n" + yaml.safe_dump(meta, sort_keys=False) + "---\n" + textwrap.dedent(body)
        path.write_bytes(raw.replace("\n", newline).encode("utf-8"))
        return path

    def page(self, slug: str, *relations: tuple[str, str, str], summary: str | None = "default", **extra) -> Path:
        meta = {"title": slug}
        if summary is not None:
            meta["summary"] = f"About {slug}." if summary == "default" else summary
        meta.update(extra)
        if relations:
            meta["relations"] = [
                {"target": self.uri(target), "type": rtype, "reason": reason} for target, rtype, reason in relations
            ]
        return self.write(slug, meta)

    def read(self, slug: str) -> str:
        return (self.root / f"{slug}.md").read_bytes().decode("utf-8")


@pytest.fixture(autouse=True)
def fake_models(monkeypatch):
    """Tests never load real models; the fakes are deterministic and instant."""
    monkeypatch.setenv("WIKI_EMBED_MODEL", "fake:hash")
    monkeypatch.setenv("WIKI_RERANKER", "fake:overlap")
    for name in ("LLM_WIKI_ROOT", "LLM_WIKI_SPACE", "WIKI_CACHE", "WIKI_MODELS_DIR"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def wiki(tmp_path: Path) -> Wiki:
    return Wiki(tmp_path / "repo")
