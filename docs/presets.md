# Presets

A preset is the set of starting files `wiki new` copies into a wiki: instructions for the
agent, page templates, and a config whose page types and relation rules match the
templates. Once copied, they are the wiki's own files, to edit freely. The preset's name
is recorded in the config (`preset = "research"`), which picks the guide variants the
wiki uses.

```bash
wiki new <folder> --preset research                 # the default
wiki new <folder> --preset code --code <code repo>
wiki new <folder> --preset writing
wiki new <folder> --preset <path to a preset folder>
```

## `research`

For a wiki built from sources: news, history, papers, a hobby, any topic you
research. Sources go in `raw/` and are never edited; the agent turns each into a source
page and works what it says into the pages it discusses.

| Type | What it is | Link sections |
|---|---|---|
| `source` | One page per ingested source | `## Entities mentioned` -> `mentions` |
| `person` | A person | `## Relationships` -> `associated-with` |
| `organization` | A company, agency, group or institution | `## Relationships` -> `associated-with` |
| `place` | A country, region, city, building or property | |
| `event` | Something that happened at a particular time | `## Participants` -> `involves`; `## Location` -> `located-at` |
| `concept` | A term, technique, law, policy, idea, product or tool found in the sources | |
| `analysis` | The wiki's own synthesis, answering a question across pages | `## Key pages` -> `synthesizes` |

Every page lists its sources in `sources:` (`draws-on`), and cites each fact inline:
`…signed in March ([[acme-report-2024]], p. 4)`. The relation takes the citing
sentence as its reason. `wiki/open-questions.md` collects contradictions and gaps.
`event` and `concept` are hub types: `wiki clusters` counts a group of pages most of
which link to one as covered.

Guides: `ingest`, `query`, `lint`.

## `code`

For a wiki about a codebase. The wiki is its own repository beside the code, or a folder
of the code's repository, and the agent that changes the code keeps it current, working
from the code repo.

| Type | What it is | Link sections |
|---|---|---|
| `file` | One source file: what it is for and what it contains, mirroring the code's layout; `## Part of` is required, so `wiki check` notices when a rewrite drops it | `## Part of` -> `part-of` |
| `module` | One area of the code: what it does, where it lives, how it fits. A module inside a larger one names it under `## Part of`, so files link the specific module and the larger one stays an overview. `wiki check` warns when a page names a broader module than the most specific one whose `covers:` match its files (`part-of-broader-module`) | `## Depends on` -> `depends-on`, `## Part of` -> `part-of` |
| `concept` | An idea from the code: a domain term, pattern, interface or piece of configuration | |
| `decision` | Why something is the way it is (like an ADR); `status:` is proposed, accepted, rejected, superseded or deprecated | `## Affects` -> `decided-for` |
| `instruction` | How to do a specific task: set up a dev environment, run, test, deploy, backport | |
| `ticket` | A tracker ticket, a record: named by `key:` and `url:`, with `synced:` its last-updated time (`wiki stale` lists those due for a recheck); `kind:` is story, bug or task (`wiki check` warns on others); its epic goes in `parent:` | `parent:` -> `child-of` |
| `epic` | A tracker epic, a record like a ticket: a feature being built, and the tickets, pull requests and code that deliver it. It and file pages don't link each other: a file links the ticket, an epic the module (`no_links_with`; `wiki check` warns). It covers its stories and, through them, the pull requests that implement them (`hub_through`) | |
| `pr` | A pull request: its description, the changes, and `merge_commit:` | `## Implements` -> `implements` |
| `dependency` | An external library or service: why and how the code uses it | |
| `gotcha` | A trap in the code: symptom, cause, and how to avoid it | `## Affects` -> `gotcha-for` |
| `analysis` | The wiki's own synthesis, answering a question across pages | `## Key pages` -> `synthesizes` |

`module`, `concept` and `epic` are hub types: `wiki clusters` counts a group of pages most of
which link to one as covered, and reports the rest as module or concept pages worth writing.
An epic covers its feature's tickets while it is built; once it is done, lint checks that a
module or concept page describes the feature. Record types are never flagged
`hub-covers-clusters`, since a feature spans several areas and the page mirrors the tracker.

There is no type for documents as such. A document from `raw/` is recorded on a page of
the type it is: a design doc or RFC as a `decision`, an exported ticket as a `ticket`, a
postmortem as a `gotcha`. That page links the file in `raw/`.

Every type's `sources:` field lists the documents a page draws on (`draws-on`). Where a
document and the code disagree about what the code does now, the code wins and the page
gives the document as history.

Pages link to code with `[name](code:src/app/pages.py)` (paths from the code repo's top
level; `refers-to-code` relations), list the files they describe in `covers:` (globs),
and record the code commit they were checked against in `verified:`. `wiki stale` lists
the pages whose covered files changed since.

`--code` is required: the top folder of the code's git repository. The wiki is beside it
or a folder inside it, never that top folder itself. The config records it:

```toml
[code]
repo = "../my-app"                              # relative to the wiki
origin = "https://github.com/me/my-app.git"     # catches a pointer to the wrong checkout
```

Weekly notes are on (`[weekly]`, grouped by module): `wiki weekly` writes one per finished
week in `weekly/`, from `templates/weekly.md`, plus a timeline; see
[config.md](config.md#weekly-weekly-notes).

`WIKI_CODE_REPO` overrides the path on a machine where the code is checked out
elsewhere. `wiki new` prints, and never writes, what the code repo needs: a
`.wiki-cli.toml` redirect (`wiki = "../my-app-wiki"`, or `"docs/wiki"`), an `AGENTS.md` section, and with
`--agent claude` its Claude settings. One code repo per wiki for now.

Guides: `ingest` (document code), `sync` (after code changes), `query` (falls back to
reading the code), `lint` (adds staleness and coverage).

## `writing`

For stories. The user writes ideas, scenes and chapters in Obsidian, in `originals/` (a
folder per book, `originals/loose/` for pieces in no book), and the agent keeps a wiki of
the stories built from them. Originals are the user's: the agent reads them and never
changes them, not even their frontmatter, which is only what the Obsidian templates fill
in (`kind:`, `created:`, `last_updated:`). Everything the wiki knows about an original is
on its page. One git repository is the vault; Obsidian Git commits it on a timer, and each
ingest ends with `wiki commit`, which commits the wiki and never the originals. See
[getting-started.md](getting-started.md#5-start-a-wiki-for-your-writing) for the Obsidian
setup, and [config.md](config.md#originals-the-users-own-writing) for `[originals]`.

| Type | What it is | Link sections |
|---|---|---|
| `idea` | The page for an idea in `originals/`; `status:` open, developed or dropped | `## Developed into` -> `developed-into` |
| `scene` | The page for a scene: summary, POV, story time, what it reveals and sets up | `## Part of` -> `part-of`; `## Appears` -> `features`; `## Setting` -> `set-in`; `## Advances` -> `advances` |
| `chapter` | The page for a chapter: the same, or an overview of its scenes | `## Appears`, `## Setting`, `## Advances`, as a scene |
| `character` | A person or being in the story: role, wants, arc, voice, what they know and when | `## Relationships` -> `associated-with`; `## Affiliations` -> `member-of` |
| `location` | A world, region, city, building or room | `## Part of` -> `part-of` |
| `organization` | A faction, family, nation, guild, church or crew | `## Members` -> `has-member`; `## Base` -> `located-at` |
| `item` | An object that matters | `## Owners` -> `owned-by` |
| `lore` | A rule of the world: magic, technology, religion, language, custom, species, history | |
| `event` | Something that happens in the story's world, on the page or off it | `## Participants` -> `involves`; `## Location` -> `located-at` |
| `plotline` | The main plot, a subplot or a character arc; `kind:` main, subplot or arc; `status:` planned, active, resolved or dropped | `## Beats` -> `advanced-by` |
| `thread` | A promise to the reader: foreshadowing, a mystery, a Chekhov's gun; `status:` open, paid or dropped | `planted:` -> `planted-in`; `paid_off:` -> `paid-off-in` |
| `theme` | A theme or motif, and where it recurs | `## Appears in` -> `recurs-in` |
| `decision` | An authorial ruling: why something happens, a canon fix, a retcon; `status:` proposed until the user approves | `## Affects` -> `decided-for` |
| `style` | Rules for the writing: POV, tense, a character's voice, words to avoid | |
| `source` | Real-world research ingested from `raw/` | `## Used for` -> `informs` |
| `analysis` | The wiki's own synthesis: continuity, pacing, who knows what by when | `## Key pages` -> `synthesizes` |

- **Pages for originals** name their file in `original:` (`draws-on`) and record
  `ingested:` and `original_hash:`, so `wiki pending` lists the original again once the
  user revises it. They are named `<book>-<file name>` (`the-salt-road-02-storm`), so two
  books' `01-arrival.md` do not collide.
- **Citations** go to the page of the original that establishes a fact:
  `…she cannot swim ([[the-salt-road-02-storm]])`. A fact only planned, not yet written,
  is marked `(planned)`, so an outline is never taken for canon.
- **Books are groups:** every page names its book in `book:`, or none for a loose idea and
  what grew from it. `[groups] exclusive` makes a link between two books' pages a
  warning, and `wiki clusters --group <book>` / `--ungrouped` look at one book or the
  loose pages. Page folders are one per type; a name two books share takes the book as
  a suffix (`mara-salt-road`).
- **Dates:** every page has `created:` and `last_updated:`.
- `lore`, `event`, `plotline` and `theme` are hub types. `wiki/open-questions.md` holds
  continuity conflicts for the user to settle.

Guides: `ingest` (an original or a research source, when the user asks), `query`
(keeps canon, planned and characters' claims apart), `lint` (adds continuity checks:
contradictions in story order, what characters know when, open threads, stalled
plotlines, vanished characters).

## Making your own preset

A preset is a folder with a `files/` folder inside it:

```
my-preset/
  files/                 copied into the wiki, keeping its layout
    AGENTS.md
    dot-wiki-cli.toml    "dot-" names become dotfiles (.wiki-cli.toml)
    dot-gitignore
    templates/*.md
    wiki/...
  agents-section.md      optional: the short section printed when a wiki already has an AGENTS.md
```

Text files may use `{{name}}` (the wiki folder's name) and `{{today}}`. In the config,
set `preset = "my-preset"` and declare `[types]` and `[[relations]]` to match the
templates ([workflows.md](workflows.md#page-types-templates-and-relations)). For
workflows of its own, include a `guides/` folder in `files/` and set `[guides] dir =
"guides"`: its guides replace or add to the built-in ones. Then:

```bash
wiki new my-wiki --preset path/to/my-preset
```

## An existing wiki

`wiki new .` in a folder that already has pages adopts it instead of laying the preset
over it: it drafts a `.wiki-cli.toml` from a survey of the pages (as `wiki init --write`
does), and adds only what connects agents to the wiki: an `AGENTS.md` with the workflow
section if there are no agent instructions yet (otherwise the section to add is printed),
and the `--agent` files. The same happens in a folder with a `.wiki-cli.toml` of its own.
It adds none of the preset's starter pages, and no templates except those the config
names and lacks. Existing files are never overwritten.

To adopt a preset's page types, relation rules and weekly notes as well, `wiki init
--preset <name>` prints them, leaving out what the config already declares and placing
each type's folder under the wiki's page folder. Merge what fits, then run `wiki new .
--preset <name>` again to add the templates the types name.
[Getting started](getting-started.md#6-add-to-an-existing-wiki) walks through it.
