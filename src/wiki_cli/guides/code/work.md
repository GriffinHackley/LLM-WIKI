# Work: do a task on the code, with the wiki

Do one task on the code (a fix, a feature, a refactor) the way this repo works: learn the
area from the wiki, plan, get the user's approval, do the work, keep the wiki current, and
end with one approved commit of the code and one of the wiki.

The code repo is `{{code_repo}}`. Run `wiki` commands from it: they find the wiki.

{{rules}}

**The user approves every plan and every decision.** Stop and wait for their answer at
each step marked *Stop*: don't treat silence, or approval of something else, as approval.

## Steps

1. **Learn the area.** `wiki search "<the task in a few words>"` and read the pages it
   returns. Read the module page of each area the task touches, and the gotcha and
   decision pages linked from it (`wiki neighbors <module>`). When you open a code file,
   the wiki's notes on it are shown to you the first time (or run
   `wiki covering <file>`): read the pages they name before changing the file. Where a
   page and the code disagree, trust the code and say so in your plan.
2. **Plan.** Write a short plan: what you will change and where, how you will test it, and
   any choice with more than one reasonable answer. For each such choice, say what you
   recommend and why. Name the gotchas and decisions you are working around.
3. **Stop: get the plan approved.** Show the plan and wait. Change it as the user asks
   and show it again until they approve it.
4. **Start the task.** `wiki task start "<a short title>"`. The user is asked to confirm;
   until then, and while another session holds a task, your edits are refused. If a task
   from an earlier session of yours is still open (after a restart or `/clear`), the
   refusal says so: `wiki task takeover` moves it here, with the user's confirmation.
5. **Record decisions.** For each real choice the user settled, write a decision page
   (`wiki guide ingest` says how) with `status: proposed`, its `## Affects` naming the
   pages it shapes. **Stop:** show the user each one; set `status: accepted` only when
   they approve it. A decision they reject is not written (or is set to `rejected` if
   it replaced an earlier one).
6. **Do the work and test it.** Follow the plan. When the plan turns out wrong, stop and
   say so before changing course: a changed plan needs approval again. Run the tests
   that cover what you changed, and the whole suite if it is quick.
7. **Bring the wiki up to date.** The task's changes are not committed yet, so:
   - `wiki stale` lists the pages whose covered files you changed (as uncommitted).
     Update each so it matches the code, and set its `verified:` to `"pending"`:
     `wiki finish` replaces it with the code commit it makes.
   - New files: add them to a page's `covers:`, or document them (`wiki guide ingest`).
   - What you learned the hard way (a trap, a surprising behavior) goes on a gotcha
     page, its `## Affects` naming the pages where the trap lies.
   - `wiki check` on every page you changed, and fix what it reports.
8. **Stop: get the work approved.** Report what you changed, the test results, the wiki
   pages you updated or wrote, and anything unresolved. Propose a commit message: one
   line saying what the task did, and a short body if it needs one.
9. **Finish.** Once the user approves, `wiki finish -m "<the approved message>"`. It
   shows the user the files it will commit and asks them to confirm, then commits the
   files this task edited in the code repo, sets the pending pages' `verified:` to that
   commit, and commits the wiki. Never run `git commit` yourself: it is refused.
