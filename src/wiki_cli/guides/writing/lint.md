# Lint: check continuity and audit the wiki

Find what is broken, missing or inconsistent: in the wiki, and in the stories as the wiki
records them. Fix what is mechanical, report what needs the user's judgment, and commit
the fixes. Never change an original: what is wrong in one goes in the report.

{{types}}

{{rules}}

## Steps

1. **Refresh.** `wiki index refresh`.
2. **Mechanical checks, from the tool.** Run each with `--format json`:
   - `wiki check --all`: pages with no summary, missing sections or fields, values a
     type does not allow (`bad-value`), uncited sources, stale summaries
     (`summary-stale`), links between books (`cross-group-link`: remove the link, or, if
     the page truly belongs to both, name both books), pages whose original is gone
     (`missing-original`: find where it moved, `wiki pending` lists it as new, and link
     it), and originals missing their template's frontmatter (`original-missing-field`,
     `original-bad-kind`). The originals are the user's: list those in the report, never
     fix them.
   - `wiki pending`: originals not ingested yet, and those **changed since they were
     ingested**. Don't ingest during lint; list them, since pages citing a changed
     original may be out of date.
   - `wiki unwritten`: link targets with no page, most-linked first: a typo, a renamed
     page (fix the links), a page worth writing (report it), or a link to remove.
   - `wiki orphans`: pages nothing links to. Link each from the pages that discuss it, or
     report it.
   - `wiki clusters --group <book>` for each book, and `wiki clusters --ungrouped`:
     groups of densely linked pages that no hub page (a plotline, an event, a piece of
     lore, a theme) covers. One that gathers around something no page is about (a
     rivalry, a journey, a secret) is a page worth writing; report it.
3. **Continuity checks, by reading.** Book by book, with `wiki list --type <type>`,
   `wiki neighbors <slug>` and `wiki search`; read only what you need.
   - **Contradictions:** pages that disagree about a fact (an age, eye colour, who was
     where, what was said), and facts that cannot both hold in story order (a character
     in two places at once, a death before an appearance). Compare each character's
     details and `## Knows` with the scenes they appear in, and each event with the
     scenes that show or recall it. File each in `wiki/open-questions.md` with the pages
     and originals on each side; the user decides.
   - **Knowledge:** a character acting on something before the scene where they learn it.
   - **Open threads:** `wiki list --type thread` with `status: open`. A thread planted
     long ago with nothing since is a promise the reader may feel broken: report it, with
     where it was planted.
   - **Plotlines:** `status: active` plotlines whose last beat is far behind the latest
     chapter, and `resolved` ones still gaining beats.
   - **Vanished characters:** characters who matter in early chapters and stop appearing,
     with no exit.
   - **Canon and plan:** facts marked `(planned)` that a scene has since written (cite the
     scene and drop the mark), and plain facts with no citation (cite them if a page of
     an original supports them; otherwise report them, never guess).
   - **Settled questions:** open questions a later original or a decision answers. Update
     the pages and remove the entry.
4. **Fix and commit.** Fix what is mechanical (links, citations an original supports,
   summaries, frontmatter of wiki pages), then `wiki index refresh` and `wiki check --all`
   again. {{part:before-commit}}
   Commit: `wiki commit "lint: <what was fixed>"`.
5. **Report** to the user: what you fixed; continuity problems for them to decide, each
   with the pages and originals involved; open threads and stalled plotlines; originals
   to ingest or whose frontmatter needs fixing; pages worth writing. `wiki map --open`
   shows the wiki in 3D by what each page is about, if they want to look.

{{part:lint-extra}}
