"""Synthetic scale benchmark for the relations cache.

Usage: python benchmarks/bench_synthetic.py [--pages 30000] [--relations 100000] [--dir PATH]
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
from wiki_cli.pages import discover, load
from wiki_cli.sync import sync_page
from wiki_cli.validation import SlugIndex, check_corpus
from wiki_cli.vocabulary import RELATION_TYPES

FOLDERS = ["concepts", "sources", "tms/admin", "tms/pipeline", "docs", "skills"]
BODY = "Synthetic body paragraph about the topic. " * 40


def generate(root: Path, page_count: int, relation_count: int, seed: int = 7) -> list[str]:
    rng = random.Random(seed)
    slugs = [f"{FOLDERS[i % len(FOLDERS)]}/page-{i:05d}" for i in range(page_count)]
    per_page = [0] * page_count
    for _ in range(relation_count):
        per_page[rng.randrange(page_count)] += 1
    for index, slug in enumerate(slugs):
        targets = set()
        while len(targets) < per_page[index]:
            candidate = rng.randrange(page_count)
            if candidate != index:
                targets.add(candidate)
        lines = ["---", f"title: Page {index}", f"summary: Synthetic page {index}.", "type: concept"]
        if targets:
            lines.append("relations:")
            for target in sorted(targets):
                lines += [f"  - target: wiki://bench/{slugs[target]}",
                          f"    type: {rng.choice(RELATION_TYPES)}",
                          f'    reason: "Synthetic reason {index}-{target}."']
        lines += ["---", f"# Page {index}", "", BODY, ""]
        path = root / f"{slug}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
    return slugs


def timed(label: str, fn, results: dict):
    start = time.perf_counter()
    value = fn()
    results[label] = time.perf_counter() - start
    print(f"{label:<42} {results[label]:8.2f} s")
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages", type=int, default=30_000)
    parser.add_argument("--relations", type=int, default=100_000)
    parser.add_argument("--dir", type=Path)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()

    base = args.dir or Path(tempfile.mkdtemp(prefix="wiki-bench-"))
    repo = base / "repo"
    root = repo / "wiki"
    root.mkdir(parents=True, exist_ok=True)
    (repo / "wiki.toml").write_text('name = "bench"\n', encoding="utf-8")
    results: dict[str, float] = {}

    try:
        slugs = timed("generate corpus", lambda: generate(root, args.pages, args.relations), results)
        settings = load_settings(root, None, repo / ".cache" / "wiki.sqlite3")

        def sync_all():
            for page_file in discover(settings):
                sync_page(load(page_file, settings.space), settings.space)
        timed("rel sync --all (first run, writes blocks)", sync_all, results)

        with Cache(settings) as cache:
            stats = timed("index rebuild (cold)", cache.rebuild, results)
            print(f"  indexed pages={stats.added}")
            timed("index rebuild (warm file cache)", cache.rebuild, results)
            timed("index refresh (no changes)", cache.refresh, results)

            rng = random.Random(1)
            for slug in rng.sample(slugs, 100):
                path = root / f"{slug}.md"
                path.write_text(path.read_text(encoding="utf-8") + "\nEdited.\n", encoding="utf-8")
            stats = timed("index refresh (100 changed pages)", cache.refresh, results)
            print(f"  changed={stats.changed}")

            status = cache.status()
            print(f"  cache pages={status['pages']} relations={status['relations']}")

            sample = rng.sample(slugs, 500)
            latencies = []
            for slug in sample:
                start = time.perf_counter()
                cache.ensure_fresh(slug)
                cache.neighbors(slug)
                latencies.append((time.perf_counter() - start) * 1000)
            latencies.sort()
            print(f"{'neighbors in-process (ensure_fresh + query)':<42} "
                  f"p50 {statistics.median(latencies):.2f} ms, p95 {latencies[int(len(latencies) * 0.95)]:.2f} ms")

        size_mb = sum(p.stat().st_size for p in (repo / ".cache").iterdir()) / 1_048_576
        print(f"{'cache size (db + wal)':<42} {size_mb:8.1f} MB")

        wiki_exe = Path(sys.executable).with_name("wiki.exe" if os.name == "nt" else "wiki")
        cli = []
        for slug in sample[:20]:
            start = time.perf_counter()
            subprocess.run([str(wiki_exe), "rel", "neighbors", slug, "--format", "json",
                            "--wiki-root", str(root), "--cache", str(settings.cache_path)],
                           check=True, capture_output=True)
            cli.append((time.perf_counter() - start) * 1000)
        print(f"{'wiki rel neighbors (CLI process, cold)':<42} median {statistics.median(cli):.0f} ms")

        def check_all():
            files = discover(settings)
            pages = [load(page_file, settings.space) for page_file in files]
            return check_corpus(pages, SlugIndex.build(files), settings.space)
        issues = timed("check --all", check_all, results)
        print(f"  errors={sum(1 for issue in issues if issue.severity == 'error')}")
    finally:
        if not args.keep and not args.dir:
            shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    main()
