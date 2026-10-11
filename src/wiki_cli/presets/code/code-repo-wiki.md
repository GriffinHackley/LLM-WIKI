# The code wiki

<!-- Written by `wiki connect`, which rewrites it. Change the wiki's own AGENTS.md or its guides instead. -->

This code has an LLM-maintained wiki at `{{wiki_path}}`, {{wiki_where}}. Its pages explain
what the code does and why: its modules, the decisions the user made, and the gotchas
learned the hard way. Run `wiki` commands from here: they find the wiki.

- **A task on the code** (a fix, a feature, a refactor): run `wiki guide work` and follow
  it. It starts from the wiki, stops for the user's approval of the plan and of each
  decision, and ends with `wiki finish`, which commits the code and then the wiki. Never
  run `git commit` yourself.
- **A question about how the code works or why:** `wiki guide query`, before reading code
  at random.
- **Code that changed outside a task** (someone else's commits): `wiki guide sync`.
- **Documenting something new** (a module, a design doc saved in the wiki's `raw/`):
  `wiki guide ingest`.

The agent hooks show you the wiki's notes on a file the first time you open it (or run
`wiki covering <file>`), and refuse changes to the code and the wiki until the user has
approved a plan and a task is open (`wiki task start`).

The wiki's own conventions are in `{{wiki_path}}/AGENTS.md` ("Your rules").
