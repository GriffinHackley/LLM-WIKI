# Getting started

This guide gets an LLM-maintained wiki running with your agent, on one of four paths:

- **[A new research wiki](#3-start-a-new-research-wiki):** sources go in, the agent writes
  and links the pages. News, history, papers, any topic you research.
- **[A wiki about a codebase](#4-start-a-wiki-about-a-codebase):** a wiki in its own repo,
  kept current by the agent that changes the code.
- **[A wiki for your writing](#5-start-a-wiki-for-your-writing):** you write stories in
  Obsidian, the agent keeps a wiki of their characters, places and plotlines.
- **[An existing folder of notes](#6-add-to-an-existing-wiki):** an Obsidian vault, a
  `docs/` folder, a wiki you already keep. Your pages are left alone.

The `wiki` command never edits pages: the agent does, following workflow guides the
command prints. Everything the command builds for search lives in a disposable cache
inside the wiki, so trying it is safe.

**The short version:**

```bash
uv tool install llm-wiki-kit        # once per machine; the command is `wiki`
wiki models download                # once per machine, about 0.2 GB
wiki new my-wiki --agent claude     # --agent is optional
cd my-wiki
# put a source in raw/, then ask your agent: "ingest raw/<file>"
```

## 1. Requirements

- [uv](https://docs.astral.sh/uv/getting-started/installation/). It installs Python 3.13
  for the tool if you don't have it.
- Git, for the wiki's history (the workflows commit after each unit of work).
- An agent that can run shell commands: Claude Code, Codex, Cursor, Copilot, OpenCode or
  similar. It reads the wiki's `AGENTS.md`; nothing depends on one platform.
- About 0.2 GB of disk for the two models, plus a cache in each wiki of roughly 40 KB per
  page.
- No GPU. Search runs locally on the CPU; nothing is sent to a server.

The default models are English-only. The tool is developed on Windows; its tests run
on Windows, macOS and Linux.

## 2. Install the tool (once per machine)

```bash
uv tool install llm-wiki-kit
wiki --version
```

The package is `llm-wiki-kit`; the command it installs is `wiki`. `uv tool install` puts
`wiki` on your PATH; if the shell can't find it, run `uv tool update-shell` and open a new
terminal. To update later, run `uv tool upgrade llm-wiki-kit`. For the latest code before
a release, install from git instead:
`uv tool install git+https://github.com/GriffinHackley/LLM-WIKI`. An install from
before 0.3.0 also provides `wiki` under another name, `wiki-cli` or (from git or a clone)
`llm-wiki-cli`; `uv tool list` shows it. Remove it first with `uv tool uninstall <name>`.
Your wikis, their config and downloaded models are untouched.

If `wiki` says this Python's sqlite3 cannot load extensions (the python.org installers for
macOS are built that way), reinstall it on uv's own Python:
`uv tool install --managed-python --reinstall llm-wiki-kit`.

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
New research wiki: ...\my-wiki
Started a git repository.

Created
  AGENTS.md               how agents work on the wiki
  .gitignore              keeps the cache out of git
  .wiki-cli.toml          settings: page types, relations, search
  raw/.gitkeep            sources go here
  templates/              page templates: analysis, concept, event, organization, person, place,
                          source
  wiki/open-questions.md  contradictions and gaps to chase

Next
  1. Ask your agent  to ingest a source you put in raw/, or to answer a question from the wiki;
                     'wiki guide' lists the workflows
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

Options: `--agent claude-code|copilot|opencode` adds slash commands for that agent ([connect your agent](workflows.md#connect-your-agent));
`--git-hook` adds a pre-commit hook that refuses edits to `raw/` and pages that fail
`wiki check`, runs any checks of the wiki's own (`[hooks] pre_commit`), and, in a wiki with weekly notes, commits last week's note with the first
commit of a new week. Running `wiki new` again never overwrites anything; it adds what is
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
section of `AGENTS.md`; every workflow applies them.

Next: [research workflows](workflows.md#research-wikis), and the ones every wiki shares.

## 4. Start a wiki about a codebase

The wiki lives in its own repository beside the code, or in a folder of the code's
repository, and needs to know where the code is:

```bash
wiki new my-app-wiki --preset code --code ../my-app     # a repository beside the code
wiki new docs/wiki --preset code --code .               # or a folder of it, run from the code repo
```

`--code` must be the top folder of the code's git repository; the wiki can't be that
folder itself. `wiki new` records the path (relative to the wiki) and the repo's origin in
the wiki's `.wiki-cli.toml`, and prints what the code repo needs so that the agent
working on the code also keeps the wiki. It never writes to the code repo itself; add
these yourself:

- `.wiki-cli.toml` in the code repo, holding only `wiki = "../my-app-wiki"`: every `wiki`
  command run in the code repo then works on the wiki.
- A section for the code repo's `AGENTS.md`: answer questions about the code with the
  query guide; after changing code, commit it and follow `wiki guide sync`.
- With `--agent claude`, the code repo's `.claude/settings.json` entries: the `wiki`
  permission, a rule against editing the wiki's `raw/`, and for a wiki beside the code,
  the wiki folder in `additionalDirectories` so Claude can edit it.

**The work loop.** For agents that run hooks (Claude Code, GitHub Copilot), connect the
code repo instead: `wiki connect ../my-app --agent claude-code` (or `--agent copilot`, or
both). It writes the files the wiki owns into the code repo: the pointer
`.wiki-cli.toml` naming the agents, `WIKI.md` saying how to work with the wiki, the
workflow skills, and Copilot's hook file and instructions. It prints the lines your own
files need, which you add by hand: one line in `AGENTS.md` sending agents to `WIKI.md`,
`@AGENTS.md` in `CLAUDE.md`, and for Claude Code the hooks and permissions to merge into
`.claude/settings.json`. `wiki connect --check` says what is still missing. From then on:

- a session starts by learning where the wiki is, the open task, and the decisions
  waiting for your approval;
- the first time the agent opens a file, it sees the wiki's pages on it and the gotchas
  and decisions that affect it (`wiki covering <file>`);
- `wiki guide work` runs each task: a plan you approve, then `wiki task start` (you
  confirm), each settled choice written as a decision you approve, the work, the wiki
  pages brought up to date, and your approval of the result;
- edits to the code and the wiki are refused until a task is open, and from any session
  but the one holding it; `git commit` is refused;
- `wiki finish -m "<message>"` (you confirm, seeing the files) commits exactly the files
  the task edited, then the wiki pages, with `verified:` set to the new commit.

Then work in the code repo as usual. To document a module or a decision, ask the agent
(it follows `wiki guide ingest`): pages link to code with `[name](code:src/app.py)`, list
the files they describe in `covers:`, and record the code commit they were checked
against in `verified:`. Before changing a file, `wiki covering <file>` shows the pages
covering it and the gotchas and decisions that affect it. After code changes, `wiki stale` lists the pages whose covered
files changed, and `wiki guide sync` brings them back in line. The code and the wiki are
committed separately: code first, then the wiki. That holds for a wiki in a folder of the
code repo too: a page records the code commit it was checked against, so the pages
describing a change are a commit after it. On a machine where the code is checked
out somewhere else, set `WIKI_CODE_REPO` to its path.

Next: [code workflows](workflows.md#code-wikis), starting with a task from plan to commit.

## 5. Start a wiki for your writing

For stories: you write ideas, scenes and chapters in Obsidian, and the agent keeps a wiki
of the characters, places, plotlines and promises to the reader, built from what you
wrote. Your writing is never touched by the agent.

```bash
wiki new my-stories --preset writing --agent claude
```

The folder is one git repository and one Obsidian vault:

```
originals/<book>/   your writing, a folder per book: 01-arrival.md (a chapter), or
                    02-the-crossing/chapter.md with its scenes 01-departure.md, 02-storm.md
originals/loose/    ideas and scenes that belong to no book yet
wiki/               the agent's pages, one folder per type
templates/originals/  Obsidian templates for your writing
```

A file's book is its folder; its place in the book is the number its name starts with.

**Set up Obsidian.** Open the folder as a vault and install three community plugins.
Their setting names vary a little between versions:

- **Templater**, for the frontmatter every original has. Set its template folder to
  `templates/originals`, turn on running Templater when a new file is created, and add
  folder templates on `originals/` (or on each book's folder) so a new file gets one:
  `scene.md` gives `kind: scene` and today's `created:`. Use `idea.md` and `chapter.md`
  for those. `wiki check --all` lists any original that ends up without them.
- **Linter**, for `last_updated:`. Templates fill values in once and never update them,
  and file times are reset by a git clone or checkout, so this is what keeps the date
  current. Turn on only its YAML timestamp rule, with the modified-date key
  `last_updated`, the created-date key `created`, and the date format `YYYY-MM-DD`;
  then turn on linting on save (and on file change, if you want it without saving).
  Leave its other rules off, or it will reformat your prose.
- **Obsidian Git**, for commits you never think about: turn on auto commit (and push, if
  the vault has a remote) every few minutes. It commits the whole vault, the agent's
  wiki edits included.

Also turn on **Files and links → Automatically update internal links**, so renaming an
original updates the wiki's links to it.

**Day to day.** Write in `originals/`. When a piece is ready, ask your agent to ingest
it ("ingest the storm scene"). It writes the piece's own page (`wiki/scenes/the-salt-road-02-storm.md`)
and updates the character, location, plotline and thread pages it touches, citing your
piece, then commits the wiki with `wiki commit`, which never commits your originals.
`wiki pending` lists what is new and what you revised since it was ingested; ask for those
to be ingested again. `wiki clusters --group <book>` looks at one book's pages, and
`--ungrouped` at the loose ones. Ask your agent for a continuity check (`wiki guide lint`)
now and then: contradictions, open threads, plotlines that stalled.

The agent may not edit `originals/`: Claude Code's settings deny it (`Edit(/originals/**)`),
Copilot's hook refuses it, and `AGENTS.md` tells every agent so.

Next: [writing workflows](workflows.md#writing-wikis), starting with a writing session.

## 6. Add to an existing wiki

For a folder of notes that already exists: an Obsidian vault, a `docs/` folder, a wiki
an agent already keeps. Your pages, folders, templates and instructions stay as they are;
nothing is moved, renamed or rewritten.

**The whole path:**

```bash
cd my-notes
wiki new . --agent claude     # --agent is optional; also copilot, opencode
# review .wiki-cli.toml, and paste the section it prints into your agent instructions
wiki index refresh            # index and embed: about 2 minutes per 500 pages on CPU
wiki check --all              # what the checks find in your pages as they are
```

`wiki new .` sees that the folder already holds pages and adopts it rather than laying a
new wiki over it:

```
Existing wiki: ...\my-notes (6 pages)
Added the research workflows. Your pages are unchanged.

Created
  .wiki-cli.toml          drafted from your pages: review it
  .gitignore              keeps the cache out of git
  .claude/settings.json   lets Claude Code run wiki; blocks edits in raw/
  .claude/skills/         workflow skills: wiki-ingest, wiki-lint, wiki-query

Not added: the preset's 8 starter pages and templates (your wiki keeps its own).

Add to CLAUDE.md:
    ## LLM wiki
    ...

Next
  1. Review .wiki-cli.toml  which files are pages, summaries, relation rules
  2. Add the lines above    to the files named
  3. wiki index refresh     index and embed the pages, about 2 minutes per 500 on CPU
  4. wiki check --all       see what the checks find in the pages as they are
  5. Ask your agent         to ingest a source you put in raw/, or to answer a question from the
                            wiki; 'wiki guide' lists the workflows
```

- **The config** is drafted from a survey of your pages, as [`wiki init`](config.md#reviewing-a-drafted-config)
  does (below: what to review in it).
- **Agent instructions.** If the wiki has none, it writes an `AGENTS.md` holding just the
  workflow section. If it has some (`AGENTS.md`, `CLAUDE.md`, `GEMINI.md` or
  `.github/copilot-instructions.md`), it prints the section to paste into the first of them
  and leaves the file alone. The workflows apply your own rules over their own, so keep
  what your instructions already say.
- **Agent files** (`--agent`) are written as for a new wiki ([connect your agent](workflows.md#connect-your-agent)).
- **Not added:** the preset's templates and starter pages. Your wiki keeps its own layout,
  and the workflows follow the pages you have.

Running `wiki new .` again is safe: it adds only what is missing and prints only what is
still to add. A folder holding only a `README.md` counts as empty, and gets the full preset.

**A wiki about a codebase** adopts the same way, with the code repo named:
`wiki new . --preset code --code ../my-app`. The config then points at the code, the
guides include the code steps and `wiki guide sync`, and the lines for the code repo are
printed as in [section 4](#4-start-a-wiki-about-a-codebase).

**The preset's page types, templates and weekly notes** are not added to an existing
wiki, since its pages have their own shape. To adopt them:

```bash
wiki init --preset code       # prints the preset's [types], [[relations]] and [weekly] tables
```

Merge what fits into `.wiki-cli.toml` (it leaves out what the config already declares, and
puts each type's `folder` under your page folder, so new pages are indexed), then run
`wiki new . --preset code` again: it adds the templates the config now names. Pages
without a `type:` get one from their folder, and `wiki check --all` then says which
pages lack a type's required sections or fields.

**Review the drafted config** before relying on it: which files are pages, where
summaries are, which folders are types, and the relation rules it suggests.
[Reviewing a drafted config](config.md#reviewing-a-drafted-config) goes through each.
`wiki` finds the wiki from any folder inside it, the way git finds a repository.

Next: [workflows.md](workflows.md) for what to do with it, day to day.

## 7. Next steps

- **[workflows.md](workflows.md)** walks through what you do with a wiki: asking
  questions, linting, finding gaps, and each preset's own workflows.
- **[concepts.md](concepts.md)** explains the pieces and the terms the docs use.
- **[commands.md](commands.md)** has every command, with examples. A few to try:
  `wiki search "<a question>"`, `wiki neighbors <page>`, `wiki check --all`,
  `wiki map --open`.
- **[troubleshooting.md](troubleshooting.md)** lists the messages you may see, what
  each means, and what to do.
