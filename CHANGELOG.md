# Changelog

## Unreleased

**Agent hooks**
- `wiki hook <event> --agent <name>` is the command an agent's hook files run. For now
  `--log` only records each call in `<wiki>/.cache/hook-log.jsonl`, with long text cut
  short, to learn an agent's real tool names and fields. It always exits 0 and prints
  nothing (Copilot denies a tool call when its pre-tool hook fails), and does nothing in a
  repo with no wiki.
- `agents = [...]` in `.wiki-cli.toml` names the coding agents that work in its folder. A
  code repo's redirect may hold it beside `wiki`, for the agents working on the code; the
  wiki's own list is then not used. The tool knows each agent's hook file, event and tool
  names, and how to read its hook payloads and write replies, in one table (`agents.py`).
- Agents are named by hook format: `claude-code` (`claude` still works), `copilot` (VS
  Code's Copilot agent and the Copilot CLI) and `opencode`. An unknown agent in
  `wiki new --agent` now says `no agent '<name>'`.

**Start a wiki**
- A code wiki can be a folder inside the code's repository (`wiki new docs/wiki --preset
  code --code .`), committed with the code, as well as a repository beside it. Only the
  code repo's top folder itself is refused. Its `AGENTS.md` and the section printed for
  the code repo say the pages are committed after the code, as a commit of their own; the
  printed Claude settings deny edits to the wiki's `raw/` by its path in the repository
  and add no extra directory. `--git-hook` sets `core.hooksPath` from the top of the
  repository (`docs/wiki/.githooks`), and the hook runs from the wiki's folder.

**See the wiki**
- `wiki map` draws the cache's embeddings in 3D as one self-contained HTML page
  (`.cache/map.html`, works offline; `--open` opens it). Points are pages, or sections
  with `--chunks`, coloured by page type, link cluster, age, or how often agents read
  them; relations are drawn as lines. Hover for a page's summary, click for its path.
  Link clusters are named after the hub page that covers them, else the terms their
  pages share ("museum, archive, loan").
- `--query "<question>"` places a question in the map with lines to its search results,
  marking which are also its nearest pages by embedding. `--nav <session>|last|all`
  draws navigation sessions as paths (question, pages read, follow-up searches, pages
  cited) to step through.
- Layout by UMAP (now a dependency, about 0.3 GB with numba and scikit-learn, imported
  only by `wiki map`), cached until pages change. If UMAP cannot be imported or fails,
  the map uses PCA and says so on stderr, in `--format json` (`fallback_reason`) and in
  the page's header; `--method umap` fails instead, and `--method pca` skips UMAP.

**Keep it healthy**
- `part-of-broader-module` compares a page's `## Part of` only with pages of the type it
  names: a concept page whose `covers:` match the same files is not a more specific module.
- `wiki clusters` no longer reports a cluster that is several subjects, each with its
  hub page, as a gap. A cluster no single hub covers is split once more, at 1.5 times the
  run's resolution (2 if that does not split it). When every part has its own hub it is
  listed as *covered in parts*, with each part and its hub. When a part has none, the
  cluster stays a gap and the report names the part: `splits into ... NOT COVERED`. An
  uncovered split is not retried higher, since that only breaks the gap into fragments
  small enough for some page to cover. JSON output carries the split under `split`.
  `wiki map --color-by cluster` colours such a cluster by its parts, each named after its
  own hub (or its terms, for the part with none) instead of one unnamed mass; parts with
  the same hub share a colour.
- `wiki check` warns (`hub-covers-clusters`) when one hub page, such as a module or
  concept, covers two or more separate clusters of linked pages: usually several areas
  described on one page. The warning names each cluster and recommends a page per
  cluster with the old page kept as an overview. The lint guides say how to judge and
  split it; the ingest guide leaves it for the report.
- Code preset: modules nest. A module inside a larger one names it under `## Part of`
  (`part-of`, like a file's), so file pages link the specific module and the larger one
  stays an overview instead of the hub of every area under it. An existing code wiki
  gets the rule from `wiki init --preset code`, which prints the preset's relation rules.
- `wiki check` warns (`part-of-broader-module`) when a page's `## Part of` names a module
  while another module's `covers:` include its files more specifically. The ingest guide
  files each page under the most specific module, writing a nested module first when it
  documents three or more files of a folder only a broader module covers; the sync
  guide documents a new folder with a job of its own as a nested module instead of
  widening `covers:`.
- Code preset: file pages require a `## Part of` section (`missing-section` when a
  rewrite drops it).
- Code preset: epics are their own type, `epic`, a record and a hub: the tickets, pull
  requests and files of a feature gather around it, so `wiki clusters` counts them as
  covered. Tickets keep `kind:` story, bug or task and name their epic in `parent:`.
  Record pages are exempt from `hub-covers-clusters`. The lint guide checks that a
  finished epic's feature has a module or concept page. An existing wiki moves its epic
  pages to `type: epic` and adds the `[types.epic]` table.
- `no_links_with` on a page type lists the types whose pages should not link its pages,
  or be linked by them; `wiki check` warns (`link-not-allowed`) on the page holding the
  link. The code preset sets it on `epic` to `["file"]`: a file page links its ticket and
  an epic the module, so code pages do not gather around the epic in place of the module
  that describes them.
- Ingest: a record whose parent (a story's epic) has no page yet gets the parent's
  page written first, found by key so it is not duplicated under another slug, and
  `parent:` set to its slug. Only the parent, not its own parent or siblings.
- `hub_through` on a hub type: a page counts toward the hub's clusters when it reaches
  it through a chain of these typed relations (up to three steps), not only when it
  links it directly. The code preset sets `["child-of", "implements"]` on `epic`, so an
  epic covers the pull requests implementing its stories; `wiki clusters` says how many
  were reached that way.
- Ingest: a pull request's tickets, found by the keys in its title, description, branch
  or commits, get their pages written first when they have none (and, through the
  record-parent rule, their epic), so the pull request reaches its epic. Where a
  wiki's pull requests carry their keys is its own `pr-tickets` part
  (`guides/parts/pr-tickets.md`); the default looks in the title, description, branch
  name and commit messages.
- Ingest in a code wiki: a pull request or commits named by number, link or branch are
  part of the code, read from the repo and the forge, and never copied into `raw/`
  (the guide used to send them through `fetch-source`, which saves a copy). A pull
  request's page names it by `url:` and `merge_commit:`. How to read a description and
  its reviews is a new part, `fetch-pr` (default: the forge's CLI or API). Records are
  likewise listed before the catch-all for other sources.
- The pre-commit hook calls `wiki precommit`, which does the checking, so the checks
  update with the command. A wiki adds its own checks as `[hooks] pre_commit` commands
  in the config; one with `files` runs only when a matching file is staged, and is given
  those files. The hook no longer refuses changes to README files or `[pending] ignore`
  files in `raw/` (a manifest updated as sources arrive). An existing wiki gets the new
  hook by deleting `.githooks/pre-commit` and running `wiki new . --git-hook`.
- `--group <name>` on `wiki clusters` and `wiki map` looks at one part of the wiki: the
  pages whose frontmatter names the group, and every page they link to or are linked
  from (shared pages, such as people, rarely carry the field). `[groups] fields` says
  which fields name a page's groups (default `tags`). The map lays a group out on its
  own, cached apart from the whole wiki's layout, and names the group in its title.
- `hub_through` takes a relation's inverse name as well as its type: the inverse follows
  the relation's links reversed. Hubs whose links point outward can now cover their
  clusters: an event lists its sources (`appears-in`, inverse `features`), so
  `hub_through = ["features", "sourced-by"]` on `event` lets the claims sourced by those
  documents reach the event. Type names follow links as before, and a symmetric relation
  is followed both ways.
- `[clusters]`: `ignore` leaves pages out of clustering (the presets ignore
  `wiki/open-questions.md`, which links pages from every corner of the wiki and glued
  unrelated groups together), and `resolution` sets how readily Louvain splits clusters;
  `wiki clusters --resolution` tries a value without changing the config.

## 0.2.0: first release on PyPI

The first release as `llm-wiki-cli` (the command is `wiki`). Earlier installs named
`wiki-cli` also provide `wiki`: `uv tool uninstall wiki-cli` first.

**Start a wiki**
- `wiki new` sets up a wiki from a preset: `research` (sources in, pages out) or `code`
  (a wiki in its own repo describing a code repo, with `--code`). Page templates, an
  `AGENTS.md`, and a `.wiki-cli.toml` whose page types and relation rules match them.
- In a folder that already has pages, `wiki new .` adopts it: it drafts the config from
  a survey of the pages and adds only the agent files, leaving the pages alone.
- `wiki init` drafts a config from a survey of an existing folder; `--preset` adds a
  preset's page types, relation rules and weekly notes.
- `--agent claude|copilot|opencode` adds slash commands for the workflows, and for
  Claude Code a permission to run `wiki` and a rule against editing `raw/`.
  `--git-hook` adds a pre-commit hook.

**Workflows for agents**
- `wiki guide ingest|query|lint` (and `sync` for code wikis) prints the steps an agent
  follows, versioned with the command, with the wiki's own page types and rules filled
  in. A wiki's own guides can replace or add to them.
- Guide parts: a wiki fills a step with its own text, such as how to fetch a ticket or
  what to run before committing, in `guides/parts/<name>.md`, and the guide prints it at
  that step. `wiki guide --parts` lists the parts.

**Find and navigate**
- `wiki search`: keyword and vector search, fused and reranked, with local models
  (`wiki models download`, about 0.2 GB; nothing is sent to a server).
- Typed relations from rules in the config (links under a heading, or in a frontmatter
  field), each with a reason taken from the page. `wiki neighbors`, `wiki vocab`.
- `wiki nav`: guided traversal sessions within a page limit.
- `wiki suggest`: pages a page names but does not link, shares linked pages with
  (discounting hub pages), or resembles.

**Keep it healthy**
- `wiki check`: frontmatter, summaries, page types with required sections, fields and
  allowed values, uncited sources, ambiguous and unwritten links, stale summaries, code
  links. `wiki check <source> --ingested` checks a whole ingest.
- `wiki unwritten`, `wiki orphans`, `wiki pending` (sources in `raw/` not yet ingested),
  `wiki stale` (code wiki pages whose code changed).
- `wiki clusters`: groups of densely linked pages that no hub page (a module, a concept)
  covers, as leads for pages worth writing.
- `wiki weekly`: a note per week of work and a timeline, built from the git history.
- Records: a page type marked `record = true` (the code preset's `ticket`) holds items
  that live in another system, such as Jira tickets. Their pages name them by `key:` and
  `url:` and record in `synced:` the tracker's last-updated time; `wiki stale` lists those
  due for a recheck. The tool never contacts the tracker; the agent does.
