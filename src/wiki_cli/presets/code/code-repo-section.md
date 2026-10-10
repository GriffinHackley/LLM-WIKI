## Wiki

This code has an LLM-maintained wiki at `{{wiki_path}}`, {{wiki_where}}. Keep it current as you work:

- **Questions about how the code works or why:** run `wiki guide query` and follow it
  before reading code at random.
- **After changing code:** commit the code, then run `wiki guide sync` and follow it, so
  the pages describing what you changed stay accurate. {{wiki_commit}}
- **Documenting something new** (a module, a decision), or a design doc, RFC or postmortem
  saved in the wiki's `raw/`: `wiki guide ingest`.
