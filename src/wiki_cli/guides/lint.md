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
     several files: link by path instead), and summaries not revised since their page
     changed (`summary-stale`).
   - `wiki unwritten`: link targets with no page, most-linked first. For each, decide:
     a typo or a renamed page (fix the links), a page worth writing (list it in the
     report; the most-linked first), or a link that should not exist (remove it).
   - `wiki orphans`: pages nothing links to. Link each from the pages that discuss it
     (`wiki suggest <slug>` and `wiki search` find them), or report it if nothing
     should. An open-questions page is an orphan until a page raises a question; that is
     fine.
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
   summaries, frontmatter), then `wiki index refresh` and `wiki check --all` again. Commit
   the fixes with a message listing what was fixed.
5. **Report** to the user: what you fixed, and what needs their judgment (contradictions,
   uncited facts you could not source, pages worth writing, orphans with no natural
   home).
