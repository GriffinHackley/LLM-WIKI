# Config reference: `.wiki-cli.toml`

Every setting, its type, its default, and what checks it. Every setting is optional: with
no config at all, every `*.md` file under the wiki's folder is a page and nothing else is
checked. `wiki new` writes a config from a preset ([presets.md](presets.md) lists what each
preset declares); in a folder that already has pages, `wiki new .` and `wiki init --write`
draft one from a survey of them, and `wiki init --preset <name>` adds a preset's page
types, relation rules and weekly notes to the draft.

An unknown key, or a value of the wrong type, is an error when any command loads the
config, naming the key. Nothing is silently ignored.

## At a glance

Every section, with typical values. Each is optional; the sections below give every
key's type and default.

```toml
preset = "research"               # the preset `wiki new` used; picks guide variants
pages = ["wiki/**/*.md"]          # default ["**/*.md"]
exclude = ["wiki/index.md"]      # default none
raw = ["raw/**/*.txt"]            # default; searched only with --include-raw
embed_model = "BAAI/bge-small-en-v1.5"          # default; $WIKI_EMBED_MODEL overrides
reranker = "jinaai/jina-reranker-v1-turbo-en"   # default, or "none"; $WIKI_RERANKER overrides

[types.person]                    # page types: guides list them, `wiki check` flags others
description = "A person."
folder = "wiki/people"            # pages here get this type when frontmatter has none
template = "templates/person.md"
sections = ["Summary"]            # headings and frontmatter every page of the type has,
fields = ["title", "type", "sources", "last_updated"]   # checked by `wiki check`
values = { role = ["author", "subject"] }               # allowed values of a field
hub = false                       # true: pages gather around it (see `wiki clusters`)
record = false                    # true: each page describes a ticket or the like, kept by key, url, synced

[records]                         # record types: when `wiki stale` asks for a recheck
recheck_days = 30                 # default

[summary]                         # where a page's summary comes from, in order
fields = ["summary", "description"]            # frontmatter keys (default)
headings = ["Summary", "Overview"]             # default ["Summary"]; else the first paragraph

[page_type]
field = "type"                    # frontmatter key holding the page type (default)

[check]
require_frontmatter = false       # default
summary_types = ["person"]        # page types that must have a summary ("*" = all)

[suggest]
named_types = ["person", "organization"]  # pages `suggest` matches by name (default: all)

[search]
results = 3                       # default; pages from search, nav start and nav search (1-20)

[pending]
ignore = ["raw/SOURCES.md"]       # files in raw/ that are not sources (default none)

[weekly]                          # weekly notes (`wiki weekly`); off without this table
folder = "weekly"                 # default; never indexed
group_by = "module"               # "where the work went" by module, or "type" (default)

[groups]                          # --group on clusters and map: pages naming a group, and their neighbours
fields = ["tags"]                 # default; the frontmatter fields that name a page's groups

[hooks]                           # the pre-commit hook's extra checks, run from the root
pre_commit = ["python tools/check.py", { run = "python tools/lint.py", files = ["drafts/*.md"] }]

[guides]
dir = "guides"                    # the wiki's own guides, overriding built-in ones by name
parts = "guides/parts"            # default; <name>.md is the wiki's own text for a guide step

[code]                            # code wikis only
repo = "../my-app"                # the code repo, relative to the wiki; $WIKI_CODE_REPO overrides
origin = "https://github.com/me/my-app.git"    # catches a pointer to the wrong checkout
```

## Where the config is read from

The wiki's root folder is, in order:

1. `--root <folder>`, on any command;
2. `$WIKI_ROOT`;
3. the nearest folder upward holding a `.wiki-cli.toml`;
4. the enclosing git repository, else the current folder. Commands say which folder they
   chose. The home folder and drive roots are refused.

The config is `<root>/.wiki-cli.toml`, unless `$WIKI_CONFIG` names another file.

**Redirect.** A `.wiki-cli.toml` holding `wiki = "<path>"` sends every command run
under it to the wiki at that path (relative to the file). A code repo uses this to point
at its wiki. The only other key it may hold is `agents`, the agents working in the code
repo; the target must have its own `.wiki-cli.toml`, and that one must not redirect again.

## Overrides outside the file

| Setting | Command-line flag | Environment variable | Default |
|---|---|---|---|
| Wiki root | `--root` | `WIKI_ROOT` | see above |
| Config file | | `WIKI_CONFIG` | `<root>/.wiki-cli.toml` |
| Search index | `--cache` | `WIKI_CACHE` | `<root>/.cache/wiki.sqlite3` |
| Model folder | | `WIKI_MODELS_DIR` | `~/.cache/wiki-cli/models` |
| Embedding model | `--embed-model` | `WIKI_EMBED_MODEL` | `embed_model`, below |
| Reranker | `--reranker` | `WIKI_RERANKER` | `reranker`, below |
| Code repo | | `WIKI_CODE_REPO` | `[code] repo`, below |

A flag beats the environment variable, which beats the config file.

## Top-level keys

| Key | Type | Default | What it does |
|---|---|---|---|
| `preset` | string | none | The preset `wiki new` used. Picks the preset's variant of each guide (`code` also turns on the guides' code steps). |
| `pages` | list of globs | `["**/*.md"]` | Which Markdown files are pages. |
| `exclude` | list of globs | `[]` | Files that are never pages or sources, even if `pages` or `raw` match them. |
| `raw` | list of globs | `["raw/**/*.txt"]` | Source text that is indexed but searched only with `--include-raw`, and listed by `wiki pending` until a page links it. |
| `embed_model` | string | `"BAAI/bge-small-en-v1.5"` | The embedding model for search. |
| `reranker` | string | `"jinaai/jina-reranker-v1-turbo-en"` | The reranker for search, or `"none"` to skip reranking. See [model-selection.md](model-selection.md). |
| `agents` | list of strings | `[]` | The coding agents that work in this config's folder: `claude-code` (also `claude`), `copilot`, `opencode`. In a redirect it names the agents working in the code repo, and a wiki's own list is not used there. |

Globs are matched against paths relative to the root, with forward slashes; `**` matches
any number of folders. Hidden files and folders (a name starting with `.`), and
`node_modules/` and `__pycache__/`, are never scanned.

## `[types.<name>]`: page types

One table per page type. Declaring any type turns on type checks. With none declared,
any `type:` is fine.

```toml
[types.ticket]
description = "A tracker ticket (a Jira epic, story, bug or task)."
folder = "wiki/tickets"
template = "templates/ticket.md"
sections = ["Summary"]
fields = ["title", "type", "kind", "last_updated"]
values = { kind = ["epic", "story", "bug", "task"] }
```

| Key | Type | Default | What it does |
|---|---|---|---|
| `description` | string | `""` | What pages of this type are. The guides show it to the agent, which picks a page's type from it, so make the boundaries between types sharp. |
| `folder` | string | none | Pages in this folder get this type when their frontmatter has no `type:`. The guides tell the agent to put new pages here. |
| `template` | string | none | The file new pages of this type start from, relative to the root. The guides name it, and the `wiki check` warnings below point to it. |
| `sections` | list of strings | `[]` | `##` headings every page of the type has. A page missing one gets a `missing-section` warning. Case is ignored. |
| `fields` | list of strings | `[]` | Frontmatter keys every page of the type has. A page missing one gets a `missing-field` warning. |
| `hub` | boolean | `false` | Each page of the type stands for an idea other pages gather around (a module, a concept, an event). `wiki clusters` counts a cluster most of whose pages link to one as covered, and lists only the others. A cluster no one hub covers is split once at a higher resolution; if each part then has its own hub, it is *covered in parts* (several linked subjects, each with its page), and if not, the report names the part that has none. With no hub types, it lists every cluster. `wiki check` warns when one hub page covers two or more clusters (`hub-covers-clusters`): usually several subjects on one page, worth splitting. |
| `hub_through` | list of relation names | `[]` | With `hub = true`: a page also counts toward this type's pages' clusters when it reaches one through a chain of these relations, up to three steps, not only when it links one directly. Each step goes toward the hub. A relation's `type` follows its links as written, from the linking page to the linked one: the code preset sets `hub_through = ["child-of", "implements"]` on `epic`, so a pull request that implements a story that is a child of the epic reaches it. A relation's `inverse` follows its links reversed, for hubs whose links point outward: in a wiki of medical studies a trial lists its papers (`trial -reported-in-> paper`, inverse `reports`) and findings list theirs (`finding -drawn-from-> paper`), so `hub_through = ["reports", "drawn-from"]` on `trial` lets a paper reach the trial it reports, and a finding reach it through its paper. A symmetric relation (its `type` and `inverse` the same, such as `associated-with`) is followed both ways. Each name must be a `type` or `inverse` a `[[relations]]` rule defines; plain links cannot be listed. |
| `record` | boolean | `false` | Each page of the type describes an item that lives and changes in another system (a ticket in a tracker). It names it by `key:` and `url:` rather than a file in `raw/`, and records in `synced:` the tracker's last-updated time it reflects; `wiki stale` lists those due for a recheck. See `[records]`. |
| `no_links_with` | list of type names | `[]` | Types whose pages should not link pages of this type, or be linked by them: either direction joins them in the relations graph. `wiki check` warns (`link-not-allowed`) on the page holding the link, in prose or a list. The code preset sets `no_links_with = ["file"]` on `epic`: a file page links its ticket, and an epic the module, not each other. Names must be declared types. |
| `values` | table of lists of strings | `{}` | The only values a frontmatter field may hold, per field. A value not listed gets a `bad-value` warning (case ignored; for a list, each item is checked). An absent field is not flagged here; list it in `fields` for that. |

A page's type is its `type:` frontmatter (the key is `[page_type] field`), lowercased;
else the type of the longest `folder` its path starts with. A page whose `type:` is not
declared gets an `unknown-type` warning.

List only what *every* page of a type must have in `sections` and `fields`: agents delete
template sections they have nothing for, and the rest of the template is a suggestion.
The warnings are warnings: `wiki check` still passes unless run with `--strict`. The
ingest guide and `wiki check --ingested` require the agent to fix them on the pages it
touched.

## `[[relations]]`: typed links

Each rule gives some links a relation type, shown from the linking page, and an inverse
label, shown from the page linked to. A rule takes exactly one of `heading` or `field`.

```toml
[[relations]]
heading = "Implements"          # links in list items under this heading
page_type = "pr"                # only on pages of this type
type = "implements"
inverse = "implemented-by"

[[relations]]
field = "parent"                # links in this frontmatter field
page_type = "ticket"
type = "child-of"
inverse = "parent-of"
```

| Key | Type | Default | What it does |
|---|---|---|---|
| `heading` | string or list of strings | none | Links in list items under this heading (case ignored) get the type. The text after the link and dash becomes the relation's reason. |
| `field` | string | none | Links in this frontmatter field (slugs or `[[links]]`) get the type. |
| `page_type` | string or list of strings | any type | Apply the rule only on pages of these types. |
| `type` | string | required | The relation, as lowercase words joined by hyphens. Not one of the built-ins below. |
| `inverse` | string | required | The label shown from the target page, same format. One type always has the same inverse. |
| `reason` | string | the sentence around the link | A fixed reason for every link the rule matches (for `field` rules, where there is no sentence). |

Built in, without rules: `links-to` (any other link), `embeds` (a `![[page#^block]]`
embed) and `refers-to-code` (a `[name](code:path)` link in a code wiki). When a page reaches
one target several ways, the most specific type wins: heading-rule types in file order,
then `embeds`, then field-only types, then `links-to`. `wiki vocab` lists the wiki's
types.

**An example.** With this rule:

```toml
[[relations]]
heading = "Entities mentioned"
page_type = "source"
type = "mentions"
inverse = "mentioned-in"
```

the list item

```markdown
## Entities mentioned
- [[charles-babbage]] — Inventor who delayed the demonstration (p. 2)
```

becomes `mentions -> charles-babbage` with reason "Inventor who delayed the
demonstration (p. 2)". From `charles-babbage`'s page, the same relation shows as
`mentioned-in`. A link in parentheses under a typed heading,
such as a citation `([[report]], p. 2)`, stays a plain link: it supports the line rather
than being its subject. Frontmatter values written as `[[links]]` count as links, as in
Obsidian. After changing the rules, the next command re-derives every relation without
re-embedding anything.

## `[summary]`: where a page's summary comes from

Search results and navigation show each page's summary.

| Key | Type | Default | What it does |
|---|---|---|---|
| `fields` | list of strings | `["summary", "description"]` | Frontmatter keys tried first, in order. |
| `headings` | list of strings | `["Summary"]` | Then the first paragraph under the first of these headings (case ignored). |

With neither, the summary is the page's first paragraph.

## `[page_type]`

| Key | Type | Default | What it does |
|---|---|---|---|
| `field` | string | `"type"` | The frontmatter key holding a page's type. |
| `folders` | table of strings | `{}` | Folder to type, for wikis without `[types]`: `folders = { "wiki/people" = "person" }`. A type's own `folder` does the same and wins. |

## `[check]`: extra `wiki check` rules

| Key | Type | Default | What it does |
|---|---|---|---|
| `require_frontmatter` | boolean | `false` | Every page must have YAML frontmatter with a `title` and a type: a page without frontmatter is an error, a missing key a warning. |
| `summary_types` | list of strings | `[]` | Page types that must have a summary (a field or heading from `[summary]`), or `["*"]` for every page. Others get a `missing-summary` warning. |

## `[suggest]`

| Key | Type | Default | What it does |
|---|---|---|---|
| `named_types` | list of strings | all types | Page types `wiki suggest` looks for by name (title and `aliases:`) in a page's text. Limit it to types with distinctive names, such as people and modules. |

## `[clusters]`: groups of linked pages

Used by `wiki clusters`, the `hub-covers-clusters` check and the map's cluster colours.

| Key | Type | Default | What it does |
|---|---|---|---|
| `ignore` | list of globs | `[]` (the presets: `["wiki/open-questions.md"]`) | Pages left out of clustering. A list page that links pages from every corner of the wiki (open questions, an index) glues unrelated groups together; ignored pages stay searchable and linkable. |
| `resolution` | number above 0 | `1.0` | Louvain resolution: higher splits clusters more readily, lower merges them. `wiki clusters --resolution` tries a value without changing the config. |

## `[groups]`: the parts of a wiki its pages name

`wiki clusters --group <name>` and `wiki map --group <name>` look at one part of the wiki:
a project, a client, a course. A page belongs to a group when one of these frontmatter
fields holds the group's name, as a single value or in a list (case and a leading `#` are
ignored). Pages every group shares, such as a person or a place, rarely carry the field,
so a group also takes in every page its pages link to or are linked from, one step out.
An unknown name is an error that lists the groups the pages name. `--ungrouped` looks at
the pages that name no group instead, without taking in their neighbours.

```toml
[groups]
fields = ["projects", "project"]   # source pages say projects: [a, b]; notes say project: a
```

| Key | Type | Default | What it does |
|---|---|---|---|
| `fields` | list of strings | `["tags"]` | Frontmatter fields that name the groups a page belongs to. |
| `exclusive` | true or false | `false` | The groups are separate worlds (the writing preset's books): `wiki check` warns when a page links a page whose groups share none of its own (`cross-group-link`). A page in no group may link, and be linked by, anything. |

## `[originals]`: the user's own writing

The writing preset's ideas, scenes and chapters: files the user writes, which the agent
reads and never changes. The folder counts as a raw folder (`wiki pending` lists its
files, pages link them, `wiki search --include-raw` searches them, and `wiki new --agent
claude` writes a rule denying edits there), but the user edits its files, so the
pre-commit hook lets them change, and `wiki commit` never commits them. An original's
book is the first folder inside the originals folder; the `loose` folder holds pieces
with no book.

```toml
[originals]
folder = "originals"
loose = "loose"
kinds = ["idea", "scene", "chapter"]
fields = ["kind", "created", "last_updated"]
```

| Key | Type | Default | What it does |
|---|---|---|---|
| `folder` | folder name | `"originals"` | Where the originals are. Its `**/*.md` is added to `raw`. |
| `loose` | folder name | `"loose"` | The folder inside it for pieces that belong to no book. |
| `kinds` | list of strings | `["idea", "scene", "chapter"]` | The values `kind:` may take; `wiki check --all` warns on another (`original-bad-kind`). |
| `fields` | list of strings | `["kind", "created", "last_updated"]` | The frontmatter every original has, filled in by the user's Obsidian templates; `wiki check --all` warns on an original missing one (`original-missing-field`). The ones other than `kind` are dates: the hash ingest records leaves them out. |

**Revised originals.** An original's page records `original:` (a link to it) and
`original_hash:` (from `wiki pending --hash <original>`) when it is ingested. `wiki pending`
lists the original again, marked changed, once its hash no longer matches. The hash leaves
out the date lines and line-ending differences, so a timestamp bump alone is no change.
`wiki check` warns on a page whose `original:` names a file that is gone
(`missing-original`).

## `[search]`

| Key | Type | Default | What it does |
|---|---|---|---|
| `results` | whole number, 1 to 20 | `3` | Pages returned by `wiki search`, `wiki nav start` and `wiki nav search`. |

## `[pending]`

| Key | Type | Default | What it does |
|---|---|---|---|
| `ignore` | list of globs | `[]` | Files matched by `raw` that are not sources, which `wiki pending` skips (a manifest, say). `README.md` and `README.txt` are always skipped. |

## `[weekly]`: weekly notes

With this table, `wiki weekly` writes a note for each finished week of work on the wiki,
and a timeline across the weeks, from the git history
([workflows.md](workflows.md#weekly-review)). Without it, the
command refuses and the pre-commit hook skips it. The `code` preset turns it on.

| Key | Type | Default | What it does |
|---|---|---|---|
| `folder` | string | `"weekly"` | Where notes and `timeline.md` go, relative to the root. This folder is never scanned: notes are not pages, not searched and not in the relations graph. |
| `template` | string | `"templates/weekly.md"` | The file a new note starts from. `{{week}}`, `{{start}}`, `{{end}}` and `{{dates}}` are filled in. The generated part goes between `<!-- wiki:weekly start -->` and `<!-- wiki:weekly end -->` (under the title if the template has no markers). Without the file, a built-in template is used. |
| `sections` | list of strings | all | Generated sections, in order: `summary`, `activity`, `pages`, `work`, `sources`, `questions`, `health`, `code` (`code` only with `[code] repo`). |
| `group_by` | `"type"` or `"module"` | `"type"` | What "where the work went" groups changed pages by. `module`: a module page itself, or the modules a page reaches through typed relations (up to two steps, such as a gotcha affecting a file that is part of a module). |

## `[records]`: items that live elsewhere

For page types marked `record = true` (see [guides.md](guides.md#records-tickets-and-other-items-that-live-elsewhere)).

| Key | Type | Default | What it does |
|---|---|---|---|
| `recheck_days` | whole number, 1 or more | `30` | A record page synced longer ago than this is due for a recheck in `wiki stale`. |
| `final` | list of strings | done, closed, resolved, merged, released, cancelled, canceled, rejected, won't do, wont do, won't fix, wontfix, duplicate | `status:` values (case ignored) of records that no longer change: `wiki stale` does not ask to recheck them once synced. |

## `[guides]`

| Key | Type | Default | What it does |
|---|---|---|---|
| `dir` | string | none | A folder of the wiki's own guides, relative to the root. A file named like a built-in guide (`ingest.md`) replaces it; any other name adds a workflow. See [guides.md](guides.md). |
| `parts` | string | `guides/parts`, or `<dir>/parts` with `dir` set | A folder of parts: `<name>.md` fills the guides' `{{part:<name>}}` slot with the wiki's own text for that step. Never indexed as pages. `wiki guide --parts` lists the slots. |

## `[hooks]`: the wiki's own pre-commit checks

The pre-commit hook (`wiki new --git-hook`) runs `wiki precommit`: sources in the raw
folders unchanged (README files and `[pending] ignore` files may change), last week's
note with `[weekly]`, and no `wiki check --all` errors. A wiki adds its own checks here.

```toml
[hooks]
pre_commit = [
  "python tools/check_quotes.py",                                   # every commit
  { run = "python tools/format.py --check", files = ["drafts/*.md"] },  # only when these change
]
```

| Key | Type | Default | What it does |
|---|---|---|---|
| `pre_commit` | list of commands | `[]` | Each runs with the system shell from the wiki root; one that exits non-zero blocks the commit, and its output is shown. An entry is a command, or a table with `run` and `files`: a list of globs, so the command runs only when a staged file (added or changed) matches them, with those files appended as arguments. |

## `[code]`: code wikis

| Key | Type | Default | What it does |
|---|---|---|---|
| `repo` | string | none | The code repo the wiki describes, relative to the wiki. `$WIKI_CODE_REPO` overrides it on a machine where the code is elsewhere. Turns on `code:` links, `wiki stale` and the guides' code steps. |
| `origin` | string | none | The code repo's remote URL, to catch `repo` pointing at the wrong checkout: `wiki stale` refuses to run, and `wiki check` warns instead of checking code links. |

No other keys are allowed in `[code]`.
