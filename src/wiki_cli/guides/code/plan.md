# Plan: plan a task on the code, without doing it

Plan one task on the code, such as a fix, a feature or a refactor, following this repo's
workflow: learn the area from the wiki, write a plan, and get the user's approval. Change
nothing: doing the task is `wiki guide work`, which picks up from an approved plan.

The code repo is `{{code_repo}}`. Run `wiki` commands from it: they find the wiki.

{{rules}}

**The user approves every plan.** Stop and wait for their answer at each step marked
*Stop*: don't treat silence, or approval of something else, as approval.

## Steps

1. **Split the task into parts.** List each feature, behavior, kind of data or
   user-facing piece the task changes or relies on, as it says or clearly implies. Write
   the list down: it heads the plan, so the user sees how you read the task.
2. **Find the pages for each part,** from the wiki:
   - For each part, `wiki search "<a few words for it>"`. When a search misses, search
     again with the titles and summaries of the pages it did return: they are the
     wiki's names for things. A part is covered once a page matches it; after two
     searches that find nothing, it has no page.
   - Only when the task names a specific file, function or error message: find that one
     thing in the code, and `wiki covering <file>` names the wiki pages on its file.
     Don't search or browse the code otherwise; learn the area from the wiki.
   - From a page you found: `wiki neighbors <page>` lists the pages it relates to (the
     modules it depends on, and those depending on it), for when the task reaches
     beyond it.
3. **Stop: ask about the parts with no page.** Skip this when every part has one. Don't
   look in the code for them on your own: name each such part and ask the user whether
   to look in the code for it. If they agree, look for that part only. When you find
   the files that likely hold it, read them and recommend documenting them: if the user
   agrees, the plan includes ingesting them (`wiki guide ingest`) during the task, so
   the wiki covers that part from then on. If the user declines, plan without looking,
   and say so in the plan.
4. **Read what the wiki knows.** Read each page you found that bears on the task. Then
   run `wiki covering` on those pages (their slugs will do) and on each code file you
   know you will change (one the task names, or one a page names), all in one call: it
   lists the open gotchas and decisions that affect them. Read every one of those pages
   before you plan. The hooks show the same notes as you open files, but only once a
   session, so run `wiki covering` anyway. Where a page and the code disagree, trust
   the code and say so in your plan.
5. **Plan.** Write a short plan. Start with the parts of the task from step 1, each with
   the pages that cover it, or "no page" and what the user decided in step 3. Then: what
   you will change and where, how you will test it, the files you will document, and
   any choice with more than one reasonable answer. For each such choice, say what you
   recommend and why. For each gotcha and decision from step 4, say how the plan
   respects it, or why it doesn't apply.
6. **Stop: get the plan approved.** Show the plan and wait. Change it as the user asks
   and show it again until they approve it.
7. **Hand back, or stop.** If you are planning as the first step of `wiki guide work`,
   go on with its next step now. Otherwise stop there: don't run `wiki task start` and
   don't edit the code or the wiki (with no task open, the edits are refused anyway).
   Tell the user the plan is approved and that `wiki guide work` carries it out; in this
   session it uses the approved plan rather than planning again.
