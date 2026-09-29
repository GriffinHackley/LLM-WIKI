# Lint: audit the code wiki

Find what is stale, broken, missing or inconsistent. Fix what is mechanical, report what
needs judgment, and commit the fixes.

The code repo is `{{code_repo}}`.

{{types}}

{{rules}}

## Steps

1. **Refresh.** `wiki index refresh`.
2. **Mechanical checks, from the tool.** Run each with `--format json`:
   - `wiki stale`: pages whose code changed since they were verified, and pages never
     verified. Sync them (`wiki guide sync`), or report them if there are many.
   - `wiki check --all`: frontmatter, missing summaries, undeclared types, ambiguous
     links, stale summaries, `code:` links to files that no longer exist
     (`missing-code-file`), `covers:` globs that match nothing (`covers-nothing`), and
     pages that cover files without a `verified:` commit.
   - `wiki unwritten`: links to pages not written yet. Fix typos; list the rest in the
     report as pages worth writing, most-linked first.
   - `wiki orphans`: pages nothing links to. Link them from related pages, or report them.
   - `wiki pending`: documents in `raw/` no page links to yet. List them in the report;
     don't ingest them during lint.
3. **Coverage.** Compare the code's top-level folders (`git -C <code repo> ls-files`) with
   the `covers:` of the module pages (`wiki list --type module`): report important code no
   page covers.
4. **Judgment checks, by reading.** Pages that contradict each other or the code; entries
   in `wiki/open-questions.md` that the code now settles; summaries that no longer fit
   (revise, or `wiki check <slug> --summary-ok`).
5. **Fix and commit** what is mechanical, then `wiki index refresh` and
   `wiki check --all` again. Commit the wiki with a message listing the fixes.
6. **Report** what you fixed, and what needs the user's judgment.
