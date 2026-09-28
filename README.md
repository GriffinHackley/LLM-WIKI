# wiki-cli

Search and navigation for a folder of Markdown notes maintained by an LLM (an
Obsidian vault, a `docs/` folder, a Karpathy-style wiki). Agents find the best
starting page without reading an index, see where each page leads through typed
relations with reasons, and read only the sections they need, within a page limit.

**New here? Start with [docs/getting-started.md](docs/getting-started.md)**: install, set
up an existing wiki, and connect Claude Code, in about ten minutes.

See [PLAN.md](PLAN.md) for the current plan,
[docs/archive/upgrade-plan-v2.md](docs/archive/upgrade-plan-v2.md) for the design so far,
and [FUTURE_IDEAS.md](FUTURE_IDEAS.md) for deferred ideas.

The tool never edits pages. Everything it builds lives in a disposable SQLite
cache in `<root>/.cache/` (add `.cache/` to the repo's `.gitignore`).

## Install

Requires Python 3.13.

To use `wiki` from any folder (an isolated install that follows this checkout):

```bash
uv tool install --editable <path-to-this-repo>
wiki models download   # once: bge-small + jina-reranker-v1-turbo, about 0.2 GB
```

For development:

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[test]"
```

## Quick start

It works with no configuration: run it anywhere inside a folder of Markdown.

```bash
cd my-notes
wiki index refresh                 # index and embed (about 2 minutes per 500 pages on CPU)
wiki search "how do I configure the exporter?"
wiki neighbors setup-guide
wiki init                          # print a starter .wiki-cli.toml for this repo
```

With no config, every `*.md` file under the folder is a page (hidden folders and
`node_modules` are skipped), `raw/**/*.txt` is searchable source text if a `raw/`
folder exists, both `[[wikilinks]]` and `[text](path.md)` links are followed, and
every link is a `links-to` relation whose reason is the sentence around it.

## Configure: `.wiki-cli.toml`

Commands find the root by walking up to the nearest `.wiki-cli.toml`, else use the
enclosing git repository, else the current folder, and say which folder they chose
when no config did. They refuse to treat your home folder or a drive root as a wiki.
`--root` or `WIKI_ROOT` overrides all of this, and `WIKI_CONFIG` points at a
different config file. `wiki init` drafts one from a survey of the repo. Every
setting is optional:

```toml
pages = ["wiki/**/*.md"]          # default ["**/*.md"]
exclude = ["wiki/index.md"]
raw = ["raw/**/*.txt"]            # default; searched only with --include-raw
embed_model = "BAAI/bge-small-en-v1.5"
reranker = "jinaai/jina-reranker-v1-turbo-en"   # or "none"

[summary]                         # where a page's summary comes from, in order
fields = ["summary", "description"]            # frontmatter keys (default)
headings = ["Summary", "Overview"]             # default ["Summary"]; else the first paragraph

[page_type]
field = "type"                    # frontmatter key holding the page type (default)
[page_type.folders]               # optional: type by folder when the field is absent
"notes/people" = "person"

[check]
require_frontmatter = false       # default
summary_types = ["person"]        # page types that must have a summary ("*" = all)

[suggest]
named_types = ["person", "organization"]  # pages `suggest` matches by name (default: all)

[search]
results = 3                       # pages from search, nav start and nav search (1-20); --limit overrides
```

## Relations

Typed relations come from rules in the same file. A rule gives links under a
heading, or the values of a frontmatter field, a type and an inverse label:

```toml
[[relations]]
heading = "Entities mentioned"    # or a list of headings
page_type = "document"            # optional, or a list
type = "mentions"
inverse = "mentioned-in"

[[relations]]
field = "sources"                 # frontmatter field holding slugs or [[links]]
type = "draws-on"
inverse = "drawn-on-by"
reason = "Listed in sources."     # optional fixed reason
```

With that rule, this list item

```markdown
## Entities mentioned
- [[mike-johnson]] — Speaker who delayed the oath (p. 2)
```

becomes `mentions -> mike-johnson` with reason "Speaker who delayed the oath
(p. 2)". Without rules, links are still relations (`links-to`), and frontmatter
values written as `[[links]]` count as links, as in Obsidian. Two types are built
in: `embeds` (a `![[page#^block]]` embed) and `links-to`. When a page reaches one
target several ways, the most specific type wins: heading-rule types in file order,
then `embeds`, then field-only types, then `links-to`. `wiki vocab` lists the types.
Changing the rules re-derives relations on the next refresh without re-embedding.

## Commands

| Command | Purpose |
|---|---|
| `wiki search "<question>" [--limit 3] [--include-raw] [--keyword-only]` | Best pages for a question: keyword + vector search, fused and reranked |
| `wiki nav start \| read \| candidates \| search \| end \| log` | Guided traversal sessions (see [docs/navigation.md](docs/navigation.md)) |
| `wiki suggest <slug>` | Pages a page names but does not link, shares linked pages with, or resembles |
| `wiki unwritten [--limit N]` | Link targets with no page, most-linked first |
| `wiki orphans` | Pages nothing relates to |
| `wiki neighbors <slug> [--incoming] [--outgoing] [--relation T] [--limit N]` | A page's typed relations with reasons, no page bodies |
| `wiki check <slug> \| --all [--verify-cache] [--strict] [--no-warnings]` | Frontmatter, summaries, ambiguous and unwritten links, stale summaries |
| `wiki index refresh \| rebuild [--no-embed] \| status` | Manage the cache and embeddings |
| `wiki init [--write]` | Survey the repo and draft a `.wiki-cli.toml` (never overwrites) |
| `wiki models list \| download` | Supported models; `download` is the only command that downloads |
| `wiki eval sample \| run` | Search-quality evaluation (see [docs/evaluation.md](docs/evaluation.md)) |
| `wiki vocab` | Relation types, their inverses, and where each comes from |

All commands accept `--format json` (compact, deterministic), `--root` and
`--cache`. Exit codes: 0 success, 1 validation failures, 2 usage or runtime
errors.

## Models

`--embed-model` / `WIKI_EMBED_MODEL` and `--reranker` / `WIKI_RERANKER` override
`.wiki-cli.toml`. Models load from `~/.cache/wiki-cli/models/` only; run
`wiki models download` once. Without a downloaded model, search falls back to
keyword-only and says so.

## Tests and benchmark

```bash
.venv/Scripts/python -m pytest
.venv/Scripts/python benchmarks/bench_synthetic.py --pages 30000 --relations 100000
```
