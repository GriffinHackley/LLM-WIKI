# Command reference

Every `wiki` command, with its options and an example. `wiki <command> --help` prints the
same options from the command itself. For what the commands are for, see
[workflows.md](workflows.md); for the terms, [concepts.md](concepts.md).

- **Set up:** [new](#wiki-new), [init](#wiki-init), [connect](#wiki-connect),
  [models](#wiki-models-list)
- **Workflows:** [guide](#wiki-guide)
- **Find:** [search](#wiki-search), [nav](#wiki-nav-start), [list](#wiki-list),
  [neighbors](#wiki-neighbors), [suggest](#wiki-suggest)
- **Check and improve:** [check](#wiki-check), [pending](#wiki-pending),
  [unwritten](#wiki-unwritten), [orphans](#wiki-orphans), [clusters](#wiki-clusters),
  [map](#wiki-map), [vocab](#wiki-vocab)
- **Code wikis:** [stale](#wiki-stale), [covering](#wiki-covering), [task](#wiki-task-start),
  [finish](#wiki-finish)
- **Commit:** [commit](#wiki-commit), [weekly](#wiki-weekly), [precommit](#wiki-precommit),
  [hook](#wiki-hook)
- **The cache:** [index](#wiki-index-refresh)
- **Search quality:** [eval](#wiki-eval-sample)

## Options most commands take

| Option | What it does |
|---|---|
| `--root <folder>` | The wiki to work on. Default: `$WIKI_ROOT`, else the nearest `.wiki-cli.toml` upward, else the enclosing git repository, else the current folder. |
| `--cache <file>` | The cache database. Default: `<root>/.cache/wiki.sqlite3`. |
| `--format text\|json` | `json` prints compact, stable JSON for scripts and agents. Text output wraps to the terminal; piped elsewhere, it is plain. `NO_COLOR` turns colour off. |
| `--embed-model <name>` | The embedding model, on commands that search. Default: `$WIKI_EMBED_MODEL`, else `embed_model` in the config. |
| `--reranker <name>` | The reranker, or `none`, on commands that search. Default: `$WIKI_RERANKER`, else `reranker` in the config. |

**Exit codes:** 0 success; 1 the check or validation failed (problems found, a refused
navigation step); 2 a usage or runtime error.

## Set up

### `wiki new`

Create a wiki from a preset, or in a folder that already has pages, adopt it: draft its
config and add the agent files, leaving the pages alone. Never overwrites a file; run it
again to add what is missing.

```
wiki new [folder] [--preset research|code|writing|<folder>] [--code <repo>] [--agent <name>] [--git-hook]
```

| Option | What it does |
|---|---|
| `folder` | The wiki's folder. Default: the current folder. |
| `--preset` | `research` (default), `code`, `writing`, or the path to a preset folder of your own. |
| `--code` | Code preset only, and required there: the top folder of the code's git repository. |
| `--agent` | Also write this agent's files (`claude-code` or `claude`, `copilot`, `opencode`). Repeatable. |
| `--git-hook` | Install a pre-commit hook: sources in `raw/` unchanged, and `wiki check --all` passing. |

```bash
wiki new my-app-wiki --preset code --code ../my-app --agent claude
```

### `wiki init`

Survey the folder's pages and print a draft `.wiki-cli.toml`.

| Option | What it does |
|---|---|
| `--write` | Save the draft as `.wiki-cli.toml`. Never overwrites one. |
| `--preset` | Also include a preset's page types, relation rules and weekly notes. With a config already there, print only those, to merge in by hand. |

```bash
wiki init --preset code
```

### `wiki connect`

Set up a code repo for the agents working in it: write the files the wiki owns there
(the pointer `.wiki-cli.toml`, `WIKI.md`, skills, hook files), and print the lines your
own files need.

| Option | What it does |
|---|---|
| `code_repo` | The code repo. Default: the wiki's `[code] repo`. |
| `--agent` | An agent working in the code repo: `claude-code` (or `claude`), `copilot`, `opencode`. Repeatable. Default: those the code repo's pointer already names. |
| `--check` | Write nothing; report what is missing or out of date. Exits 1 if anything is. |

```bash
wiki connect ../my-app --agent claude-code --agent copilot
```

### `wiki models list`

The supported embedding models and rerankers, and which are downloaded.

### `wiki models download`

Download the configured models (about 0.2 GB for the defaults) into
`~/.cache/wiki-cli/models/`, or `$WIKI_MODELS_DIR`. The only command that downloads
anything. `--embed-model` and `--reranker` download others.

```bash
wiki models download
```

## Workflows

### `wiki guide`

Print a workflow's steps for an agent, filled in for this wiki: its page types, its
rules, and the steps that apply to it. Without a name, list the workflows.

| Option | What it does |
|---|---|
| `name` | The workflow: `ingest`, `query`, `lint`, and in a code wiki `sync` and `work`, plus any of the wiki's own. |
| `--parts` | List the parts (the steps a wiki can fill with its own text), which guides print each, and which this wiki fills. |

```bash
wiki guide ingest
```

## Find

### `wiki search`

The best pages for a question: keyword and vector search, combined, then reranked.

| Option | What it does |
|---|---|
| `question` | The question, in plain words. |
| `--limit` | How many pages. Default: `[search] results` in the config, else 3. |
| `--include-raw` | Also search the text of sources. |
| `--keyword-only` | Skip vector search and reranking: for exact strings such as an ID or a quote. |

```bash
wiki search "how are jobs prioritised so none wait forever?"
```

### `wiki nav start`

Start a navigation session: search for the question and open a session that tracks what
is read. [navigation.md](navigation.md) walks through a session.

| Option | What it does |
|---|---|
| `question` | The question the session answers. |
| `--max-pages` | Pages the session may read. Default 6. |
| `--limit` | Search results shown. Default: as for `wiki search`. |

```bash
wiki nav start "Who gave evidence at the North Mill fire inquiry?"
```

### `wiki nav read`

Read one page in a session: its summary, the section that best matches the question, and
the names of its other sections.

| Option | What it does |
|---|---|
| `session` | The session id `nav start` printed. |
| `slug` | The page. |
| `--why` | Required: a few sentences on the open question and why this page. At most 400 characters. |
| `--section` | Read this section instead of the best match. |
| `--full` | Read the whole page. |

```bash
wiki nav read a41f0c north-mill-fire-inquiry-1887 --why "The inquiry's page should list the witnesses."
```

### `wiki nav candidates`

Where to go next from the page just read: its relations with their reasons, pages like
it, and candidates shown earlier.

| Option | What it does |
|---|---|
| `session` | The session id. |
| `--limit` | At most this many candidates. |

### `wiki nav search`

Search again within a session, leaving out pages already read. At most three times per
session, never the same question twice.

| Option | What it does |
|---|---|
| `session` | The session id. |
| `question` | The part of the question still open. |
| `--limit` | Search results shown. |

### `wiki nav end`

Close the session.

| Option | What it does |
|---|---|
| `session` | The session id. |
| `--cited` | The pages the answer cites, comma-separated. |

### `wiki nav log`

A session's steps, with each read's reason.

| Option | What it does |
|---|---|
| `session` | The session id. |

### `wiki list`

Every page, with its type and summary.

| Option | What it does |
|---|---|
| `--type` | Only pages of this type. |

```bash
wiki list --type decision
```

### `wiki neighbors`

A page's relations, with each one's type and reason, without the page bodies.

| Option | What it does |
|---|---|
| `slug` | The page. |
| `--incoming` | Only relations from other pages to this one. |
| `--outgoing` | Only relations from this page. |
| `--relation` | Only this relation type (see `wiki vocab`). |
| `--limit` | At most this many. |

```bash
wiki neighbors ada-lovelace --outgoing
```

### `wiki suggest`

Pages a page should probably link: pages it names without linking, pages that share
several of its links, and pages with similar summaries.

| Option | What it does |
|---|---|
| `slug` | The page. |
| `--limit` | At most this many suggestions. |

## Check and improve

### `wiki check`

Check pages for the problems the tools rely on not having: frontmatter, summaries, types
and their required sections and fields, uncited sources, ambiguous links, stale
summaries, code links. Errors fail the check; warnings don't, unless `--strict`.

| Option | What it does |
|---|---|
| `target` | The page to check, by slug or path. |
| `--all` | Check every page, and the originals' frontmatter in a writing wiki. |
| `--verify-cache` | With `--all`: also check the cache matches the files. |
| `--strict` | Fail on warnings too. |
| `--no-warnings` | Show errors only. |
| `--ingested` | For a source page: check the whole ingest. The source's file is linked, the pages it discusses cite it, everything passes and is committed. |
| `--summary-ok` | Record that the page's summary still fits its changed body, clearing `summary-stale`. |

```bash
wiki check --all
wiki check acme-annual-report-2024-03 --ingested
```

### `wiki pending`

Sources in `raw/` that no page links yet, flagging copies of files already there. In a
writing wiki, also the originals not yet ingested, and those revised since they were.

| Option | What it does |
|---|---|
| `--hash` | Print an original's hash, for its page's `original_hash:`. |

### `wiki unwritten`

Link targets with no page, most-linked first: the pages most worth writing.

| Option | What it does |
|---|---|
| `--limit` | At most this many. |

### `wiki orphans`

Pages nothing relates to.

### `wiki clusters`

Groups of pages that link each other densely and that no hub page covers: usually a
subject that needs a page of its own. Needs about 30 pages to say anything.

| Option | What it does |
|---|---|
| `--all` | Also list the clusters a hub page covers. |
| `--min-size` | The smallest cluster listed. Default 4. |
| `--resolution` | Higher splits clusters more readily. Default: `[clusters] resolution`, else 1.0. |
| `--group` | Only this group's pages, and the pages they link to or are linked from. |
| `--ungrouped` | Only the pages that name no group. |

```bash
wiki clusters --group the-salt-road
```

### `wiki map`

A 3D map of the pages by what they are about, as one HTML file in `.cache/` that works
offline. Points are coloured by type, cluster, age or visits; relations are lines.

| Option | What it does |
|---|---|
| `--open` | Open the map in your browser. |
| `--query` | Place this question in the map, with lines to the pages search returns for it. |
| `--limit` | Search results drawn for `--query`. |
| `--nav` | Draw a navigation session as a path: a session id, `last`, or `all`. |
| `--color-by` | The first colouring: `type`, `cluster`, `age`, or `visits` (needs `--nav`). The page can switch. |
| `--chunks` | One point per section instead of per page. |
| `--method` | The layout: `auto` (UMAP, else PCA with a note), `umap` (fail if it can't), `pca`. |
| `--group` | Only this group's pages, laid out on their own. |
| `--ungrouped` | Only the pages that name no group. |

```bash
wiki map --open --query "what do we know about the scheduler?"
```

### `wiki vocab`

The relation types in effect, their inverses, and where each comes from.

## Code wikis

### `wiki stale`

Pages whose covered code changed since their `verified:` commit (uncommitted changes
included), pages never verified, and record pages due for a recheck.

### `wiki covering`

The wiki's notes on code files: the pages covering each, most specific first, and the
gotchas and decisions affecting them.

| Option | What it does |
|---|---|
| `path` | One or more code files, absolute or relative to the current folder. |
| `--session` | An agent session's id: files already shown in that session are skipped. Used by the agent hooks. |

```bash
wiki covering src/app/save.py
```

### `wiki task start`

Open a task on the code, held by the agent session that runs it. The user confirms it
through the agent's prompt.

| Option | What it does |
|---|---|
| `title` | A short title for the task. |
| `--session` | The session to hold it. Default: the one whose hook asked. |

### `wiki task takeover`

Move the open task to this session, after a restart or a cleared conversation.

| Option | What it does |
|---|---|
| `--session` | The session to hold it. Default: the one whose hook asked. |

### `wiki task cancel`

Close the open task without committing anything.

### `wiki task status`

The open task, and the files it would commit.

### `wiki finish`

End the open task: commit the code files it edited, set the pending pages' `verified:`
to that commit, then commit the wiki. The user confirms, seeing the files.

| Option | What it does |
|---|---|
| `-m`, `--message` | The commit message the user approved. |
| `--include` | Also commit this changed code file the task did not edit. Repeatable. |
| `--dry-run` | Show what it would commit, and stop. |

```bash
wiki finish -m "Retry failed uploads with backoff" --dry-run
```

## Commit

### `wiki commit`

Commit every change in the wiki except the originals folder, waiting while another git
process (Obsidian Git, say) holds the index lock.

| Option | What it does |
|---|---|
| `message` | The commit message. |

```bash
wiki commit "ingest: the-salt-road/02-storm"
```

### `wiki weekly`

With `[weekly]` in the config: write a note for each finished week of work, from the git
history, and a timeline across the weeks.

| Option | What it does |
|---|---|
| `--week` | Only this week (`2026-W40`), rewriting its generated part; `current` shows this week so far without writing it. |
| `--hook` | For the pre-commit hook: stage what it writes, and do nothing without `[weekly]`. |

### `wiki precommit`

What the pre-commit hook runs: sources in the raw folders unchanged, last week's note,
`wiki check --all` without errors, and the wiki's `[hooks] pre_commit` commands.

### `wiki hook`

The command an agent's hook files run. It reads the event the agent sends on standard
input and replies in that agent's format; it always exits 0. You don't run it yourself.

| Option | What it does |
|---|---|
| `event` | The hook event, as the agent names it (`preToolUse`, `PostToolUse`, ...). |
| `--agent` | The agent whose hook file runs it: `claude-code`, `copilot`, `opencode`. |
| `--log` | Record each call in `<root>/.cache/hook-log.jsonl`, long text cut short. |

## The cache

### `wiki index refresh`

Index new, changed, moved and deleted files, then embed the changed sections. Unchanged
files are skipped without being read.

| Option | What it does |
|---|---|
| `--no-embed` | Skip embedding; keyword search still works. |

### `wiki index rebuild`

Delete the cache and build it again from the files.

| Option | What it does |
|---|---|
| `--no-embed` | Skip embedding. |

### `wiki index status`

How many pages, relations and embeddings the cache holds, and whether it is out of date.

## Search quality

### `wiki eval sample`

Pick pages to write evaluation questions about. [evaluation.md](evaluation.md) walks
through an evaluation.

| Option | What it does |
|---|---|
| `--single` | How many single pages. |
| `--multi` | How many pairs of linked pages. |
| `--seed` | Makes the sample repeatable. |

### `wiki eval run`

Score search against a question file.

| Option | What it does |
|---|---|
| `questions` | The question file. Default: `<root>/eval/questions.yaml`. |
| `--split` | `tune`, `test` or `all`. |
| `--keyword-only` | Score keyword search alone. |

```bash
wiki eval run --split test
```
