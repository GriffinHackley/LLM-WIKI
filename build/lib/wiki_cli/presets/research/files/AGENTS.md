# {{name}}: LLM wiki

This repository is an **LLM-maintained wiki**: a persistent, interlinked knowledge base
built from source documents. You, the agent, are the librarian. People put sources in
`raw/`; you read them, extract what matters, and keep `wiki/` accurate,
cross-referenced and cited. Knowledge compounds across sessions because it lives in
files, not in chat history.

## Layout

```
raw/                   Source documents. Never edit, rename or delete anything here.
wiki/                  The wiki: one folder per page type (wiki/people/, wiki/sources/, ...).
wiki/open-questions.md Contradictions, gaps and unverified claims worth chasing.
templates/             One template per page type. New pages start from these.
.wiki-cli.toml         Page types, relation rules and search settings for the `wiki` command.
.cache/                Search index. Disposable; never commit it.
```

## Workflows

Each workflow's steps come from the `wiki` command, so they always match the installed
version. Before starting one, run its guide and follow it:

- **Ingest a source:** `wiki guide ingest`
- **Answer a question from the wiki:** `wiki guide query`
- **Audit the wiki:** `wiki guide lint`

`wiki guide` lists them all. Page types, their folders and templates are listed by the
guides and declared in `.wiki-cli.toml` under `[types]`.

## Finding things

Use the `wiki` command instead of grepping or opening pages at random:
`wiki search "<question>"`, `wiki list --type <type>`, `wiki neighbors <slug>` (what a
page links to and what links to it, with reasons), `wiki suggest <slug>` (pages a page
should probably link). Add `--format json` when you parse the output.

## Writing pages

- **One page per thing.** File names are lowercase kebab-case (`wiki/people/ada-lovelace.md`);
  the name is the one the sources use most.
- **Start from the template** for the page's type, and fill every section you have
  material for. Remove the template's placeholder text.
- **Merge, don't append.** When a new source adds to an existing page, work the new facts
  into the existing prose. Never add a section per source.
- **Cite every fact** with an inline link to its source page at the end of the sentence
  or paragraph: `…signed in March ([[senate-report-2024]], p. 4)`. A fact with no
  citation is a lint finding. Add the source to the page's `sources:` list too.
- **Link on first mention** with `[[slug]]`. Linking to a page that does not exist yet is
  fine: it marks a page worth writing.
- **In link lists**, write `- [[page]] — why it matters`. The text after the dash is the
  reason the `wiki` command shows for that link, so make it specific.
- **Contradictions** between sources, or claims no source supports, go in
  `wiki/open-questions.md`, linked from the pages involved. Don't silently pick a side.
- **Keep `last_updated`** current on every page you change.

## Git

History is git. Commit after each unit of work (one ingest, one lint pass) with a
message that says what changed and why: the source ingested, pages created and updated,
questions raised. The commit message is the record; there is no changelog.

## Your rules

Add this wiki's own conventions here: editorial rules, citation details, naming, what
to leave out. Every workflow applies them.
