# LLM Wiki Upgrades Plan (v2)

Version 1 of this plan assumed the geronimo-iia `llm-wiki` engine. The real target was
an existing Obsidian wiki maintained by Claude from instructions in its `CLAUDE.md`,
with its own `/ingest`, `/query` and `/lint` skills. No wiki engine is involved.
Version 1 is in git history.

## Purpose

Cut the tokens an agent spends answering questions from the wiki, and improve
answer quality, by giving it:

1. Search that finds the best starting page without reading `wiki/index.md`
   (45 KB, about 11k tokens, read on every `/query` then).
2. A typed relationship graph, **derived from the structure the wiki already
   has**, so an agent can see where a page leads before reading anything.
3. Guided traversal: candidate pages shown as short summaries with the reason for
   each link, section-level reads, a visited list, and a hard page limit.

## Decisions

| Decision | Choice |
|---|---|
| Relationship source | Derived from existing sections and frontmatter by rules in the wiki's `.wiki-cli.toml`; the tool has no built-in knowledge of any wiki's templates |
| Wiki engine | None. The `wiki` tool integrates with the wiki's existing skills |
| Search scope | The wiki's pages (except `index.md`), including files that share a name in different folders; `raw/*.txt` as a second tier |
| Changes to the wiki's repo | On a branch, delivered as a pull request |
| Storage | One disposable SQLite file: FTS5 keyword index, sqlite-vec vectors, relations, traversal sessions |
| Models | Local only, CPU, fastembed (ONNX); chosen by evaluation on the wiki |
| Language | Python 3.13 |
| Summaries | Already present in pages (`## Summary`, `## What this is`, `## Claim`); no backfill |
| Traversal rationale | A few sentences at most (hard cap ~400 characters) |
| Reads | Best-matching section by default; whole page on request |
| Command | One `wiki` command |

Deferred ideas live in `FUTURE_IDEAS.md`.

## Wiki layout the tool relies on

- Pages carry YAML frontmatter with `title` and `type` (`person`, `organization`,
  `place`, `event`, `document`, `topic`, `claim`, `meta`, ...).
- Links are Obsidian-style. `[[slug]]` resolves by file name; `[[slug|title]]`
  sets display text (`\|` inside tables); `![[doc#^q-id]]` embeds a quote block;
  files that share a name (`claims.md`, `open-questions.md`) are linked by path,
  `[[notebooks/<name>/claims|...]]`.
- Claim pages are generated from ledgers (`claims.md`). They are indexed like any page.
- `raw/` is immutable. Extracted text sits beside originals as `raw/<name>.txt`;
  document pages point at their original with `source_path:`.

## Configuration: `.wiki-cli.toml` in the wiki repo root

```toml
pages = [
  "wiki/**/*.md",
  "notebooks/*/claims.md",
  "notebooks/*/open-questions.md",
]
exclude = ["wiki/index.md"]
raw = ["raw/*.txt"]
```

The root is found from `--root`, then `WIKI_ROOT`, then by walking up from the
current directory to the first `.wiki-cli.toml`. The cache lives in
`<root>/.cache/wiki.sqlite3`; `.cache/` is added to the wiki repo's `.gitignore`.

## Page model

- **Slug:** Obsidian's resolution. The file name without `.md` when unique among
  indexed files, else the path from the root without `.md`
  (`notebooks/<name>/claims`).
- **Summary:** the first paragraph of `## Summary`, else `## What this is`, else
  `## Claim`, else the first prose paragraph; truncated to ~300 characters.
- **Raw text files** are search-only "sources": slug `raw/<stem>`, linked to the
  document page whose `source_path` shares the stem.

## Derived relations

Edges come from the section a link sits in and from frontmatter. Each edge
carries a reason drawn from the source line, so routing needs no page reads.

Since Phase 7 the mapping below is not in the code: it is that wiki's
`[[relations]]` rules in its `.wiki-cli.toml`. Without rules, every link is a
`links-to` edge with the surrounding sentence as its reason. The `source_path` and
bare claim-ID rows no longer apply: claim IDs are now `[[N-049]]` links in the wiki,
and each document page links its raw text in the body.

| From page type | Where | Edge type | Reason text |
|---|---|---|---|
| any | frontmatter `sources:` | `draws-on` | "Listed in sources." |
| document | `## Entities mentioned` | `mentions` | the list line (role in the document) |
| document | `## Claims supported` (plain `N-049` IDs or links) | `supports` | the list line |
| document | `source_path:` / matching raw text | `transcribes` | "Extracted text of this document." |
| person, organization | `## Relationships`, `## People associated` | `associated-with` | the list line |
| person, organization, place | `## Appearances in sources` | `appears-in` | the list line |
| event | `## Participants` | `involves` | the list line |
| event | `## Location` | `located-at` | the line |
| place | `## Events here` | `hosted` | the line |
| claim | `## Sources` | `sourced-by` | the ledger text around the link |
| claim | frontmatter `rests_on:` | `rests-on` | "Premise of this argument." |
| topic | `## Key pages` | `synthesizes` | the list line |
| any | quote embed `![[doc#^q-id]]` | `quotes` | "Embeds quote q-id." |
| any | any other link | `links-to` | section heading plus the sentence fragment |

When one page reaches a target through several routes, the most specific type
wins (`mentions` over `links-to`, for example). Links to pages that do not exist
are kept as unresolved edges: the wiki allows them, and they flag pages worth
writing. Incoming edges show inverse labels (`mentions` → `mentioned-in`).

## Search

`wiki search "<question>" [--limit 3] [--include-raw]`

1. Keyword: FTS5 BM25 over chunks (title, heading path, text weighted 4 : 2 : 1).
2. Vector: nearest chunks by cosine similarity (sqlite-vec, exact).
3. Reciprocal rank fusion, then a cross-encoder reranks the top ~30.
4. Page score = best chunk. Output: slug, title, type, summary, best section.

Raw text is excluded unless `--include-raw` (the `/query` fallback). Missing
models degrade to keyword search with a note.

## Traversal

```bash
wiki nav start "<question>" [--max-pages 6]
wiki nav read <session> <slug> --why "..." [--section <heading> | --full]
wiki nav candidates <session>
wiki nav search <session> "<open question>"
wiki nav end <session> --cited <slug>[,<slug>...]
```

- `start` searches, stores the question vector, and opens a session.
- `read` refuses already-visited pages and refuses past `--max-pages`. It
  requires `--why`, of a few sentences at most (about 400 characters). By default
  it returns the section that best matches the question, plus the page's section
  list.
- `candidates` lists the current page's relations first (type, reason, summary),
  then similar pages by vector, excluding linked and visited pages.
- When nothing looks useful: backtrack to the best unvisited candidate seen
  earlier; then one `nav search` per open question, excluding visited pages; then
  stop and state the gap. Every fallback counts toward the limit.
- `end` records cited pages. Session logs live in the cache and expire after 14
  days.

## Integration with the wiki's skills (pull request)

- `/query`: `wiki nav` replaces reading `wiki/index.md`; `raw/` fallback via
  `wiki search --include-raw`.
- `/ingest`: after writing pages, `wiki index refresh`; `wiki suggest <slug>` to
  find pages the new document should link or update.
- `/lint`: `wiki check --all` alongside the existing scripts.
- `.gitignore`: add `.cache/`. Add `.wiki-cli.toml` and `eval/questions.yaml`.
- `.claude/settings.json`: allow `Bash(wiki:*)` so the commands run without
  prompts.
- `CLAUDE.md`: describe the tools where the workflows mention them.

## Evaluation

About 50 questions generated by Claude from sampled pages, stored in the wiki
repo at `eval/questions.yaml`: ~35 single-page (paraphrased away from the page's
wording), ~10 two-page (along a relation), ~5 unanswerable. Split into tune and
test. Metrics: hit@1, hit@3, MRR, both pages in the top 5 for two-page questions,
and whether the top score separates answerable from unanswerable questions.
Embedding candidates: `bge-small-en-v1.5`, `nomic-embed-text-v1.5`,
`Qwen3-Embedding-0.6B-Q`. Reranker candidates: `bge-reranker-base`,
`ms-marco-MiniLM-L-12-v2`, `jina-reranker-v1-turbo-en`. Choose the smallest
combination within a small margin of the best on tune, then confirm on test.

## Phases

- [x] **Phase 1:** relations cache, `check`, `neighbors`, `index` (v1 format).
- [x] **Phase 2:** chunking, FTS5, embeddings, reranking, `search`, `eval`.
- [x] **Phase 3: Adapt to the wiki.** `.wiki-cli.toml` and root discovery; Obsidian
  slugs and link parsing; derived relations replacing the authored `relations:` field
  and generated block (removed); summary extraction; raw text tier; `check` reworked
  for this wiki.
- [x] **Phase 4: Model selection.** Chose bge-small + jina-reranker-v1-turbo-en, reranking 20 passages
  of 1,200 characters (see `docs/model-selection.md`). Download candidate models, generate the
  evaluation set from the wiki, run the comparison, set the defaults.
- [x] **Phase 5: Traversal.** `wiki nav` sessions as above, plus `wiki suggest`, `wiki unwritten`
  and `wiki orphans` for `/ingest` and `/lint`. The default section is the closest by stored
  vectors (no model load; the reranker agreed on 51 of 68 evaluation pages); reads include the
  page summary.
- [x] **Phase 6: Integration.** A pull request to the wiki's repo: `/query` uses `wiki nav`,
  `/ingest` uses `wiki suggest` and `wiki index refresh`, `/lint` uses `wiki unwritten`,
  `wiki orphans` and `wiki check --all`. `wiki` is installed with `uv tool install --editable`.
- [x] **Phase 7: Wiki-agnostic.** Everything specific to one wiki moved into `.wiki-cli.toml`:
  `[[relations]]` rules (heading or frontmatter field -> type and inverse), `[summary]` fields and
  headings, `[page_type]` field or folder mapping, `[check]` and `[suggest]` page types. The
  config is optional: defaults index every `*.md`, follow `[[wikilinks]]` and `[text](path.md)`
  links, and treat `raw/**/*.txt` as source text when a `raw/` folder exists. `wiki init`
  drafts a config from a survey of the repo. The wiki declares its rules in its config and
  links its claim IDs; its relation counts are unchanged except the removed `transcribes`
  edges, and search quality is within noise (one of 50 questions moved from rank 3 to 4).

Dropped from v1: authored `relations:` frontmatter, the generated link block and
`rel sync`, the llm-wiki `ingest`/`suggest` wrappers and permission rules, the
Obsidian / llm-wiki compatibility check, summary backfill, and the
`Related pages` migration. None apply to this wiki.

## Performance (synthetic, 30,000 pages, 100,000 relations, 209,000 chunks)

| Operation | Measured |
|---|---|
| keyword search | 17 ms |
| keyword + vector search (database cost only) | 190 ms |
| `wiki neighbors` (CLI process, cold) | 122 ms |
| no-change refresh | 0.36 s |
| refresh after 100 changed pages | 1.0 s |
| `check --all` | 5.0 s |
| cache size with 384-dim vectors | 606 MB |

The wiki (414 pages plus 101 raw text files, 2,656 derived relations) indexes in about
1 second without embeddings. Real model costs are measured in Phase 4.

Commands on the wiki (CPU, median of 3 fresh processes). Model-free commands cost
about 140 ms, almost all Python start-up and imports. Models run directly with
onnxruntime; importing fastembed for inference cost another 0.5 s per command.

| Command | Before | Now |
|---|---|---|
| `neighbors`, `nav candidates`, `index status`, `search --keyword-only` | 150 ms | 150 ms |
| `search` (embed query, rerank 20 passages) | 1.36 s | 0.98 s |
| `nav start` | 1.36 s | 1.00 s |
| `nav read` (section chosen from stored vectors) | 0.97 s | 0.15 s |

## Open decisions

- Default `--max-pages` per question (proposed: 6).
- Whether to add authored typed relations later for free-text `## Relationships`
  lines, if derived `associated-with` edges prove too coarse.
