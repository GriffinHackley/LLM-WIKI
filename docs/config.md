# Config reference: `.wiki-cli.toml`

Every setting, its type, its default, and what checks it. Every setting is optional: with
no config at all, every `*.md` file under the wiki's folder is a page and nothing else is
checked. `wiki new` writes a config from a preset ([presets.md](presets.md) lists what each
preset declares); in a folder that already has pages, `wiki new .` and `wiki init --write`
draft one from a survey of them, and `wiki init --preset <name>` adds a preset's page
types, relation rules and weekly notes to the draft.

An unknown key, or a value of the wrong type, is an error when any command loads the
config, naming the key. Nothing is silently ignored.

## Where the config is read from

The wiki's root folder is, in order:

1. `--root <folder>`, on any command;
2. `$WIKI_ROOT`;
3. the nearest folder upward holding a `.wiki-cli.toml`;
4. the enclosing git repository, else the current folder. Commands say which folder they
   chose. The home folder and drive roots are refused.

The config is `<root>/.wiki-cli.toml`, unless `$WIKI_CONFIG` names another file.

**Redirect.** A `.wiki-cli.toml` holding only `wiki = "<path>"` sends every command run
under it to the wiki at that path (relative to the file). A code repo uses this to point
at its wiki. `wiki` must be the only key, the target must have its own
`.wiki-cli.toml`, and that one must not redirect again.

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
| `hub` | boolean | `false` | Each page of the type stands for an idea other pages gather around (a module, a concept, an event). `wiki clusters` counts a cluster most of whose pages link to one as covered, and lists only the others. With no hub types, it lists every cluster. `wiki check` warns when one hub page covers two or more clusters (`hub-covers-clusters`): usually several subjects on one page, worth splitting. |
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
types. The README's [Relations](../README.md#relations) section has a worked example.

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
and a timeline across the weeks, from the git history (see the README). Without it, the
command refuses and the pre-commit hook skips it. The `code` preset turns it on.

| Key | Type | Default | What it does |
|---|---|---|---|
| `folder` | string | `"weekly"` | Where notes and `timeline.md` go, relative to the root. This folder is never scanned: notes are not pages, not searched and not in the relations graph. |
| `template` | string | `"templates/weekly.md"` | The file a new note starts from. `{{week}}`, `{{start}}`, `{{end}}` and `{{dates}}` are filled in. The generated part goes between `<!-- wiki:weekly start -->` and `<!-- wiki:weekly end -->` (under the title if the template has no markers). Without the file, a built-in template is used. |
| `sections` | list of strings | all | Generated sections, in order: `summary`, `activity`, `pages`, `work`, `sources`, `questions`, `health`, `code` (`code` only with `[code] repo`). |
| `group_by` | `"type"` or `"module"` | `"type"` | What "where the work went" groups changed pages by. `module`: a module page itself, or the modules a page reaches through typed relations (up to two steps, such as a gotcha affecting a file that is part of a module). |

## `[records]`: items that live elsewhere

For page types marked `record = true` (see [workflows.md](workflows.md#records-tickets-and-other-items-that-live-elsewhere)).

| Key | Type | Default | What it does |
|---|---|---|---|
| `recheck_days` | whole number, 1 or more | `30` | A record page synced longer ago than this is due for a recheck in `wiki stale`. |
| `final` | list of strings | done, closed, resolved, merged, released, cancelled, canceled, rejected, won't do, wont do, won't fix, wontfix, duplicate | `status:` values (case ignored) of records that no longer change: `wiki stale` does not ask to recheck them once synced. |

## `[guides]`

| Key | Type | Default | What it does |
|---|---|---|---|
| `dir` | string | none | A folder of the wiki's own guides, relative to the root. A file named like a built-in guide (`ingest.md`) replaces it; any other name adds a workflow. See [workflows.md](workflows.md). |
| `parts` | string | `guides/parts`, or `<dir>/parts` with `dir` set | A folder of parts: `<name>.md` fills the guides' `{{part:<name>}}` slot with the wiki's own text for that step. Never indexed as pages. `wiki guide --parts` lists the slots. |

## `[code]`: code wikis

| Key | Type | Default | What it does |
|---|---|---|---|
| `repo` | string | none | The code repo the wiki describes, relative to the wiki. `$WIKI_CODE_REPO` overrides it on a machine where the code is elsewhere. Turns on `code:` links, `wiki stale` and the guides' code steps. |
| `origin` | string | none | The code repo's remote URL, to catch `repo` pointing at the wrong checkout: `wiki stale` refuses to run, and `wiki check` warns instead of checking code links. |

No other keys are allowed in `[code]`.
