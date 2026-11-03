# Navigation

`wiki nav` lets an agent answer a question by reading as little as possible: start
from search, read one section at a time, and choose the next page from summaries and
relation reasons instead of opening candidates to see what they contain.

## A session

```bash
wiki nav start "Who gave evidence at the inquiry into the North Mill fire, and what did it cost?"   --part "who gave evidence at the North Mill fire inquiry" --part "cost of the inquiry" --format json
```

```json
{"session":"a41f0c","max_pages":6,"coverage":"good","results":[
 {"slug":"north-mill-fire-inquiry-1887","title":"Inquiry into the North Mill fire",
  "type":"event","score":1.21,"summary":"On 9 March 1887 the town council ...","section":"... > Witnesses"}],
 "parts":[{"part":"who gave evidence at the North Mill fire inquiry","coverage":"good",
           "pages":["north-mill-fire-inquiry-1887","ashford-courier-1887-03-10"]},
          {"part":"cost of the inquiry","coverage":"none","pages":["town-council"]}]}
```

Each `--part` is searched on its own, so a page that answers one part well is found even
when it ranks low for the whole question; each page is listed once.

```bash
wiki nav read a41f0c north-mill-fire-inquiry-1887 \
  --why "Need the witness list; the inquiry's event page is the most direct source." --format json
```

Returns the page summary, a section (`content`), the page's `sections` with their sizes
and which have been read, the closest unread section (`next_section`), and `pages_left`.
The section is the one `nav start` or `nav search` showed for the page, so the agent
reads what it chose the page for. Otherwise it is the one among sections of at least 200
characters whose stored vector is closest to the question's vector, saved at
`nav start`, so a read loads no model and takes about 150 ms. Without embeddings the
reranker chooses, then keyword match. On one wiki's evaluation set the reranker picked
the same section on 51 of 68 answer pages, with no clear winner on the rest. The section
the page's summary comes from is never the default: the summary is returned anyway.

Ask for `--section "<heading>"` (repeat it for several) or `--full` when the match is
not enough; other parts of a page already read do not count against the limit.

The text is cut at about 4,000 characters (`--max-chars`), at a line end, because agents'
shells often show only the start of a long output. A cut response carries `more`, the
command that continues it with `--more`; continuing is not a revisit. A section asked
for by name can be read again, for a shell that cut the output anyway.

```bash
wiki nav candidates a41f0c --format json
```

```json
{"session":"a41f0c","from":"north-mill-fire-inquiry-1887",
 "linked":[{"slug":"ashford-courier-1887-03-10","title":"Ashford Courier, 10 March 1887 (the inquiry)",
            "type":"source","summary":"...","relation":"appears-in","reason":"prints each witness's evidence"}],
 "similar":[...],"earlier":[...],"pages_left":5}
```

- `linked`: the current page's relations, both directions, with the type and the reason
  taken from the page, ranked by how well each page's summary matches the question.
- `similar`: pages whose summaries resemble the question and the current page.
- `earlier`: unvisited candidates shown earlier, for backtracking.

```bash
wiki nav search a41f0c "Which mill workers were questioned?"   # when nothing fits
wiki nav end a41f0c --cited north-mill-fire-inquiry-1887,ashford-courier-1887-03-10   --gap "cost of the inquiry"
wiki nav log a41f0c                                          # review the steps and reasons
```

`nav end` runs before the answer is written, and returns what the answer must reflect:

```json
{"session":"a41f0c","pages_read":["north-mill-fire-inquiry-1887"],
 "cited":["north-mill-fire-inquiry-1887","ashford-courier-1887-03-10"],
 "cited_without_reading":["ashford-courier-1887-03-10"],
 "not_covered":[{"part":"cost of the inquiry","checked":"searched the wiki: best match Town council § Budget (none)"}],
 "sources":"From the wiki: Inquiry into the North Mill fire. From page summaries only: Ashford Courier, 10 March 1887 (the inquiry). Not found in the wiki: cost of the inquiry."}
```

`cut_short` lists pages whose text was cut and never continued. Each `--gap` is checked:
the tool searches the wiki for it, and while a well-matched section is unread it refuses
with `possible-answer`, naming the page and section to read first. At the page limit it
reports the gap instead.

## Coverage

`nav start`, each `--part` and `nav search` report a `coverage` from the reranker's top
score, so an agent can tell "the wiki may not cover this" from "I have not read enough":

| Coverage | jina-turbo score | MiniLM score |
|---|---|---|
| `good` | 0 or more | 0 or more |
| `weak` | −1 to 0 | −3 to 0 |
| `none` | below −1 | below −3 |

The bands come from [model selection](model-selection.md): jina-turbo's median top score
is +0.70 for answerable questions and −1.21 for unanswerable ones. With no reranker, or
one not measured, coverage is `unknown`. A label is a hint, not proof; `nav end --gap`
is the check.

## Rules the session enforces

| Rule | Refusal code |
|---|---|
| `--why` is required, at most 400 characters | `why-required`, `why-too-long` |
| A page already read cannot be read again without naming a section, `--more` or `--full` | `already-read` |
| At most `--max-pages` pages (default 6) | `page-limit` |
| `nav search` at most 3 times, never the same question twice | `search-limit`, `repeated-search` |
| `--more` only after a read that was cut | `nothing-more` |
| At most 5 `--part` | `too-many-parts` |
| `nav end --gap` while a well-matched section is unread | `possible-answer` |
| Nothing after `nav end` | `session-ended` |

A refused step exits with status 1 and, with `--format json`, prints
`{"error": "<code>", "message": "..."}`.

## When nothing looks useful

1. Take an `earlier` candidate.
2. `wiki nav search <session> "<the open question>"`, which excludes pages already read.
3. `wiki nav end` with a `--gap` for each part still open, and answer with what was found,
   stating what is missing and what was checked.

## Storage

Sessions, their steps (with each `--why`), and every candidate shown are stored in the
cache database and deleted after 14 days. The question's vector is stored with the
session, so reads and candidates never load the embedding model; only `start` and
`search` do.

## Other commands

- `wiki suggest <slug>`: pages the page (or its raw text) names but does not link,
  pages sharing several of its linked pages, and pages with similar summaries. The
  ingest guide runs it on each new page.
- `wiki unwritten`: link targets with no page, most-linked first. The lint guide runs it.
- `wiki orphans`: pages nothing relates to. The lint guide runs it.
- `wiki neighbors <slug>`: a page's relations without a session.

---

**Next:** [Evaluating search](evaluation.md): measure search on your own wiki. · [All docs](README.md)
