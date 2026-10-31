# The code wiki

<!-- Written by `wiki connect`, which rewrites it. Change the wiki's own AGENTS.md or its guides instead. -->

This code has an LLM-maintained wiki at `{{wiki_path}}`, {{wiki_where}}. Its pages explain
what the code does and why: its modules, the decisions the user made, and the gotchas
learned the hard way. Run `wiki` commands from here: they find the wiki.

**Search the wiki first.** For any question about the code, whether how it works, where
something lives, or where a change belongs, your first search action must be
`wiki nav start` (`wiki guide query` says how). Until you have run it and read the matched
sections with `wiki nav read`, do not run grep, glob or semantic search (not even one
targeted keyword grep) and do not open source files. A code search before `wiki nav start`,
or in the same command, breaks this rule. Fall back to the code only for a part that
`wiki nav candidates` and `wiki nav search` have both failed to answer, and say which parts
of your answer came from the wiki and which from the code.

- **A task on the code** (a fix, a feature, a refactor): run `wiki guide work` and follow
  it. It starts from the wiki, stops for the user's approval of the plan and of each
  decision, and ends with `wiki finish`, which commits the code and then the wiki. Never
  run `git commit` yourself. To plan a task without doing it yet, `wiki guide plan`: it
  stops at the approved plan, and `wiki guide work` carries it out from there.
- **A question about the code:** how it works or why, where something lives ("where does
  X live?", "which file handles Y?"), or where a change would go ("where would a change to
  Z be made?"): `wiki guide query`, which starts with `wiki nav start`. If the user then
  wants the change made, that is a task: `wiki guide work`.
- **Code that changed outside a task** (someone else's commits): `wiki guide sync`.
- **Documenting something new** (a module, a design doc saved in the wiki's `raw/`):
  `wiki guide ingest`.

The agent hooks show you the wiki's notes on a file the first time you open it, and the
gotchas and decisions that affect a wiki page the first time you read it (or run
`wiki covering <file>`), and refuse changes to the code and the wiki until the user has
approved a plan and a task is open (`wiki task start`).

The wiki's own conventions are in `{{wiki_path}}/AGENTS.md` ("Your rules").
