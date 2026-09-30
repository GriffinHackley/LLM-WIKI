# Future Ideas

Possible improvements deferred from the plans ([PLAN.md](PLAN.md)). Nothing here is
committed scope; promote an item into the plan only after deciding to build it.

## Token budget for traversal sessions

Pages vary widely in size, so a page-count limit alone does not bound cost: a
6-page limit could mean 3k tokens or 60k. Enforce a character or token budget per
`wiki-nav` session alongside the page limit. `wiki-nav read` would refuse (or
offer a truncated or section-only read) once the remaining budget cannot cover the
requested page, and `candidates` could show each page's approximate size so the
agent can weigh cost when choosing.

## Source intake

Deferred from the starter kit plan: `/wiki-ingest` assumes a readable file is already in
`raw/`.

- **`wiki add <url | file>`:** save a web page as HTML plus extracted text, and a PDF or
  DOCX as extracted text beside the original, so the raw search tier covers it. Politics'
  `.claude/fetch_html.py` is a rough version for web pages.
- **Provenance:** URL, retrieval date and original file name, in the text file's header
  or a `raw/SOURCES.md`.
- **`wiki extract <file>`:** write `raw/<name>.txt` beside a PDF or HTML original, and
  have the ingest guide run it when no `.txt` exists. Matters for the "any agent"
  principle: Claude reads PDFs, several other agents do not. The text is also what the
  raw search tier indexes.

## Agent-platform adapters

Deferred from the starter kit plan, and optional by design: the kit must work with any
agent through the `wiki` command and `AGENTS.md`, so these only add convenience on one
platform and never become the only way to do something.

- A **"file this answer"** workflow (upstream `llm-wiki` calls it `crystallize`) that
  saves a good synthesis from a query as an `analysis` page. This one is generic: a
  `wiki guide file-answer`, not a platform feature.
- **Guardrails** that block edits under `raw/` and run `wiki index refresh` after page
  edits. The generic form is a git pre-commit hook that rejects changes under `raw/`;
  agent hooks (Claude Code's `PreToolUse`, for example) would be per-platform extras.
- **More adapters** beside Claude Code, Copilot and OpenCode (`wiki new --agent`), such as
  Cursor or Gemini CLI, as they are wanted.
- **Packaging** the commands as a Claude Code plugin or similar, if copying them per wiki
  becomes a chore.
- **An MCP server** exposing the commands as tools, for agents without a shell.

## License

TODO before publishing to PyPI or anyone else installs it. Dependencies are all
permissive (fastembed and tokenizers Apache-2.0, sqlite-vec MIT/Apache, onnxruntime MIT,
PyYAML MIT, numpy BSD); the models are downloaded, not shipped. Leaning MIT for the tool,
with 0BSD or CC0 for `src/wiki_cli/starter/` so files `wiki new` copies into a wiki carry
no notice requirement. Needs the copyright holder's name.

## Editable files in raw/

The pre-commit hook (`wiki new --git-hook`) refuses any edit, rename or deletion under
`raw/`. Politics keeps a manifest there (`raw/SOURCES.md`) that it updates as sources
arrive, so it keeps its own hook with an exemption. If other wikis do the same, let the
config name files in `raw/` that may change (`[raw] editable = ["raw/SOURCES.md"]`) and
have the hook read it.

## Several code repos per wiki

The `code` preset handles one code repo per wiki. Microservices, or a frontend and
backend in separate repos, would want one wiki across several: the config would hold
`[code.repos.<name>]` pointers and links would read `code:<name>/path`. Moving to that
from `code:path` means rewriting every code link, so decide before code wikis pile up.

## Graph health checks

`orphans` and `unwritten` catch missing links, not weak structure. Borrowed from the
geronimo-iia `llm-wiki` engine's lint rules, a `wiki check --graph` (or an `orphans`
extension) for `/lint` could report, over the relations graph:

- **Articulation points:** pages whose removal splits the graph; add paths around them.
- **Bridges:** single relations whose removal splits the graph; add a parallel path.
- **Isolated clusters:** communities (Louvain) of two pages or fewer, as a review
  queue for `wiki suggest`.
- **Top hubs** and cluster counts as a short summary line.

`wiki suggest` could add a "same cluster, no link path" candidate type, which
upstream finds the most valuable link suggestions. Community detection is noise on
small graphs; upstream skips it below 30 pages.

**Clusters with no page about them.** A cluster of pages that link each other densely
usually shares an idea: the files and PRs around a caching layer, the people around a
negotiation. When no page in the cluster is about that idea (no `concept`, `module` or
similar page that most of the cluster links to), report it as a page worth writing:
list the cluster's pages, the names and phrases they share, and the typed relations
between them, so the agent can name the idea and write its page (`wiki guide ingest`),
then link the cluster to it. Weight relations by type when clustering: `part-of` and
`implements` bind pages more than a passing `links-to`. Where the wiki declares hub
types (`[types]` could mark `concept` and `module` as such), a cluster already linked
to one of those pages is covered. Pairs well with `wiki unwritten`, which finds pages
that are linked but not written; this finds ones nobody has linked yet.

## Relation-aware ranking in traversal

`wiki nav candidates` ranks a page's linked neighbors by similarity to the question
alone: an `implements` link, a `has-gotcha` link and a passing `links-to` mention all
compete on text. The relation types the config declares say more than that, and could
feed the ranking:

- **Weights per relation type** in the config, for example `weight = 1.5` on a
  `[[relations]]` rule, multiplying the question similarity. Typed links would beat
  plain `links-to` mentions, and a wiki could favor `has-gotcha` or `decided-in`.
- **Weights per question kind:** "why" questions favor `decided-in`, "how do I" favors
  `instruction` pages, "what broke" favors `has-gotcha` and `implemented-by`. This could
  come from the question's wording or from the agent passing a hint to `nav start`.
- **Path shape:** favor paths that follow typed chains (file -> module -> decision) over
  hops through hubs such as `open-questions`, and damp pages many links point to.
- **The link's reason text:** the reason on a typed link ("stores its index in
  SQLite") is a better match target than the whole target page.

Test any of these with `wiki eval` against plain similarity first: on small wikis the
relation signal may be too sparse to help, and hand-set weights are easy to overfit.

## Other items deferred during planning

- **Persistent model server:** a local process that keeps embedding and reranker
  models loaded, if per-call model load time at `search` proves too slow. It would
  take `search` from about 1.0 s to about 0.3 s: about 0.45 s goes to importing
  onnxruntime and loading both models, 0.2 s to reranking.
- **LanceDB migration:** if the corpus grows well past about 1M chunks and
  sqlite-vec's exact search becomes slow.
- **Quantized vectors:** store int8 or binary vectors in sqlite-vec to cut the
  ~230 MB of float32 chunk vectors at 30k pages and speed up the ~150 ms exact
  vector scan, rescoring the top candidates with full-precision vectors.
- **Embed plain text:** send link-stripped chunk text (`[[a|B]]` -> `B`) to the embedding
  model and reranker instead of raw Markdown. Linking claim IDs in the Politics wiki moved
  one evaluation question from rank 3 to 4, which suggests the markup is noise.
  *Tested for the reranker (2026-09-26), no effect:* on the 50 Politics questions, plain
  text left hit@3 at 0.778 and moved MRR from 0.639 to 0.650; four questions changed rank,
  two up and two down. Replacing bare `[[slug]]` links with page titles did no better.
  Link markup is 7% of chunk characters and the reranker ignores it. Not worth
  re-embedding for; revisit only if a wiki's pages are much denser in links.
- **GPU embedding:** Ollama or ONNX Runtime with DirectML on the AMD RX 7900 XT,
  if CPU embedding time becomes a bottleneck.
- **Rust implementation:** a single fast binary, if the tool needs distributing
  or much higher query volume.
