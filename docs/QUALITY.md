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
| test_search.py | accepted/rejected LinkedIn identity/company/role evidence, literal email filtering, provider failure, Tavily call budget + early-stop, subdomain email rejection |
| test_cli.py | domain normalization (URL -> bare host), localhost/private-IP/non-http rejection, case-insensitive dedupe, config fail-fast |
| test_redirect.py | cross-domain homepage redirect adopts the final domain; same-domain and www-only redirects don't |
| test_prompt_injection.py | hidden-div/HTML-comment payload never reaches candidate emails, LinkedIn links, cleaned markdown, or the final profile — full pipeline (real LLM if a key is configured) plus a hand-built worst-case extraction through verify.py |

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

### Requested failure-matrix rerun — 17 Sep 2026, 16:53–16:57 IST

Each command ran separately. Every command exited 0 and its resulting `output.json`
passed `DomainResult.model_validate`.

| # | Actual command | Exit | Actual output/result | Pass |
|---:|---|---:|---|:---:|
| 1 | `.venv/bin/python -m enrich example.invalid` | 0 | `failed`; homepage `error`; two `dns_error` records for bare + `www` attempts | PASS |
| 2 | `.venv/bin/python -m enrich example.com --max-pages 2` | 0 | `partial`; `/` HTTP 200; `/about` and `/company` HTTP 404, both `status=not_found` with `http_404` errors | PASS |
| 3 | `.venv/bin/python -m enrich vapi.ai --timeout 1` | 0 | `failed`; `cli/timeout: exceeded 1s`; TOTAL duration 1.0s | PASS |
| 4 | `env TAVILY_API_KEY= .venv/bin/python -m enrich vapi.ai` | 0 | `partial`; `search_calls=0`; console logged `route_log=search_linkedin:skipped_no_tavily_key`; expected `/team` 404 only | PASS |
| 5 | `env DEEPSEEK_API_KEY=bad .venv/bin/python -m enrich example.com --max-pages 0` | 0 | DeepSeek HTTP 401 captured as `extract/llm_error`; final status `failed` | PASS |
| 6 | `.venv/bin/python -m enrich postman.com --debug` | 0 | `ok`, confidence 0.97; 7/7 pages HTTP 200; no errors; `bot_wall_count=0`; $0.042871 | PASS* |

\* Postman did not present a bot wall in this live run, so the detector correctly did
not create a false-positive error. The deterministic Cloudflare fixture in
`test_bot_wall.py` covers detection, and `test_pipeline_failure.py` covers degradation
to a completed result when a bot wall is present. Post-matrix verification:
`uv run pytest -q` → 60 passed; `ruff check` and `ruff format --check` passed for
`enrich/` and `tests/`.

## Output sanity (read it like the reviewer)

- Every leader name visible on the site or on the linked search result.
- Overview is exactly two sentences and not marketing fluff.
- No junk emails.
- Confidence components add up to the shown score.
