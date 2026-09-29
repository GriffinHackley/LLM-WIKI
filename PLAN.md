# Starter Kit Plan (v3)

Version 2 (search, relations and navigation, built for the Politics wiki and then made
wiki-agnostic) is complete and archived at
[docs/archive/upgrade-plan-v2.md](docs/archive/upgrade-plan-v2.md). Deferred ideas live in
[FUTURE_IDEAS.md](FUTURE_IDEAS.md).

## Purpose

Make the tool a drop-in way to start and run an LLM wiki. Today it is a read-side layer
for a wiki that already exists: everything that creates a wiki and keeps it growing lives
in the Politics repo, most of it specific to Politics. After this plan, one install and
one command give an empty folder (or an existing vault) a working LLM wiki that any
agent can ingest into, query, and audit, whether it is a research wiki built from
sources or a wiki describing a codebase.

## Principle: works with any agent

Nothing depends on one agent platform. The interface is the `wiki` command and plain
files that any agent with a shell can use: `AGENTS.md` (read by Codex, Cursor, Copilot and
others), templates, and `.wiki-cli.toml`. Workflow steps are served by the command
(`wiki guide <name>`), not stored in platform-specific skill files. Anything specific to
one platform (a Claude Code skill stub, a permission in `.claude/settings.json`) is a thin,
optional adapter over that core, never the only way to do something. Automation uses git
hooks, not agent hooks.

## Decisions

| Decision | Choice |
|---|---|
| Scope | Setup (`wiki new`) with presets, workflow guides (query, ingest, lint), the `code` preset, install, docs |
| Not in scope | Source intake (`wiki add`, `wiki extract`), agent-platform packaging: see FUTURE_IDEAS.md. `wiki pending` moved into Phase 9 |
| Instructions file | `AGENTS.md`. The Claude adapter adds a `CLAUDE.md` that imports it (`@AGENTS.md`) |
| Workflows | `wiki guide query \| ingest \| lint` prints the steps, versioned with the command, so they never go stale or disagree with it. Agents are told to run it; platform adapters are one-line stubs that do the same |
| Customising workflows | A wiki's own rules go in the "Your rules" section of `AGENTS.md`. To change the steps themselves, copy the guide's output into your own instructions and maintain it |
| Kinds of wiki | Presets: `research` (default) and `code`. A preset is a folder of starting files; everything it writes becomes the wiki's own editable files. `wiki new --preset <folder>` accepts a custom one |
| Page types | Defined once, in `.wiki-cli.toml` `[types.<name>]` (description, folder, template). Guides, `AGENTS.md` and `wiki check` read them from there |
| Where the kit lives | `src/wiki_cli/presets/<name>/` (templates, config, `AGENTS.md`, guide variants) and `src/wiki_cli/guides/`, shipped as package data. They replace `integrations/` |
| Setup never overwrites | `wiki new` creates only missing files and lists what it skipped, so it is safe on an existing repo |
| `index.md` | Dropped. The kit has no hand-maintained index; `wiki list` covers enumeration (see below) |
| Tool stays read-only | Agents write pages; the `wiki` command still never edits pages. `wiki new` writes only scaffold files |
| Politics | Unaffected until Phase 8, when converting it to the kit is the final test of the research phases. Until then its tests and evaluation must still pass |
| Second agent for testing | OpenCode (open source, reads `AGENTS.md`) with a local model through Ollama on the RX 7900 XT: free, unlimited, and a weaker model is a harder test of the guides |

### Why no `index.md`

The only real job an index does that the tool can't is enumeration ("what people are in
the wiki?"): search returns its top few pages, and nothing lists them all. Browsing is
covered by Obsidian's file tree and GitHub's folder view, and "is there already a page
for X?" by `wiki search` and `wiki suggest`. Against it: it is maintained by hand, it
drifts (Politics' `/lint` has a step just for drift), and it costs about 11k tokens to
read. `wiki list [--type T] [--format json]` prints slug, title, type and summary for
every page, from the cache, so it can never drift.

## Phase 1 (done): Presets, `research` preset, `wiki new`, `wiki guide`, `wiki list`

`wiki new [folder] [--preset research|code|<folder>]` turns an empty folder into a wiki,
or adds what is missing to an existing one. This phase builds the preset mechanism and
the `research` preset, the default, suited to news, politics, history and papers. It
writes:

```
raw/                  Sources. Never edited by the agent.
wiki/                 Pages, one folder per type, created when the first page needs it.
wiki/open-questions.md  Contradictions and gaps found while ingesting.
templates/            One template per page type.
AGENTS.md             Starter librarian instructions (below).
.wiki-cli.toml        Pages, summaries, page types and relation rules matching the templates.
.gitignore            .cache/
```

It runs `git init` when the folder is not in a repo, then prints the next steps
(`wiki models download` if the models are missing, then "ask your agent to ingest a
source").

`wiki new --agent claude` (off by default) also writes the Claude Code adapter: `CLAUDE.md` containing
`@AGENTS.md`, `Bash(wiki:*)` in `.claude/settings.json`, and skill stubs
`.claude/skills/wiki-{query,ingest,lint}/SKILL.md`, each saying "run `wiki guide <name>`
and follow it". Other platforms get adapters only when a real need shows up; `AGENTS.md`
alone is enough for most.

- **Presets.** A preset folder holds templates, a config with `[types]` and
  `[[relations]]`, the `AGENTS.md` text, and guide variants where its workflows differ
  from the generic guides. The config records `preset = "<name>"`, so `wiki guide`
  prints the right variant. Guides print the wiki's page types from the config, so one
  generic guide also serves custom types. `wiki check` flags a page whose type is not
  defined (when any types are). `[types.<name>] folder` replaces `[page_type.folders]`;
  the old key is still accepted.
- **Page types (`research`).** Seven, each defined in one sentence:

  | Type | What it is |
  |---|---|
  | source | One page per ingested source: what it is, what it says, what it names |
  | person | A person |
  | organization | A company, agency, group or institution |
  | place | A location: a city, building, property or region |
  | event | Something that happened at a time |
  | concept | A thing that exists in the sources: a term, technique, law or policy |
  | analysis | The wiki's own synthesis: a page answering a question by drawing on many others |

  `concept` vs `analysis` is the boundary to keep sharp: a concept is found in the
  sources, an analysis is written by the wiki. Trimming the set, or adding a type (a
  `person` page in a code wiki, say), is editing a template, its `[types]` entry and its
  rule.
- **Templates and relations are designed together.** Each template's link headings have
  a `[[relations]]` rule in the starter config, so typed relations work from the first
  page:

  | Type | Link sections -> relation |
  |---|---|
  | all | frontmatter `sources:` -> `draws-on` / `drawn-on-by` |
  | source | `## Entities mentioned` -> `mentions` / `mentioned-in` |
  | person, organization | `## Relationships` -> `associated-with` |
  | event | `## Participants` -> `involves` / `involved-in`; `## Location` -> `located-at` / `location-of` |
  | analysis | `## Key pages` -> `synthesizes` / `synthesized-in` |

  Every page has a `## Summary`. List items are written `[[page]] — why it matters`, so
  the reasons are useful to navigation.
- **Starter `AGENTS.md`.** The generic core of Politics' `CLAUDE.md`, without dossiers,
  claims or quotes: the agent is the librarian; `raw/` is immutable; merge new facts into
  existing prose instead of appending per source; cite the source page for every fact
  with an inline link at the end of the sentence or paragraph,
  `…signed in March ([[senate-report-2024]], p. 4)`, which the tool also turns into a
  relation whose reason is that sentence;
  put contradictions in `open-questions.md`; commit after each unit of work with a
  message that records it; use `wiki` instead of grepping; for each workflow, run
  `wiki guide <name>` and follow it. It ends with a "Your rules" section for the wiki's own
  conventions.
- **Existing `AGENTS.md`, `CLAUDE.md` or settings.** `wiki new` does not edit them: it
  prints the section or the permission to add.
- **`wiki guide <name>`** prints a workflow's steps. The query guide is the existing
  query skill's text, made platform-neutral.
- **`wiki list`** as above.

Done when: `wiki new` on an empty folder followed by `wiki index refresh` and
`wiki check --all` reports no errors, re-running it changes nothing, and running it in a
copy of Politics creates no file that Politics already has.

## Phase 2 (done): Ingest guide

`wiki guide ingest`: a generic ingest workflow, modelled on Politics' `/ingest` without
its domain rules.

1. With no source named, list the files in `raw/` and ask which to ingest.
2. Read the whole source (PDFs in page batches, long text in chunks).
3. Pick a stable slug and write `wiki/sources/<slug>.md` from the template, linking the
   original in `raw/`.
4. Entity pass: `wiki suggest <slug>` for leads, checked against the source, never added
   blindly. For each entity materially discussed, merge into its page or create one;
   add the source to `sources:`.
5. Contradictions and unsupported claims go in `wiki/open-questions.md`.
6. `wiki index refresh`, then `wiki check` on every page touched; fix errors.
7. Commit (if a git repo) with the slug and the pages created and updated.
8. Report what was ingested, pages created and updated, and questions raised.

The guide tells the agent to apply the "Your rules" section of `AGENTS.md` throughout.

Done when: an agent following the guide ingests three varied sources (a web page, a
PDF, notes) into a fresh wiki, yielding source and entity pages with typed relations, no
`wiki check` errors, and one commit per source. Tried with Claude Code and at least one
other agent (OpenCode with a local model).

Result: Claude, following only `wiki guide ingest`, ingested an NPR web page, a UN
resolution PDF and a notes file into a fresh wiki: 3 source pages and 12 entity pages,
typed relations with the citing sentences as reasons, no `wiki check` errors, one commit
per source. The trial added two things to the kit: `place` covers countries, and the guide
says what to do about `summary-stale` after merging facts into a page. **Pending:** the
same trial with OpenCode and a local model, which needs OpenCode and Ollama installed.

## Phase 3 (done): Lint guide

`wiki guide lint`:

1. `wiki index refresh`.
2. Mechanical, from the tool: `wiki check --all` (frontmatter, summaries, ambiguous
   links, stale summaries), `wiki unwritten` (link targets worth writing), `wiki orphans`.
3. Judgment, by reading: contradictions between pages about the same event or entity,
   factual paragraphs with no cited source, and `open-questions.md` items a later source
   has answered.
4. Fix what is mechanical, report what needs judgment, commit the fixes.

`wiki new` also offers an optional git pre-commit hook that runs `wiki check --all` and
rejects changes under `raw/`, as a backstop for edits made outside the agent (Obsidian,
by hand) and the generic replacement for agent-specific guardrails. The graph health checks
in FUTURE_IDEAS.md would slot into step 2 if built.

Done when: on a fresh wiki with planted problems (a broken link, an orphan, a missing
summary, a contradiction), an agent following the guide finds all four and fixes the
first three.

Result: on the Phase 2 trial wiki with a typo link, a contradicting date, an orphan and a
page with no summary, following the guide found all four and fixed the typo, the date
(the source settled it) and the summary, and reported the orphan with its uncited fact.
The trial added `wiki check <slug> --summary-ok` (a summary that still fits after an edit
no longer warns forever), a tip on where contradictions collect, a fix so that line
endings alone no longer make a summary stale, and a `.gitattributes` entry keeping the
hook's line endings LF. The hook refuses edits to `raw/` and pages with check errors.

## Phase 4 (done): `code` preset

A wiki describing a codebase. It differs from a research wiki in more than its types:
its source is the code, which keeps changing, so the main risk is pages going stale.

- **Location: a separate repo, one code repo per wiki.** The wiki is its own repo and
  points at the code's repo (several code repos per wiki: FUTURE_IDEAS.md).
  `wiki new --preset code` requires `--code <path>` and refuses without it. The path must
  be the top level of a git repo: pointing inside one names the top level to use instead.
- **Recording the pointer.** The committed config holds `[code] repo = "<path>"`, relative
  to the wiki when the two sit side by side, plus the code repo's `origin` URL so the
  tool can tell when a pointer names the wrong repo. `WIKI_CODE_REPO` overrides the path
  on a machine where the checkout lives elsewhere. Commands that need the code say
  clearly when the pointer is missing or wrong; the rest of the wiki still works.
- **Working from the code repo.** The agent that changes the code also keeps the wiki,
  so its session starts in the code repo, not the wiki. For that:
  - **Finding the wiki.** Run from the code repo, `wiki` would find the code repo and
    treat its Markdown as a wiki. A redirect fixes this: a `.wiki-cli.toml` in the code
    repo holding only `wiki = "<path to the wiki>"` sends every command to the wiki.
  - **Telling the agent.** A short section for the code repo's `AGENTS.md`: this code has
    a wiki; answer questions about the code with the query guide; after changing code,
    follow the sync guide.
  - **Permissions.** Agent settings belong to the repo the session starts in, so the
    Claude adapter's `Bash(wiki:*)` permission and the wiki's path in
    `additionalDirectories` go in the code repo's `.claude/settings.json`.

  `wiki new --preset code` writes none of this into the code repo: it prints the
  redirect, the `AGENTS.md` section and, with `--agent claude`, the settings, for you to
  add. The wiki repo still gets its own `AGENTS.md`, for sessions started there.
- **Two repos, two commits.** Code changes are committed first, then the pages updated,
  with `verified:` set to the new code commit, then the wiki committed; the sync guide
  says so. `wiki stale` also lists pages whose covered files have uncommitted changes, so
  staleness shows before the code is committed.
- **Page types:**

  | Type | What it is |
  |---|---|
  | module | One area of the code: what it does, where it lives, how it fits |
  | concept | A domain idea or pattern the code relies on |
  | decision | Why something is the way it is (like an ADR) |
  | guide | How to run, test, deploy or extend |
  | analysis | The wiki's own synthesis, answering a question across pages |

- **Relations:** module `## Depends on` -> `depends-on` / `used-by`; decision
  `## Affects` -> `decided-for` / `decided-in`; analysis `## Key pages` ->
  `synthesizes` / `synthesized-in`.
- **Links to code files.** Pages refer to code as `[pages.py](code:src/wiki_cli/pages.py)`,
  a path in the code repo. They are for the agent and the tool; viewers such as Obsidian
  need not open them. Today such a link is ignored, like any link with a scheme. The
  tool resolves `code:` links against the code repo: they are file references, shown by
  `neighbors`, never reported by `wiki unwritten`; a reference to a file that no longer
  exists is a `wiki check` warning.
- **`wiki stale`.** Pages list the files they describe (`covers:` globs) and the commit
  they were last checked against (`verified: <sha>`, a commit in the code repo).
  `wiki stale` lists pages whose covered files changed between that commit and the code
  repo's current `HEAD`, with the changed files, by running git in the code repo.
  `wiki check` warns on `covers:` globs that match nothing.
- **Guides.** `ingest` becomes "document this module, pull request or decision": read the
  code, write or update pages, set `covers:` and `verified:`. A new `wiki guide sync`
  updates the pages `wiki stale` lists after code changes. `lint` adds `wiki stale`.

Done when: a new wiki repo made with `wiki new --preset code --code <this repo>`, plus an
agent following the guides in a session started in this repo (using the printed
redirect and `AGENTS.md` section), documents three modules and one decision of this repo;
changing a covered file in this repo makes its page show in `wiki stale`; following
`wiki guide sync` brings it back to clean; and `wiki new --preset code` without `--code`,
or with a path inside a repo, refuses with a clear message.

Result: on a clone of this repo, `wiki new --preset code --code <clone>` made a wiki beside
it and printed the redirect, the `AGENTS.md` section and the Claude settings for the
clone. Working from the clone, following the guides documented Search, Navigation
sessions, Scaffold and the derived-relations decision, with `code:` links and typed
relations. Changing `search.py` showed the Search page in `wiki stale` as uncommitted,
then as changed after the commit; following `wiki guide sync` updated the page and
`verified:`, and `wiki stale` came back clean. The refusals (no `--code`, a folder inside
the repo, a wiki inside the code repo) all work. Also added: a `code` variant of the
query guide (read the code when the wiki cannot answer), preset-aware next steps, and
`not-verified` / `covers-nothing` / `missing-code-file` / `code-repo` checks.

## Phase 5 (done, publishing pending): Install

Today install needs a clone plus `uv tool install --editable`.

1. **Install from git without a clone:**
   `uv tool install git+https://github.com/GriffinHackley/LLM-WIKI`. Requires the repo
   to be public, or the user to have access.
2. **Publish to PyPI.** `wiki-cli` and `wikicli` are taken; `llm-wiki-cli` is free. The
   package name changes, the command stays `wiki`. Build and publish from a GitHub
   Actions workflow on tag. Needs a license first (FUTURE_IDEAS.md).
3. **Starter files and guides as package data**, checked by installing the built wheel
   in a clean environment and running `wiki new` and `wiki guide`.
4. **Model download.** It stays explicit, but `wiki new` and the first `wiki search`
   without models name the command and the size.

Done when: on a machine without the repo, installing the package, then
`wiki models download`, then `wiki new` works end to end.

Result: the package is `llm-wiki-cli` 0.2.0 (command still `wiki`). A wheel built on
Windows carries every guide, preset and the hook, all with LF line endings
(`.gitattributes`), and `wiki new` and `wiki guide` run from it. `uv tool install
git+<repo URL>` installs a working `wiki` without a clone (tried from a local git URL into
an isolated tool folder). A missing model now names the command and the size. Added
GitHub Actions: tests on Windows, macOS and Linux on every push, and publishing to PyPI
on a `v*` tag. **Pending, for the owner:** push the repo (and make it public, or share
access, for git installs); add a license (FUTURE_IDEAS.md); set up trusted publishing
for `llm-wiki-cli` on PyPI; tag `v0.2.0`. Existing installs of `wiki-cli` need
`uv tool uninstall wiki-cli` before installing `llm-wiki-cli`, as both provide `wiki`.

## Phase 6 (done): Docs

- **README:** reframe from "read-side tooling" to a starter kit for LLM wikis that works
  with any agent; command table gains `new`, `guide` and `list`; point to this plan
  instead of the archived one. Say what each preset is for.
- **Getting started:** two paths, "start a new wiki" (`wiki new`, first ingest, first
  query) and "add to an existing wiki" (today's guide, updated for `wiki new`'s
  missing-files mode). A short "connect your agent" section: `AGENTS.md` for any agent,
  `--agent claude` for Claude Code. Install from PyPI instead of cloning.
- **New `docs/workflows.md`:** the guides (including `sync` for code wikis), how to add
  a wiki's own rules, and how to customise types, templates and relation rules together.
- **New `docs/presets.md`:** the two presets, and how to make a custom one.
- **`pyproject.toml` description** and `__init__` docstring: no longer "read-side".
- Remove `integrations/` and every reference to it.

Done when: someone following only the docs on a fresh machine gets from nothing to a
first answered query.

## Phase 7 (done, second agent pending): Dry run

Start a `research` wiki on a topic unrelated to Politics: ingest about ten sources
following the guides, answer five questions with the query guide, run the lint guide.
Start a `code` wiki for a real repo other than this one, working from the code repo:
document it, change some code, sync. Use Claude Code for most of it and OpenCode with a
local model for part. Fix the rough edges found, as for the Phase 7 dry run of v2.
Re-run the test suite and the Politics evaluation to confirm nothing regressed.

Result:
- **Research wiki** on an unrelated topic: ten sources (the package metadata of the
  libraries in this repo's environment), ingested one by one following the guide: 38
  pages (10 sources, 13 concepts, 5 organizations, 8 people, an analysis, open
  questions), one commit per source, no check errors. Five questions through the query
  guide: four answered in one page read each, the fifth (how sqlite-vec works) correctly
  reported as a gap the sources do not cover. One answer filed as an `analysis` page.
  The lint pass found the analysis orphaned and an uncited line in it; fixed, and caught
  a link broken by the fix. Ends at 0 errors, 0 warnings.
- **Code wiki** for a copy of the D&D character sheet app (its `app/` folder, as a git
  repo), worked from the code repo: four modules, a concept and a decision documented
  with `code:` links; a change to `compute.ts` showed in `wiki stale` and the sync guide
  brought it back to clean; the coverage step names the UI folders no page covers yet.
- **Regressions:** 228 tests pass; the Politics evaluation is identical before and after
  the kit (hit@1 0.511, hit@3 0.778, MRR 0.639 on all 50 questions).
- **Fixed from the dry run:** `concept` covers products and tools (a software library had
  no type); summaries keep underscores inside words (`huggingface_hub` showed as
  "huggingfacehub"), recomputed on the next refresh; the query guide says citing a page
  known only from its summary is fine.
- **Pending:** the part run with OpenCode and a local model (needs OpenCode and Ollama
  installed).

## Phase 8 (done, pull request open): Convert Politics

The final test of the research phases, started only once Phases 1 to 7 are done and the
kit is in good shape. Politics becomes a wiki built on the kit, with its own rules on
top, instead of a parallel set of skills:

- Its page types move into `[types]`; its `CLAUDE.md` becomes `AGENTS.md` (plus the
  Claude adapter's `CLAUDE.md` importing it), with the dossier, claims, quote and
  editorial rules in "Your rules".
- `/query`, `/ingest` and `/lint` become the generic guides plus those rules. Where a
  Politics step cannot be expressed as a rule on top of a generic guide (the claims
  ledger pass in ingest, the presentation checks in lint), note why: each is a gap in
  the kit's customisation model to fix, or a reason for Politics to keep its own guide.
- `/claims` and `/new-dossier` stay Politics' own workflows.
- `wiki/index.md` is removed; anything that read it uses `wiki list`.
- Its Claude-only guardrail hooks are kept or replaced by the pre-commit hook, case by
  case.

Done on a branch, delivered as a pull request, as in v2 Phase 6. Done when: search
quality on its 50 evaluation questions is within noise of today, an ingest and a query
following the converted workflows match the quality of the current skills, and the
notes on customisation gaps are resolved or recorded in FUTURE_IDEAS.md.

Result, on the Politics branch `wiki-starter-kit`:
- `AGENTS.md` holds what `CLAUDE.md` held: workflows point at the guides, and everything
  specific to Politics (editorial rules, source tiers, page conventions, and the dossier,
  claims, quote and presentation steps) sits under "Your rules", headings unchanged, with
  a "Workflow additions" subsection for ingest, query and lint. `CLAUDE.md` imports it.
  `/ingest`, `/query` and `/lint` are stubs that run their guides; `/claims` and
  `/new-dossier` stay Politics' own. Types are in `[types]`; `index.md` is gone.
- Search quality is identical (hit@1 0.511, hit@3 0.778, MRR 0.639).
- **Ingest:** a document page removed in a scratch copy and re-ingested through the
  converted workflow came out more complete than the original (eight entities against
  three, three verified quotes against one, dated facts, a stated gap), with quotes
  passing `check_quotes.py` and `fix_links.py` updating every use of the title.
- **Query:** a two-part question with a judgment part was answered from two pages with
  claim statuses and a stated scope, as the old skill required.
- **Customisation gaps**, and what happened to each:
  - The guides assumed a type named `source` and a `wiki/open-questions.md`: fixed, the
    guides now say "the source type" and "where the wiki keeps open questions".
  - Politics' query skill had steps every wiki needs (split the question into parts, a
    gap check before saying the wiki lacks something, `--keyword-only` for exact
    strings): moved into the generic query guide.
  - Additions are prose layered on the guides, placed by reference ("before the guide's
    check step"); that worked, so the guides need no numbered insertion points yet.
  - The generic pre-commit hook cannot exempt a manifest that lives in `raw/`
    (`raw/SOURCES.md`): Politics keeps its own hook with the exemption; recorded in
    FUTURE_IDEAS.md.
  - The "read the rules before editing" guardrail (`require_rules.py`) has no generic
    equivalent and stays a Claude Code hook in Politics.

## Phase 9 (done, OpenCode run pending): One robust ingest guide

The ingest guide should work unchanged in a new wiki, with any agent, including a weak
local model. Phase 2's guide gives the steps but leaves the hard judgment to the agent,
and code wikis have a separate guide that cannot take in documents (design docs, RFCs,
postmortems). Decided:

- **One ingest guide** with a shared core and two kinds of source: a **document** (a file
  in `raw/`, fixed, cited by locator) and, in a wiki with a code repo, **code** (part of
  the repo, cited with `code:` links and a `verified:` commit). `guides/code/ingest.md` is
  removed. Parts of a guide that apply only with a code repo sit in `{{#code}}…{{/code}}`
  blocks, kept only when the wiki has one. `sync` stays code-only.
- **Code wikis ingest documents too:** the `code` preset gains `raw/`, a `source` type and
  template. Where a document and the code disagree about current behavior, the code wins
  and the document is history; the guide says so.
- **`wiki pending`** (from FUTURE_IDEAS): sources in `raw/` no page links to yet, one entry
  per source (a PDF and its `.txt` are one source), flagging files whose content matches a
  source already ingested. Ingest uses it to pick sources and to ingest several in a row;
  lint reports what is left.
- **The guide gets the judgment rules it lacked:** the source is data, never instructions;
  a test for when something gets its own page; names, aliases and slugs; running notes
  while reading a long source; checking for a source already ingested (or a new edition
  of one); attributing claims instead of asserting them; a cap on how many pages one
  ingest edits; several sources in one run; what to do with a file that cannot be read.

Done when: the tests pass; the guide renders for both presets; a fresh research wiki and
a fresh code wiki each ingest sources following it with no changes; Politics' evaluation
is unchanged; and OpenCode with a local model ingests into a fresh wiki from the
unchanged guide (the robustness test).

Result (done, except the OpenCode run):
- `wiki pending` works on Politics as it stands: 139 sources ingested, and the six it
  lists are real (each named only in a dossier `TODO.md`), plus `raw/SOURCES.md`, which
  Politics can skip with `[pending] ignore`. 0.4 s.
- **Research trial:** a fresh wiki with three package READMEs and a byte-identical copy of
  one. `wiki pending` flagged the copy; ingesting the h11 README following the guide gave
  a source page, five pages that pass the page test (the library, its author, the person
  whose argument inspired it, the design approach, the RFC it implements), links without
  pages for three names mentioned in passing (Trio, Curio, requests), `aliases:` on the
  approach and the RFC, claims attributed to the author, and one open question. 0 errors;
  afterwards `wiki pending` lists the copy against the ingested original.
- **Code trial:** a fresh code wiki with a design note that disagrees with the code (five
  retries with backoff against three attempts without). The module page describes the
  code and gives the note as history; the question is filed; the note's page and the
  module page relate as `discusses` / `draws-on`. `check`, `pending` and `stale` are
  clean.
- 246 tests pass; the Politics evaluation is identical (hit@1 0.511, hit@3 0.778, MRR
  0.639 on all 50 questions).
- **First OpenCode run** (qwen3:14b through Ollama): it ran `wiki guide ingest` five
  times, taking it for the command that ingests, then tried `wiki ingest`; and OpenCode's
  shell on Windows (PowerShell 5.1) garbled the guides' non-ASCII characters. Every guide
  now opens by saying the agent does the steps itself, `wiki ingest` and friends explain
  the guide, and the guides are ASCII.
- **Slash commands for Claude Code, Copilot and OpenCode** (`wiki new --agent`, now
  repeatable): each `/wiki-<workflow>` embeds `` !`wiki guide <workflow>` ``, so the guide
  arrives as the prompt with no copy to go stale, plus a line telling agents that don't run
  embedded commands to run it, and the user's arguments. Checked: Claude Code (haiku, `-p`)
  and OpenCode (qwen3:14b, `opencode run --command`) both received `/wiki-lint` with the
  guide inlined and the arguments in place. Copilot reads the same `SKILL.md` format, from
  `.github/skills/` or `.claude/skills/`; not yet checked in VS Code.
- **Pending:** the OpenCode ingest rerun, and a check of the skills in Copilot.

## Open decisions

- None yet.
