# Sync: update the wiki after the code changed

Bring the pages that describe changed code back in line with it, then commit the wiki.
Run it after committing code changes.

The code repo is `{{code_repo}}`.

{{rules}}

## Steps

1. **Commit the code first.** The wiki records which code commit each page was checked
   against, so the code change must be a commit. (`wiki stale` also lists uncommitted
   changes, so you can see what a change will affect before committing.)
2. **Find the stale pages.** `wiki stale --format json` lists each page whose covered
   files changed since its `verified:` commit, with the files, plus pages that were never
   verified{{#records}}, and under `records` the record pages due for a recheck{{/records}}.
3. **For each stale page:**
   - See what changed: `git -C <code repo> diff <verified>..HEAD -- <files>`.
   - Read the page, and the changed code where the diff is not enough.
   - Update what the page says so it matches the code: behavior, names, `code:` links,
     `covers:` globs (files added, moved or deleted). Keep the page explaining why, not
     transcribing the diff.
   - Set `verified:` to the new code commit (`git -C <code repo> rev-parse HEAD`), quoted,
     even when the change needed no edit to the text: the page has been checked.
   - If the change made a new module or decision worth its own page, document it with
     `wiki guide ingest`.
{{#records}}
   **For each record to recheck:**
   - **Fetch it.**
     {{part:fetch-record}}
   - If its last-updated time is later than `synced:`, update the page from it: status,
     what was asked and what was done, linking the pull requests and code involved.
   - Set `synced:` to its last-updated time, quoted, even when nothing on the page
     changed: the page has been checked.
{{/records}}
4. **Check.** `wiki index refresh`, `wiki check` on every page you changed (fix errors,
   `missing-code-file` and `covers-nothing`; revise or `--summary-ok` stale summaries), and
   `wiki stale` again: it should list nothing you meant to sync.
5. **Commit the wiki** with a message naming the code commit synced to and the pages
   updated.
   {{part:before-commit}}
6. **Report** the pages updated, and anything you could not reconcile (also filed in
   `wiki/open-questions.md`).

{{part:sync-extra}}
