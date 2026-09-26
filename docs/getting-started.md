# Getting started

This guide takes an existing folder of Markdown notes (an Obsidian vault, a `docs/`
folder, an LLM-maintained wiki) from nothing to searchable and navigable by an agent. The
tool never edits your pages: everything it builds lives in a disposable cache inside the
repo, so trying it is safe.

**The short version:**

```bash
uv tool install --editable ./LLM-WIKI    # once per machine
wiki models download                      # once per machine, about 0.2 GB
cd my-wiki
wiki init --write                         # draft .wiki-cli.toml, then review it
echo ".cache/" >> .gitignore
wiki index refresh                        # index and embed
wiki search "a question your wiki answers"
```

Then [connect your agent](#5-connect-claude-code). The rest of this page explains each
step and what to look for.

## 1. Requirements

- [uv](https://docs.astral.sh/uv/getting-started/installation/). It installs Python 3.13
  for the tool if you don't have it.
- Git access to the LLM-WIKI repository.
- About 0.2 GB of disk for the two models, plus a cache in each wiki of roughly 40 KB per
  page.
- No GPU. Everything runs locally on the CPU; nothing is sent to a server.

The default models are English-only. The tool has been tested on Windows 11; it should
work on macOS and Linux too, but hasn't been tried there yet.

## 2. Install the tool (once per machine)

```bash
git clone https://github.com/GriffinHackley/LLM-WIKI.git
uv tool install --editable ./LLM-WIKI
wiki --version
```

`--editable` means a later `git pull` in `LLM-WIKI` updates the command with no
reinstall. `uv tool install` puts `wiki` on your PATH; if the shell can't find it, run
`uv tool update-shell` and open a new terminal.

Then download the models:

```bash
wiki models download
```

This fetches the embedding model (`BAAI/bge-small-en-v1.5`) and the reranker
(`jinaai/jina-reranker-v1-turbo-en`) into `~/.cache/wiki-cli/models`, shared by every
wiki on the machine. It is the only command that ever downloads anything; the others load
models from that folder or, if they are missing, fall back to keyword search and say so.

## 3. Set up a wiki

### Run it from inside the wiki

`wiki` finds the wiki it is working on the same way git finds a repo: it walks up from the
current folder to the nearest `.wiki-cli.toml`. Without one it uses the enclosing git
repository, else the current folder, and prints a note saying which folder it chose. It
refuses to treat your home folder or a drive root as a wiki. `--root <folder>` overrides
all of this.

It works with no config at all: every `*.md` file becomes a page, `[[wikilinks]]` and
`[text](path.md)` links are followed, and `raw/**/*.txt` (if you have a `raw/` folder) is
searchable source text. You can search right away:

```bash
cd my-wiki
wiki search "how are jobs prioritised so none wait forever?"
```

```
note: no .wiki-cli.toml found; using the git repository ...\my-wiki as the wiki root
note: vector search skipped: no embeddings yet; run 'wiki index refresh'
1. scheduler  (-0.372)
   Decides the order card stacks run in, using a priority queue with aging so low-priority jobs are never starved.
```

### Draft a config with `wiki init`

A config makes results better by telling the tool which files are pages, where summaries
live, and what your headings mean. `wiki init` surveys the repo and drafts one:

```bash
wiki init            # print the draft
wiki init --write    # save it as .wiki-cli.toml (never overwrites an existing file)
```

Open `.wiki-cli.toml` and review four things:

1. **Which files are pages.** If most pages live in one folder, the draft proposes only
   that folder and lists what it leaves out:

   ```toml
   # Most pages are under notes/. Indexing only that folder leaves out: README.md.
   # Delete this line to index every *.md file instead.
   pages = ["notes/**/*.md"]
   ```

   Keep it if the left-out files aren't wiki content (a README, contributor docs).
   Add `exclude` globs for anything else that isn't a page. When there is a `templates/`
   folder, the draft already excludes it.

2. **Summaries.** Search results and navigation show each page's summary, taken from the
   first of: a frontmatter field (`summary`, `description`), a heading (`## Summary`), or
   the page's first paragraph. Adjust `[summary]` if your pages use other names, such as
   `headings = ["Overview", "TL;DR"]`.

3. **Page types.** If pages have a frontmatter field like `type: person`, the draft finds
   it. If your types are folders instead, map them:

   ```toml
   [page_type.folders]
   "notes/people" = "person"
   ```

4. **Relation rules.** This is the step worth a few minutes. Every link is already a
   relation (`links-to`) whose reason is the sentence around it. A rule gives the links
   under a heading, or in a frontmatter field, a name. The draft lists headings that hold
   links on at least three pages, commented out:

   ```toml
   # Heading 'Works with': 4 links on 3 pages  # mostly on: person
   # [[relations]]
   # heading = "Works with"
   # type = "works-with"
   # inverse = "works-with-of"
   ```

   Uncomment the ones that mean something and give them good names (`inverse` is what
   the relation is called from the other page; it can be the same word). A rule turns

   ```markdown
   ## Works with
   - [[charles-babbage]] — co-lead; they split hardware and software
   ```

   into `works-with -> charles-babbage` with the reason "co-lead; they split hardware and
   software". An agent reads these reasons to decide which page to open next, so list
   items written as `[[page]] — why it matters` pay off. Rules for frontmatter fields
   (`field = "sources"`) work the same way. The [README](../README.md#relations) has
   the full rule syntax.

One more setting you may want: how many pages a search returns. The default is 3, which
keeps an agent's context small; raise it if your wiki's answers tend to span more pages.

```toml
[search]
results = 5     # wiki search, nav start and nav search; 1-20, and --limit overrides it
```

Every setting is optional, and you can change them at any time: the next command notices,
and re-derives relations without re-embedding anything (summary settings trigger a full
rebuild).

### Keep the cache out of git

```bash
echo ".cache/" >> .gitignore
```

Commit `.wiki-cli.toml`, so everyone working on the wiki, and every agent, uses the same
settings.

### Build the index

```bash
wiki index refresh
```

```
embedding 7/7 files
added: 7, changed: 0, touched: 0, removed: 0, moved: 0, unchanged: 0, embedded: 7
```

The first run embeds every page: about 2 minutes per 500 pages on a laptop CPU (long raw
text files take longer). It saves progress as it goes, so an interrupted run picks up
where it stopped. Later runs only touch files that changed and take a fraction of a
second when nothing did. [What refresh does](#what-wiki-index-refresh-does) explains the
details.

## 4. Try it

```bash
wiki search "how are jobs prioritised so none wait forever?"
```

```
1. scheduler  (-0.372)
   Decides the order card stacks run in, using a priority queue with aging so low-priority jobs are never starved.
2. ada-lovelace  § Ada Lovelace > Role  (-2.515)
   Lead engineer on the analytical-engine rebuild; owns the scheduler design.
3. 2026-09-01-planning  § Planning meeting, 2026-09-01 > Decisions  (-3.100)
   Agreed the March demo date and flagged the brass shortage.
```

A page's relations, with reasons taken from the pages themselves:

```bash
wiki neighbors ada-lovelace
```

```
-> works-with       charles-babbage  co-lead; they split hardware and software
-> works-with       grace-hopper  compiler work for the new instruction set
-> links-to         scheduler  Role: Wrote the scheduler design doc and reviews every change to the instruction set.
<- linked-from      scheduler  Design: Written by ada-lovelace.
```

A navigation session, the way an agent uses it: start from a question, read the section
that matches, see where the page leads, and record what the answer cites.

```bash
wiki nav start "Who attended the planning meeting and what did they decide?"
wiki nav read 187e6d 2026-09-01-planning --why "The meeting page should list attendees and decisions."
wiki nav candidates 187e6d
wiki nav end 187e6d --cited 2026-09-01-planning
```

Health checks for the wiki itself:

```bash
wiki check --all     # frontmatter errors, missing summaries, ambiguous and unwritten links
wiki unwritten       # link targets with no page yet, most-linked first
wiki orphans         # pages nothing links to
wiki suggest <slug>  # pages this one names but doesn't link, or resembles
```

`wiki check` exits with 1 when it finds errors, so it works in a pre-commit hook or CI.
Every command accepts `--format json` for scripts and agents.

## 5. Connect Claude Code

Three small changes in the wiki's repo let Claude Code use the tool.

**Allow the command** without a prompt for each call, in `.claude/settings.json`:

```json
{
  "permissions": {
    "allow": ["Bash(wiki:*)"]
  }
}
```

**Add the query skill.** Copy
[`integrations/claude-code/skills/wiki-query/`](../integrations/claude-code/skills/wiki-query/SKILL.md)
into the wiki's `.claude/skills/`. It tells Claude to answer questions with `wiki nav`:
start from search, read one section at a time, follow relations by their reasons, stay
within a page limit, and cite what it used. Edit it to add your wiki's own citation
rules.

**Tell Claude about it** in the wiki's `CLAUDE.md`:

```markdown
## Finding things in this wiki

Use the `wiki` command instead of reading an index or grepping:
- Answer questions with the /wiki-query skill (`wiki nav`), not by opening pages directly.
- `wiki neighbors <slug>` shows what a page links to and what links to it, with reasons.
- After adding or editing pages, run `wiki index refresh`, then `wiki check <slug>` on each
  page you touched and fix any error it reports.
- When auditing the wiki, use `wiki check --all`, `wiki unwritten` and `wiki orphans`.
```

If your agent writes new pages, add `wiki suggest <slug>` to that workflow as well. It
finds pages the new one should probably link to.

## 6. Day to day

- **After editing pages, run `wiki index refresh`.** Search and navigation update their
  keyword index automatically before every search, so edits are findable by keyword
  immediately; vector search sees them after the next refresh.
- **After changing `.wiki-cli.toml`,** nothing extra: the next command picks it up.
- **Updating the tool:** `git pull` in `LLM-WIKI`. If an update changes the cache format,
  the next command rebuilds the cache by itself.
- **If anything looks wrong,** `wiki index rebuild` recreates the cache from the files.
  Deleting `.cache/` does the same.

### What `wiki index refresh` does

1. Checks the cache still fits the settings (rebuilding it or re-deriving relations if not).
2. Walks the folder and skips files whose modified time and size are unchanged, without
   reading them.
3. Re-parses changed files: title, type, summary, sections for search, and relations.
4. Removes pages whose files are gone, and re-checks links that pointed at missing pages.
5. Embeds the sections of every changed file (skip with `--no-embed`).

## 7. Measure search quality (optional)

To check whether search finds the right pages in *your* wiki, or to compare models,
write a small set of questions with known answers and score them.
[docs/evaluation.md](evaluation.md) covers `wiki eval sample` (which picks pages to write
questions about) and `wiki eval run`.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `...is not a wiki folder; cd into the wiki, pass --root, or add a config` | You ran `wiki` from your home folder or a drive root. `cd` into the wiki first. |
| `note: no .wiki-cli.toml found; using ... as the wiki root` | No config yet. Check the folder is the one you meant, then run `wiki init --write`. |
| `vector search skipped: no embeddings yet` | Run `wiki index refresh`. |
| `N files are not embedded yet` | Pages changed since the last refresh; run `wiki index refresh`. |
| `embedding model ... is not downloaded` | Run `wiki models download`. |
| Search returns files that aren't wiki pages | Narrow `pages` or add `exclude` globs in `.wiki-cli.toml`. |
| `ambiguous-link` warnings | Two files share a name, so `[[name]]` could mean either. Link with the path (`[[people/name]]`), as Obsidian does. |
| Every relation is `links-to` | Add `[[relations]]` rules; `wiki init` suggests them from your headings. |
| The first search after a reboot takes a few seconds | The model files are read from disk the first time; later searches take about 1 second. |
