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
Claude Code skill stubs (`wiki new --agent claude`) do the same.

## The workflows

| Guide | Research wiki | Code wiki |
|---|---|---|
| `ingest` | Read one source from `raw/`, write its source page, create or update the pages it discusses with inline citations, file contradictions, check, commit | Document a module, pull request or decision from the code: explain it, link the code with `code:` links, set `covers:` and `verified:`, check, commit the wiki |
| `query` | Answer from the wiki with `wiki nav`: search, read one section at a time, follow relations by their reasons, cite the pages; fall back to raw source text | The same; when the wiki can't answer, read the code, and offer to document what was learned |
| `lint` | `wiki check --all`, `wiki unwritten`, `wiki orphans`, then contradictions, uncited facts, settled questions, stale summaries; fix and commit | Adds `wiki stale` and coverage of the code's folders |
| `sync` | (none) | After committing code: `wiki stale`, update each stale page and its `verified:`, check, commit the wiki |

Each guide fills in what depends on the wiki: its page types (from `[types]` in
`.wiki-cli.toml`, with their folders and templates) and, for a code wiki, the code repo's
path.

## Adding your own rules

Put the wiki's own conventions in the **"Your rules"** section of `AGENTS.md`: editorial
rules, citation format, naming, what to leave out, how cautious to be. Every guide tells
the agent to apply them, and that they win over the guide where they conflict. This is
the first place to customise, and usually enough.

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
shows. Guides may use `{{types}}` and `{{rules}}`, which are filled in as for the built-in
ones. A replaced guide no longer updates with the tool: that is the trade.

To start from a built-in guide: `wiki guide ingest > guides/ingest.md`.

## Page types, templates and relations

A page type is three things that should agree:

1. **An entry in `.wiki-cli.toml`:**

   ```toml
   [types.person]
   description = "A person."
   folder = "wiki/people"
   template = "templates/person.md"
   ```

   The guides show the agent each type's description, folder and template, so the
   description is how the agent decides what a thing is. Make the boundaries between
   types sharp. `wiki check` flags pages whose `type:` is not declared.

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

- **Summaries.** `wiki check` warns `summary-stale` when a page's body changed but its
  summary did not. The agent revises the summary, or runs
  `wiki check <slug> --summary-ok` when it still fits.
- **Unwritten pages.** Links to pages that don't exist yet are fine: `wiki unwritten`
  lists them, most-linked first, as the pages most worth writing.
- **The pre-commit hook** (`wiki new --git-hook`) refuses commits that edit, rename or
  delete sources in `raw/`, or that leave `wiki check` errors: a backstop for edits made
  outside the workflows.
- **Code wikis.** `wiki stale` lists pages whose covered code changed since they were
  verified; `wiki check` warns on `code:` links to files that are gone and `covers:`
  globs that match nothing.
