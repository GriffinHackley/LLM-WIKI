# Changelog

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
