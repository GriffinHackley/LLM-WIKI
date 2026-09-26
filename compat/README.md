# Compatibility Check

A two-page wiki for confirming that the generated link block produces a graph
edge in both Obsidian and llm-wiki. `tms/editor` has no manual link to
`tms/save-pipeline`; its only link is the generated block, already synced.

## Setup

llm-wiki registers only git repositories, so copy this folder somewhere outside
the project first (PowerShell):

```powershell
Copy-Item -Recurse compat $env:TEMP\wiki-compat
cd $env:TEMP\wiki-compat
git init; git add .; git commit -m "compat fixture"
llm-wiki spaces register . --name compat
llm-wiki ingest tms/editor --wiki compat
llm-wiki ingest tms/save-pipeline --wiki compat
```

## Checks

1. **llm-wiki graph:** `llm-wiki graph --wiki compat --format json`
   - An edge `tms/editor` → `tms/save-pipeline` with relation `links-to`.
   - Both nodes resolve (neither is `external`).
2. **Obsidian:** open `$env:TEMP\wiki-compat\wiki` as a vault.
   - Graph view shows an edge between the two pages.
   - Backlinks on `save-pipeline` list `editor`.
   - No dangling node named `tms/save-pipeline` (it resolves to the real file).
3. **Re-ingestion:** edit prose in `tms/editor.md`, run
   `llm-wiki ingest tms/editor --wiki compat`, and confirm the block between the
   `wiki-relations:v1` markers is unchanged and `wiki check --all
   --wiki-root wiki` still reports 0 errors.
4. **llm-wiki lint:** `llm-wiki lint --wiki compat` reports no broken link.

If any check fails, the fallback in the plan is a generated `## Connections`
section with the same canonical frontmatter.
