# Architecture

## Top level

```
CLI (cli.py)
  └─ run_all(domains)                     asyncio.gather with Semaphore(MAX_CONCURRENT_DOMAINS)
       └─ per domain: graph.ainvoke(DomainState)   ← one compiled LangGraph, one state per domain
            └─ DomainResult  →  output.json + summary table (rich)
```

One shared Playwright `Browser`; each domain gets its own `BrowserContext` (isolated
cookies, closed in `finally`). The whole per-domain invoke is wrapped in
`asyncio.wait_for(..., DOMAIN_TIMEOUT)` and a top-level try/except that converts any
escaped exception into a `failed` `DomainResult`. That wrapper is the last line of
defence; nodes should already have handled their own errors.

## LangGraph pipeline (per domain)

```
START
  → fetch_home
  → discover_links
  ├─(links thin?)→ agentic_navigate ─┐        Browser Use fallback, capped
  └──────────────────────────────────┴→ fetch_subpages
  → clean_pages
  → extract                                   LLM + with_structured_output
  → verify                                    drop unsupported people/emails
  ├─(leaders missing LinkedIn or none found & TAVILY key set)→ search_linkedin ─┐
  └─────────────────────────────────────────────────────────────────────────────┴→ score
  → finalize → END
```

Conditional edges:

- `route_after_discover`: go to `agentic_navigate` if `BROWSER_USE_ENABLED` and no page
  classified as `team`/`about`/`leadership` was found, or homepage cleaned text < 800 chars.
- `route_after_verify`: go to `search_linkedin` if any verified leader lacks a LinkedIn URL,
  or zero leaders survived verification, and a Tavily key exists.
- `fetch_home` hard failure (DNS error, non-2xx after retries, bot wall with no content):
  short-circuit to `score` → `finalize` with status `failed`. Do not call the LLM on nothing.

## Node contract

Every node:

- is `async def node(state: DomainState) -> dict` returning a **partial update**;
- wraps its body in try/except, appending an `ErrorRecord(stage, kind, message, url)`
  to `errors` instead of raising;
- logs start/end with domain + duration;
- never mutates state in place.

`errors` and `usage_events` use additive reducers (`Annotated[list, operator.add]`).

## Modules

| Module | Responsibility |
|---|---|
| `config.py` | Load `.env` into a frozen `Settings` (pydantic-settings or dataclass). |
| `models.py` | Output Pydantic models (`CompanyProfile`, `Leader`, `DomainResult`, ...). |
| `state.py` | `DomainState` TypedDict + reducers. |
| `fetcher.py` | Playwright page fetch with timeout, retries, bot-wall detection → `FetchedPage`. |
| `discovery.py` | `robots`/`sitemap.xml` parsing + anchor scoring → ranked `CandidateLink`s. |
| `cleaner.py` | HTML → cleaned markdown; token counts before/after. |
| `navigator.py` | Browser Use fallback; returns extra URLs only (never final data). |
| `extractor.py` | Build prompt from cleaned pages; structured LLM call; usage capture. |
| `verify.py` | Name/email/LinkedIn grounding checks. |
| `search.py` | Tavily LinkedIn lookups with result validation. |
| `scoring.py` | Deterministic confidence formula. |
| `cost.py` | Usage events → tokens + USD per domain; pricing table. |
| `graph.py` | Build + compile the StateGraph and routing functions. |
| `cli.py` | Typer CLI, concurrency, writing outputs, summary table. |

Dependency direction: `cli → graph → nodes(modules) → models/state/config`.
Modules never import `graph` or `cli`.

## Debug artifacts

With `--debug`, write `debug/<domain>/<slug>.md` (cleaned text) and
`debug/<domain>/trace.json` (node timings, routes taken). Useful for the Loom.
