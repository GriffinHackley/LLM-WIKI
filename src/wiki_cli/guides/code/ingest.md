# Ingest: document part of the code

Document one part of the codebase (a module, a pull request, a design decision) from the
code itself: read it, write or update the pages that describe it, and commit the wiki.

The code repo is `{{code_repo}}`. Read the code there; never edit it as part of this
workflow.

{{types}}

{{rules}}

## Steps

1. **Pick the subject.** If none was named, ask what to document. For a pull request or
   a range of commits, list what it changed: `git -C <code repo> diff --stat <base>..<head>`.
2. **Read the code.** Read the files involved fully enough to explain them: entry points,
   the main types and functions, how data flows, and the tests, which show intended
   behavior. For a decision, find the change that made it and any discussion in commit
   messages or comments.
3. **Find the pages it touches.** `wiki search "<subject>"`, `wiki list --type module`,
   and `wiki suggest <slug>` for an existing page. Update a page that already covers the
   subject rather than writing a second one.
4. **Write or update the pages.** Copy the template for the page's type into its folder,
   or edit the existing page:
   - Explain what the code does and why, how the parts fit, and what is easy to get
     wrong. Don't transcribe the code.
   - Link to the code with `code:` links, paths from the code repo's top level:
     `[the resolver](code:src/app/pages.py)`.
   - Set `covers:` to globs of the files the page describes, and `verified:` to the code
     commit you read, quoted: `git -C <code repo> rev-parse HEAD`. If the files you read
     have uncommitted changes, say so to the user: the page describes code that is not
     committed yet.
   - Link related pages with `[[slug]]`; list a module's dependencies under
     `## Depends on`, a decision's modules under `## Affects`.
5. **Record questions.** Behavior you could not explain, or code that contradicts a page,
   goes in `wiki/open-questions.md`.
6. **Check.** `wiki index refresh`, then `wiki check <slug>` on every page you created or
   changed; fix every error, and every `missing-code-file` and `covers-nothing` warning.
   A `summary-stale` warning means you changed a page's body but not its summary: revise
   the summary, or, if it still fits, run `wiki check <slug> --summary-ok`.
7. **Commit the wiki** (a separate repository from the code), with a message naming what
   was documented, the pages created and updated, and the code commit they were verified
   against.
8. **Report** to the user: what you documented, pages created and updated, and open
   questions.
