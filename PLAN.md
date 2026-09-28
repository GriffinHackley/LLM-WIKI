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
| Not in scope | Source intake (`wiki add`, `wiki pending`), agent-platform packaging: see FUTURE_IDEAS.md |
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

## Phase 1: Presets, `research` preset, `wiki new`, `wiki guide`, `wiki list`

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

## Phase 2: Ingest guide

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

## Phase 3: Lint guide

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

## Phase 4: `code` preset

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

## Phase 5: Install

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

## Phase 6: Docs

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

## Phase 7: Dry run

Start a `research` wiki on a topic unrelated to Politics: ingest about ten sources
following the guides, answer five questions with the query guide, run the lint guide.
Start a `code` wiki for a real repo other than this one, working from the code repo:
document it, change some code, sync. Use Claude Code for most of it and OpenCode with a
local model for part. Fix the rough edges found, as for the Phase 7 dry run of v2.
Re-run the test suite and the Politics evaluation to confirm nothing regressed.

## Phase 8: Convert Politics

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

## Open decisions

- None yet.
