# wiki-cli

Read-side tooling for [llm-wiki](https://github.com/geronimo-iia/llm-wiki):
typed page relations now; hybrid search and guided traversal in later phases.
See [UPGRADE_PLAN.md](UPGRADE_PLAN.md) for the design and
[FUTURE_IDEAS.md](FUTURE_IDEAS.md) for deferred ideas.

llm-wiki stays the write-side engine. This tool never modifies frontmatter; it
only edits its own generated link block at the end of a page body.

## Install

Requires Python 3.13.

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[test]"
```

## Relations

Declare relations in page frontmatter:

```yaml
relations:
  - target: wiki://sp-wiki/tms/save-pipeline
    type: depends-on
    reason: "Uses this pipeline to persist mitigation changes."
```

Types: `depends-on`, `implements`, `implemented-by`, `used-by`, `configures`,
`tested-by`, `documents`, `related-to`. llm-wiki's `superseded_by` field is read
as a `superseded-by` relation. Run `wiki vocab` for the list with inverse labels.

## Commands

| Command | Purpose |
|---|---|
| `wiki rel sync <slug> \| --all [--dry-run]` | Regenerate the managed link block from frontmatter |
| `wiki rel neighbors <slug> [--incoming] [--outgoing] [--relation T] [--limit N]` | Related pages as compact routing metadata |
| `wiki check <slug> \| --all [--verify-cache] [--strict] [--no-warnings] [--require-summary]` | Validate relations, summaries, and link blocks (no writes) |
| `wiki index refresh \| rebuild \| status` | Manage the derived SQLite cache |
| `wiki vocab` | List relation types |

All commands accept `--format json` (compact, deterministic) and `--wiki-root`,
`--space`, `--cache`. Exit codes: 0 success, 1 validation failures, 2 usage or
runtime errors.

## Configuration

- **Wiki root:** `--wiki-root`, else `LLM_WIKI_ROOT`, else `~/llm-wiki/wiki`.
- **Local space:** `--space`, else `LLM_WIKI_SPACE`, else `name` in the repo's
  `wiki.toml`. When none is set, every target is treated as local.
- **Cache:** `--cache`, else `WIKI_CACHE`, else `<repo>/.cache/wiki.sqlite3`.
  Add `.cache/` to the wiki repo's `.gitignore`. The cache is disposable;
  `wiki index rebuild` recreates it from frontmatter.
- **Exclusions:** `[ingest] exclude` and `skip_no_frontmatter` from `wiki.toml`
  are honored, matching llm-wiki's page discovery.

## Tests and benchmark

```bash
.venv/Scripts/python -m pytest
.venv/Scripts/python benchmarks/bench_synthetic.py --pages 30000 --relations 100000
```

The Obsidian / llm-wiki compatibility check is manual: see
[compat/README.md](compat/README.md).
