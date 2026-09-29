# Getting started

This guide gets an LLM-maintained wiki running with your agent, on one of three paths:

- **[A new research wiki](#3-start-a-new-research-wiki):** sources go in, the agent writes
  and links the pages. News, politics, history, papers, any topic you research.
- **[A wiki about a codebase](#4-start-a-wiki-about-a-codebase):** a wiki in its own repo,
  kept current by the agent that changes the code.
- **[An existing folder of notes](#5-add-to-an-existing-wiki):** an Obsidian vault, a
  `docs/` folder, a wiki you already keep. Your pages are left alone.

The `wiki` command never edits pages: the agent does, following workflow guides the
command prints. Everything the command builds for search lives in a disposable cache
inside the wiki, so trying it is safe.

**The short version:**

```bash
uv tool install git+https://github.com/GriffinHackley/LLM-WIKI   # once per machine
wiki models download                                               # once per machine, about 0.2 GB
wiki new my-wiki --agent claude                                    # --agent is optional
cd my-wiki
# put a source in raw/, then ask your agent: "ingest raw/<file>"
```

## 1. Requirements

- [uv](https://docs.astral.sh/uv/getting-started/installation/). It installs Python 3.13
  for the tool if you don't have it.
- Git, and access to the LLM-WIKI repository (until the package is on PyPI).
- An agent that can run shell commands: Claude Code, Codex, Cursor, Copilot, OpenCode or
  similar. It reads the wiki's `AGENTS.md`; nothing depends on one platform.
- About 0.2 GB of disk for the two models, plus a cache in each wiki of roughly 40 KB per
  page.
- No GPU. Search runs locally on the CPU; nothing is sent to a server.

The default models are English-only. The tool is developed on Windows 11; its tests run
on Windows, macOS and Linux.

## 2. Install the tool (once per machine)

```bash
uv tool install git+https://github.com/GriffinHackley/LLM-WIKI
wiki --version
```

Once the package is on PyPI, `uv tool install llm-wiki-cli` does the same. `uv tool
install` puts `wiki` on your PATH; if the shell can't find it, run `uv tool update-shell`
and open a new terminal. To update later, run `uv tool upgrade llm-wiki-cli`. If you
installed an earlier version named `wiki-cli`, remove it first:
`uv tool uninstall wiki-cli`.

To work on the tool itself, clone the repository and run `uv tool install --editable .`
in it: the command then follows your checkout.

Then download the models:

```bash
wiki models download
```

This fetches the embedding model (`BAAI/bge-small-en-v1.5`) and the reranker
(`jinaai/jina-reranker-v1-turbo-en`) into `~/.cache/wiki-cli/models`, shared by every
wiki on the machine. It is the only command that ever downloads anything; the others load
models from that folder or, if they are missing, fall back to keyword search and say so.

## 3. Start a new research wiki

```bash
wiki new my-wiki
```

```
research wiki in ...\my-wiki
created: AGENTS.md, .gitignore, .wiki-cli.toml, raw/.gitkeep, templates/analysis.md, ...
initialized a git repository
```

It creates:

| | |
|---|---|
| `raw/` | Your sources: PDFs, saved web pages, notes. The agent never edits them. |
| `wiki/` | The pages, one folder per type, created as pages are written. |
| `wiki/open-questions.md` | Contradictions and gaps the agent finds while ingesting. |
| `templates/` | A template per page type: source, person, organization, place, event, concept, analysis. |
| `AGENTS.md` | The agent's instructions: it is the librarian; merge, don't append; cite every fact. |
| `.wiki-cli.toml` | Page types and relation rules that match the templates, so typed relations work from the first page. |

Options: `--agent claude|copilot|opencode` adds slash commands for that agent ([section 7](#7-connect-your-agent));
`--git-hook` adds a pre-commit hook that refuses edits to `raw/` and pages that fail
`wiki check`. Running `wiki new` again never overwrites anything; it adds what is
missing.

**Ingest a source.** Put a file in `raw/` and ask your agent to ingest it, or to "ingest
everything new". `AGENTS.md` tells it to run `wiki guide ingest` and follow it: read the
whole source, write its source page, create or update the pages for the people, places,
events and ideas it discusses, cite every fact, file contradictions, check, and commit.
One commit per source. `wiki pending` lists the sources not ingested yet.

**Ask questions.** Ask your agent; it follows `wiki guide query`: search, read one section
at a time, follow relations by their reasons, stay within a page limit, and cite the
pages used.

**Audit now and then.** Ask your agent to lint the wiki (`wiki guide lint`): broken and
unwritten links, orphans, missing summaries, contradictions between pages, uncited facts.

Put your own conventions (editorial rules, citation details, naming) in the "Your rules"
section of `AGENTS.md`; every workflow applies them. [workflows.md](workflows.md) has
more.

## 4. Start a wiki about a codebase

The wiki lives in its own repository beside the code, and needs to know where the code
is:

```bash
wiki new my-app-wiki --preset code --code ../my-app
```

`--code` must be the top folder of the code's git repository, and the wiki must be
outside it. `wiki new` records the path (relative to the wiki) and the repo's origin in
the wiki's `.wiki-cli.toml`, and prints what the code repo needs so that the agent
working on the code also keeps the wiki. It never writes to the code repo itself; add
these yourself:

- `.wiki-cli.toml` in the code repo, holding only `wiki = "../my-app-wiki"`: every `wiki`
  command run in the code repo then works on the wiki.
- A section for the code repo's `AGENTS.md`: answer questions about the code with the
  query guide; after changing code, commit it and follow `wiki guide sync`.
- With `--agent claude`, the code repo's `.claude/settings.json` entries: the `wiki`
  permission, and the wiki folder in `additionalDirectories` so Claude can edit it.

Then work in the code repo as usual. To document a module or a decision, ask the agent
(it follows `wiki guide ingest`): pages link to code with `[name](code:src/app.py)`, list
the files they describe in `covers:`, and record the code commit they were checked
against in `verified:`. After code changes, `wiki stale` lists the pages whose covered
files changed, and `wiki guide sync` brings them back in line. The code and the wiki are
committed separately: code first, then the wiki. On a machine where the code is checked
out somewhere else, set `WIKI_CODE_REPO` to its path.

## 5. Add to an existing wiki

For a folder of notes that already exists. `wiki new . --agent claude` (or without
`--agent`) adds the agent files; when the folder already has its own `.wiki-cli.toml`,
it adds no templates or pages, only what connects agents to the wiki. The rest of this
section tunes search and relations for your pages.

### Run it from inside the wiki

`wiki` finds the wiki it is working on the same way git finds a repo: it walks up from the
current folder to the nearest `.wiki-cli.toml`. Without one it uses the enclosing git
repository, else the current folder, and prints a note saying which folder it chose. It
refuses to treat your home folder or a drive root as a wiki. `--root <folder>` overrides
all of this.

It works with no config at all: every `*.md` file becomes a page, `[[wikilinks]]` and
`[text](path.md)` links are followed, and `raw/**/*.txt` (if you have a `raw/` folder) is
searchable source text. You can search right away:

```bash
cd my-wiki
wiki search "how are jobs prioritised so none wait forever?"
```

```
note: no .wiki-cli.toml found; using the git repository ...\my-wiki as the wiki root
note: vector search skipped: no embeddings yet; run 'wiki index refresh'
1. scheduler  (-0.372)
   Decides the order card stacks run in, using a priority queue with aging so low-priority jobs are never starved.
```

### Draft a config with `wiki init`

A config makes results better by telling the tool which files are pages, where summaries
live, and what your headings mean. `wiki init` surveys the repo and drafts one:

```bash
wiki init            # print the draft
wiki init --write    # save it as .wiki-cli.toml (never overwrites an existing file)
```

Open `.wiki-cli.toml` and review four things:

1. **Which files are pages.** If most pages live in one folder, the draft proposes only
   that folder and lists what it leaves out:

   ```toml
   # Most pages are under notes/. Indexing only that folder leaves out: README.md.
   # Delete this line to index every *.md file instead.
   pages = ["notes/**/*.md"]
   ```

   Keep it if the left-out files aren't wiki content (a README, contributor docs).
   Add `exclude` globs for anything else that isn't a page. When there is a `templates/`
   folder, the draft already excludes it.

2. **Summaries.** Search results and navigation show each page's summary, taken from the
   first of: a frontmatter field (`summary`, `description`), a heading (`## Summary`), or
   the page's first paragraph. Adjust `[summary]` if your pages use other names, such as
   `headings = ["Overview", "TL;DR"]`.

3. **Page types.** If pages have a frontmatter field like `type: person`, the draft finds
   it. If your types are folders instead, map them:

   ```toml
   [page_type.folders]
   "notes/people" = "person"
   ```

4. **Relation rules.** This is the step worth a few minutes. Every link is already a
   relation (`links-to`) whose reason is the sentence around it. A rule gives the links
   under a heading, or in a frontmatter field, a name. The draft lists headings that hold
   links on at least three pages, commented out:

   ```toml
   # Heading 'Works with': 4 links on 3 pages  # mostly on: person
   # [[relations]]
   # heading = "Works with"
   # type = "works-with"
   # inverse = "works-with-of"
   ```

   Uncomment the ones that mean something and give them good names (`inverse` is what
   the relation is called from the other page; it can be the same word). A rule turns

   ```markdown
   ## Works with
   - [[charles-babbage]] — co-lead; they split hardware and software
   ```

   into `works-with -> charles-babbage` with the reason "co-lead; they split hardware and
   software". An agent reads these reasons to decide which page to open next, so list
   items written as `[[page]] — why it matters` pay off. Rules for frontmatter fields
   (`field = "sources"`) work the same way. The [README](../README.md#relations) has
   the full rule syntax.

One more setting you may want: how many pages a search returns. The default is 3, which
keeps an agent's context small; raise it if your wiki's answers tend to span more pages.

```toml
[search]
results = 5     # wiki search, nav start and nav search; 1-20, and --limit overrides it
```

Every setting is optional, and you can change them at any time: the next command notices,
and re-derives relations without re-embedding anything (summary settings trigger a full
rebuild).

### Keep the cache out of git

```bash
echo ".cache/" >> .gitignore
```

Commit `.wiki-cli.toml`, so everyone working on the wiki, and every agent, uses the same
settings.

### Build the index

```bash
wiki index refresh
```

```
embedding 7/7 files
added: 7, changed: 0, touched: 0, removed: 0, moved: 0, unchanged: 0, embedded: 7
```

The first run embeds every page: about 2 minutes per 500 pages on a laptop CPU (long raw
text files take longer). It saves progress as it goes, so an interrupted run picks up
where it stopped. Later runs only touch files that changed and take a fraction of a
second when nothing did. [What refresh does](#what-wiki-index-refresh-does) explains the
details.

## 6. Try the commands

```bash
wiki search "how are jobs prioritised so none wait forever?"
```

```
1. scheduler  (-0.372)
   Decides the order card stacks run in, using a priority queue with aging so low-priority jobs are never starved.
2. ada-lovelace  § Ada Lovelace > Role  (-2.515)
   Lead engineer on the analytical-engine rebuild; owns the scheduler design.
3. 2026-09-01-planning  § Planning meeting, 2026-09-01 > Decisions  (-3.100)
   Agreed the March demo date and flagged the brass shortage.
```

A page's relations, with reasons taken from the pages themselves:

```bash
wiki neighbors ada-lovelace
```

```
-> works-with       charles-babbage  co-lead; they split hardware and software
-> works-with       grace-hopper  compiler work for the new instruction set
-> links-to         scheduler  Role: Wrote the scheduler design doc and reviews every change to the instruction set.
<- linked-from      scheduler  Design: Written by ada-lovelace.
```

A navigation session, the way an agent uses it: start from a question, read the section
that matches, see where the page leads, and record what the answer cites.

```bash
wiki nav start "Who attended the planning meeting and what did they decide?"
wiki nav read 187e6d 2026-09-01-planning --why "The meeting page should list attendees and decisions."
wiki nav candidates 187e6d
wiki nav end 187e6d --cited 2026-09-01-planning
```

Health checks for the wiki itself:

```bash
wiki check --all     # frontmatter errors, missing summaries, ambiguous and unwritten links
wiki unwritten       # link targets with no page yet, most-linked first
wiki orphans         # pages nothing links to
wiki suggest <slug>  # pages this one names but doesn't link, or resembles
```

`wiki check` exits with 1 when it finds errors, so it works in a pre-commit hook or CI.
Every command accepts `--format json` for scripts and agents.

## 7. Connect your agent

**Any agent.** The wiki's `AGENTS.md` is the connection: Codex, Cursor, Copilot, OpenCode
and others read it, and it tells the agent to run `wiki guide <workflow>` and follow it.
`wiki guide` lists the workflows. The agent only needs to be allowed to run `wiki`
commands; how you allow that depends on the agent. If your agent reads a different file
(some read their own, such as `GEMINI.md`), point it at `AGENTS.md` or copy the section in.

**Slash commands.** `--agent` adds commands `/wiki-ingest`, `/wiki-query`, `/wiki-lint`
(and `/wiki-sync` for a code wiki) for Claude Code, GitHub Copilot and OpenCode. Repeat it
for several: `wiki new my-wiki --agent claude --agent copilot --agent opencode`. Each
command is a few lines that pull in its guide when it runs (`` !`wiki guide ingest` ``),
so the agent gets the steps as its prompt, never a copy that could go stale; text after
the command (`/wiki-ingest raw/report.pdf`) says what to work on. An agent that doesn't
run the embedded command is told to run it. Without adapters, `AGENTS.md` still works: the
commands only remove a step, which matters most for smaller models.

| `--agent` | Writes | Notes |
|---|---|---|
| `claude` | `CLAUDE.md` (`@AGENTS.md`), `.claude/settings.json` allowing `Bash(wiki:*)` (and, for a code wiki, the code repo in `additionalDirectories`), skills in `.claude/skills/` | The skills may run `wiki` without asking |
| `copilot` | `.github/copilot-instructions.md` pointing at `AGENTS.md`, skills in `.github/skills/` | Copilot also reads `.claude/skills/`, so with `claude` too the skills are written once. Copilot's docs describe no embedded commands, so Copilot is told to run `wiki guide` itself; allow `wiki` in its terminal auto-approve settings to skip the prompts |
| `opencode` | Commands in `.opencode/commands/` | Run by OpenCode's build agent, which can edit files |

It never edits an existing file; for one that lacks what the wiki needs, it prints what
to add. Run it again after upgrading the tool if a new workflow has appeared: it adds the
missing commands.

## 8. Day to day

- **After editing pages, run `wiki index refresh`.** Search and navigation update their
  keyword index automatically before every search, so edits are findable by keyword
  immediately; vector search sees them after the next refresh.
- **After changing `.wiki-cli.toml`,** nothing extra: the next command picks it up.
- **Updating the tool:** `uv tool upgrade llm-wiki-cli` (or `git pull` in an editable
  checkout). Guides come with the command, so every wiki gets the new steps at once. If an
  update changes the cache format, the next command rebuilds the cache by itself.
- **After an edit that doesn't change what a page is about,** `wiki check` may warn
  `summary-stale`; re-read the summary and revise it, or run
  `wiki check <slug> --summary-ok`.
- **If anything looks wrong,** `wiki index rebuild` recreates the cache from the files.
  Deleting `.cache/` does the same.

### What `wiki index refresh` does

1. Checks the cache still fits the settings (rebuilding it or re-deriving relations if not).
2. Walks the folder and skips files whose modified time and size are unchanged, without
   reading them.
3. Re-parses changed files: title, type, summary, sections for search, and relations.
4. Removes pages whose files are gone, and re-checks links that pointed at missing pages.
5. Embeds the sections of every changed file (skip with `--no-embed`).

## 9. Measure search quality (optional)

To check whether search finds the right pages in *your* wiki, or to compare models,
write a small set of questions with known answers and score them.
[docs/evaluation.md](evaluation.md) covers `wiki eval sample` (which picks pages to write
questions about) and `wiki eval run`.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `...is not a wiki folder; cd into the wiki, pass --root, or add a config` | You ran `wiki` from your home folder or a drive root. `cd` into the wiki first. |
| `note: no .wiki-cli.toml found; using ... as the wiki root` | No config yet. Check the folder is the one you meant, then run `wiki init --write`. |
| `vector search skipped: no embeddings yet` | Run `wiki index refresh`. |
| `N files are not embedded yet` | Pages changed since the last refresh; run `wiki index refresh`. |
| `embedding model ... is not downloaded` | Run `wiki models download`. |
| Search returns files that aren't wiki pages | Narrow `pages` or add `exclude` globs in `.wiki-cli.toml`. |
| `ambiguous-link` warnings | Two files share a name, so `[[name]]` could mean either. Link with the path (`[[people/name]]`), as Obsidian does. |
| Every relation is `links-to` | Add `[[relations]]` rules; `wiki init` suggests them from your headings. |
| `summary-stale` warning | The page's body changed but its summary did not. Revise the summary, or `wiki check <slug> --summary-ok` if it still fits. |
| `code repo ... does not exist` or `is not the top of a git repository` | A code wiki's `[code] repo` points at the wrong place on this machine. Set `WIKI_CODE_REPO` to the code's checkout, or fix the path. |
| `code repo ... has origin ..., but this wiki describes ...` | The pointer names a different repository than the one the wiki was made for. Point it at the right checkout. |
| `wiki` in a code repo works on the code repo, not the wiki | Add the redirect `wiki new --preset code` printed: a `.wiki-cli.toml` in the code repo holding only `wiki = "<path to the wiki>"`. |
| The first search after a reboot takes a few seconds | The model files are read from disk the first time; later searches take about 1 second. |
