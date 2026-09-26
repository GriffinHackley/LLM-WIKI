# LLM Wiki Upgrades Plan

## Purpose

Improve long-term token efficiency and answer quality by giving AI agents:

1. A small, typed, directional relationship graph between wiki pages.
2. Hybrid search (keyword + vector + reranker) to find the best starting page.
3. Guided traversal that shows linked and similar pages as short summaries, so an
   agent chooses where to go before paying to read a page.

`llm-wiki` stays the write-side engine (schema validation, ingest, git commits,
MCP server, spaces, history). A new `wiki` CLI owns the read side (search,
navigation, typed relations) and wraps the two write steps where ordering matters.
The `llm-wiki` engine is not modified.

## Goals

- Store robust, directional, typed relationships between pages in frontmatter.
- Find the best starting page for a question with hybrid search.
- Let agents traverse by relation type, reason, and summary without reading full
  page bodies, and read only the relevant section of a page.
- Enforce visited-page tracking and a hard page limit per question.
- Generate Obsidian and `llm-wiki` compatible links mechanically from one
  canonical declaration.
- Improve link suggestions at authoring time with semantic similarity.
- Detect broken, stale, duplicate, and invalid relationships and summaries
  deterministically.
- Measure search and traversal quality with a generated evaluation set.
- Migrate away from manually maintained `Related Pages` sections.

## Non-goals

- Modifying or replacing the `llm-wiki` engine.
- Rebuilding `llm-wiki`'s write side (ingest, schemas, git, MCP, spaces, history).
- Remote or API-based models. All models run locally.
- Storing reciprocal relationship declarations on both pages.
- Fully connecting every page that shares broad tags.
- Replacing useful contextual links inside page prose.

Deferred ideas live in `FUTURE_IDEAS.md` and are not part of this plan.

## Architecture

```text
            write side                                read side
   ┌──────────────────────────┐              ┌──────────────────────────────┐
   │ llm-wiki (unchanged)     │              │ wiki CLI (new, Python)       │
   │ schemas, ingest, git,    │◄─ wiki ingest│ rel, search, nav, index,     │
   │ MCP, spaces, history     │◄─ wiki suggest check, ingest/suggest wrap │
   └────────────┬─────────────┘              └──────────────┬───────────────┘
                │ reads/writes                               │ reads pages,
                ▼                                            │ edits only its
   ┌──────────────────────────┐                              │ generated block
   │ wiki/**/*.md             │◄─────────────────────────────┘
   │ canonical: frontmatter   │
   │ relations + summary      │──── derived ───► ~/llm-wiki/.cache/wiki.sqlite3
   └──────────────────────────┘                  (pages, relations, FTS5,
                                                  sqlite-vec, nav sessions)
```

Page frontmatter is the only source of truth. The SQLite cache is disposable,
uncommitted, and always rebuildable.

## Page Model

### Slugs

Follow `llm-wiki`'s rule exactly: a slug is the page's path relative to `wiki/`,
without the `.md` extension, and may contain folders (`concepts/scaling-laws`).

- Like llm-wiki (`skip_no_frontmatter = true`), `.md` files without frontmatter
  are not pages and are ignored. `[ingest] exclude` globs from `wiki.toml` are
  honored.
- The local space defaults to `name` in the repo's `wiki.toml`.
- The wiki uses flat files only. Bundle pages (`x/index.md`) are not used; `check`
  warns if one appears, because Obsidian cannot resolve bundle slugs.
- `check` errors when two slugs differ only in case (Windows and Obsidian treat
  them as the same page).
- The Obsidian vault is opened at `wiki/`, so `[[concepts/scaling-laws]]` resolves
  identically in Obsidian and `llm-wiki`.

### Frontmatter: `relations`

```yaml
relations:
  - target: wiki://sp-wiki/tms/mitigation-save-pipeline
    type: depends-on
    reason: "Uses this pipeline to persist mitigation changes."
  - target: wiki://sp-wiki/tms/realtime-summary-config
    type: implemented-by
    reason: "Provides the concrete real-time editing widget."
```

Fields:

- `target`: required canonical `wiki://<space>/<slug>` URI. The slug may contain
  `/` and must not include `.md`.
- `type`: required value from the controlled vocabulary.
- `reason`: required concise explanation of why following the edge is useful.
- No other fields are allowed.

### Frontmatter: `summary`

Reuse `llm-wiki`'s existing optional base-schema field `summary` ("one-line
scope"). The same summaries then improve `llm-wiki suggest`'s BM25 strategy and
our traversal.

- New and substantially updated pages must have a summary written at ingest;
  `wiki ingest` rejects pages without one.
- Existing pages are backfilled by importance (see Summary Backfill). Until then,
  the tool uses a placeholder summary derived from the first body paragraph, held
  only in the cache and labeled as a placeholder in output.

### Relationship Vocabulary

- `depends-on`
- `implements`
- `implemented-by`
- `used-by`
- `configures`
- `tested-by`
- `documents`
- `related-to` (fallback; not the default for newly curated relationships)

`supersedes` is dropped: `llm-wiki` already has a native `superseded_by` field that
its graph understands. The `wiki` tool reads `superseded_by` and exposes it as a
relation in neighbor and candidate output.

Inverse relationships are computed, not stored. Incoming edges show an inverse
label (`depends-on` → `used-by`, `implements` → `implemented-by`,
`configures` → `configured-by`, `tested-by` → `tests`, `documents` →
`documented-by`, `related-to` → `related-to`).

The vocabulary lives in one versioned Python module. The JSON Schema fragment for
`relations` duplicates the enum, and a test asserts they stay in sync.

## Generated Compatibility Links

A managed block at the end of the page body, generated from frontmatter:

```markdown
<!-- wiki-relations:v1:start -->
[[tms/mitigation-save-pipeline]]
[[tms/realtime-summary-config]]
<!-- wiki-relations:v1:end -->
```

- One sorted, deduplicated `[[full-slug]]` line per local relation target.
- Cross-wiki targets are omitted from the block (Obsidian cannot resolve them).
- No `[[slug|title]]` form (`llm-wiki` does not support it).
- Owned entirely by tooling; `check` rejects manual content inside it.
- Replaced atomically when relations change; removed when there are none.
- `llm-wiki` turns these into generic `links-to` edges; typed edges exist only in
  our frontmatter and cache. Generating per-type `x-graph-edges` fields is
  explicitly not done (it would require rewriting frontmatter).

## Derived Cache (SQLite + FTS5 + sqlite-vec)

One file: `~/llm-wiki/.cache/wiki.sqlite3`. `.cache/` is added to the wiki repo's
`.gitignore`. All journals, temp files, downloaded models, and session data live
under `.cache/`. Agents use CLI commands only and never query the database.

WAL mode, one transaction per page update, so readers never see partial state.

### Schema (initial)

```sql
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
-- schema_version, wiki_root, space, embedding_model, embedding_revision,
-- embedding_dims, reranker_model, reranker_revision

-- Keyed by path, not slug: colliding slugs (x.md vs x/index.md, or case
-- variants) must both be tracked for change detection; check reports them.
CREATE TABLE pages (
  path TEXT PRIMARY KEY,
  slug TEXT NOT NULL,
  is_page INTEGER NOT NULL,   -- 0 for .md files llm-wiki skips (no frontmatter)
  mtime_ns INTEGER NOT NULL,
  size INTEGER NOT NULL,
  content_hash TEXT NOT NULL,
  body_hash TEXT,             -- body without the generated block
  title TEXT,
  page_type TEXT,
  summary TEXT,
  summary_is_placeholder INTEGER NOT NULL,
  summary_body_hash TEXT      -- body hash when summary last changed
  -- Phase 2 adds an integer id (for vector rowids) and embedded_hash
);
CREATE INDEX pages_by_slug ON pages(slug);

CREATE TABLE relations (
  source_path TEXT NOT NULL,
  source_slug TEXT NOT NULL,
  target_uri TEXT NOT NULL,
  target_space TEXT NOT NULL,
  target_slug TEXT NOT NULL,
  relation_type TEXT NOT NULL,
  reason TEXT NOT NULL,
  PRIMARY KEY (source_path, target_uri, relation_type)
);
CREATE INDEX relations_by_source ON relations(source_slug);
CREATE INDEX relations_by_target ON relations(target_slug);
CREATE INDEX relations_by_type ON relations(relation_type);

CREATE TABLE chunks (
  id INTEGER PRIMARY KEY,
  page_id INTEGER NOT NULL,
  ordinal INTEGER NOT NULL,
  heading_path TEXT NOT NULL,
  start_offset INTEGER NOT NULL,
  end_offset INTEGER NOT NULL,
  text TEXT NOT NULL,
  UNIQUE (page_id, ordinal)
);

CREATE VIRTUAL TABLE chunks_fts USING fts5(
  title, heading_path, text, content='chunks', content_rowid='id'
);
CREATE VIRTUAL TABLE chunk_vectors USING vec0(embedding float[<dims>]);
CREATE VIRTUAL TABLE summary_vectors USING vec0(embedding float[<dims>]);
-- vector rowids match chunks.id and pages.id respectively

-- nav session tables: sessions, session_visits, session_frontier, session_log
```

- A schema-version, wiki-root, or embedding-model mismatch triggers an automatic
  full rebuild, not an in-place migration.
- Vectors are compared exactly (no approximate index). At 30k pages × ~5 chunks ×
  384 dims this is about 230 MB and should query in tens of milliseconds.
  int8/binary quantization is available if needed.

### Indexing

`index refresh`:

1. Walk `wiki/` for `*.md` (skipping dot-folders), `stat` each file.
2. Skip files whose mtime and size match the cache.
3. Hash changed files; if the hash matches, update mtime only.
4. Reparse changed pages: frontmatter, relations, summary, chunks, FTS rows.
5. Delete rows for removed pages.
6. Embed pages whose `embedded_hash` differs from `content_hash`, in batches.

Steps 1–5 run in one transaction and complete quickly. Step 6 (embedding) runs
after, so relations and keyword search are fresh immediately; pages awaiting
embedding remain findable by keyword search. `refresh --no-embed` skips step 6.

`index rebuild` deletes and recreates the cache. `index status` reports version,
models, page/relation/chunk counts, stale pages, and pages awaiting embedding.

### Chunking

- Split by markdown heading; cap chunks at about 400 tokens with a small overlap.
- Prefix each chunk with the page title and heading path.
- Exclude frontmatter and the generated link block.
- Keep fenced code blocks whole where possible.
- Chunk 0 of every page is its title + summary.

### Summary staleness

When a page's body changes but its `summary` text does not, the cache keeps the
old `summary_body_hash` and `check` warns "body changed, summary unchanged". This
is derived state; after a rebuild all summaries are treated as fresh.

## Search

`wiki search "<question>" [--limit 3] [--format json]`

1. Keyword: FTS5 BM25 over chunks, top ~50.
2. Vector: embed the question, sqlite-vec nearest chunks, top ~50.
3. Fuse the two lists with reciprocal rank fusion (k = 60).
4. Rerank the top ~30 fused chunks against the question with a cross-encoder.
5. Page score = best chunk score. Return the top 1–3 pages.

Output per page: slug, title, summary, best-matching section heading, score. No
page bodies. The agent then reads the best page (or its best section) and decides
whether it has enough to answer.

## Traversal

Traversal runs inside a session so the tool can enforce rules across calls.

```bash
wiki nav start "<question>" [--max-pages 6]        # search + new session id
wiki nav read <session> <slug> --rationale "..." [--section <heading> | --full]
wiki nav candidates <session>                      # next-hop options
wiki nav search <session> "<open question>"        # fallback re-query
wiki nav end <session> --cited <slug>[,<slug>...]
```

### Session rules

- `start` embeds the question once and stores the vector, so `read` and
  `candidates` never load a model.
- `read` refuses already-visited pages and refuses once `--max-pages` is reached.
- `read` requires `--rationale`: a few sentences at most (hard cap ~400 chars)
  naming the open question and why this page beats the alternatives. Longer
  rationales are rejected.
- Default read returns the section that best matches the session question
  (by stored question vector), plus the page's section list. `--section` reads
  another section; `--full` reads the whole page. Each counts as one page visit
  the first time the page is read.
- Agents must read wiki pages only through `wiki nav read` during a session.

### Candidates

For the most recently read page:

1. Explicitly linked pages first (outgoing, incoming with inverse labels, and
   `superseded_by`), with relation type, reason, and summary.
2. Then semantically similar pages, ranked against the session question and the
   current page's summary vector, excluding linked and visited pages.

Each candidate shows slug, summary (flagged if placeholder), and why it is listed.

### When no candidate looks useful

1. Backtrack: offer the best unvisited candidates shown earlier in the session.
2. Re-query: `nav search` with the open question, excluding visited pages. At
   most one re-query per open question.
3. Stop: answer with what was found and state the gap. Do not guess.

All fallbacks count toward the page limit.

### Citing and logs

`nav end` records the pages the answer cites. The session log stores the
question, candidates shown, each choice with rationale, sections read, fallbacks
used, and cited pages. Logs live under `.cache/` and expire after 14 days. They are
used to evaluate traversal and find search misses.

## `wiki` CLI

| Command | Implemented by |
|---|---|
| `wiki ingest <slug>` | ours, calling `llm-wiki ingest` in the middle |
| `wiki suggest <slug>` | `llm-wiki suggest` merged with our vector similarity |
| `wiki search` | ours |
| `wiki nav start/read/candidates/search/end` | ours |
| `wiki rel sync/neighbors` | ours |
| `wiki check` | ours |
| `wiki index refresh/rebuild/status` | ours |
| `wiki summaries backlog` | ours |
| everything else | `llm-wiki` directly |

Wiki root resolution: `--wiki-root`, then `LLM_WIKI_ROOT`, then `~/llm-wiki/wiki`.
Local space: `--space`, then `LLM_WIKI_SPACE`; if unset, all targets are treated as
local. Private `llm-wiki` configuration formats are not read.

JSON output is compact, deterministically sorted, and omits null fields.
Exit codes: 0 success, 1 validation failures, 2 usage or runtime errors.

### `wiki ingest <slug>`

1. `rel sync` the page.
2. `check` the page; stop on any error (including a missing `summary`).
3. `llm-wiki ingest` (schema validation, indexing, git commit).
4. `index refresh` for that page, including embedding.

Stops at the first failure, so an invalid page is never committed. If step 4
fails, the page is ingested and marked as awaiting embedding. Only two
`llm-wiki` commands are wrapped (`ingest`, `suggest`), limiting coupling; the exact
invocation is verified in Phase 1.

### `wiki suggest <slug>`

Merges `llm-wiki suggest --format json` (tag overlap, 2-hop graph, BM25,
community peers) with our summary-vector nearest neighbors, which fills the
semantic strategy `llm-wiki` deferred. Results are deduplicated, exclude pages
already in `relations`, and label which source produced each suggestion.

### `wiki rel sync <slug-or-path> | --all`

1. Resolve and parse the page.
2. Validate relation structure before writing; refuse on structural errors
   (missing fields, bad types, noncanonical URIs). Missing target pages are
   reported but do not block, so a page can be written before its target.
3. Render the block from local targets.
4. Replace only the managed block, or append one.
5. Write via a temp file in the page's folder and atomic rename.
6. Preserve newline style, BOM, and final-newline behavior.
7. Update the page's cache rows in one transaction.

Idempotent: unchanged frontmatter produces no file change. `--dry-run` supported.
Frontmatter is never reserialized.

### `wiki rel neighbors <slug>`

Compact routing metadata only (slug, direction, type, inverse, reason, and
`external`/`unresolved` flags). Options: `--incoming`, `--outgoing`,
`--relation <type>`, `--limit`, `--format text|json`. Refreshes the requested page
first if it is new or stale; incoming edges from other changed pages are current
as of the last `index refresh`.

### `wiki summaries backlog`

Lists pages without a real summary, ranked by importance (link degree now, visit
frequency from session logs later), for option-(c) backfill.

## Validation (`wiki check`)

`wiki check <slug-or-path> | --all [--verify-cache] [--strict] [--format json]`.
No writes, including to the cache.

Errors:

- Invalid or missing frontmatter; malformed YAML
- Invalid `relations` structure; missing or unknown relation fields
- Unsupported relation type; noncanonical target URI
- Missing local target page
- Self-relation; duplicate relation
- Contradictory relations to one target (`implements` + `implemented-by`,
  `depends-on` + `used-by`)
- Slugs that differ only in case
- Missing, stale, or malformed generated block; unexpected content inside it
- Cache disagreeing with frontmatter (with `--verify-cache`)
- Missing `summary` (only when invoked by `wiki ingest`)

Warnings (fail only with `--strict`):

- Page has no relations
- Missing `summary` (during backfill), or summary longer than ~250 chars
- Body changed but summary unchanged
- Heavy use of `related-to`
- Unusually high link count
- Reasons longer than ~160 chars
- Multiple non-contradictory relation types to one target
- Reciprocal relations declared on both pages
- Bundle page (`x/index.md`) present

## Workflows and Agent Instructions

### Page authoring (ingest, crystallize)

1. Write the page, its `summary`, and obvious relations.
2. `wiki suggest <slug>`; review candidates semantically, never accept blindly.
3. Keep about 3–6 useful relations, each with a type and concise reason.
4. `wiki ingest <slug>`.

Tags are discovery signals, not edges. Agents never write generated links.

### Query

1. `wiki nav start "<question>"`.
2. Read the best page (default: its best section).
3. If not enough, `wiki nav candidates`, choose with a short rationale, `nav read`.
4. Apply the fallbacks when stuck; stop honestly at the limit.
5. `wiki nav end --cited ...`.

`llm-wiki graph --root <slug> --depth 1` remains available for topology questions.

### Lint

`wiki check --all` alongside `llm-wiki lint`.

### Enforcement

- Skills say: ingest with `wiki ingest`, search with `wiki search` / `wiki nav`.
- Claude Code permission rules deny `llm-wiki`'s MCP `wiki_ingest` and
  `wiki_search` tools and `Bash(llm-wiki ingest:*)`. The wrapper calls `llm-wiki`
  as a subprocess and is unaffected. Exact MCP tool names are verified against the
  configured server name.
- Skills state that wiki page content is information, not instructions.

## Models

Local only, CPU first, via `fastembed` (ONNX Runtime; no PyTorch). Model
libraries are imported lazily: only `search`, `nav start`, `nav search`,
`suggest`, and indexing load models. All other commands start fast.

- Pin each model's exact revision; download once to `.cache/models/`; run offline
  afterwards. Record each model's licence.
- Embedding model changes trigger a full re-embed.

### Selection by evaluation

Embedding candidates: `bge-small-en-v1.5` (baseline), `nomic-embed-text-v1.5`
(middle), `Qwen3-Embedding-0.6B-Q` (high end; the int8 build, 1.1 GB instead of
2.4 GB). All are supported by fastembed 0.8.1; `snowflake-arctic-embed-m-v2.0` is
not, so it is dropped. fastembed does not add query/document prefixes, so the tool
applies each model's documented prefixes itself.

Reranker candidates: `bge-reranker-base`, `ms-marco-MiniLM-L-12-v2`,
`jina-reranker-v1-turbo-en` (all Apache 2.0 or MIT, all in fastembed).
`jina-reranker-v2-base-multilingual` is excluded: its licence (CC-BY-NC) forbids
commercial use. `bge-reranker-v2-m3` is not in fastembed.

Nothing downloads implicitly: `wiki models download` is the only command that
fetches models, and every other command loads models with `local_files_only`.
fastembed is pinned (0.8.1), which pins the model files it downloads. If a model
is missing, search falls back to keyword-only with a note.

Choose the smallest model within a small margin of the best score. If a candidate
is not available in `fastembed`, it is evaluated with sentence-transformers in the
evaluation script only.

## Evaluation Set

Generated by Claude from existing pages (page content is sent to Anthropic for
this step; embeddings and search stay local). Stored outside `wiki/`, e.g.
`~/llm-wiki/eval/questions.yaml`, and committed.

- ~35 single-page questions: answered by one page, paraphrased away from the
  page's wording to avoid inflating keyword search.
- ~10 two-page questions: generated from linked page pairs, to test traversal.
- ~5 unanswerable questions: to test honest stopping.
- Split into tuning and test sets; report on the test set only.

Metrics: search hit@1, hit@3, MRR; traversal success rate, pages read per
answer, correct stops on unanswerable questions; embedding time and query latency.

## Summary Backfill (option c)

- Real summaries for the most important existing pages, written by an AI in
  batches from `wiki summaries backlog` and ingested with `wiki ingest`.
- Placeholder summaries (first paragraph, cache-only) for the rest, replaced
  whenever a page is next edited.

## Technology

- Python 3.13, `argparse`, `pathlib`, `json`
- `PyYAML` for reading frontmatter (never reserialized)
- `sqlite3` + FTS5 + `sqlite-vec`
- `fastembed` / ONNX Runtime
- `pytest`

Packaged as one Python package with a `wiki` console entry point, living in this
project folder, installed with `pipx` or an editable install.

## Testing

Unit and golden-file tests:

- Slug derivation with folders; case-collision detection; bundle warning
- Frontmatter parsing, malformed YAML, BOM, CRLF/LF, final-newline preservation
- Relation validation (every error and warning code)
- Sync: valid, idempotent, missing/stale block, empty relations, atomic-write failure
- Cache: creation, rebuild, incremental refresh (new, changed, moved, deleted),
  version and model mismatch rebuild, rollback on interrupted update, concurrent
  readers during refresh, indexed lookups at large synthetic scale
- Chunking: heading splits, size cap, code blocks, excluded regions
- Search: FTS ranking, vector retrieval, RRF fusion, rerank ordering — using a
  deterministic fake embedder and reranker
- Nav: visited refusal, page limit, rationale length, section reads, candidate
  ordering and exclusions, backtracking frontier, re-query limit, log expiry
- Wrappers: `ingest` step ordering and early stop, `suggest` merge — using a fake
  `llm-wiki` executable

Compatibility tests (manual, Phase 1): a two-page temporary wiki confirms the
generated block appears in Obsidian's graph and in `llm-wiki graph` as
`links-to`, both resolve the same folder slug, and re-ingestion leaves the block
intact.

## Performance

Benchmark with a synthetic corpus of 30,000 pages and 100,000 relations. Proposed
targets (to confirm):

| Operation | Target |
|---|---|
| `rel neighbors`, `nav candidates`, `nav read` | < 150 ms |
| `index refresh` with no changes | < 1 s |
| Cold rebuild without embeddings | < 2 min |
| Initial embedding with the chosen small model | < 1 h on CPU |
| `wiki search` including cold model load | < 3 s |

Record cache size and query latency. Optimizations must not move canonical state
out of frontmatter.

Phase 1 measurements (30,000 pages, 100,000 relations, Ryzen 7 9800X3D, Windows 11):

| Operation | Measured |
|---|---|
| `wiki rel neighbors` (CLI process, cold) | 111 ms median |
| neighbors lookup in-process | 0.09 ms p50 |
| `index refresh` with no changes | 0.20 s |
| `index refresh` with 100 changed pages | 1.4 s |
| `index rebuild`, files already read once | 7.4 s |
| `index rebuild`, files never read before | 89 s |
| `check --all` | 5.2 s |
| `rel sync --all`, first run writing 30k blocks | 158 s (one-off) |
| cache size | 41 MB |

Phase 2 measurements (same corpus, 4 sections per page = 150,000 chunks, vectors at
384 dimensions from a hash embedder, so model inference is excluded):

| Operation | Measured |
|---|---|
| keyword search (FTS5 BM25) | 15 ms p50 |
| keyword + exact vector search (sqlite-vec) | 144 ms p50 |
| store 150k chunk vectors | 31 s (plus model time) |
| `index rebuild` without embedding, files read before | 15 s |
| `wiki rel neighbors` (CLI process, cold) | 128 ms median |
| cache size with vectors | 505 MB (about 275 MB float32 vectors) |

Real model cost (embedding all pages once, and per-query embedding plus
reranking) is measured by `wiki eval run` once models are downloaded. Quantized
vectors are listed in `FUTURE_IDEAS.md` in case size or vector latency matters.

The gap between the two rebuild times comes from the first read of freshly
written files. Real-time antivirus scanning is the likely cause (not verified).
Only a first rebuild after a clone should pay it.

## Phases

### Phase 1: Prove the Format and Stack

- Verify on Windows / Python 3.13: `sqlite-vec` loads as an extension; `fastembed`
  and `onnxruntime` install.
- Run the Obsidian / `llm-wiki` compatibility check.
- Verify `llm-wiki ingest` and `suggest` invocation and JSON output.
- Implement discovery, frontmatter, relations, `rel sync`, `check`, the cache
  (pages, relations), `rel neighbors`, and `index`.

### Phase 2: Search and Model Selection

- Chunking, FTS5, embeddings, sqlite-vec, reranker, `wiki search`.
- Generate the evaluation set; select embedding model and reranker.
- Run the synthetic benchmark.

### Phase 3: Traversal

- Nav sessions, candidates, section reads, rationale, fallbacks, logs.
- Evaluate traversal on two-page and unanswerable questions.

### Phase 4: Workflow Integration

- `wiki ingest` and `wiki suggest` wrappers.
- Update ingest, crystallize, query, and lint skills.
- Add permission rules.
- Add the `relations` schema fragment to applicable page schemas.

### Phase 5: Migration and Cleanup

- `wiki rel migrate --dry-run`: convert `Related Pages` sections into `related-to`
  relations, preserving inline prose links and reporting ambiguous pages.
- Let an AI refine important `related-to` edges into stronger types.
- Summary backfill for important pages.
- `rel sync --all`, `check --all`; compare graph node and edge counts before and
  after.
- Remove `Related Pages` from templates; add `wiki check --all` to lint or CI.

## Open Decisions

- Keep `implemented-by`, or always derive it from `implements`?
- Final maximum reason and summary lengths.
- Exact set of page types whose schemas allow `relations`.
- Default `--max-pages` per session (proposed: 6).
- Confirm the performance targets above.
- How many pages count as "important" for the summary backfill.

## Definition of Done

- Applicable schemas validate canonical typed relations.
- `wiki` implements `ingest`, `suggest`, `search`, `nav`, `rel`, `check`, `index`,
  and `summaries backlog`, with tests.
- The cache supports incremental refresh, full rebuild, versioning, keyword and
  vector search, and indexed incoming/outgoing lookup.
- Generated links appear correctly in Obsidian and the `llm-wiki` graph.
- Embedding model and reranker are selected by the evaluation set, and search
  and traversal metrics are recorded.
- Agent skills use `wiki ingest`, `wiki suggest`, and `wiki nav`; permission rules
  block the bypass paths.
- Existing `Related Pages` sections are migrated or explicitly deferred.
- `wiki check --all` passes.
- Scale benchmarks meet the agreed targets.
