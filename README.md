# wiki-cli

Search and navigation tools for an Obsidian wiki maintained by an LLM, built for
the Politics wiki. Agents find the best starting page without reading the index,
see where each page leads through typed relations with reasons, and read only
the sections they need. See [UPGRADE_PLAN.md](UPGRADE_PLAN.md) for the design and
[FUTURE_IDEAS.md](FUTURE_IDEAS.md) for deferred ideas.

The tool never edits wiki pages. Everything it builds lives in a disposable
SQLite cache.

## Install

Requires Python 3.13.

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[test]"
```

## Configure the wiki

Put `.wiki-cli.toml` in the wiki's root folder:

```toml
pages = ["wiki/**/*.md", "dossiers/*/DOSSIER.md", "dossiers/*/claims.md", "dossiers/*/open-questions.md"]
exclude = ["wiki/index.md"]
raw = ["raw/*.txt"]          # searched only with --include-raw
# embed_model = "BAAI/bge-small-en-v1.5"
# reranker = "jinaai/jina-reranker-v1-turbo-en"
```

Add `.cache/` to the wiki's `.gitignore`. Commands find the root by walking up
from the current folder; `--root` or `WIKI_ROOT` overrides that, and
`WIKI_CONFIG` points at a different config file.

## Relations

Relations are derived from what the pages already say, so there is nothing
extra to write. The section a link sits in sets its type, and the rest of the
list line becomes its reason:

```markdown
## Entities mentioned
- [[mike-johnson]] — Speaker who delayed the oath (p. 2)
```

becomes `abc-doc -> mentions -> mike-johnson` with reason "Speaker who delayed
the oath (p. 2)". Frontmatter `sources:`, `rests_on:` and `source_path:`, bare
claim IDs under `## Claims supported`, and quote embeds also become edges. Run
`wiki vocab` for every type and its inverse label.

## Commands

| Command | Purpose |
|---|---|
| `wiki search "<question>" [--limit 3] [--include-raw] [--keyword-only]` | Best pages for a question: keyword + vector search, fused and reranked |
| `wiki neighbors <slug> [--incoming] [--outgoing] [--relation T] [--limit N]` | A page's typed relations with reasons, no page bodies |
| `wiki check <slug> \| --all [--verify-cache] [--strict] [--no-warnings]` | Frontmatter, summaries, ambiguous and unwritten links, stale summaries |
| `wiki index refresh \| rebuild [--no-embed] \| status` | Manage the cache and embeddings |
| `wiki models list \| download` | Supported models; `download` is the only command that downloads |
| `wiki eval sample \| run` | Search-quality evaluation (see [docs/evaluation.md](docs/evaluation.md)) |
| `wiki vocab` | Relation types |

All commands accept `--format json` (compact, deterministic), `--root` and
`--cache`. Exit codes: 0 success, 1 validation failures, 2 usage or runtime
errors.

## Models

`--embed-model` / `WIKI_EMBED_MODEL` and `--reranker` / `WIKI_RERANKER` (or
`none`) override `.wiki-cli.toml`. Models load from `~/.cache/wiki-cli/models/`
only; run `wiki models download` once. Without a downloaded model, search falls
back to keyword-only and says so.

## Tests and benchmark

```bash
.venv/Scripts/python -m pytest
.venv/Scripts/python benchmarks/bench_synthetic.py --pages 30000 --relations 100000
```
