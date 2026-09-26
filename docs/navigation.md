# Navigation

`wiki nav` lets an agent answer a question by reading as little as possible: start
from search, read one section at a time, and choose the next page from summaries and
relation reasons instead of opening candidates to see what they contain.

## A session

```bash
wiki nav start "Who attended the Situation Room meeting with Boebert?" --format json
```

```json
{"session":"a41f0c","max_pages":6,"results":[
 {"slug":"situation-room-meeting-2025-11-12","title":"Situation Room meeting with Rep. Boebert",
  "type":"event","score":5.21,"summary":"On the morning of 2025-11-12 ...","section":"... > What happened"}]}
```

```bash
wiki nav read a41f0c situation-room-meeting-2025-11-12 \
  --why "Need the attendee list; the event page is the most direct source." --format json
```

Returns the page summary, the section that best matches the question (`content`),
the page's other sections, and `pages_left`. The section is chosen by the reranker
among sections of at least 200 characters (falling back to vector or keyword
similarity when no reranker is available); a read takes under a second. Ask for `--section "<heading>"` or `--full` when the match
is not enough; other parts of a page already read do not count against the limit.

```bash
wiki nav candidates a41f0c --format json
```

```json
{"session":"a41f0c","from":"situation-room-meeting-2025-11-12",
 "linked":[{"slug":"abc-officials-met-boebert-2025-11-12","title":"ABC News, 2025-11-12 (Boebert meeting)",
            "type":"document","summary":"...","relation":"appears-in","reason":"anonymous sources"}],
 "similar":[...],"earlier":[...],"pages_left":5}
```

- `linked`: the current page's relations, both directions, with the type and the reason
  taken from the page, ranked by how well each page's summary matches the question.
- `similar`: pages whose summaries resemble the question and the current page.
- `earlier`: unvisited candidates shown earlier, for backtracking.

```bash
wiki nav search a41f0c "Which FBI officials were present?"   # when nothing fits
wiki nav end a41f0c --cited situation-room-meeting-2025-11-12,abc-officials-met-boebert-2025-11-12
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
  pages sharing several of its linked pages, and pages with similar summaries. For
  `/ingest`.
- `wiki unwritten`: link targets with no page, most-linked first. For `/lint`.
- `wiki orphans`: pages nothing relates to. For `/lint`.
- `wiki neighbors <slug>`: a page's relations without a session.
