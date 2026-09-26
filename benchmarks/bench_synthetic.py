"""Synthetic scale benchmark for the cache, derived relations, and search.

Usage:
    python benchmarks/bench_synthetic.py [--pages 30000] [--relations 100000]

Pages are laid out like the Politics wiki (typed folders, sections, list-item links
with roles). Search uses a hash embedder at 384 dimensions (bge-small's size), so
the numbers cover SQLite / FTS5 / sqlite-vec cost, not real model inference.
"""

from __future__ import annotations

import argparse
import os
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from wiki_cli.cache import Cache
from wiki_cli.config import load_settings
from wiki_cli.models import HashEmbedder
from wiki_cli.pages import Resolver, discover, load
from wiki_cli.search import search
from wiki_cli.validation import check_corpus

FOLDERS = [("people", "person"), ("organizations", "organization"), ("events", "event"),
           ("documents", "document"), ("places", "place"), ("topics", "topic")]
SECTIONS = ["Documented role", "Timeline", "Background", "Open questions"]
_vocab_rng = random.Random(3)
VOCABULARY = ["".join(_vocab_rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(_vocab_rng.randint(4, 10)))
              for _ in range(3000)]


def sentence(rng: random.Random, words: int = 60) -> str:
    return " ".join(rng.choice(VOCABULARY) for _ in range(words)) + "."


def generate(root: Path, page_count: int, relation_count: int, seed: int = 7) -> dict[str, Path]:
    rng = random.Random(seed)
    slugs = [f"page-{i:05d}" for i in range(page_count)]
    per_page = [0] * page_count
    for _ in range(relation_count):
        per_page[rng.randrange(page_count)] += 1
    paths: dict[str, Path] = {}
    for index, slug in enumerate(slugs):
        folder, page_type = FOLDERS[index % len(FOLDERS)]
        targets = set()
        while len(targets) < per_page[index]:
            candidate = rng.randrange(page_count)
            if candidate != index:
                targets.add(candidate)
        lines = ["---", f"title: Page {index}", f"type: {page_type}", "---", f"# Page {index}", "",
                 "## Summary", sentence(rng, 12), ""]
        for section in SECTIONS:
            lines += [f"## {section}", "", sentence(rng), "", sentence(rng, 40), ""]
        if targets:
            lines.append("## Relationships")
            lines += [f"- [[{slugs[target]}]] — {sentence(rng, 6)}" for target in sorted(targets)]
        path = root / "wiki" / folder / f"{slug}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        paths[slug] = path
    return paths


def timed(label: str, fn, results: dict):
    start = time.perf_counter()
    value = fn()
    results[label] = time.perf_counter() - start
    print(f"{label:<44} {results[label]:8.2f} s")
    return value


def latency(label: str, samples: list[float]) -> None:
    samples = sorted(samples)
    print(f"{label:<44} p50 {statistics.median(samples):7.2f} ms, p95 {samples[int(len(samples) * 0.95)]:7.2f} ms")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages", type=int, default=30_000)
    parser.add_argument("--relations", type=int, default=100_000)
    parser.add_argument("--dir", type=Path)
    args = parser.parse_args()

    base = args.dir or Path(tempfile.mkdtemp(prefix="wiki-bench-"))
    root = base / "wiki-root"
    root.mkdir(parents=True, exist_ok=True)
    (root / ".wiki-cli.toml").write_text('pages = ["wiki/**/*.md"]\n', encoding="utf-8")
    results: dict[str, float] = {}

    try:
        paths = timed("generate corpus", lambda: generate(root, args.pages, args.relations), results)
        slugs = list(paths)
        settings = load_settings(root, root / ".cache" / "wiki.sqlite3")
        embedder = HashEmbedder(dims=384)
        embedder.name = "fake:hash-384"

        with Cache(settings) as cache:
            stats = timed("index rebuild, no embedding (cold)", cache.rebuild, results)
            print(f"  indexed pages={stats.added}")
            timed("index rebuild, no embedding (warm)", cache.rebuild, results)
            timed("index refresh (no changes)", cache.refresh, results)

            rng = random.Random(1)
            for slug in rng.sample(slugs, 100):
                path = paths[slug]
                path.write_text(path.read_text(encoding="utf-8") + "\nEdited.\n", encoding="utf-8")
            timed("index refresh (100 changed pages)", cache.refresh, results)

            timed("store 384-dim vectors (hash embedder)", lambda: cache.embed_pending(embedder), results)
            status = cache.status()
            print(f"  pages={status['pages']} relations={status['relations']} chunks={status['chunks']}")

            sample = rng.sample(slugs, 500)
            neighbor_ms = []
            for slug in sample:
                start = time.perf_counter()
                cache.ensure_fresh(slug)
                cache.neighbors(slug)
                neighbor_ms.append((time.perf_counter() - start) * 1000)
            latency("neighbors in-process", neighbor_ms)

            questions = [" ".join(rng.sample(VOCABULARY, 6)) for _ in range(100)]
            for label, kwargs in (
                ("search keyword only", {"embedder": None}),
                ("search keyword + vector (no model cost)", {"embedder": embedder}),
            ):
                samples = []
                for question in questions:
                    start = time.perf_counter()
                    search(cache.conn, question, reranker=None, embed_model=cache.embed_model, limit=3, **kwargs)
                    samples.append((time.perf_counter() - start) * 1000)
                latency(label, samples)

        for path in sorted((root / ".cache").iterdir()):
            print(f"{'cache file ' + path.name:<44} {path.stat().st_size / 1_048_576:8.1f} MB")

        wiki_exe = Path(sys.executable).with_name("wiki.exe" if os.name == "nt" else "wiki")
        cli = []
        for slug in sample[:20]:
            start = time.perf_counter()
            subprocess.run([str(wiki_exe), "neighbors", slug, "--format", "json",
                            "--root", str(root), "--cache", str(settings.cache_path)],
                           check=True, capture_output=True)
            cli.append((time.perf_counter() - start) * 1000)
        print(f"{'wiki neighbors (CLI process, cold)':<44} median {statistics.median(cli):.0f} ms")

        def check_all():
            files = discover(settings)
            return check_corpus([load(page_file) for page_file in files],
                                Resolver([(page_file.slug, page_file.rel) for page_file in files]))
        issues = timed("check --all", check_all, results)
        print(f"  errors={sum(1 for issue in issues if issue.severity == 'error')}")
    finally:
        if not args.dir:
            shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    main()
