# Changelog

## Unreleased

## 0.6.0 (2026-10-16): clusters on the map

- `wiki map` can open a link cluster. The panel lists the clusters, with how many of
  their pages sit away from the rest and how many links leave them; clicking one turns
  the camera to look across the cluster's main group of pages, dims other clusters' points
  (or hides them and the links that leave the cluster), labels the most-linked pages,
  outlines the group, and points to its far-off pages from the edge of the screen. The
  panel then shows the cluster's pages (most linked first), its far-off pages and the
  clusters it links to, with a picker to switch clusters. Closing it keeps the camera
  where it is; Reset view goes back to the whole map. The selected page's details stay
  at the top of the panel, and clicking the question flies to it. Clusters come from
  links and positions from embeddings, so a cluster can be spread across the map; the
  points never move.
- `wiki map` draws its lines: relations, the lines from a question to its search
  results, and navigation paths. Since the map's first release every line was drawn as a
  stub at the middle of the scene, with one tooltip there that never moved.
- Every `wiki` command, and every agent hook call, starts faster: numpy is loaded only by
  `wiki map`, not by every command (about 180 ms instead of 300 to start).

## 0.5.0 (2026-10-12): a faster map

- `wiki map` lays pages out with t-SNE (scikit-learn) instead of UMAP. It keeps pages'
  neighbourhoods as well, and on a 527-page wiki a changed map takes about 2 seconds
  instead of 11, and placing a question about 1 instead of 11. Its settings are tuned so
  small wikis aren't scrambled. Chunk maps (`--chunks`) keep UMAP, which scales to
  thousands of points. `--method` takes `tsne` as well as `umap` and `pca`. A question on
  a page map is placed at the average of its nearest pages, since t-SNE cannot place new
  points; search is unaffected. The first map after upgrading is redrawn once, and looks
  different.
- `wiki map --chunks` computes each chunk's exact nearest neighbours for UMAP, up to 20,000
  chunks, instead of UMAP's approximate search, whose compiling took most of a refit: on
  4,161 chunks a changed map takes about 12 seconds instead of 21, and placing a question
  on a drawn map about 1 instead of 11, at the average of its nearest chunks.
- `wiki map` on an unchanged wiki takes well under a second instead of several: it no
  longer imports UMAP (seconds of numba compiling) to draw a cached layout, only to fit
  a new one or place a question.

## 0.4.0 (2026-10-12): plan before the work

- `wiki guide plan` (code wikis, `/wiki-plan` after `wiki connect`): plan a task without
  doing it. The agent splits the task into parts and finds the pages on each with the
  wiki's search and the pages' relations, looking in the code only for a file or
  function the task names. For a part with no page it asks before looking in the code,
  and recommends documenting the files it finds as part of the task. It runs
  `wiki covering` on the pages and files the task touches, so every open gotcha and
  decision affecting them is read before the plan is written. The plan starts with the
  parts and their pages, and says how it respects each gotcha and decision. Then it
  stops, without opening a task or changing anything. Re-run `wiki connect` to add the
  command to a connected code repo.
- `wiki guide work` now runs `wiki guide plan` as its first step, and in the same session
  uses a plan already approved that way.
- The agent hooks in a connected code repo now show the gotchas and decisions that affect
  a wiki page the first time the agent reads it, as they already did for a code file.
  `wiki covering <wiki page>` prints the same, and takes a page's slug as well as its path.
- Guides: the opening note now forbids only re-running the same guide (a guide may name
  another), and the line on the wiki's own rules says plainly that the rules win over the
  guide where they conflict.

## 0.3.1 (2026-10-11)

- The code preset's `epic`, `pr` and `ticket` templates and the research preset's
  `source` template now quote their placeholder titles: unquoted, the `: ` in them made
  the frontmatter invalid YAML.
- Docs reorganised: a shorter README, and new pages for concepts, every command, and
  troubleshooting, with a docs index. All examples are invented.

## 0.3.0 (2026-10-11): first release on PyPI

The first release as `llm-wiki-kit` (the command is `wiki`), with everything from 0.2.0,
which was never published. Earlier installs also provide `wiki` under another name,
`wiki-cli` or (from git or a clone) `llm-wiki-cli`: `uv tool uninstall` it first.

**Install**
- On a Python whose sqlite3 cannot load extensions (the python.org macOS builds), `wiki`
  now says so and names the fix, `uv tool install --managed-python --reinstall
  llm-wiki-kit`, instead of failing with an `AttributeError`.

**The `writing` preset**
- `wiki new <folder> --preset writing`: a wiki for stories. The user writes ideas, scenes
  and chapters in Obsidian in `originals/` (a folder per book, `originals/loose/` for
  pieces in no book), and the agent keeps a wiki of the stories built from them. Sixteen
  page types: pages for originals (`idea`, `scene`, `chapter`); the world (`character`,
  `location`, `organization`, `item`, `lore`, `event`); the story's shape (`plotline`,
  `thread`, `theme`); and `decision`, `style`, `source`, `analysis`. Templates for each,
  Templater templates for originals in `templates/originals/`, and writing variants of
  the `ingest`, `query` and `lint` guides (lint adds continuity checks). Every page has
  `created:` and `last_updated:`.
- `[originals]` in `.wiki-cli.toml`: the user's writing, read by the agent and never
  changed. Its folder counts as a raw folder (`wiki pending`, links, `--include-raw`, the
  Claude deny rule), but the pre-commit hook lets its files change. `wiki check --all`
  warns on an original missing its template's fields (`original-missing-field`) or with an
  unknown kind (`original-bad-kind`).
- `wiki pending` lists originals revised since they were ingested, marked changed: an
  original's page records `original_hash:` (`wiki pending --hash <original>` prints it),
  and the hash leaves out the date lines, so a timestamp bump alone is no change.
  `wiki check` warns on a page whose `original:` is gone (`missing-original`).
- `wiki commit "<message>"` commits every change in the wiki except the originals folder,
  waiting while another git process (Obsidian Git on a timer) holds the index lock. It
  reports a failing pre-commit hook, and defers to `wiki finish` while a code task is open.
- `wiki clusters --ungrouped` and `wiki map --ungrouped`: the pages that name no group.
  `[groups] exclusive = true` makes groups separate worlds: `wiki check` warns on a link
  between pages whose groups share none (`cross-group-link`).
- `wiki hook` now works in any wiki whose config names the agent, code repo or not: it
  refuses an edit to an original, or to a source already in a raw folder (a new source
  may be saved). Without a code repo that is all it does. `wiki new --agent copilot`
  writes Copilot's hook file for a wiki with originals.

**Agent hooks**
- `wiki hook <event> --agent <name>` is the command an agent's hook files run. For an
  agent the code repo's `agents` names, it:
  - at session start, tells the agent where the wiki is, to work through
    `wiki guide work`, never to commit, and which decisions wait for approval
    (`status: proposed`); and notes the files already changed, for the end-of-task commit;
  - shows `wiki covering`'s notes the first time a session touches a file: Claude Code
    before an edit and after a read, Copilot (whose pre-tool hooks cannot show text) after
    either;
  - records the files each session edits, in `.cache/sessions/`;
  - denies `git commit` in the shell and asks the user before `wiki finish`.
  It always exits 0 and prints nothing but its reply (Copilot denies a tool call when its
  pre-tool hook fails), does nothing in a repo with no wiki, and ignores a payload in
  another agent's format (Copilot also runs Claude Code's hook file). `--log` records each
  call in `<wiki>/.cache/hook-log.jsonl`, with long text cut short, to learn an agent's
  real tool names and fields.
- `agents = [...]` in `.wiki-cli.toml` names the coding agents that work in its folder. A
  code repo's redirect may hold it beside `wiki`, for the agents working on the code; the
  wiki's own list is then not used. The tool knows each agent's hook file, event and tool
  names, and how to read its hook payloads and write replies, in one table (`agents.py`).
- Agents are named by hook format: `claude-code` (`claude` still works), `copilot` (VS
  Code's Copilot agent and the Copilot CLI) and `opencode`. An unknown agent in
  `wiki new --agent` now says `no agent '<name>'`.
- `wiki covering <path>...` shows a code file's notes before an agent works on it: the
  pages whose `covers:` match it, most specific first, each marked when the file changed
  since the page was checked, and the gotchas and decisions whose `## Affects` names those
  pages (decisions with their status; superseded, rejected and withdrawn ones left out).
  Paths are matched without regard to case, absolute or relative. `--session <id>` shows
  each file once per agent session. It reads only the cache, which now keeps each page's
  `covers:`, `verified:` and `status:` (an existing cache gains them on its next refresh,
  without a rebuild), so it takes well under a second.
- `wiki guide work` (code wikis): one task on the code, from the code repo. Learn the area
  from the wiki, plan, and stop for the user's approval; `wiki task start`; record each
  settled choice as a decision page with `status: proposed` and stop for its approval;
  do and test the work; bring the pages it touched up to date with `verified: "pending"`;
  stop for approval of the work; then `wiki finish`. It is not written as a slash command
  into the wiki's own folder, since it runs from the code repo.
- `wiki check` warns (`decision-proposed`) on a decision page still waiting for approval.
- One task at a time on the code. `wiki task start "<title>"` opens a task, held by the
  agent session that ran it (its hook names the session; `--session` for a person);
  `wiki task takeover` moves it to another session, after a restart; `wiki task cancel`
  closes it, committing nothing; `wiki task status` shows it. With no task open, or one
  held by another session, the hooks refuse edits to files in the code repo and the wiki.
  Starting, taking over and cancelling ask the user first.
- `wiki finish -m "<message>"` ends the task: it commits exactly the files the task edited
  in the code repo, sets `verified:` on each page the task checked (`"pending"`) to that
  commit, and commits the wiki with the same message plus `Code: <commit>`. Other changes
  in the code repo are left out and listed (`--include <path>` adds one). It refuses
  while a page covering a changed file is unchecked, or a changed page has errors;
  `--dry-run` shows the plan. The hook asks the user before it runs, showing the files.
- `wiki stale` and `wiki check` accept `verified: "pending"`, and `covers:` match new files
  not yet committed.
- `wiki connect <code-repo> --agent <name>` sets up a code repo for its agents in two
  steps. It writes the files the wiki owns, and rewrites them when the tool's version
  changed: the pointer `.wiki-cli.toml` with `agents`, `WIKI.md` (how to work with the
  wiki), the workflow skills, and for Copilot `.github/hooks/wiki.json` and
  `.github/instructions/wiki.instructions.md`. It prints, and never writes, the lines
  your own files need: `AGENTS.md` sending agents to `WIKI.md`, `@AGENTS.md` in
  `CLAUDE.md`, and the hooks and permissions to merge into `.claude/settings.json`.
  `--check` writes nothing and exits 1 while anything is missing or out of date.
- `wiki new --agent` saves the agents in the new wiki's config (`agents = [...]`).

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
  clusters: a trial lists its papers (`reported-in`, inverse `reports`), so
  `hub_through = ["reports", "drawn-from"]` on `trial` lets the findings drawn from those
  papers reach the trial. Type names follow links as before, and a symmetric relation
  is followed both ways.
- `[clusters]`: `ignore` leaves pages out of clustering (the presets ignore
  `wiki/open-questions.md`, which links pages from every corner of the wiki and glued
  unrelated groups together), and `resolution` sets how readily Louvain splits clusters;
  `wiki clusters --resolution` tries a value without changing the config.

## 0.2.0 (not published)

Prepared as the first PyPI release; its changes first shipped in 0.3.0.

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
