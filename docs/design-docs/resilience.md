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
`empty_content`, `empty_response`, `sitemap_error`, `agent_error`, `llm_error`,
`parse_error`, `unverified_person`, `search_error`, `invalid_domain`, `internal`.

`invalid_domain` (`cli.py`, stage `"cli"`): the input string didn't normalize to a
fetchable domain — a non-http(s) scheme, `localhost`, or a loopback/private/link-local
IP literal. Produced by `_prepare_domains` before any fetching starts, as an immediate
`failed` `DomainResult`, exactly like any other per-domain failure — one bad entry in a
domain list must not stop the rest from running.

## Input hygiene (`cli.py`)

Before any domain is fetched: `normalize_domain` strips scheme/`www.`/path/query/
trailing slash and lowercases (`https://Supabase.com/pricing/` -> `supabase.com`),
rejecting `localhost` and loopback/private/link-local/reserved IPs; `_prepare_domains`
then dedupes case-insensitively (first occurrence wins) and turns anything that fails
to normalize into an `invalid_domain` failure rather than passing it to the fetcher.
Separately, `config.validate_settings` checks the configured LLM provider's API key
(and `DEEPSEEK_BASE_URL`'s scheme) once at startup — before any fetching — so a missing
key fails fast with one message naming the env var and `.env.example`, instead of every
domain independently discovering the same `llm_error` after paying for a fetch.

`empty_response` is a Playwright navigation failure (`ERR_EMPTY_RESPONSE`, connection
reset/refused/closed). `ErrorRecord.message` is one line (first line, capped); the full
Playwright call log is only emitted at DEBUG (`--debug`). After two consecutive
`empty_response` failures on a domain, remaining `discovered_by="guess"` URLs are skipped.

## Rate limits

- Concurrency: `Semaphore(MAX_CONCURRENT_DOMAINS)` across domains; pages within a domain
  fetched sequentially with 0.2–0.6 s jitter (polite, and avoids tripping bot defences).
- LLM 429/overloaded: rely on the SDK's `max_retries` plus one tenacity layer with backoff.

## Must-pass failure scenarios (see QUALITY.md)

- nonexistent domain (`example.invalid`)
- 404 subpage
- `--timeout 1`
- missing `TAVILY_API_KEY`
- invalid LLM key → domains `failed` with `llm_error`, run still completes
- Browser Use disabled or erroring
- a nonexistent domain must fail well inside `DOMAIN_TIMEOUT_S`, every time — see
  `tests/test_dns_failure_timeout.py` and the Progress Log (16 Sep) for the bug this
  guards against: `asyncio.wait_for` cancels a task on timeout and then *awaits* the
  cancellation, so an unbounded op in a cancelled task's `finally` block (`page.close()`
  had none) can block the timeout from ever actually firing. Fixed by bounding every
  Playwright op with no native `timeout=` kwarg (`fetcher._bounded`) and by not
  retrying a DNS resolution failure (permanent — retrying can't fix it).

## Profiling (`--debug`)

`fetcher.py` logs one `PROFILE` line per page fetch (`goto=`, `settle=` with which exit
fired — `idle` / `stable_text` / `cap`, `scroll=`) and one per subpage jitter delay, all
at DEBUG level — visible only under `--debug` (see logging_setup.py: `enrich`'s own
loggers go to DEBUG there, `httpx`/`httpcore` do not). Added 16 Sep to find where
per-domain time actually goes; see build-plan.md's Progress Log for the measured
before/after and the fixes applied (networkidle cap + stable-text early exit, shorter
jitter) and the ones still deferred (concurrent subpage fetch — see tech-debt.md).
