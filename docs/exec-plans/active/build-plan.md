# Exec Plan — Lead Enrichment Agent

Owner: Harshit. Deadline: night of 18 Sep 2026. Target submit: 18 Sep afternoon.
When all milestones are done, move this file to `exec-plans/completed/`.

Work milestones in order. Each ends with a runnable state and a Progress Log entry.

Sequenced to match the assignment's own step order (fetch/discover → clean → extract/
verify/score → resilience → ship baseline → bonuses), not by module. See Decisions below
for the 16 Sep re-sequencing and why LangGraph/Browser Use moved from "day 1 spine" to
"bonus layer."

## Day 1 — 16 Sep (tonight)

### M0 · Scaffold
- [x] Confirm `.venv` Python >= 3.11; recreate if not
- [x] `pip install -r requirements.txt`; resolve conflicts; `playwright install chromium`
- [x] Run the verify-before-use checklist in `docs/references/stack.md`; log findings
- [x] Create `enrich/` package per ARCHITECTURE.md with empty typed stubs; `python -m enrich --help` works
- [x] `config.py` loads `.env`; `models.py` + `state.py` from schemas.md
- [x] `git init`, first commit

### M1 · Fetch + discover (assignment step 1)
- [x] `fetcher.py`: shared browser, per-domain context, resource blocking, timeouts, retries
- [x] Bot-wall detection + one reload
- [x] `discovery.py`: robots/sitemap via httpx; anchor scoring; guesses; page cap
- [x] Email + LinkedIn link harvesting from every page
- [x] Smoke: fetch + discover for the 3 domains, print status/title/len + discovered URLs

### M2 · Clean (assignment step 2)
- [ ] `cleaner.py` with fallback + token counts (raw vs clean); TEAM CARDS extraction
- [ ] `--debug` writes cleaned markdown; eyeball all 3 domains
- [ ] Tests: discovery, cleaner, emails, bot_wall

## Day 2 — 17 Sep

### M3 · Extraction (DeepSeek) + verification + confidence (assignment step 3)
- [ ] `pipeline.py`: plain async `fetch → discover → clean → extract → verify → score →
      finalize`, each stage a small function that catches its own errors and appends
      `ErrorRecord`s. Keep stage signatures `async def stage(state) -> dict` so they can
      become LangGraph nodes unchanged in M8.
- [ ] `extractor.py`: `ChatDeepSeek` (`langchain-deepseek`), `with_structured_output(
      LLMExtraction, method="function_calling", include_raw=True)`; on `parsing_error` one
      repair retry, then fall back to `method="json_mode"` +
      `LLMExtraction.model_validate_json(raw.content)`; usage events from `usage_metadata`
- [ ] Add `deepseek` to `config.Settings.llm_provider` Literal + `deepseek_api_key` field
      (small, do alongside `extractor.py` — not before)
- [ ] `verify.py` + tests
- [ ] `scoring.py` + tests
- [ ] Status rule (ok/partial/failed) in `finalize`
- [ ] End-to-end on 3 domains producing `output.json`

### M4 · Resilience (assignment step 4)
- [ ] Stage-level error capture everywhere; domain + run level wrappers
- [ ] Incremental output writes
- [ ] Run the full failure matrix in QUALITY.md — 404, bot walls, timeouts, missing
      elements, bad API key — fix until all pass
- [ ] `test_pipeline_failure.py` (a stage stub that raises still yields a `failed` `DomainResult`)

### M5 · Ship baseline (assignment: working baseline before bonuses)
- [ ] README: what/why, linear pipeline diagram, setup (.env), run, sample output excerpt,
      design decisions (deterministic-first, verification, confidence formula), limitations
- [ ] Final clean baseline run → commit `output.json` for the 3 domains
- [ ] `ruff`, `pytest` green

## Day 3 — 18 Sep (morning)

### M6 · Bonus: cost tracking
- [ ] `cost.py` pricing (check official pages, note date) + rich summary table
- [ ] Confirm usage events from M3's extractor roll up correctly per domain

### M7 · Bonus: Tavily LinkedIn search
- [ ] `search.py` (Tavily) as an optional pipeline stage after `verify`, gated on
      `TAVILY_API_KEY` being set (skip silently, log to `route_log`, if not)

### M8 · Bonus: LangGraph orchestration
- [ ] `graph.py`: compile M3's stage functions into a `StateGraph` with the conditional
      edges from ARCHITECTURE.md's bonus diagram (`route_after_fetch_home`,
      `route_after_discover`, `route_after_verify`)
- [ ] `cli.py` invokes `graph.ainvoke()` instead of `pipeline.run_domain()` once this lands

### M9 · Bonus: Browser Use fallback
- [ ] `navigator.py` (Browser Use) wired into the LangGraph path via
      `route_after_discover`, capped by `BROWSER_USE_MAX_STEPS`, costed via
      `Agent(calculate_cost=True)` / `history.usage`

### M10 · Ship (final)
- [ ] README: add bonus sections (LangGraph diagram in mermaid, cost table, search)
- [ ] Final clean run with all bonuses → commit `output.json`
- [ ] `ruff`, `pytest` green; remove dead code; push to GitHub (public)
- [ ] Human: record Loom (structure 30s → live run 60s → output + confidence 45s → failure demo 20s)
- [ ] Human: send email (repo, Loom, LinkedIn, explicit "Yes" to the 40% operations question)

## Decisions

- 16 Sep: Leaders must be grounded in page text or search results; unverified are dropped.
- 16 Sep: Confidence = documented weighted blend, not raw LLM self-rating.
- 16 Sep (supersedes the line below): re-sequenced to follow the assignment's own step
  order. M1–M5 is a plain async `pipeline.py` (fetch → discover → clean → extract →
  verify → score → finalize) with **no LangGraph and no Browser Use** — those are now
  explicit bonus milestones (M8, M9), matching product-spec.md's own bonus table
  ("Agentic framework / dynamic navigation"). Every stage function still takes the
  shape `async def stage(state: DomainState) -> dict` so M8 can wire the *same*
  functions into a `StateGraph` without touching their internals — this is the whole
  reason `state.py`'s `DomainState` (with its additive reducers, built for LangGraph
  merge semantics) is kept as-is rather than swapped for a plain dataclass now: paying
  for LangGraph's state shape once, in M0, is cheaper than migrating twice. LLM provider
  switched to DeepSeek (`langchain-deepseek`, `ChatDeepSeek`) — see stack.md for the
  verified model ids and structured-output findings, and extraction-and-verification.md
  for the function-calling/json_mode fallback strategy.
- ~~16 Sep: LangGraph pipeline; Playwright deterministic path first; Browser Use only as
  capped fallback.~~ Superseded same day — see above.

## Progress Log

<!-- YYYY-MM-DD HH:MM — milestone — what changed — why it works this way (3–5 lines) -->

2026-09-16 — M0 — Scaffolded `enrich/` (13 modules + `__main__.py`), fully implemented
`config.py`/`models.py`/`state.py`, `git init` + first commit. `requirements.txt` needed
4 pins relaxed (`langchain-anthropic`, `langchain-openai`, `python-dotenv`, `rich`) because
`browser-use==0.13.10` pins its own LLM/util deps to exact versions — details and the full
resolution reasoning are in `docs/references/stack.md` Gotchas. Every "internal" dataclass
the schema doc wants "next to the module that produces them" (`FetchedPage`, `CandidateLink`,
`CleanPage`, `UsageEvent`, ...) lives in its producer module, and `state.py` imports *them*
rather than the other way around; each producer module only references `DomainState` behind
`if TYPE_CHECKING` + `from __future__ import annotations`, so there's no runtime import
cycle even though `state.py` -> modules -> (type-only) `state.py` looks circular on paper.
`graph.py` wires the full node/edge shape from ARCHITECTURE.md, including the
`fetch_home`-hard-failure short-circuit to `score`, even though every node body is still
`raise NotImplementedError` — this exercises `StateGraph.compile()` now instead of waiting
until M3 to discover a wiring mistake. `cli.py`'s `run_domain` already wraps
`graph.ainvoke()` in the domain-level try/except from resilience.md, so `python -m enrich
<domain>` degrades to a `failed` `DomainResult` today (via the expected
`NotImplementedError`) instead of crashing — that behavior doesn't need to change as real
nodes replace the stubs.

2026-09-16 — M1 — Fully implemented `fetcher.py` (shared Playwright `Browser` + one
`BrowserContext` per domain cached in a module-level dict, resource blocking for
image/media/font/stylesheet, tenacity retries with 429 `Retry-After` handling and 5xx
retry, bot-wall detection + one reload, DNS-error classification verified live against
`net::ERR_NAME_NOT_RESOLVED`) and `discovery.py` (robots→sitemap via `httpx` with
sitemapindex depth-1 support, anchor scoring, guesses, email/LinkedIn harvesting shared
between `discover_links` and `fetch_subpages`). Verified every Playwright/httpx/tenacity
API against installed source before using it (`route.request.resource_type`,
`response.headers`, `AsyncClient(follow_redirects=...)` defaulting to `False`, etc.) —
no surprises there. The real lesson came from the smoke test against the actual 3
domains, which is exactly why that checklist item exists rather than trusting the design
doc's keyword table on faith: naive substring matching let `"mission"` match inside
`"submissions"` (vapi.ai got tagged "about" for a speaker-CFP page), and bare
single-word keywords (`"management"`, `"team"`, `"story"`) collided with ordinary
marketing copy on content-heavy sites — vapi.ai sells "X management agent" pages as a
product, so all 6 of its picked candidates were junk before the fix. Fixed by (1)
switching to whole-token matching, never substring-in-word, and (2) a specificity
discount — a bare keyword's contribution is divided by how many tokens make up the path's
last segment or the anchor text, so a dedicated `/team` page or a plain "About" nav link
still outranks a four-word product slug or a repeated "Read the story →" CTA. Dropped
bare `"management"` entirely (replaced with compound forms like `management-team`) since
no amount of discounting saved it. Re-ran the smoke test after each fix; final candidate
lists for all 3 domains are clean. Updated `discovery-and-cleaning.md`'s keyword table
and added a footnote explaining why. Also fixed a duplicate-email bug in
`fetch_subpages` (the same address appearing on both the homepage and a subpage wasn't
deduped across pages, only within a single page's regex pass) — now dedupes globally by
lowercased address/URL every time a page's harvest is merged in. Not committed yet —
leaving that for an explicit request per policy.
