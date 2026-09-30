# Presets

A preset is the set of starting files `wiki new` copies into a wiki: instructions for the
agent, page templates, and a config whose page types and relation rules match the
templates. Once copied, they are the wiki's own files, to edit freely. The preset's name
is recorded in the config (`preset = "research"`), which picks the guide variants the
wiki uses.

```bash
wiki new <folder> --preset research                 # the default
wiki new <folder> --preset code --code <code repo>
wiki new <folder> --preset <path to a preset folder>
```

## `research`

For a wiki built from sources: news, politics, history, papers, a hobby, any topic you
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
`…signed in March ([[senate-report-2024]], p. 4)`. The relation takes the citing
sentence as its reason. `wiki/open-questions.md` collects contradictions and gaps.

Guides: `ingest`, `query`, `lint`.

## `code`

For a wiki about a codebase. The wiki is its own repository beside the code, and the
agent that changes the code keeps it current, working from the code repo.

| Type | What it is | Link sections |
|---|---|---|
| `file` | One source file: what it is for and what it contains, mirroring the code's layout | `## Part of` -> `part-of` |
| `module` | One area of the code: what it does, where it lives, how it fits | `## Depends on` -> `depends-on` |
| `concept` | An idea from the code: a domain term, pattern, interface or piece of configuration | |
| `decision` | Why something is the way it is (like an ADR); `status:` is proposed, accepted, rejected, superseded or deprecated | `## Affects` -> `decided-for` |
| `instruction` | How to do a specific task: set up a dev environment, run, test, deploy, backport | |
| `ticket` | A tracker ticket; `kind:` is epic, story, bug or task (`wiki check` warns on others) | `parent:` -> `child-of` |
| `pr` | A pull request: its description, the changes, and `merge_commit:` | `## Implements` -> `implements` |
| `dependency` | An external library or service: why and how the code uses it | |
| `gotcha` | A trap in the code: symptom, cause, and how to avoid it | `## Affects` -> `gotcha-for` |
| `analysis` | The wiki's own synthesis, answering a question across pages | `## Key pages` -> `synthesizes` |

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

`--code` is required: the top folder of the code's git repository, outside the wiki. The
config records it:

```toml
[code]
repo = "../my-app"                              # relative to the wiki
origin = "https://github.com/me/my-app.git"     # catches a pointer to the wrong checkout
```

`WIKI_CODE_REPO` overrides the path on a machine where the code is checked out
elsewhere. `wiki new` prints, and never writes, what the code repo needs: a
`.wiki-cli.toml` redirect (`wiki = "../my-app-wiki"`), an `AGENTS.md` section, and with
`--agent claude` its Claude settings. One code repo per wiki for now.

Guides: `ingest` (document code), `sync` (after code changes), `query` (falls back to
reading the code), `lint` (adds staleness and coverage).

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

`wiki new .` in a folder that already has its own `.wiki-cli.toml` (from `wiki init`, or
written by hand) adds only what connects agents to it: an `AGENTS.md` with the workflow
section if there are no agent instructions yet (otherwise the section to add is
printed), and with `--agent claude` the Claude Code files. It adds no templates or
pages. Existing files are never overwritten.
