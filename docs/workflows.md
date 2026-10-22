# Workflows

What you do with a wiki, step by step: what to ask your agent, what it does, and the
commands that help you along. The agent's side of each workflow comes from a guide the
`wiki` command prints (`wiki guide`); [guides.md](guides.md) is the reference for those,
and for customising them.

Most workflows are the same in every wiki. Each preset then adds its own:

- **Every wiki:** [setting up](#setting-up), [day to day](#day-to-day),
  [upkeep](#upkeep), [customising](#customising)
- **[Code wikis](#code-wikis):** tasks, syncing after code changes, records
- **[Research wikis](#research-wikis):** researching a topic, ingesting sources
- **[Writing wikis](#writing-wikis):** writing sessions, revisions, continuity

In what follows, "ask your agent" means saying it in plain words. With `--agent`, the
slash commands (`/wiki-ingest`, `/wiki-query`, `/wiki-lint`, `/wiki-sync`) do the same
with one step less.

## Setting up

### Start a new wiki

```bash
uv tool install llm-wiki-kit
wiki models download                                    # once per machine: about 0.2 GB
wiki new my-wiki --agent claude                         # research, the default
wiki new my-app-wiki --preset code --code ../my-app     # a wiki about a codebase
wiki new my-stories --preset writing --agent claude     # a wiki for your writing
```

`wiki new` starts a git repository and writes the agent's instructions (`AGENTS.md`), the
page templates, and a `.wiki-cli.toml` whose page types match them. Add `--git-hook` for
a pre-commit hook that refuses edits to sources and pages that fail `wiki check`. Running
it again never overwrites anything. [presets.md](presets.md) says what each preset is
for; [getting-started.md](getting-started.md) goes through each one in full.

### Adopt a folder of notes you already have

For an Obsidian vault, a `docs/` folder, or a wiki an agent already keeps:

```bash
cd my-notes
wiki new . --agent claude     # drafts .wiki-cli.toml from your pages; moves nothing
wiki index refresh            # index and embed: about 2 minutes per 500 pages on CPU
wiki check --all              # what the checks find in your pages as they are
```

Review the drafted config: which files are pages, where summaries are, which folders
are types, and above all the relation rules it suggests, commented out. Uncomment the
ones that mean something. If your agent instructions already exist, `wiki new .` prints
the section to paste in rather than editing them. To take on a preset's page types as
well, `wiki init --preset <name>` prints them to merge.
[Getting started](getting-started.md#6-add-to-an-existing-wiki) has the details.

### Connect your agent

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
| `claude` | `CLAUDE.md` (`@AGENTS.md`), `.claude/settings.json` allowing `Bash(wiki:*)` and denying edits under `raw/` (`Edit(/raw/**)`), and, for a code wiki, the code repo in `additionalDirectories`; skills in `.claude/skills/` | The skills may run `wiki` without asking; Claude Code refuses to change sources |
| `copilot` | `.github/copilot-instructions.md` pointing at `AGENTS.md`, skills in `.github/skills/` | Copilot also reads `.claude/skills/`, so with `claude` too the skills are written once. Copilot's docs describe no embedded commands, so Copilot is told to run `wiki guide` itself; allow `wiki` in its terminal auto-approve settings to skip the prompts |
| `opencode` | Commands in `.opencode/commands/` | Run by OpenCode's build agent, which can edit files |

It never edits an existing file; for one that lacks what the wiki needs, it prints what
to add. Run it again after upgrading the tool if a new workflow has appeared: it adds the
missing commands.

For a code wiki, the code repo is connected separately: [set up the code repo](#set-up-the-code-repo).

## Day to day

### Ask the wiki a question

Ask your agent. It follows `wiki guide query`: it starts a navigation session from the
question, reads one section at a time, picks the next page from summaries and the
reasons on each link, stops within a page limit, and cites the pages it used. If the
answer is a new synthesis, it offers to keep it as a page
([write an analysis page](#write-an-analysis-page)).

What a good question gets back depends on the links: a page whose link lists say why
each link matters (`- [[page]] — why`) is one the agent can navigate well.
[navigation.md](navigation.md) shows a session.

### Find a page yourself

The same commands work without an agent:

```bash
wiki search "what caused the mill fire?"   # the best pages for a question
wiki list --type person                   # every page of a type, with its summary
wiki neighbors edith-thorne               # a page's typed relations, with reasons
wiki suggest edith-thorne                 # pages it names but doesn't link, or resembles
wiki map --open                           # the whole wiki in 3D, in your browser
```

`--keyword-only` on `wiki search` finds exact strings: an ID, a version, a quote.

### Review what the agent changed

Each workflow ends in a commit, one per source or task, so `git log` and `git show` are
the review. Things worth a look:

- **Its report.** Every guide ends with one: what it created and updated, questions it
  filed, follow-ups it left.
- **`wiki check --all`.** Errors and warnings across the wiki. `summary-stale` means a
  page changed but its summary didn't: the agent revises it, or marks it still fitting
  with `wiki check <slug> --summary-ok`.
- **An ingest:** `wiki check <source-slug> --ingested` checks it did what the guide
  asks: the source page links its file, at least one page cites it, everything is
  committed.
- **`wiki/open-questions.md`.** Contradictions and gaps it found, for you to settle.

If something is wrong, say so: the agent fixes it in a new commit.

## Upkeep

### Lint the wiki

Ask your agent to lint the wiki now and then: every few dozen sources, or before relying
on it for something. It follows `wiki guide lint`:

1. `wiki check --all`, `wiki unwritten`, `wiki orphans`, `wiki pending` and
   `wiki clusters` for what the tools can find;
2. reading for what they can't: contradictions between pages, uncited facts, questions
   a later source settled, summaries that no longer fit;
3. fixing what is mechanical, committing, and reporting the rest.

Code wikis add staleness and coverage; writing wikis add continuity
([continuity check](#continuity-check)).

### Find knowledge gaps

Three commands each show a different kind of gap:

```bash
wiki unwritten        # pages linked but not written, most-linked first
wiki clusters         # groups of linked pages that no hub page covers
wiki map --open       # where the pages are, and aren't
```

- **`wiki unwritten`** is the simplest: a link with no page is a page someone already
  wanted. The top of the list is the wiki's most-wanted page.
- **`wiki clusters`** finds groups of pages that link each other densely, and reports
  those that no hub page covers (a concept, an event, a module, a plotline: the preset
  says which types are hubs). Such a group is usually about something no page is about
  yet: a rivalry, a project, a subsystem. Its report names the pages; ask your agent to
  write the page they share. It needs about 30 pages to say anything.
- **`wiki map --open`** draws every page by what it is about. Colour it by cluster
  (`--color-by cluster`) to see the groups, and look for thin regions near busy ones.
  `--query "<question>"` places a question in the map with lines to the pages search
  returns: a question that lands far from every page is one the wiki can't answer yet.
  `--nav last` replays the agent's last question as a path through the pages.

Then fill the gap: [research a topic](#research-a-topic-and-ingest-only-what-is-relevant),
document the code, or write the missing piece.

### Grow the links between pages

Links are what navigation follows, so a page linked from nowhere is hard to reach.

```bash
wiki orphans              # pages nothing relates to
wiki suggest <slug>       # pages this one names without linking, shares links with, or resembles
```

For each orphan, ask your agent to link it from the pages that discuss it. The ingest
guide runs `wiki suggest` on every new page, so this is mostly for pages written by hand.

### Weekly review

`wiki weekly` writes a note for each finished week from the git history, with no agent:
pages added and changed, where the work went, sources, open questions and health, and in
a code wiki, what changed in the code. `weekly/timeline.md` runs across every week. Code
wikis have it on; any wiki can turn it on with
[`[weekly]`](config.md#weekly-weekly-notes). With the pre-commit hook, last week's note is
written by the first commit of a new week.

### Work on one part of the wiki at a time

When a wiki holds several things (projects, clients, a writing wiki's books), give pages
a field naming their part and list it in [`[groups]`](config.md#groups-the-parts-of-a-wiki-its-pages-name):

```toml
[groups]
fields = ["tags"]      # the default; the writing preset uses ["book"]
```

Then `wiki clusters --group <name>` and `wiki map --group <name>` look at one part
(plus the pages it links to, such as shared people), and `--ungrouped` at the pages that
name none. `exclusive = true` makes the parts separate worlds: `wiki check` warns on a
link between them.

## Customising

### Change the page types and relation rules

A page type is three things that agree: its entry under `[types]` in `.wiki-cli.toml`, a
template, and relation rules for the template's link sections. To add or remove a type,
change all three; the next command picks the config up. `wiki vocab` lists the relation
types in effect, and `wiki neighbors <slug>` shows what a page's links became.
[guides.md](guides.md#page-types-templates-and-relations) walks through it.

### Add your own rules or steps

From the lightest touch to the heaviest:

1. **"Your rules" in `AGENTS.md`:** editorial rules, citation format, naming, what to
   leave out. Every guide applies them, and they win where they conflict with the
   guide. Usually enough.
2. **A part:** the wiki's own text for one step, such as how to fetch a ticket or what
   to run before committing, in `guides/parts/<name>.md`. `wiki guide --parts` lists
   them.
3. **A guide of its own:** a file in the wiki's guides folder replaces a built-in guide
   or adds a workflow. It no longer updates with the tool.
4. **A check of its own:** `[hooks] pre_commit` commands run with the pre-commit hook.

[guides.md](guides.md) has each in detail.

### Tune search

Search returns 3 pages by default, which keeps an agent's context small. If answers
tend to span more pages, raise it:

```toml
[search]
results = 5
```

To measure search on your own wiki rather than guess, build an evaluation set:
`wiki eval sample` picks pages, an agent writes questions about them, and `wiki eval run`
scores search against those questions. [evaluation.md](evaluation.md) walks through it,
and [model-selection.md](model-selection.md) has the results that chose the default models.

### When something looks wrong

- **Search misses a page you just edited:** `wiki index refresh`. Keyword search sees
  edits at once; vector search after a refresh.
- **Results look wrong in general:** `wiki index rebuild` recreates the cache from the
  files. Deleting `.cache/` does the same; nothing in it is lost.
- **The pre-commit hook refuses a commit:** it names the reason. Usually a `wiki check`
  error (fix the page) or a change to a source in `raw/` (sources never change: add a
  new file instead).
- **`wiki` can't find the wiki:** it walks up to the nearest `.wiki-cli.toml`, as git
  does. Run it from inside the wiki, or pass `--root <folder>`.

[troubleshooting.md](troubleshooting.md) has more.

## Code wikis

### A task, start to finish

The work loop: one task on the code (a fix, a feature, a refactor), run from the code
repo, with you approving each step. It needs the code repo
[connected](#set-up-the-code-repo) and an agent that runs hooks (Claude Code or Copilot).

Ask your agent to do the task. It follows `wiki guide work`:

1. **It learns the area** from the wiki. It splits the task into parts (each feature,
   behavior or kind of data it changes or relies on) and finds the pages on each with
   `wiki search`, and along relations with `wiki neighbors`. It looks in the code only
   for a file or function the task names. For a part with no page, it stops and asks
   whether to look in the code for it, and recommends documenting the files it finds.
   Then it reads the pages, and every open gotcha and decision that affects them or the
   files it will change.
2. **It plans, and stops.** The parts of the task and the pages on each, what it will
   change and where, how it will test it, the files it will document, its
   recommendation for each choice with more than one answer, and how the plan respects
   each gotcha and decision. You approve it, or ask for changes until you do.
3. **`wiki task start "<title>"`** opens the task, and you confirm. Until then, edits to
   the code and the wiki are refused, as they are from any other session while a task is
   open.
4. **It records decisions, and stops.** Each choice you settled becomes a decision page
   with `status: proposed`, which you approve (`accepted`) or reject.
5. **It does the work and tests it.** If the plan turns out wrong, it stops and asks
   before changing course.
6. **It brings the wiki up to date:** the pages describing what it changed (from
   `wiki stale`), with `verified: "pending"`; new files added to a page's `covers:`; a
   gotcha page for anything learned the hard way.
7. **It reports, and stops,** with the changes, the test results, the pages it updated,
   and a commit message. You approve it.
8. **`wiki finish -m "<message>"`** shows you the files and asks to confirm, then
   commits exactly the files the task edited in the code repo, sets the pending pages'
   `verified:` to that commit, and commits the wiki.

Steps 1 and 2 are `wiki guide plan`, which `wiki guide work` runs first. To get a plan
and decide later, ask for `wiki guide plan` (`/wiki-plan`) on its own: it stops at the
approved plan, changing nothing. Then `wiki guide work` (`/wiki-work`) in the same
session uses that plan and goes on from step 3.

The agent never runs `git commit` itself: it is refused. Between steps:

```bash
wiki task status       # the open task, and what it would commit
wiki task takeover     # move the open task to this session, after a restart or /clear
wiki task cancel       # close it without committing
```

### Set up the code repo

`wiki new --preset code --code <repo>` records where the code is. To let the agents
working on the code use the wiki, connect the code repo:

```bash
wiki connect ../my-app --agent claude-code      # or --agent copilot, or both
```

It writes the files the wiki owns into the code repo: a `.wiki-cli.toml` pointing at the
wiki, `WIKI.md` saying how to work with it, the workflow skills, and Copilot's hook file.
It prints, and never writes, the lines your own files need: one line in `AGENTS.md`,
`@AGENTS.md` in `CLAUDE.md`, and Claude Code's hooks and permissions. Add those by hand,
then `wiki connect --check` says what is still missing. From then on every `wiki` command
run in the code repo works on the wiki.

On another machine where the code is checked out elsewhere, set `WIKI_CODE_REPO` to its
path.

### Catch up after code changes

For code changed outside a task (by hand, by a teammate, by a merge), ask your agent to
sync the wiki. Commit the code first: pages record the commit they were checked against.
It follows `wiki guide sync`:

1. `wiki stale` lists the pages whose covered files changed since their `verified:`
   commit, and pages never verified;
2. for each, it reads the diff, updates the page to match the code, adjusts `covers:` for
   moved and deleted files, and sets `verified:` to the new commit;
3. new modules or decisions get pages of their own;
4. it checks and commits the wiki, naming the code commit it synced to.

`wiki stale` also shows uncommitted changes, so you can see what a change will affect
before you commit it.

### Before editing a file

```bash
wiki covering src/app/save.py
```

The pages that cover the file, and the gotchas and decisions that affect it. With the
code repo connected, an agent sees this by itself the first time it opens a file.

### Document code from the code itself

Ask your agent to document a module, a pull request, a range of commits, or a decision
(`wiki guide ingest`). It reads the code, never edits it, and writes a page that:

- links the code with `[name](code:src/app/save.py)`, so `wiki check` notices when the
  file is gone;
- lists the files it describes in `covers:`, so `wiki stale` knows when they change;
- records the code commit it was checked against in `verified:`.

A pull request's description and review come from its forge (`gh pr view`, say) and are
never copied into `raw/`. Documents about the code (a design doc, an RFC, a postmortem)
go in `raw/` and are ingested as pages of the type they are: an RFC as a decision, a
postmortem as a gotcha. Where a document and the code disagree, the code wins.

### Bring in tickets and epics

Tickets and epics live in a tracker and change there. Ask your agent to ingest one by key
or link: it fetches it, writes a `ticket` or `epic` page naming it by `key:` and `url:`,
and records the tracker's last-updated time in `synced:`. A story's epic goes in
`parent:`, and gets a page if it has none. Ingesting a pull request writes no ticket
pages: its page names its tickets by key and link, and links their epic.

`wiki stale` lists records due for a recheck: never synced, or synced more than 30 days
ago and still open ([`[records]`](config.md#records-items-that-live-elsewhere) changes
both). Sync and lint refetch them. The `wiki` command never contacts the tracker; the
agent fetches with whatever access it has. Say how in the `fetch-record` part
([guides.md](guides.md#parts-the-wikis-own-text-for-one-step)):

```markdown
<!-- guides/parts/fetch-record.md -->
Use the Jira MCP tool getJiraIssue with the key; our tracker is acme.atlassian.net.
```

### Approve or reject a proposed decision

The agent writes each settled choice as a decision page with `status: proposed`, and
never accepts it itself. With the code repo connected, every session starts by listing
the decisions waiting for you. To review them yourself:

```bash
wiki list --type decision
```

Read the page, then tell the agent: approved (it sets `status: accepted`), or rejected
(it removes the page, or sets `rejected` if it replaced an earlier decision). A decision
that a later one replaces becomes `superseded`.

## Research wikis

### Research a topic and ingest only what is relevant

The wiki grows from what you put in `raw/`, so the choice of sources is yours. A way to
work through a topic:

1. **See what the wiki already holds.** Ask your agent the question, or run
   `wiki search "<topic>"`. What it can't answer is what to look for.
2. **Gather candidates outside the wiki.** Search yourself, or ask an agent with web
   access for a list of sources on the open questions, with a line on what each covers.
3. **Pick, then save.** Read the list and keep only what answers something: a primary
   source over a summary of it, one good overview over five similar ones. Save each into
   `raw/` (a PDF, a saved page, a text file). Leave the rest out: once committed, a
   source in `raw/` is there for good (the pre-commit hook, if the wiki has one,
   refuses to delete it).
4. **Ingest them.** Ask your agent to ingest everything new. It works through
   `wiki pending` one source at a time, oldest first, each with its own commit
   ([catch up on a backlog](#catch-up-on-a-backlog)).
5. **Ask again.** Re-ask the question from step 1. What is still missing is the next
   round, and `wiki/open-questions.md` holds the contradictions found on the way.

An agent with web access can also ingest a link directly: it saves a copy in `raw/`
first, so the page cites something that won't change.

### Ingest a single source

Put the file in `raw/` and ask your agent to ingest it. It follows `wiki guide ingest`:

1. reads all of it, keeping notes, before writing anything;
2. writes its source page: what it is, who produced it, its main points with page
   numbers, and a link to its file;
3. finds the pages it touches, under every name they go by, and decides what earns a
   page of its own (enough to write a summary and one more fact);
4. updates or creates those pages, merging new facts in and citing each one:
   `…the mill reopened in May ([[ashford-courier-1887-03-10]], p. 4)`;
5. files contradictions in `wiki/open-questions.md`, checks, and commits.

It edits at most about 15 pages per source and lists the rest as follow-ups. Then
`wiki check <source-slug> --ingested` confirms the ingest is complete.

A file the agent can't read (a scan, audio) is converted to `raw/<name>.txt` if it has a
tool for that, or it asks you to; it is never ingested from its name.

### Catch up on a backlog

```bash
wiki pending      # sources in raw/ no page links to yet; flags duplicates
```

Ask your agent to "ingest everything new". It takes them oldest first, by the source's
own date, so later sources update earlier ones, and finishes each, commit included,
before starting the next. It skips duplicates and files it can't read, and stops early
if it runs short of context, saying what is left: `wiki pending` picks up there next
time. Files in `raw/` that aren't sources (a manifest) go in
[`[pending] ignore`](config.md#pending).

### Contradictions and open questions

When sources disagree, the agent doesn't pick a side: it attributes each claim to its
source on the pages ("the report estimates…") and files the conflict in
`wiki/open-questions.md`, linked from the pages involved. So do gaps the sources leave.

Read the file now and then. Settle a question by ingesting a source that answers it, or
by telling the agent what you know and where from. Lint also looks for questions a later
source has settled, and clears them.

### Write an analysis page

An answer that draws several pages together is worth keeping. After answering, the
agent offers to file a new synthesis as an `analysis` page; you can also ask for one
outright ("write an analysis of how the three reports' forecasts differ"). Its
`## Key pages` links the pages it draws on, each with why, so the analysis becomes a
page navigation can reach, and lint can check against later sources.

## Writing wikis

### A writing session

You write in Obsidian; the agent keeps the wiki. With the Obsidian setup from
[getting started](getting-started.md#5-start-a-wiki-for-your-writing):

1. **Start a piece.** Create a file in the book's folder, `originals/<book>/`, or
   `originals/loose/` for a piece in no book. Templater fills its frontmatter from the
   folder's template: `kind:` (idea, scene or chapter) and `created:`. Its number orders
   it in the book: `03-the-crossing.md`.
2. **Write.** Linter keeps `last_updated:` current as you save; Obsidian Git commits
   every few minutes. The agent never edits your originals.
3. **Ingest at the end.** Ask your agent to ingest what's new. It runs `wiki pending`,
   which lists the originals not yet ingested and those you revised since, and asks
   which. For each piece it writes the piece's own page (`wiki/scenes/the-salt-road-03-the-crossing.md`),
   updates the characters, places, plotlines and threads it touches, citing your piece,
   files anything that contradicts earlier chapters, and commits the wiki with
   `wiki commit`.
4. **Read its report.** What it created and updated, and the questions it filed for you:
   contradictions, facts the story leaves open, notes you left yourself.

`wiki check --all` lists any original missing its template's frontmatter.

### Revise an original

Edit the file as usual. `wiki pending` lists it again as **changed**: each page for an
original records a hash of what was ingested, leaving out the date lines, so only a real
change counts. Ask your agent to ingest it again: it updates the piece's page, and takes
the facts you rewrote out of every page that cites it, so the wiki never keeps something
you changed your mind about.

Renaming or moving an original is fine with Obsidian's "Automatically update internal
links" on: the wiki's links follow. If one breaks, `wiki check` warns `missing-original`.

### Continuity check

Before a draft goes out, or every few chapters, ask your agent for a continuity check
(`wiki guide lint`). Book by book, it looks for:

- **contradictions:** details that disagree (eye colour, ages, who was where), and facts
  that can't both hold in story order;
- **knowledge:** a character acting on something before the scene where they learn it;
- **open threads:** promises to the reader planted long ago with nothing since;
- **plotlines:** active ones with no beat in a long while;
- **vanished characters:** important early, then gone with no exit;
- **planned versus written:** facts marked `(planned)` that a scene has since written.

It fixes the wiki's own pages, never your originals, and reports the rest for you to
decide. A ruling you give becomes a `decision` page, and the pages it affects follow it.

### Ask about your own story

Ask your agent ("what does Mara know about the ship by chapter 6?"). It follows the
writing variant of `wiki guide query`, which keeps three things apart: what the story
shows (canon), what you plan (`(planned)`, from ideas and outlines), and what a character
claims. It answers within one book, and falls back to reading the originals when the wiki
falls short.

### Several books in one wiki

Each book is a folder of `originals/`, and every page names its book in `book:`. The
books are separate worlds (`[groups] exclusive = true`):

- `wiki check` warns on a link from one book's page to another's (`cross-group-link`);
- a name two books share gets two pages, the book as a suffix (`mara-salt-road`);
- `wiki clusters --group <book>` and `wiki map --group <book> --open` look at one book,
  and `--ungrouped` at the loose ideas;
- an idea that moves from `originals/loose/` into a book takes `book:` with it, on its
  page and the pages that grew from it.

### Living with Obsidian Git

Obsidian Git commits the whole vault on a timer, the agent's edits included, and the
agent commits the wiki with `wiki commit` at the end of each ingest. They share one
repository without getting in each other's way:

- `wiki commit` commits everything except `originals/`, which are yours to commit;
- when Obsidian Git holds git's lock, `wiki commit` waits for it rather than failing;
- the pre-commit hook (`--git-hook`), if the wiki has one, lets your originals change,
  while still refusing changes to research sources in `raw/`.

If a commit fails, `wiki commit` says why: usually a `wiki check` error for the agent to
fix.

---

**Next:** [Commands](commands.md): every command and option. · [All docs](README.md)
