# Troubleshooting

What a message or symptom means, and what to do about it. If nothing here fits,
`wiki index rebuild` recreates the cache from the files; it is safe, since nothing in
the cache is lost by rebuilding it.

## Installing

| Symptom | Cause and fix |
|---|---|
| `wiki: command not found` after installing | uv's tool folder is not on your PATH. Run `uv tool update-shell`, then open a new terminal. |
| `uv tool install llm-wiki-kit` fails with `Executable already exists: wiki` | An install from before 0.3.0, named `wiki-cli` or `llm-wiki-cli`. `uv tool list` shows it; `uv tool uninstall <name>`, then install again. Wikis, configs and models are untouched. |
| `this Python's sqlite3 cannot load extensions` | The Python uv chose was built without SQLite extensions, as the python.org installers for macOS are. Reinstall on uv's own Python: `uv tool install --managed-python --reinstall llm-wiki-kit`. |
| `embedding model ... is not downloaded` | Run `wiki models download` (once per machine, about 0.2 GB). Until then search uses keywords only, and says so. |

## Finding the wiki

| Symptom | Cause and fix |
|---|---|
| `...is not a wiki folder; cd into the wiki, pass --root, or add a config` | You ran `wiki` from your home folder or a drive root. `cd` into the wiki first. |
| `note: no .wiki-cli.toml found; using ... as the wiki root` | No config yet. Check the folder is the one you meant, then run `wiki new .` (or just `wiki init --write`). |
| `wiki` in a code repo works on the code repo, not the wiki | The code repo needs a pointer: a `.wiki-cli.toml` holding only `wiki = "<path to the wiki>"`. `wiki connect` writes it. |
| `code repo ... does not exist` or `is not the top of a git repository` | A code wiki's `[code] repo` points at the wrong place on this machine. Set `WIKI_CODE_REPO` to the code's checkout, or fix the path. |
| `code repo ... has origin ..., but this wiki describes ...` | The pointer names a different repository than the one the wiki was made for. Point it at the right checkout. |

## Search

| Symptom | Cause and fix |
|---|---|
| `vector search skipped: no embeddings yet` | Run `wiki index refresh`. |
| `N files are not embedded yet` | Pages changed since the last refresh. Run `wiki index refresh`. Keyword search already sees the changes. |
| Search returns files that aren't wiki pages | Narrow `pages`, or add `exclude` globs, in `.wiki-cli.toml`. |
| Every relation is `links-to` | No relation rules yet. Add `[[relations]]` rules; `wiki init` suggests them from your headings. |
| The first search after a reboot takes a few seconds | The model files are read from disk the first time; later searches take about a second. |
| Results look wrong in a way a refresh doesn't fix | `wiki index rebuild`. |

## `wiki check` warnings

| Warning | Cause and fix |
|---|---|
| `ambiguous-link` | Two files share a name, so `[[name]]` could mean either. Link with the path (`[[people/name]]`), as Obsidian does. |
| `missing-section`, `missing-field` | The page lacks a heading or frontmatter key that its type's `sections` or `fields` require. Add it (the template shows it), or drop it from the config if your pages don't need it. |
| `bad-value` | A frontmatter field holds a value its type's `values` don't list. Use a listed value, or add yours to the list. |
| `uncited-sources` | The page lists a source in `sources:` that its text never cites. Cite it where its facts are used, or remove it. |
| `summary-stale` | The page's body changed but its summary did not. Revise the summary, or run `wiki check <slug> --summary-ok` if it still fits. |
| `unknown-type` | The page's `type:` is not declared under `[types]`. Fix the type, or declare it. |
| `missing-code-file`, `covers-nothing` | A code wiki page links a file that is gone, or its `covers:` matches nothing. The code moved: update the page (`wiki guide sync`). |
| `cross-group-link` | A page links a page from another group in a wiki whose groups are separate (`[groups] exclusive`). Remove the link, or name both groups on the page. |
| `missing-original` | A writing wiki page's original has moved or been deleted. `wiki pending` lists the moved file as new; link it from the page. |

[config.md](config.md) has every setting these checks read.

## Commits

| Symptom | Cause and fix |
|---|---|
| `sources in raw/ are never edited, renamed or deleted` | The pre-commit hook refused a change to a source. Restore it (`git restore --staged --worktree <file>`) and commit again. To correct a source, add a new file instead. |
| `'wiki check --all' found errors` | The pre-commit hook refused pages with errors. Fix what the report lists, then commit again. |
| `'<command>' failed ([hooks] pre_commit ...)` | One of the wiki's own pre-commit checks failed. Its output says why. |
| `wiki commit` says `a task is open` | In a code wiki with a task open, `wiki finish` commits instead: the code, then the wiki. |
| `git's index stayed locked` | Another git process (Obsidian Git on a timer, an editor) held the lock for longer than `wiki commit` waits. Run it again; if it keeps happening, look for a stale `.git/index.lock` from a crashed git. |

## Code tasks

| Symptom | Cause and fix |
|---|---|
| The agent's edits are refused: no task is open | With the code repo connected, edits need an open task. The agent runs `wiki task start "<title>"` after you approve its plan. |
| `Another session holds the open task` | A different agent session started it. Finish or cancel it there, or, if that session is gone (a restart, a cleared conversation), `wiki task takeover` moves it here. |
| `no agent session asked for this` | `wiki task start` and `takeover` are run by the agent, so its hook knows which session holds the task. Ask the agent to run them. |
| `the task changed no file ... nothing to commit` | `wiki finish` found nothing the task edited. `wiki task status` shows what it tracked; a changed file it did not edit goes in with `--include <file>`. |
| `git commit` is refused during a task | By design: `wiki finish -m "<message>"` makes both commits, once you approve. |

## Agents

| Symptom | Cause and fix |
|---|---|
| The agent doesn't follow the workflows | Check it reads `AGENTS.md` (some read their own file, such as `GEMINI.md`: point it at `AGENTS.md`). The slash commands from `wiki new --agent` pull the guide in directly. |
| The agent asks permission for every `wiki` command | Allow `wiki` in the agent's settings. `--agent claude` does it for Claude Code; for Copilot, add `wiki` to its terminal auto-approve list. |
| An edit to an original or a source is refused | By design: the agent may read originals and sources, never change them. |
| A new workflow from an upgrade has no slash command | Run `wiki new . --agent <name>` again; it adds the missing commands. |

---

[All docs](README.md)
