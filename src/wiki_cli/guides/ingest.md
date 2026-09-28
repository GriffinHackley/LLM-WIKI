# Ingest: add a source to the wiki

Read one source from `raw/`, write its source page, work what it says into the pages it
touches, and commit. One source per run; repeat for the next.

{{types}}

{{rules}}

## Steps

1. **Pick the source.** If none was named, list the files in `raw/` that no source page
   links to yet and ask which to ingest. (`wiki list --type source --format json` gives
   the source pages; each one links its original under `## Original`.) Never edit, move or
   rename anything in `raw/`.
2. **Read the whole source.** Long documents in parts (PDFs a batch of pages at a time,
   long text in chunks) until you have read all of it. Never summarize from a partial
   read. If you cannot read the file (an image-only scan, a format you cannot open), stop
   and say so.
3. **Write the source page.** Pick a stable, descriptive kebab-case slug
   (`senate-report-2024-03`); check `wiki search` or `wiki list --type source` that it is
   new. Copy the source template to the source folder, then fill every section you have
   material for and delete placeholder text:
   - `## Summary`: what the source is, who produced it, when, and its main point.
   - `## Key points`: what it says that matters, each with a locator (p. N, section).
   - `## Entities mentioned`: one line per person, organization, place, event or concept
     it materially discusses, as `- [[slug]] — role in this source (p. N)`.
   - `## Original`: a link to the file in `raw/`.
4. **Find the pages it touches.** Run `wiki index refresh`, then
   `wiki suggest <source-slug> --format json`: pages the source names without linking,
   pages that share its links, and pages that resemble it. Treat each as a lead to check
   against the source, never as a link to add blindly: a passing mention is not
   involvement. Use `wiki search "<name>"` to check whether a page already exists before
   creating one, including under another name.
5. **Update or create each page it materially discusses.**
   - **Existing page:** read it, then work the new facts into its existing prose and
     lists. Don't append a section per source, and don't repeat what it already says.
     Add the source to `sources:`, and update `last_updated`.
   - **New page:** copy the template for its type into the type's folder and fill it from
     this source.
   - **Cite every fact** you add: `…signed in March ([[source-slug]], p. 4)`.
   - **Link on first mention** with `[[slug]]`, including to pages not written yet.
6. **Record questions.** Where the source contradicts a page, or claims something no
   source supports, add an entry to `wiki/open-questions.md`: what conflicts, the sources
   on each side, and the pages involved. Mention it in the source page's
   `## Questions raised`. Don't resolve a contradiction by silently picking a side.
7. **Check.** Run `wiki index refresh`, then `wiki check <slug>` on every page you created
   or changed, and fix every error. Warnings about links to pages not written yet are
   expected. A `summary-stale` warning means you changed a page's body but not its
   `## Summary`: re-read the summary and revise it to cover what the page now says, or,
   if it still fits, run `wiki check <slug> --summary-ok`.
8. **Commit** (if the wiki is a git repository): one commit for this source, with a
   message naming the source slug, the pages created and updated, and any questions
   raised.
9. **Report** to the user: the source ingested, pages created, pages updated, and
   questions raised.
