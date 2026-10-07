# Lint: audit the code wiki

Find what is stale, broken, missing or inconsistent. Fix what is mechanical, report what
needs judgment, and commit the fixes.

The code repo is `{{code_repo}}`.

{{types}}

{{rules}}

## Steps

1. **Refresh.** `wiki index refresh`.
2. **Mechanical checks, from the tool.** Run each with `--format json`:
   - `wiki stale`: pages whose code changed since they were verified{{#records}}, record pages
     due for a recheck (fetch each and update it, setting `synced:`; see `wiki guide sync`),{{/records}} and pages never
     verified. Sync them (`wiki guide sync`), or report them if there are many.
   - `wiki check --all`: frontmatter, missing summaries, undeclared types, ambiguous
     links, stale summaries, `code:` links to files that no longer exist
     (`missing-code-file`), `covers:` globs that match nothing (`covers-nothing`),
     pages that cover files without a `verified:` commit, file pages that name a
     broader module under `## Part of` than the most specific one covering them
     (`part-of-broader-module`: point them at that one), and links between types a
     type's `no_links_with` keeps apart, such as a file page and an epic
     (`link-not-allowed`: from a file page, link the ticket; from an epic, the module).
   - `wiki unwritten`: links to pages not written yet. Fix typos; list the rest in the
     report as pages worth writing, most-linked first.
   - `wiki orphans`: pages nothing links to. Link them from related pages, or report them.
   - `wiki pending`: documents in `raw/` no page links to yet. List them in the report;
     don't ingest them during lint.
   - `wiki clusters`: groups of pages that link each other densely, with no module,
     concept or epic page most of them link to. For each, read the pages' summaries and
     the terms they share. If they gather around an area of the code or an idea no page is
     about (a feature a PR built, a pattern several files follow), list it in the report as
     a module or concept page worth writing, with the cluster's pages; if not, leave it.
   - **Finished epics:** an epic covers its cluster while the feature is built, but it
     records what was asked, not how the code works now. For each cluster an epic covers
     (`wiki clusters --all`) whose epic is done or closed, check that a module or concept
     page describes the feature; if none does, list one in the report as a page worth
     writing.
   - `hub-covers-clusters` (from `wiki check --all`): a module or concept page that two or
     more separate clusters gather around. Usually it describes several areas of the code
     at once. Read the clusters it names. If they are distinct areas (different folders,
     different jobs), split the page: a module page for each area, with `covers:` narrowed
     to that area's files, the matching prose moved there, and the old page under its
     `## Part of`; point each file page's `## Part of` at its new module; and cut the old
     page to a short overview of its parts, keeping its slug so links still resolve and
     its `covers:` to the files no new module describes. If the clusters are one area in two
     groups, or the page is a cross-cutting concept every area uses (logging,
     configuration), leave it and say why in the report. (Epics are never flagged: a
     feature spans several areas.)
3. **Coverage.** Compare the code's top-level folders (`git -C <code repo> ls-files`) with
   the `covers:` of the module pages (`wiki list --type module`): report important code no
   page covers.
4. **Judgment checks, by reading.** Pages that contradict each other or the code; entries
   in `wiki/open-questions.md` that the code now settles; summaries that no longer fit
   (revise, or `wiki check <slug> --summary-ok`).
5. **Fix and commit** what is mechanical, then `wiki index refresh` and
   `wiki check --all` again. Commit the wiki with a message listing the fixes.
   {{part:before-commit}}
6. **Report** what you fixed, and what needs the user's judgment. If they want to look
   over the wiki themselves, `wiki map --open` shows every page in 3D by what it is about.

{{part:lint-extra}}
