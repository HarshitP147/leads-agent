# Resilience

Goal: the run always finishes and always writes `output.json`, even if every domain fails.

## Layers

1. **Request level** (`fetcher.py`): per-navigation timeout, tenacity retries on
   transient errors, 429 `Retry-After`, bot-wall detection + one reload.
2. **Stage level**: every stage catches exceptions and appends `ErrorRecord`; returns a
   safe partial update (empty lists, `None`).
3. **Domain level** (`cli.py`): `asyncio.wait_for(pipeline.run_pipeline(...),
   DOMAIN_TIMEOUT=240s)` inside try/except → on anything escaping (in practice, only a
   timeout — `run_pipeline` already turns its own stage exceptions into a `failed`
   `DomainResult` rather than raising), build a `failed` `DomainResult`. (Becomes
   `graph.ainvoke(...)` once the M8 LangGraph bonus lands — same wrapper, same contract.)
4. **Run level**: `asyncio.gather(..., return_exceptions=True)`; results written
   incrementally (rewrite `output.json` after each domain completes) so a Ctrl-C still
   leaves useful output. Browser closed in `finally`.

## Failure taxonomy (`ErrorRecord.kind`)

`dns_error`, `timeout`, `http_404`, `http_4xx`, `http_5xx`, `rate_limited`, `bot_wall`,
`empty_content`, `sitemap_error`, `agent_error`, `llm_error`, `parse_error`,
`unverified_person`, `search_error`, `internal`.

## Rate limits

- Concurrency: `Semaphore(MAX_CONCURRENT_DOMAINS)` across domains; pages within a domain
  fetched sequentially with 0.5–1.5 s jitter (polite, and avoids tripping bot defences).
- LLM 429/overloaded: rely on the SDK's `max_retries` plus one tenacity layer with backoff.

## Must-pass failure scenarios (see QUALITY.md)

- nonexistent domain (`example.invalid`)
- 404 subpage
- `--timeout 1`
- missing `TAVILY_API_KEY`
- invalid LLM key → domains `failed` with `llm_error`, run still completes
- Browser Use disabled or erroring
