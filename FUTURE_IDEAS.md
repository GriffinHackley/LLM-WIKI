# Future Ideas

Possible improvements deferred from the main upgrade plan. Nothing here is
committed scope; promote an item into the plan only after deciding to build it.

## Token budget for traversal sessions

Pages vary widely in size, so a page-count limit alone does not bound cost: a
6-page limit could mean 3k tokens or 60k. Enforce a character or token budget per
`wiki-nav` session alongside the page limit. `wiki-nav read` would refuse (or
offer a truncated or section-only read) once the remaining budget cannot cover the
requested page, and `candidates` could show each page's approximate size so the
agent can weigh cost when choosing.

## Other items deferred during planning

- **Persistent model server:** a local process that keeps embedding and reranker
  models loaded, if per-call model load time at `search` proves too slow. It would
  take `search` from about 1.0 s to about 0.3 s: about 0.45 s goes to importing
  onnxruntime and loading both models, 0.2 s to reranking.
- **LanceDB migration:** if the corpus grows well past about 1M chunks and
  sqlite-vec's exact search becomes slow.
- **Quantized vectors:** store int8 or binary vectors in sqlite-vec to cut the
  ~230 MB of float32 chunk vectors at 30k pages and speed up the ~150 ms exact
  vector scan, rescoring the top candidates with full-precision vectors.
- **Embed plain text:** send link-stripped chunk text (`[[a|B]]` -> `B`) to the embedding
  model and reranker instead of raw Markdown. Linking claim IDs in the Politics wiki moved
  one evaluation question from rank 3 to 4, which suggests the markup is noise.
  *Tested for the reranker (2026-09-26), no effect:* on the 50 Politics questions, plain
  text left hit@3 at 0.778 and moved MRR from 0.639 to 0.650; four questions changed rank,
  two up and two down. Replacing bare `[[slug]]` links with page titles did no better.
  Link markup is 7% of chunk characters and the reranker ignores it. Not worth
  re-embedding for; revisit only if a wiki's pages are much denser in links.
- **GPU embedding:** Ollama or ONNX Runtime with DirectML on the AMD RX 7900 XT,
  if CPU embedding time becomes a bottleneck.
- **Rust implementation:** a single fast binary, if the tool needs distributing
  or much higher query volume.
