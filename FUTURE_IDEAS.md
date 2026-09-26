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
  models loaded, if per-call model load time at `search` proves too slow.
- **LanceDB migration:** if the corpus grows well past about 1M chunks and
  sqlite-vec's exact search becomes slow.
- **GPU embedding:** Ollama or ONNX Runtime with DirectML on the AMD RX 7900 XT,
  if CPU embedding time becomes a bottleneck.
- **Rust implementation:** a single fast binary, if the tool needs distributing
  or much higher query volume.
