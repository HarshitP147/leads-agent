# Quality Bar

## Definition of done (per milestone)

- Code typed, `ruff check` clean, functions small and named for what they do.
- Unit tests for pure logic added/updated.
- Relevant design doc still true (or updated in the same change).
- Build-plan checkbox ticked + Progress Log entry with the "why" note.

## Tests (`tests/`, no network in unit tests)

| Test | Covers |
|---|---|
| test_discovery.py | link scoring picks /about over /blog; same-site filter; sitemap index parsing (fixture XML) |
| test_cleaner.py | scripts/svg/nav removed; fallback path when trafilatura returns little; token counts shrink |
| test_emails.py | regex + filters (drops `logo@2x.png`, keeps `hello@x.com`) |
| test_verify.py | invented name dropped; middle-name match; bad LinkedIn URL nulled |
| test_scoring.py | empty → 0.0; perfect → ≥0.9; blocked penalty applied |
| test_bot_wall.py | Cloudflare-style fixture HTML detected |
| test_pipeline_failure.py | raised stage, 404 subpage, bot wall, and missing Tavily key degrade safely |
| test_cost.py | exact DeepSeek model pricing, component rollup, unknown-model warning |
| test_search.py | accepted/rejected LinkedIn identity/company/role evidence, literal email filtering, provider failure |

## End-to-end matrix (manual, before submission)

| Command | Expect |
|---|---|
| `python -m enrich postman.com supabase.com vapi.ai` | 3 results; no crash; summary table; output.json valid |
| `python -m enrich example.invalid` | status `failed`, `dns_error`, exit 0 |
| `python -m enrich supabase.com --timeout 1` | `failed`/`partial` with `timeout`, exit 0 |
| a discovered subpage returns 404 | parent domain completes; page/error records `http_404`, exit 0 |
| bot-walled URL | `failed`/`partial` with `bot_wall`, exit 0 |
| unset `TAVILY_API_KEY` and run | runs; route_log notes search skipped |
| `BROWSER_USE_ENABLED=false` | runs deterministic path only |
| `DEEPSEEK_API_KEY=bad` | `llm_error`, run completes |

Validate output: `python -c "import json;from enrich.models import DomainResult as D;[D.model_validate(x) for x in json.load(open('output.json'))]"`.

### M4 run — 17 Sep 2026

Every command/harness exited 0, wrote `output.json`, and passed the Pydantic validation
command above.

| Case | Result |
|---|---|
| nonexistent domain: `example.invalid` | `failed`; two `dns_error` records |
| 404 subpage: `example.com --max-pages 2` | `partial`; `/about` and `/company` recorded as `http_404` |
| timeout: `supabase.com --timeout 1` | `failed`; `timeout` |
| missing Tavily key: `TAVILY_API_KEY=` + `example.com --max-pages 2` | completed; unit route assertion `search_linkedin:skipped_no_tavily_key` |
| bad LLM key: `DEEPSEEK_API_KEY=bad` + `example.com --max-pages 0` | `failed`; `llm_error` |
| bot wall: ScrapingCourse Cloudflare challenge URL via `_fetch_one` + `finalize` | HTTP 403; `failed`; `bot_wall` |

### M7 live run — 17 Sep 2026

`uv run python -m enrich postman.com supabase.com vapi.ai --out output.json` exited 0;
all three results passed `DomainResult.model_validate`.

| Domain | Result | Leaders with LinkedIn | Public emails | Search calls | Est. total cost |
|---|---:|---:|---:|---:|---:|
| postman.com | ok / 0.95 | 3/3 | 6 | 5 | $0.042859 |
| supabase.com | ok / 0.86 | 2/2 | 10 | 5 | $0.042390 |
| vapi.ai | ok / 0.84 | 2/2 | 3 | 4 | $0.033501 |
| **TOTAL** | 3 domains | 7/7 | 19 | 14 | **$0.118750** |

## Output sanity (read it like the reviewer)

- Every leader name visible on the site or on the linked search result.
- Overview is exactly two sentences and not marketing fluff.
- No junk emails.
- Confidence components add up to the shown score.
