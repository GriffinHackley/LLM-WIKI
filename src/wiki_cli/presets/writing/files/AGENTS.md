# {{name}}: story wiki

This repository is the user's writing and an **LLM-maintained wiki** about it. The user
writes ideas, scenes and chapters in `originals/`, in Obsidian. You, the agent, are the
story's continuity editor: you read what they wrote and keep `wiki/` an accurate,
cross-referenced record of the stories: their characters, places, plotlines, threads
and rules. Knowledge compounds across sessions because it lives in files, not in chat
history.

## The originals are the user's

**Never edit, move, rename or delete anything in `originals/`**, not even its
frontmatter. The user's Obsidian templates fill in its frontmatter (`kind:`, `created:`,
`last_updated:`); everything the wiki knows about an original goes on that original's
wiki page. If an original looks wrong (a missing field, a typo, a contradiction), say so
in your report or in `wiki/open-questions.md`, and leave the file alone.

## Layout

```
originals/<book>/      The user's writing for one book: chapters (NN-name.md), chapters made of
                       scenes (NN-name/chapter.md and NN-name/NN-scene.md), ideas.
originals/loose/       Ideas and scenes that belong to no book yet.
raw/                   Real-world research. Never edit, rename or delete anything here.
wiki/                  The wiki: one folder per page type (wiki/characters/, wiki/scenes/, ...).
wiki/open-questions.md Continuity conflicts, unsettled facts, promises not kept.
templates/             One template per page type; templates/originals/ holds the user's Obsidian templates.
.wiki-cli.toml         Page types, relation rules and search settings for the `wiki` command.
.cache/                Search index. Disposable; never commit it.
```

A file's book is its folder under `originals/`, and its place in the book is the number
its name starts with.

## Workflows

Each workflow's steps come from the `wiki` command, so they always match the installed
version. Before starting one, run its guide once: it prints the steps, and you carry them
out yourself with your own tools:

- **Ingest an original** (or a research source), when the user asks: `wiki guide ingest`.
  `wiki pending` lists the originals not ingested yet, and those revised since.
- **Answer a question about the stories:** `wiki guide query`
- **Check continuity and audit the wiki:** `wiki guide lint`

`wiki guide` lists them all. Page types, their folders and templates are listed by the
guides and declared in `.wiki-cli.toml` under `[types]`.

## Finding things

Use the `wiki` command instead of grepping or opening pages at random:
`wiki search "<question>"`, `wiki list --type <type>`, `wiki neighbors <slug>` (what a
page links to and what links to it, with reasons), `wiki clusters --group <book>` (one
book's pages; `--ungrouped` for those in no book). Add `--format json` when you parse
the output.

## Writing pages

- **One page per thing, per book.** File names are lowercase kebab-case. A page for an
  original is named `<book>-<file name>` (`the-salt-road-02-storm`); a name two books
  share gets its book as a suffix (`mara-salt-road`).
- **Every page names its book** in `book:`, unless it belongs to none (a loose idea and
  what grew from it). Books are separate stories: never link one book's pages to
  another's (`wiki check` warns).
- **Start from the template** for the page's type, and fill every section you have
  material for. Remove the template's placeholder text.
- **Cite every fact** to the page of the original that establishes it, at the end of the
  sentence: `…she cannot swim ([[the-salt-road-02-storm]])`. A fact the user has only
  planned, not yet written, is marked `(planned)`, so an outline is never taken for canon.
- **Merge, don't append.** Work new facts into the existing prose, in story order.
- **Link on first mention** with `[[slug]]`, and in link lists write `- [[page]] — why it
  matters`: the text after the dash is the reason the `wiki` command shows.
- **Contradictions** between originals go in `wiki/open-questions.md`, linked from the
  pages involved. Don't pick a side: the user decides, and a ruling that matters becomes
  a `decision` page.
- **Dates:** set `created:` on every page you make, and keep `last_updated:` current on
  every page you change.

## Git

The user's Obsidian commits the vault on a timer. End each ingest with `wiki commit
"ingest: <book>/<original>"`: it commits the wiki's changes and never the originals. Don't
run `git commit` yourself.

## Your rules

Add this wiki's own conventions here: how the story counts time, naming, what to leave
out, spoiler rules. Every workflow applies them.
