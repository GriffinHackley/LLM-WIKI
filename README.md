# LLM wiki starter kit (`wiki`)

Start and run an **LLM-maintained wiki** (a folder of Markdown notes that an agent
keeps: an Obsidian vault, a research notebook, a wiki about a codebase) with any agent
that can run shell commands: Claude Code, Codex, Cursor, Copilot, OpenCode and others.

- **`wiki new`** sets up a wiki from a preset: folder layout, page templates, an
  `AGENTS.md` telling the agent how to be the librarian, and a config whose relation
  rules match the templates. Presets: `research` (sources in, pages out), `code` (a
  wiki in its own repo describing a code repo) and `writing` (stories you write in
  Obsidian, and a wiki of their characters, places and plotlines).
- **`wiki guide`** prints the workflow an agent follows (ingest a source, answer a
  question, audit the wiki, sync after code changes), versioned with the command so the
  steps never go stale.
- **Search and navigation** find the best starting page without reading an index, show
  where each page leads through typed relations with reasons, and read only the sections
  needed, within a page limit.

**How it fits together:** you ask your agent to add a source or answer a question; the
agent follows the steps `wiki guide` prints, and uses `wiki search`, `wiki nav` and
`wiki check` as it writes the pages. The `wiki` command never writes a page's content,
calls no AI model, and needs no network after a one-time model download. What it builds
for search lives in a disposable cache inside the wiki.

**New here? Start with [docs/getting-started.md](https://github.com/GriffinHackley/LLM-WIKI/blob/main/docs/getting-started.md),
then [docs/workflows.md](https://github.com/GriffinHackley/LLM-WIKI/blob/main/docs/workflows.md) for what you do with a wiki, step by
step:** asking questions, finding gaps, a code task from plan to commit, researching a
topic, a writing session.

More ([all the docs](https://github.com/GriffinHackley/LLM-WIKI/tree/main/docs#readme)):

- [docs/concepts.md](https://github.com/GriffinHackley/LLM-WIKI/blob/main/docs/concepts.md): how the pieces fit, and what each term means
- [docs/presets.md](https://github.com/GriffinHackley/LLM-WIKI/blob/main/docs/presets.md): what each preset is for, and its page types
- [docs/guides.md](https://github.com/GriffinHackley/LLM-WIKI/blob/main/docs/guides.md): the guides agents follow, and how to customise them
- [docs/commands.md](https://github.com/GriffinHackley/LLM-WIKI/blob/main/docs/commands.md): every command and option
- [docs/config.md](https://github.com/GriffinHackley/LLM-WIKI/blob/main/docs/config.md): every config setting
- [docs/troubleshooting.md](https://github.com/GriffinHackley/LLM-WIKI/blob/main/docs/troubleshooting.md): what a message means, and what to do

## Install

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/), which installs
Python 3.13 for the tool if needed.

```bash
uv tool install llm-wiki-kit    # once per machine; the command is `wiki`
wiki models download            # once: about 0.2 GB
```

`uv tool upgrade llm-wiki-kit` updates it. The latest code, before a release:
`uv tool install git+https://github.com/GriffinHackley/LLM-WIKI`.

An install from before 0.3.0 also provides `wiki` and has another name: `wiki-cli`, or
`llm-wiki-cli` if it came from git or a clone. `uv tool list` shows it; remove it first
with `uv tool uninstall <name>`. Wikis, their config and downloaded models are untouched.

## Quick start

A new research wiki:

```bash
wiki new my-wiki --agent claude     # optional slash commands (also copilot, opencode); AGENTS.md works for any agent
cd my-wiki
# put a source in raw/, then ask your agent: "ingest raw/<file>"
```

A wiki about a codebase, in its own repo beside the code, or in a folder of the code's repo:

```bash
wiki new my-app-wiki --preset code --code ../my-app
# or, from the code repo: wiki new docs/wiki --preset code --code .
# then connect the code repo for its agents: wiki connect ../my-app --agent claude-code
```

A wiki for your writing: you write ideas, scenes and chapters in `originals/` in
Obsidian, the agent never touches them, and keeps a wiki of the stories:

```bash
wiki new my-stories --preset writing --agent claude
# open it in Obsidian (Templater, Linter, Obsidian Git), write, then ask: "ingest the storm scene"
```

An existing folder of notes (an Obsidian vault, a `docs/` folder, a wiki an agent already
keeps):

```bash
cd my-notes
wiki new . --agent claude           # drafts a .wiki-cli.toml from your pages, adds the agent files
wiki index refresh                  # index and embed (about 2 minutes per 500 pages on CPU)
wiki search "how do I configure the exporter?"
```

It also works with no configuration at all: every `*.md` file under the folder is a
page, `raw/**/*.txt` is searchable source text, and every link is a `links-to` relation
whose reason is the sentence around it.

## Contributing

[CONTRIBUTING.md](https://github.com/GriffinHackley/LLM-WIKI/blob/main/CONTRIBUTING.md)
covers development setup, the tests, the docs and releasing.
[PLAN.md](https://github.com/GriffinHackley/LLM-WIKI/blob/main/PLAN.md) is the current
plan, and [FUTURE_IDEAS.md](https://github.com/GriffinHackley/LLM-WIKI/blob/main/FUTURE_IDEAS.md)
the deferred ideas.

## License

MIT; see [LICENSE](https://github.com/GriffinHackley/LLM-WIKI/blob/main/LICENSE).
