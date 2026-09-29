# {{name}}: code wiki

This repository is an **LLM-maintained wiki about a codebase**. The code lives in its
own repository, at `{{code_repo}}` (relative to this wiki; `[code] repo` in
`.wiki-cli.toml`). You, the agent, keep this wiki accurate as the code changes: what each
part of the code does and why, the decisions behind it, and how to work on it. Documents
about the code (design docs, RFCs, postmortems, meeting notes) go in `raw/` and are
ingested like the code; where they disagree with the code about what it does now, the
code wins.
Knowledge compounds because it lives in files, not in chat history.

Usually the agent that changes the code also keeps the wiki, working from the code repo:
the code repo's `AGENTS.md` and its `.wiki-cli.toml` (`wiki = "<path to this wiki>"`)
point `wiki` commands here. This file is for sessions started in the wiki itself.

## Layout

```
raw/                   Documents about the code. Never edit, rename or delete anything here.
wiki/                  The wiki: one folder per page type (wiki/modules/, wiki/decisions/, ...).
wiki/open-questions.md Things the code and the wiki disagree about, and gaps worth chasing.
templates/             One template per page type. New pages start from these.
.wiki-cli.toml         Page types, relation rules, and where the code repo is.
.cache/                Search index. Disposable; never commit it.
```

## Workflows

Each workflow's steps come from the `wiki` command, so they always match the installed
version. Before starting one, run its guide and follow it:

- **Document part of the code** (a module, a pull request, a decision) **or ingest a
  document from `raw/`:** `wiki guide ingest` (`wiki pending` lists documents not ingested
  yet)
- **Update the wiki after the code changed:** `wiki guide sync`
- **Answer a question about the code from the wiki:** `wiki guide query`
- **Audit the wiki:** `wiki guide lint`

`wiki guide` lists them all. Page types, their folders and templates are listed by the
guides and declared in `.wiki-cli.toml` under `[types]`.

## Writing pages

- **One page per thing.** File names are lowercase kebab-case (`wiki/modules/search.md`).
- **Start from the template** for the page's type; fill what you know and delete the
  placeholder text.
- **Link to code** with `code:` links, paths from the code repo's top level:
  `[the resolver](code:src/app/pages.py)`. `wiki check` warns when a linked file is gone.
- **Say what each page covers.** `covers:` lists globs of the files a page describes, and
  `verified:` the code commit (quoted) it was last checked against. `wiki stale` lists the
  pages whose covered files changed since.
- **Explain, don't transcribe.** The code says what; the wiki says why, how the parts fit,
  and what is easy to get wrong.
- **Link pages** with `[[slug]]`; in link lists write `- [[page]] — why it matters`.
- **Merge, don't append.** Update the existing prose when the code changes; don't add a
  section per change.

## Git

The wiki and the code are separate repositories, committed separately. Commit code
changes first, then update the pages with `verified:` set to the new code commit, then
commit the wiki with a message naming that commit.

## Your rules

Add this wiki's own conventions here. Every workflow applies them.
