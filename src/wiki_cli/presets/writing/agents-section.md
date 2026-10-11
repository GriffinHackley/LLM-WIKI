## Story wiki

This repository holds the user's writing (`originals/`) and an LLM-maintained wiki about
it. For each workflow, run its guide once and carry out the steps it prints yourself:

- `wiki guide ingest`: ingest an original or a research source, when the user asks
- `wiki guide query`: answer a question about the stories
- `wiki guide lint`: check continuity and audit the wiki

`wiki guide` lists them. Use the `wiki` command (`wiki search`, `wiki list`,
`wiki neighbors`) instead of grepping pages. Never edit, move or delete anything in
`originals/` or `raw/`. End each ingest with `wiki commit "ingest: <book>/<original>"`.
