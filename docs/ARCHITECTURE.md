# Architecture

**Status (16 Sep):** the baseline (M1–M5) is a **plain async pipeline, no LangGraph, no
Browser Use.** Those two become explicit bonus layers (M8, M9) once the baseline ships —
see the Decisions entry in `docs/exec-plans/active/build-plan.md` for why. Every stage
function is still written as `async def stage(state: DomainState) -> dict` returning a
partial update, specifically so `graph.py` can wire the *same* functions into a
`StateGraph` later without touching their internals — read this doc's two sections as
"what runs today" and "what the same code becomes."

## Top level

```
CLI (cli.py)
  └─ run_all(domains)                     asyncio.gather with Semaphore(MAX_CONCURRENT_DOMAINS)
       └─ per domain: pipeline.run_pipeline(domain, settings)   ← plain async function, one state per domain
            └─ DomainResult  →  output.json + summary table (rich)
```

One shared Playwright `Browser`; each domain gets its own `BrowserContext` (isolated
cookies, closed in `finally`). The whole per-domain call is wrapped in
`asyncio.wait_for(..., DOMAIN_TIMEOUT)` and a top-level try/except that converts any
escaped exception into a `failed` `DomainResult`. That wrapper is the last line of
defence; stages should already have handled their own errors.

## Baseline pipeline (M1–M5): `pipeline.py`

Plain sequential `await`s, no graph engine:

```
async def run_pipeline(domain: str, settings: Settings) -> DomainResult:
    state = {"domain": domain}
    _merge(state, await fetch_home(state))
    if is_hard_failure(state):            # DNS error, non-2xx after retries, bot wall
        return finalize(state)            # never call the LLM on nothing
    _merge(state, await discover_links(state))
    _merge(state, await fetch_subpages(state))
    _merge(state, await clean_pages(state))
    _merge(state, await extract(state))
    _merge(state, await verify(state))
    _merge(state, await score(state))
    return finalize(state)
```

`_merge` applies a stage's partial-update dict in place, concatenating the three
additive-reducer fields (`errors`, `usage_events`, `route_log`) and overwriting
everything else — the same merge semantics `StateGraph` applies automatically, done by
hand since there's no graph engine in the baseline. This is exactly what `pipeline.py`
does (implemented in M1, extended stage-by-stage through M2–M4); porting to M8 later is
mechanical because the stage functions themselves never change. No `agentic_navigate`
or `search_linkedin` calls in the baseline: those only exist once M9/M7 land.

## Bonus orchestration (M8+): LangGraph pipeline

Once M8 lands, `graph.py` compiles the **same stage functions** above into a `StateGraph`,
adding the two bonus branches:

```
START
  → fetch_home
  ├─(hard failure)→ score ─────────────────────────────────────────────────────┐
  └─(ok)→ discover_links                                                       │
  ├─(links thin? M9 only)→ agentic_navigate ─┐  Browser Use fallback, capped   │
  └────────────────────────────────────────────┴→ fetch_subpages               │
  → clean_pages → extract → verify                                             │
  ├─(missing LinkedIn & TAVILY key set, M7 only)→ search_linkedin ─┐           │
  └───────────────────────────────────────────────────────────────┴→ score ←───┘
  → finalize → END
```

Conditional edges (`graph.py`, bonus):

- `route_after_fetch_home`: `score` (skipping the LLM entirely) on a hard `fetch_home`
  failure; else `discover_links`. This branch is the one piece of routing logic the
  baseline *also* needs (see the `is_hard_failure` check above) — everything else below
  is bonus-only.
- `route_after_discover` (M9): `agentic_navigate` if `BROWSER_USE_ENABLED` and no page
  classified as `team`/`about`/`leadership` was found, or homepage cleaned text < 800 chars.
- `route_after_verify` (M7): `search_linkedin` if any verified leader lacks a LinkedIn URL,
  or zero leaders survived verification, and a Tavily key exists.

## Stage contract

Every stage (whether called directly by `pipeline.py` or wired as a LangGraph node by
`graph.py`):

- is `async def stage(state: DomainState) -> dict` returning a **partial update**;
- wraps its body in try/except, appending an `ErrorRecord(stage, kind, message, url)`
  to `errors` instead of raising;
- logs start/end with domain + duration;
- never mutates state in place.

`errors` and `usage_events` use additive reducers (`Annotated[list, operator.add]`) in
`state.py` — this is LangGraph's merge convention, kept from M0 even though the baseline
pipeline doesn't strictly need it, so `state.py` doesn't change shape between M5 and M8.

## Modules

| Module | Responsibility |
|---|---|
| `config.py` | Load `.env` into a frozen `Settings` (pydantic-settings or dataclass). |
| `models.py` | Output Pydantic models (`CompanyProfile`, `Leader`, `DomainResult`, ...). |
| `state.py` | `DomainState` TypedDict + reducers. |
| `fetcher.py` | Playwright page fetch with timeout, retries, bot-wall detection → `FetchedPage`. |
| `discovery.py` | `robots`/`sitemap.xml` parsing + anchor scoring → ranked `CandidateLink`s. |
| `cleaner.py` | HTML → cleaned markdown; token counts before/after. |
| `extractor.py` | Build prompt from cleaned pages; structured LLM call (DeepSeek); usage capture. |
| `verify.py` | Name/email/LinkedIn grounding checks. |
| `scoring.py` | Deterministic confidence formula. |
| `pipeline.py` | **(M1–M5, baseline)** `run_pipeline(domain, settings)`: sequential async orchestrator, calls each stage in order, applies partial updates, builds the final `DomainResult`. Started in M1 (fetch+discover only); extended through M2–M4. |
| `search.py` | **(bonus, M7)** Tavily LinkedIn lookups with result validation. |
| `cost.py` | **(bonus, M6)** Usage events → tokens + USD per domain; pricing table. |
| `graph.py` | **(bonus, M8)** Compiles `pipeline.py`'s stage functions into a `StateGraph` + routing functions. Stub until then. |
| `navigator.py` | **(bonus, M9)** Browser Use fallback; returns extra URLs only (never final data). Stub until then. |
| `cli.py` | Typer CLI, concurrency, writing outputs, summary table. Calls `pipeline.run_pipeline` (wired in M1) until M8, then `graph.ainvoke`. |

Dependency direction: `cli → pipeline (or, post-M8, graph) → stages(modules) →
models/state/config`. Modules never import `pipeline`, `graph`, or `cli`.

## Debug artifacts

With `--debug`, write `debug/<domain>/<slug>.md` (cleaned text) and
`debug/<domain>/trace.json` (stage timings, routes taken). Useful for the Loom.
