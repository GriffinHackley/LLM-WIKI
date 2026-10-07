# LLM wiki starter kit (`wiki`)

Start and run an **LLM-maintained wiki** (a folder of Markdown notes that an agent
keeps: an Obsidian vault, a research notebook, a wiki about a codebase) with any agent
that can run shell commands: Claude Code, Codex, Cursor, Copilot, OpenCode and others.

- **`wiki new`** sets up a wiki from a preset: folder layout, page templates, an
  `AGENTS.md` telling the agent how to be the librarian, and a config whose relation
  rules match the templates. Presets: `research` (sources in, pages out) and `code` (a
  wiki in its own repo describing a code repo).
- **`wiki guide`** prints the workflow an agent follows (ingest a source, answer a
  question, audit the wiki, sync after code changes), versioned with the command so the
  steps never go stale.
- **Search and navigation** find the best starting page without reading an index, show
  where each page leads through typed relations with reasons, and read only the sections
  needed, within a page limit.

**New here? Start with [docs/getting-started.md](https://github.com/GriffinHackley/LLM-WIKI/blob/main/docs/getting-started.md).**
[docs/workflows.md](https://github.com/GriffinHackley/LLM-WIKI/blob/main/docs/workflows.md) covers the workflows and how to customise them,
[docs/presets.md](https://github.com/GriffinHackley/LLM-WIKI/blob/main/docs/presets.md) the presets, [docs/config.md](https://github.com/GriffinHackley/LLM-WIKI/blob/main/docs/config.md) every config
setting. [PLAN.md](https://github.com/GriffinHackley/LLM-WIKI/blob/main/PLAN.md) is the current plan,
[FUTURE_IDEAS.md](https://github.com/GriffinHackley/LLM-WIKI/blob/main/FUTURE_IDEAS.md) the deferred ideas.

The `wiki` command never edits pages: agents do. What it builds for search lives in a
disposable SQLite cache in `<root>/.cache/`.

## Install

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/), which installs
Python 3.13 for the tool if needed.

```bash
uv tool install llm-wiki-cli    # once per machine; the command is `wiki`
wiki models download            # once: about 0.2 GB
```

`uv tool upgrade llm-wiki-cli` updates it. The latest code, before a release:
`uv tool install git+https://github.com/GriffinHackley/LLM-WIKI`. For development, from a clone:

```bash
uv tool install --editable .        # 'wiki' follows the checkout
python -m venv .venv && .venv/Scripts/python -m pip install -e ".[test]"
```

An earlier install named `wiki-cli` also provides `wiki`: remove it first with
`uv tool uninstall wiki-cli`.

## Quick start

A new research wiki:

```bash
wiki new my-wiki --agent claude     # optional slash commands (also copilot, opencode); AGENTS.md works for any agent
cd my-wiki
# put a source in raw/, then ask your agent: "ingest raw/<file>"
```

A wiki about a codebase, in its own repo beside the code:

```bash
wiki new my-app-wiki --preset code --code ../my-app
# add the lines it prints to the code repo, then work from there as usual
```

An existing folder of notes (an Obsidian vault, a `docs/` folder, a wiki an agent already
keeps):

```bash
cd my-notes
wiki new . --agent claude           # drafts a .wiki-cli.toml from your pages, adds the agent files
wiki index refresh                  # index and embed (about 2 minutes per 500 pages on CPU)
wiki search "how do I configure the exporter?"
```

Your pages stay as they are; [getting started](https://github.com/GriffinHackley/LLM-WIKI/blob/main/docs/getting-started.md#5-add-to-an-existing-wiki)
covers reviewing the drafted config and adopting a preset's page types.

It also works with no configuration at all: every `*.md` file under the folder is a
page, `raw/**/*.txt` is searchable source text, and every link is a `links-to` relation
whose reason is the sentence around it.

## Configure: `.wiki-cli.toml`

Commands find the root by walking up to the nearest `.wiki-cli.toml`, else use the
enclosing git repository, else the current folder, and say which folder they chose when
no config did. They refuse to treat your home folder or a drive root as a wiki. `--root`
or `WIKI_ROOT` overrides all of this, and `WIKI_CONFIG` points at a different config
file. Every setting is optional; [docs/config.md](https://github.com/GriffinHackley/LLM-WIKI/blob/main/docs/config.md) is the full reference,
with each key's type and default:

```toml
preset = "research"               # the preset `wiki new` used; picks guide variants
pages = ["wiki/**/*.md"]          # default ["**/*.md"]
exclude = ["wiki/index.md"]      # default none
raw = ["raw/**/*.txt"]            # default; searched only with --include-raw
embed_model = "BAAI/bge-small-en-v1.5"          # default; $WIKI_EMBED_MODEL overrides
reranker = "jinaai/jina-reranker-v1-turbo-en"   # default, or "none"; $WIKI_RERANKER overrides

[types.person]                    # page types: guides list them, `wiki check` flags others
description = "A person."
folder = "wiki/people"            # pages here get this type when frontmatter has none
template = "templates/person.md"
sections = ["Summary"]            # headings and frontmatter every page of the type has,
fields = ["title", "type", "sources", "last_updated"]   # checked by `wiki check`
values = { role = ["author", "subject"] }               # allowed values of a field
hub = false                       # true: pages gather around it (see `wiki clusters`)
record = false                    # true: each page describes a ticket or the like, kept by key, url, synced

[records]                         # record types: when `wiki stale` asks for a recheck
recheck_days = 30                 # default

[summary]                         # where a page's summary comes from, in order
fields = ["summary", "description"]            # frontmatter keys (default)
headings = ["Summary", "Overview"]             # default ["Summary"]; else the first paragraph

[page_type]
field = "type"                    # frontmatter key holding the page type (default)

[check]
require_frontmatter = false       # default
summary_types = ["person"]        # page types that must have a summary ("*" = all)

[suggest]
named_types = ["person", "organization"]  # pages `suggest` matches by name (default: all)

[search]
results = 3                       # default; pages from search, nav start and nav search (1-20)

[pending]
ignore = ["raw/SOURCES.md"]       # files in raw/ that are not sources (default none)

[weekly]                          # weekly notes (`wiki weekly`); off without this table
folder = "weekly"                 # default; never indexed
group_by = "module"               # "where the work went" by module, or "type" (default)

[guides]
dir = "guides"                    # the wiki's own guides, overriding built-in ones by name
parts = "guides/parts"            # default; <name>.md is the wiki's own text for a guide step

[code]                            # code wikis only
repo = "../my-app"                # the code repo, relative to the wiki; $WIKI_CODE_REPO overrides
origin = "https://github.com/me/my-app.git"    # catches a pointer to the wrong checkout
```

In a code repo, a `.wiki-cli.toml` holding only `wiki = "../my-app-wiki"` sends every
command run there to the wiki.

## Relations

Typed relations come from rules in the same file. A rule gives links under a heading,
or the values of a frontmatter field, a type and an inverse label:

```toml
[[relations]]
heading = "Entities mentioned"    # or a list of headings
page_type = "source"              # optional, or a list
type = "mentions"
inverse = "mentioned-in"

[[relations]]
field = "sources"                 # frontmatter field holding slugs or [[links]]
type = "draws-on"
inverse = "drawn-on-by"
reason = "Listed in sources."     # optional; without it, the sentence citing the page
```

With that rule, this list item

```markdown
## Entities mentioned
- [[charles-babbage]] — Inventor who delayed the demonstration (p. 2)
```

becomes `mentions -> charles-babbage` with reason "Inventor who delayed the
demonstration (p. 2)". A link in parentheses under a typed heading, such as a citation
`([[report]], p. 2)`, is a plain link: it supports the line rather than being its subject. Without rules, links are
still relations (`links-to`), and frontmatter values written as `[[links]]` count as
links, as in Obsidian. Built in: `embeds` (a `![[page#^block]]` embed), `refers-to-code`
(a `[name](code:path)` link to a file in a code wiki's code repo) and `links-to`. When a
page reaches one target several ways, the most specific type wins: heading-rule types in
file order, then `embeds`, then field-only types, then `links-to`. `wiki vocab` lists
the types. Changing the rules re-derives relations on the next refresh without
re-embedding.

## Commands

| Command | Purpose |
|---|---|
| `wiki new [folder] [--preset research\|code\|<folder>] [--code <repo>] [--agent claude\|copilot\|opencode ...] [--git-hook]` | Create a wiki from a preset; in a folder that already has pages, adopt it (draft its config, add the agent files, leave the pages alone); never overwrites |
| `wiki guide [<name>] [--parts]` | Print a workflow's steps for an agent; without a name, list them; `--parts` lists the steps a wiki can fill with its own text (`guides/parts/`) |
| `wiki search "<question>" [--limit 3] [--include-raw] [--keyword-only]` | Best pages for a question: keyword + vector search, fused and reranked |
| `wiki nav start \| read \| candidates \| search \| end \| log` | Guided traversal sessions (see [docs/navigation.md](https://github.com/GriffinHackley/LLM-WIKI/blob/main/docs/navigation.md)) |
| `wiki list [--type T]` | Every page with its type and summary |
| `wiki pending` | Sources in `raw/` no page links to yet, flagging copies of sources already ingested |
| `wiki neighbors <slug> [--incoming] [--outgoing] [--relation T] [--limit N]` | A page's typed relations with reasons, no page bodies |
| `wiki suggest <slug>` | Pages a page names but does not link, shares linked pages with, or resembles |
| `wiki unwritten [--limit N]` | Link targets with no page, most-linked first |
| `wiki orphans` | Pages nothing relates to |
| `wiki clusters [--all] [--min-size 4]` | Groups of pages that link each other densely (Louvain communities) with no hub page most of them link to: leads for pages worth writing. Wikis of 30 pages or more |
| `wiki map [--query "<question>"] [--nav <session>\|last\|all] [--chunks] [--method auto\|umap\|pca] [--color-by type\|cluster\|age\|visits] [--open]` | A 3D map of the pages' embeddings as one HTML page in `.cache/` (works offline): points coloured by type, link cluster or age, relations as lines; a question placed among the pages with lines to its search results; nav sessions drawn as paths to step through. UMAP by default; if UMAP cannot be used it says so and uses PCA |
| `wiki stale` | Pages whose covered code changed since they were verified, and record pages (tickets) due for a recheck |
| `wiki weekly [--week 2026-W40 \| current]` | With `[weekly]`: a note per finished week of work (pages added and changed, where the work went, sources, open questions, health, code) and a timeline, from git; `current` shows this week so far |
| `wiki check <slug> \| --all [--verify-cache] [--strict] [--no-warnings] [--summary-ok]` | Frontmatter, summaries, types and their required sections and fields, uncited sources, ambiguous and unwritten links, stale summaries, code links, hub pages that several separate clusters gather around, file pages filed under a broader module than the one covering them |
| `wiki check <source-slug> --ingested` | Whether an ingest is complete: original linked, discussed pages named and citing it, all clean and committed |
| `wiki index refresh \| rebuild [--no-embed] \| status` | Manage the cache and embeddings |
| `wiki init [--write] [--preset <name>]` | Survey an existing folder and draft a `.wiki-cli.toml` (never overwrites); `--preset` adds the preset's page types, relation rules and weekly notes, or prints just those for a wiki that has a config |
| `wiki models list \| download` | Supported models; `download` is the only command that downloads |
| `wiki eval sample \| run` | Search-quality evaluation (see [docs/evaluation.md](https://github.com/GriffinHackley/LLM-WIKI/blob/main/docs/evaluation.md)) |
| `wiki vocab` | Relation types, their inverses, and where each comes from |

All commands accept `--format json` (compact, deterministic); commands on a wiki accept
`--root` and `--cache`. Exit codes: 0 success, 1 validation failures, 2 usage or runtime
errors.

## Models

`--embed-model` / `WIKI_EMBED_MODEL` and `--reranker` / `WIKI_RERANKER` override
`.wiki-cli.toml`. Models load from `~/.cache/wiki-cli/models/` only; run
`wiki models download` once. Without a downloaded model, search falls back to
keyword-only and says so. [docs/model-selection.md](https://github.com/GriffinHackley/LLM-WIKI/blob/main/docs/model-selection.md) explains the
defaults.

## Tests and benchmark

```bash
.venv/Scripts/python -m pytest
.venv/Scripts/python benchmarks/bench_synthetic.py --pages 30000 --relations 100000
```

The tests use fake models and download nothing. GitHub Actions runs them on Windows,
macOS and Linux.

## License

MIT; see [LICENSE](https://github.com/GriffinHackley/LLM-WIKI/blob/main/LICENSE).
