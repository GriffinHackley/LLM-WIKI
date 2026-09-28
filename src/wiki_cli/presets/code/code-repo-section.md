## Wiki

This code has an LLM-maintained wiki at `{{wiki_path}}`, a separate repository (this
repository's `.wiki-cli.toml` points `wiki` commands at it). Keep it current as you work:

- **Questions about how the code works or why:** run `wiki guide query` and follow it
  before reading code at random.
- **After changing code:** commit the code, then run `wiki guide sync` and follow it, so
  the pages describing what you changed stay accurate. Commit the wiki separately.
- **Documenting something new** (a module, a decision): `wiki guide ingest`.
