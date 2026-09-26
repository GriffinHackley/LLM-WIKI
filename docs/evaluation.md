# Search Evaluation

The evaluation set measures how often search puts the right page first. It is
used to choose the embedding model and reranker, and later as a regression test.

## 1. Pick pages

```bash
wiki eval sample --single 40 --multi 12 --seed 1 > eval-sample.json
```

Output lists random pages (`single`) and linked page pairs (`multi`) with their
paths. The seed makes the sample reproducible.

## 2. Write the questions (AI agent task)

Give an agent this file and `eval-sample.json`. It reads each listed page and
writes `<repo>/eval/questions.yaml` (outside `wiki/`, committed with the wiki).

Instructions for the agent:

- **Single-page questions** (~35 of the `single` pages): one question each that
  only that page answers. Ask what a person would actually ask. **Paraphrase:**
  do not reuse the page's title or distinctive phrases, or keyword search will
  look better than it is.
- **Two-page questions** (~10 of the `multi` pairs): a question that needs both
  pages, typically following the relation (e.g. "How does the editor's save
  reach the database?" across `depends-on`). List both slugs in `answers`.
- **Unanswerable questions** (~5): plausible questions on the wiki's topics that
  no page answers. Check with `wiki search` that nothing answers them.
- **Split:** mark roughly one question in four `split: tune`, the rest
  `split: test`. Model choices are made on `tune`; results are reported on `test`.
- Skip pages too thin to ask about; use spare sampled pages instead.

Format:

```yaml
- id: s01
  question: "What happens to a mitigation edit before it is written?"
  answers: [tms/save-pipeline]
  kind: single        # single | multi | unanswerable
  split: test         # tune | test
- id: m01
  question: "How does saving in the admin editor reach the database?"
  answers: [tms/editor, tms/save-pipeline]
  kind: multi
  split: tune
- id: u01
  question: "Which vendor supplies the office printers?"
  answers: []
  kind: unanswerable
  split: test
```

Page content is sent to Claude for this step. Embeddings and search stay local.

## 3. Download candidate models

Nothing downloads implicitly. Each download is explicit:

```bash
wiki models download --embed-model BAAI/bge-small-en-v1.5 --reranker BAAI/bge-reranker-base
```

| Model | Kind | Download |
|---|---|---|
| `BAAI/bge-small-en-v1.5` | embedding, 384 dims | 0.07 GB |
| `nomic-ai/nomic-embed-text-v1.5` | embedding, 768 dims | 0.52 GB |
| `Qwen/Qwen3-Embedding-0.6B-Q` | embedding, 1024 dims, int8 | 1.12 GB |
| `BAAI/bge-reranker-base` | reranker | 1.04 GB |
| `Xenova/ms-marco-MiniLM-L-12-v2` | reranker | 0.12 GB |
| `jinaai/jina-reranker-v1-turbo-en` | reranker | 0.15 GB |

Models are stored in `<repo>/.cache/models/` (override with `WIKI_MODELS_DIR`).

## 4. Run

```bash
wiki eval run --split tune --embed-model BAAI/bge-small-en-v1.5 --reranker BAAI/bge-reranker-base
wiki eval run --split tune --keyword-only
```

Each embedding model gets its own cache file (`.cache/eval-<model>.sqlite3`), so
comparing models never re-embeds the main cache. Reported metrics:

- `hit_at_1`, `hit_at_3`, `mrr`: over single and multi questions (a hit is any
  answer page at that rank).
- `multi_all_in_top5`: two-page questions with both pages in the top 5.
- `top_score_*_median`: whether the top score separates answerable from
  unanswerable questions (useful for a "nothing relevant" threshold later).
- `latency_ms_p50`: per query, excluding model load.
- `misses`: questions whose answer was not in the top 3.

Choose the smallest model combination within a small margin of the best
`hit_at_3` and `mrr` on `tune`, then confirm on `test`.
