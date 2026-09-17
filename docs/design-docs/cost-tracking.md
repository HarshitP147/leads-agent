# Cost Tracking (`cost.py`)

## Collection

- `UsageEvent(component, model, input_tokens, output_tokens, search_calls, estimated)`.
- Extraction: read `usage_metadata` from the raw `AIMessage` (`include_raw=True`).
- Navigation: from Browser Use's own usage/history (see browser-use-fallback.md).
- Search: 1 event per Tavily call.
- Events accumulate in `state.usage_events` (additive reducer), keyed per domain by design.

## Pricing

`PRICING` dict in `cost.py`: `{model_id: (usd_per_mtok_input, usd_per_mtok_output)}` plus
`TAVILY_USD_PER_CALL`. Values filled from https://api-docs.deepseek.com/quick_start/pricing
on **17 Sep 2026** (cache-miss peak-hour list price for `deepseek-flash` and
`deepseek-v4-pro`). Unknown model → cost 0 with a warning, never a crash. CLI prints
est $ to 6 decimal places.

## Reporting

- Each `DomainResult.usage` has totals and `by_component` USD.
- CLI prints a rich table at the end: domain | status | confidence | pages ok/total |
  in tok | out tok | est $ | duration.
- Also print total raw→clean token reduction %, which is the evidence for the
  "no raw HTML" requirement.
