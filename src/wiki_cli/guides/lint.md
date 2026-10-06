# Lint: audit the wiki

Find what is broken, missing or inconsistent in the wiki. Fix what is mechanical, report
what needs judgment, and commit the fixes.

{{types}}

{{rules}}

## Steps

1. **Refresh.** `wiki index refresh`.
2. **Mechanical checks, from the tool.** Run each with `--format json`:
   - `wiki check --all`: frontmatter that does not parse, pages with no summary, types
     not declared in the config (`unknown-type`), ambiguous links (`[[name]]` matching
     several files: link by path instead), summaries not revised since their page
     changed (`summary-stale`), pages missing a section or frontmatter field their type
     requires (`missing-section`, `missing-field`), fields holding a value their type
     does not allow (`bad-value`), and sources listed in `sources:` that
     the text never cites (`uncited-sources`: cite each where its facts are used, or
     remove it from the list).
   - `wiki unwritten`: link targets with no page, most-linked first. For each, decide:
     a typo or a renamed page (fix the links), a page worth writing (list it in the
     report; the most-linked first), or a link that should not exist (remove it).
   - `wiki orphans`: pages nothing links to. Link each from the pages that discuss it
     (`wiki suggest <slug>` and `wiki search` find them), or report it if nothing
     should. An open-questions page is an orphan until a page raises a question; that is
     fine.
   - `wiki pending`: sources in `raw/` no page links to yet. Don't ingest them during
     lint; list them in the report (and any flagged as the same content as a source
     already ingested).
{{#records}}
   - `wiki stale`: record pages due for a recheck (never synced, or synced long ago and
     not closed). For each, fetch the record, and if it changed since `synced:`, update
     the page; either way, set `synced:` to its last-updated time. If there are many, do
     the oldest and list the rest in the report.
     {{part:fetch-record}}
{{/records}}
   - `wiki clusters`: groups of pages that link each other densely, with no hub page
     (a type marked `hub` in the config, such as a concept or event) most of them link
     to; or, in a wiki without hub types, every group with the page most of it links to.
     For each, read the pages' summaries and the terms they share. If they gather around
     an idea no page is about (a negotiation, a scandal, a technique), list it in the
     report as a page worth writing, with the cluster's pages; if the most-linked page is
     already about that idea, or the pages have nothing in common, leave it. It lists
     nothing in a wiki under 30 pages.
3. **Judgment checks, by reading.** Use `wiki search`, `wiki list --type <type>` and
   `wiki neighbors` to find the pages to compare; read only what you need.
   - **Contradictions:** pages that disagree about the same fact (a date, a role, a
     number, who did what). Look where disagreements collect: for each event, compare
     its page with the pages around it (`wiki neighbors <event>` shows both the pages it
     involves and the pages that link to it); for each person or organization, compare
     its timeline with the events it links. Check the sources each side cites. When a
     source settles it, fix the wrong page and cite the source; otherwise file it in
     the wiki's open questions (`wiki/open-questions.md` in the presets) with the pages
     and sources on each side.
   - **Uncited facts:** paragraphs stating facts with no link to a source page. Add the
     citation if the page's sources support the fact; otherwise report it. Never guess a
     citation.
   - **Settled questions:** open questions that a later source
     answers. Update the pages, and remove the entry.
   - **Stale summaries:** for each `summary-stale` page, re-read the summary against the
     page. Revise it to cover what the page now says, or, if it still fits, run
     `wiki check <slug> --summary-ok`.
4. **Fix and commit.** Fix what is mechanical (links, citations the sources support,
   summaries, frontmatter), then `wiki index refresh` and `wiki check --all` again.
   {{part:before-commit}}
   Commit
   the fixes with a message listing what was fixed.
5. **Report** to the user: what you fixed, and what needs their judgment (contradictions,
   uncited facts you could not source, pages worth writing, orphans with no natural
   home), and the sources still to ingest.

{{part:lint-extra}}
