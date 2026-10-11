# Navigation

`wiki nav` lets an agent answer a question by reading as little as possible: start
from search, read one section at a time, and choose the next page from summaries and
relation reasons instead of opening candidates to see what they contain.

## A session

```bash
wiki nav start "Who gave evidence at the inquiry into the North Mill fire?" --format json
```

```json
{"session":"a41f0c","max_pages":6,"results":[
 {"slug":"north-mill-fire-inquiry-1887","title":"Inquiry into the North Mill fire",
  "type":"event","score":5.21,"summary":"On 9 March 1887 the town council ...","section":"... > What happened"}]}
```

```bash
wiki nav read a41f0c north-mill-fire-inquiry-1887 \
  --why "Need the witness list; the inquiry's event page is the most direct source." --format json
```

Returns the page summary, the section that best matches the question (`content`),
the page's other sections, and `pages_left`. The section is the one among sections
of at least 200 characters whose stored vector is closest to the question's vector,
saved at `nav start`, so a read loads no model and takes about 150 ms. Without
embeddings the reranker chooses, then keyword match. On one wiki's evaluation set
the reranker picked the same section on 51 of 68 answer pages, with no clear winner
on the rest. Ask for `--section "<heading>"` or `--full` when the match
is not enough; other parts of a page already read do not count against the limit.

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
wiki nav end a41f0c --cited north-mill-fire-inquiry-1887,ashford-courier-1887-03-10
wiki nav log a41f0c                                          # review the steps and reasons
```

## Rules the session enforces

| Rule | Refusal code |
|---|---|
| `--why` is required, at most 400 characters | `why-required`, `why-too-long` |
| A page already read cannot be read again (other sections can) | `already-read` |
| At most `--max-pages` pages (default 6) | `page-limit` |
| `nav search` at most 3 times, never the same question twice | `search-limit`, `repeated-search` |
| Nothing after `nav end` | `session-ended` |

A refused step exits with status 1 and, with `--format json`, prints
`{"error": "<code>", "message": "..."}`.

## When nothing looks useful

1. Take an `earlier` candidate.
2. `wiki nav search <session> "<the open question>"`, which excludes pages already read.
3. Stop and answer with what was found, stating what is missing.

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
