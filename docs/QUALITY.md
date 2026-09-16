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
| test_graph_failure.py | graph with a fetcher stub that raises still returns `failed` DomainResult |

## End-to-end matrix (manual, before submission)

| Command | Expect |
|---|---|
| `python -m enrich postman.com supabase.com vapi.ai` | 3 results; no crash; summary table; output.json valid |
| `python -m enrich example.invalid` | status `failed`, `dns_error`, exit 0 |
| `python -m enrich supabase.com --timeout 1` | `failed`/`partial` with `timeout`, exit 0 |
| unset `TAVILY_API_KEY` and run | runs; route_log notes search skipped |
| `BROWSER_USE_ENABLED=false` | runs deterministic path only |
| `ANTHROPIC_API_KEY=bad` | `llm_error`, run completes |

Validate output: `python -c "import json;from enrich.models import DomainResult as D;[D.model_validate(x) for x in json.load(open('output.json'))]"`.

## Output sanity (read it like the reviewer)

- Every leader name visible on the site or on the linked search result.
- Overview is exactly two sentences and not marketing fluff.
- No junk emails.
- Confidence components add up to the shown score.
