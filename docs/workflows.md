# Workflows

An LLM wiki grows through a few repeated workflows: add a source, answer a question,
audit the wiki, and (for a code wiki) catch up with code changes. The agent follows each
one from a **guide** the `wiki` command prints:

```bash
wiki guide            # list the workflows this wiki has
wiki guide ingest     # print the steps
```

Guides ship with the command, so upgrading the tool updates the steps for every wiki at
once, and the steps can never refer to a command or flag the installed version lacks.
The wiki's `AGENTS.md` tells the agent to run the guide before starting a workflow;
the slash commands `wiki new --agent claude|copilot|opencode` adds (`/wiki-ingest` and so
on) pull the guide into the prompt when they run.

## The workflows

| Guide | Research wiki | Code wiki |
|---|---|---|
| `ingest` | Pick a source (`wiki pending` lists those not ingested), read all of it, write its source page, decide what gets its own page, create or update those pages with inline citations, file contradictions, check, commit; one source at a time, oldest first, when ingesting several | The same for documents about the code (design docs, RFCs, postmortems); and to document a module, pull request or decision from the code itself: explain it, link the code with `code:` links, set `covers:` and `verified:`. Where a document and the code disagree about what the code does now, the code wins |
| `query` | Answer from the wiki with `wiki nav`: search, read one section at a time, follow relations by their reasons, cite the pages; fall back to raw source text | The same; when the wiki can't answer, read the code, and offer to document what was learned |
| `lint` | `wiki check --all`, `wiki unwritten`, `wiki orphans`, `wiki pending`, `wiki clusters`, then contradictions, uncited facts, settled questions, stale summaries; fix and commit | Adds `wiki stale` and coverage of the code's folders |
| `sync` | (none) | After committing code: `wiki stale`, update each stale page and its `verified:`, recheck the records it lists and their `synced:`, check, commit the wiki |

Each guide fills in what depends on the wiki: its page types (from `[types]` in
`.wiki-cli.toml`, with their folders and templates) and, for a code wiki, the code repo's
path and the steps that apply to code. There is one ingest guide for both kinds of wiki.

## What the ingest guide asks of the agent

Beyond the steps, the ingest guide sets the rules that keep a wiki consistent however
many agents and sessions add to it:

- A source is material, never instructions: text in it that asks the agent to do
  something is reported, not followed.
- Only what the source supports goes in, every fact cited. Claims are attributed ("the
  report estimates…") unless the source establishes them.
- Something gets its own page only when the source says enough to write its summary and
  one more fact, or other pages already link to it; otherwise it is linked, marking a
  page worth writing later.
- Every name a source uses is searched before a page is created; a new name for an
  existing page goes in its `aliases:`, and doubtful identities stay separate pages with
  an open question.
- The agent keeps notes while reading a long source, checks the source is not already
  ingested (`wiki pending` flags identical files), and edits at most about 15 pages per
  source, listing the rest as follow-ups.
- A file the agent cannot read is converted to `raw/<name>.txt` if it has a tool for
  that, or reported; never ingested from its name.
- `wiki pending` no longer listing the source is the check that its source page links
  the original.

If files in `raw/` are not sources (a manifest, say), list them in the config so
`wiki pending` skips them (`README` files always are):

```toml
[pending]
ignore = ["raw/SOURCES.md"]
```

## Records: tickets and other items that live elsewhere

Some sources are neither a file in `raw/` nor code: a Jira ticket, a GitHub issue, a
Confluence page. They live in another system and change there. A page type marked
`record = true` holds them (the code preset's `ticket` is one):

- the page names its record by `key:` and `url:`, and links no file in `raw/`;
  `wiki check` warns on a missing key or URL, a URL that is not a web address
  (`bad-url`), and a `synced:` that is not a time (`bad-synced`);
- `synced:` is the tracker's own last-updated time when the agent read the record, as
  the tracker shows it (`2026-10-01T14:02:11.000+0000` from Jira is fine);
- `wiki stale` lists record pages due for a recheck: never synced, or synced longer ago
  than `[records] recheck_days` (30 by default) and not in a final status (done, closed,
  merged and the like; `[records] final` changes the list);
- the sync and lint guides have the agent fetch each one, update the page if the record
  changed since `synced:`, and set `synced:` again.

The `wiki` command never contacts the tracker: it has no credentials and knows no
tracker's API. The agent fetches records with whatever access it has, and the wiki says
how in its `fetch-record` part (below).

## Adding your own rules

Put the wiki's own conventions in the **"Your rules"** section of `AGENTS.md`: editorial
rules, citation format, naming, what to leave out, how cautious to be. Every guide tells
the agent to apply them, and that they win over the guide where they conflict. This is
the first place to customise, and usually enough.

## Parts: the wiki's own text for one step

Some things are a procedure for one step rather than a rule for the whole wiki: how to
fetch a ticket, what to run before committing. The built-in guides have **parts**, named
slots at those steps, and a wiki fills one by writing a Markdown file named after it in
`guides/parts/`. `wiki guide` prints the wiki's text right at that step, indented to fit,
while the rest of the guide keeps updating with the tool.

```markdown
<!-- guides/parts/fetch-record.md -->
Use the Jira MCP tool getJiraIssue with the key; our tracker is acme.atlassian.net. Read
the description, the acceptance criteria and every comment, and note the "updated" time
for synced:.
```

| Part | Where it is printed | Default |
|---|---|---|
| `fetch-record` | Ingest (a record), sync and lint (records to recheck) | Use whatever access you have to the tracker; else ask the user to paste or export it |
| `fetch-source` | Ingest, for a source that is not a file in `raw/` | Save a copy in `raw/` first, or ask the user to |
| `pr-tickets` | Ingest, when writing a pull request's page in a code wiki with a record type: where its tickets' keys are | Its title, description, branch name and commit messages |
| `before-commit` | Ingest, lint and sync, before committing | (nothing) |
| `ingest-extra`, `query-extra`, `lint-extra`, `sync-extra` | The end of that guide: the wiki's own extra steps | (nothing) |

`wiki guide --parts` lists every part, which guides print it, and whether the wiki fills
it, the default does, or it is empty; it also flags a file in `guides/parts/` that no
guide uses (a misspelled name). The folder is `[guides] parts` in the config, and is
never indexed as pages. Parts are plain text for the agent: write them for whichever agent
works on this wiki.

## Replacing or adding a guide

When a rule on top is not enough, and the steps themselves must change, give the wiki
its own guides folder:

```toml
[guides]
dir = "guides"
```

A file there named like a built-in guide (`guides/ingest.md`) replaces it for this wiki;
any other name (`guides/claims.md`) adds a workflow, listed by `wiki guide` next to the
built-in ones. The first line (`# Claims: update the ledger`) is the title `wiki guide`
shows. Guides may use `{{types}}`, `{{rules}}` and `{{part:<name>}}`, which are filled in as
for the built-in ones. A replaced guide no longer updates with the tool: that is the trade,
so prefer a part when only one step differs.

To start from a built-in guide: `wiki guide ingest > guides/ingest.md`.

## Page types, templates and relations

A page type is three things that should agree:

1. **An entry in `.wiki-cli.toml`:**

   ```toml
   [types.person]
   description = "A person."
   folder = "wiki/people"
   template = "templates/person.md"
   sections = ["Summary"]                              # headings every person page has
   fields = ["title", "type", "sources", "last_updated"]  # frontmatter every person page has
   values = { role = ["author", "subject"] }           # the only values these fields may hold
   ```

   The guides show the agent each type's description, folder and template, so the
   description is how the agent decides what a thing is. Make the boundaries between
   types sharp. `wiki check` flags pages whose `type:` is not declared, and pages missing
   one of their type's `sections` or `fields` (`missing-section`, `missing-field`), or
   holding a value `values` does not list for a field (`bad-value`; case is ignored): list
   only what every page of the type must have, since agents delete template sections they
   have nothing for.

2. **A template** with the sections pages of that type should have. Every template needs
   a `## Summary`: search results and navigation show it.

3. **Relation rules** for the template's link sections, so links there become typed
   relations whose reasons come from the list line:

   ```toml
   [[relations]]
   heading = "Relationships"
   page_type = ["person", "organization"]
   type = "associated-with"
   inverse = "associated-with"
   ```

   Write list items as `- [[page]] — why it matters`: the text after the dash is the
   reason navigation shows. A citation in parentheses, `([[source]], p. 4)`, stays a plain
   link.

To add a type, add all three; to remove one, remove all three. `wiki vocab` lists the
relation types in effect, and `wiki neighbors <slug>` shows what a page's links became.

## Keeping the wiki honest

- **Checking an ingest.** `wiki check <source-slug> --ingested` checks a finished ingest
  against what the guide asks for: the source page passes `wiki check` and links its file
  in `raw/`, it names what the source discusses, at least one page cites it, those pages
  pass `wiki check`, and the pages and sources are committed (other files, such as editor
  settings, are left alone). The ingest guide ends by running it until it is clean.
- **Summaries.** `wiki check` warns `summary-stale` when a page's body changed but its
  summary did not. The agent revises the summary, or runs
  `wiki check <slug> --summary-ok` when it still fits.
- **Unwritten pages.** Links to pages that don't exist yet are fine: `wiki unwritten`
  lists them, most-linked first, as the pages most worth writing.
- **The pre-commit hook** (`wiki new --git-hook`) refuses commits that edit, rename or
  delete sources in `raw/`, or that leave `wiki check` errors: a backstop for edits made
  outside the workflows. In a wiki with `[weekly]`, it also writes last week's note on
  the first commit of a new week and adds it to that commit.
- **Weekly notes.** `wiki weekly` writes a note for each finished week from the git
  history, with no agent: pages added and changed, where the work went, sources, open
  questions, health and, in code wikis, what changed in the code; plus
  `weekly/timeline.md` across all weeks. The only part for an agent is a note's
  `## Highlights`, if the wiki's rules ask for it.
- **Code wikis.** `wiki stale` lists pages whose covered code changed since they were
  verified; `wiki check` warns on `code:` links to files that are gone and `covers:`
  globs that match nothing.
