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
- [x] `pipeline.py` created (partial): `run_pipeline(domain, settings) -> DomainResult`
      running `fetch_home → discover_links → fetch_subpages` only; later stages are
      `# TODO` comments, not stubs that raise — the run path must stay exception-free
- [x] `cli.py` rewired to call `pipeline.run_pipeline`; `graph`/`navigator` are no longer
      imported anywhere on the run path
- [x] `--debug` prints a rich table of pages picked (url, kind, discovered_by, status,
      http_status) per domain
- [x] `tests/test_pipeline_smoke.py`: stubs the fetcher, asserts `run_pipeline` returns a
      `DomainResult` without raising (happy path + a stage that raises)
- [x] Collection sections (blog/docs/changelog/... children dropped, index kept as
      filler) + `tests/test_discovery.py`; interim fetch-health status rule; `--max-pages`
      flag; `--debug` candidate-considered audit table (aggregated where volume is
      unbounded) — see Progress Log for the full M1-review write-up

### M2 · Clean (assignment step 2)
- [x] `cleaner.py` with fallback + token counts (raw vs clean); TEAM CARDS extraction
- [x] Wire `clean_pages` into `pipeline.py` (status now `ok`/`partial`/`failed` via the
      M1 interim fetch-health rule — extraction still doesn't exist, that's M3)
- [x] `--debug` writes cleaned markdown (`debug/<domain>/<kind>-<slug>.md`); eyeballed
      all 3 domains
- [x] Tests: `test_cleaner.py`, `test_emails.py` (`test_discovery.py` already existed
      from the M1 review pass) — `test_bot_wall.py` still outstanding, not part of this
      pass's instructions

## Day 2 — 17 Sep

### M3 · Extraction (DeepSeek) + verification + confidence (assignment step 3)
- [ ] Extend `pipeline.py` (created partial in M1) with `extract → verify → score →
      finalize`, once `clean_pages` lands in M2. Each stage stays a small function that
      catches its own errors and appends `ErrorRecord`s; signatures stay
      `async def stage(state) -> dict` so they can become LangGraph nodes unchanged in M8.
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

2026-09-16 — M1 (follow-up) — Found and fixed a real gap: docs described `pipeline.py`
+ `cli.py` wired to it, but only the docs had been updated — `cli.py` still called
`graph.build_graph().ainvoke(...)`, which fails immediately since every graph node body
is `raise NotImplementedError` (M0 scaffolding, never meant to be the real run path).
Created `enrich/pipeline.py` with `run_pipeline(domain, settings, *, debug=False) ->
DomainResult`, running only `fetch_home → discover_links → fetch_subpages` for M1; the
remaining stages are `# TODO` comments, not stub calls, so the run path never raises
`NotImplementedError` — `status` is unconditionally `"failed"` at this milestone because
`profile` is always `None` (correct per the schemas.md status rule, not a bug: M1 has no
extraction yet, so judge it by the `--debug` pages table, not by `status`). Since
`pipeline.py` isn't a graph engine, it can't rely on LangGraph's automatic reducer
merging for `state.py`'s `Annotated[list, operator.add]` fields — added a small `_merge`
helper that concatenates `errors`/`usage_events`/`route_log` and overwrites everything
else, which is exactly the "pay once in M0, port mechanically in M8" trade `state.py`'s
design banked on. Rewired `cli.py`'s `run_domain` to wrap
`asyncio.wait_for(pipeline.run_pipeline(...), domain_timeout_s)` instead of building a
graph; confirmed by import-checking that `enrich.graph`/`enrich.navigator` never load
when only `enrich.cli`/`enrich.pipeline` are imported. Fixed a real UX bug found while
verifying `--debug`: `configure_logging(debug=True)` was setting the *root* logger to
DEBUG, which made httpx/httpcore/asyncio dump full wire-level traffic — now only
`enrich`'s own loggers go to DEBUG; `httpx`/`httpcore`/`asyncio`/`playwright` are pinned
to WARNING regardless. Added a `--debug` rich table of pages picked per domain, and
`tests/test_pipeline_smoke.py` (stubs the fetcher via `monkeypatch`, no network) covering
the happy path and a stage that raises. Updated `ARCHITECTURE.md`'s pseudocode and
`resilience.md` to say `run_pipeline`, not the `run_domain` name I'd used loosely before
(the function is real now, so the name needed to be exact). Verified live:
`python -m enrich vercel.com --debug` (7 pages, clean table), `python -m enrich
postman.com supabase.com vapi.ai --debug` (3 domains concurrently, 7 pages each, no
crash), `python -m enrich example.invalid` (`dns_error` recorded on both the bare and
`www.` candidate, exit 0, valid `output.json`), `pytest -q` (2 passed). `ruff
check`/`format` clean on `enrich/`+`tests/`. Not committed.

2026-09-16 — M1 review fixes (A–E) — Updated `discovery-and-cleaning.md` first, then
code, per instructions.

**A. Collection sections.** Added `COLLECTION_SECTIONS` (blog/docs/changelog/news/press/
customers/careers/jobs/guides/tutorials/resources/learn/events) to `discovery.py`. A URL
whose (locale-adjusted) first segment is in that set and has depth > 1 is dropped before
scoring, from both sitemap and anchor sources; the bare index (`/blog`) survives as a
`kind="other"`, `score=0.5` filler — but only when no real keyword already classified it
(so `/careers` still scores as a real `company` hit; the filler is a fallback, not an
override). Child sitemaps whose URL itself names a collection section
(`/docs/sitemap.xml`) are never fetched — confirmed live: `skipped child sitemap
https://supabase.com/docs/sitemap.xml (section=docs)`. `tests/test_discovery.py` (new,
6 tests, no network) covers exactly the four cases asked for plus the locale-prefix and
ordinary-page paths.

**B. Interim status rule.** `pipeline._interim_status`: `failed` if home didn't fetch,
`ok` if home + ≥1 subpage fetched, `partial` if only home. Documented as a `# TODO M3`
stand-in in `schemas.md` next to the real rule it will replace. Existing pipeline tests
updated (one now asserts `ok`, one asserts `partial`) since the old tests only made sense
against the previous "always failed" placeholder.

**C. Logging.** Flipped from last session's fix: `httpx`/`httpcore` are now WARNING
*unless* `--debug` (previously I'd quieted them even under `--debug`, which fought this
task's explicit spec). Verified: default runs show no wire noise; `--debug` shows it
(confirmed: 99 httpcore/asyncio DEBUG lines in one 3-domain `--debug` run).

**D. `--debug` candidate audit.** `discover_links` now returns `considered_links`
(diagnostic-only — not in `state.py`'s documented schema, never reaches `DomainResult`),
threaded through `pipeline.run_pipeline`'s new `debug_sink` param to `cli.py` for
printing, since the data doesn't survive past `run_pipeline`'s return otherwise. Hit a
real scale problem building this: printing a "considered" row per raw sitemap URL means
per-site volume, not a bounded audit — vercel.com's sitemap alone produced 4305
collection-child drops (1979 `/docs` children, 1509 `/changelog`, ...) and the sitemap
loop's other generic drops (unclassified/off-site/depth>2) would have been just as bad
on top. Fixed in two places: `discovery.py`'s sitemap loop only records the
collection-child reason (the one this task is actually about) per URL, not the generic
ones; `cli.py`'s printer aggregates collection-child/collection-section-sitemap entries
into one row per reason with a count, and only itemizes individually where volume is
naturally bounded (anchor-derived candidates — a homepage's own links, at most dozens).
Confirmed both scale fixes: total output for the 3-domain `--debug` run dropped from
5510 lines with individual per-URL rows to 493 lines with aggregation.

**E. `--max-pages`.** New CLI flag sets `os.environ["MAX_PAGES_PER_DOMAIN"]` before the
first `get_settings()` call — every stage already calls `get_settings()` independently
rather than receiving a shared instance, so an env-var override is the one mechanism that
reaches all of them without threading a new parameter through every stage signature.
Verified: `--max-pages 2` on vercel.com produced exactly 3 pages (home + 2).

**Verification run** (`python -m enrich vercel.com supabase.com harshit147.dev --debug`):
all 3 domains status `ok` (home + subpage fetched — rule B), 7 pages each, candidate
tables show real about/pricing/company/contact hits selected and collection-section
noise correctly aggregated away. `pytest -q`: 9 passed. `ruff check`/`format`: clean.

**Aside, not part of A–E:** stress-testing `example.invalid` repeatedly in quick
succession (rapid-fire headless Chromium launches while debugging D) triggered an
intermittent multi-minute hang inside `fetcher._fetch_one` that I could not reproduce in
isolation on a clean run — `_goto_with_retries` and `page.close()` were independently
fast (4s, 0.01s) every time I tested them alone, and a cooldown before retrying
`_fetch_one` as a whole made it fast again too (4.5s). Reads as sandbox/resource
flakiness from launching Chromium many times in a short window, not a logic bug — but
noting it here since a hang that eventually self-heals via `DOMAIN_TIMEOUT_S` (240s) is
still a bad experience if it recurs. Nothing changed in `fetcher.py` this session; worth
a closer look in M4 if it resurfaces.

2026-09-16 — M1 follow-ups: real hang fix + timing profile — Committed the M1-review
work above (`57315d8`) first. The "sandbox flakiness" call above was wrong — same
symptom reproduces locally, not just in my sandbox. Found and fixed the real cause.

**Root cause.** `asyncio.wait_for(coro, timeout)` does not hard-kill a task when it
times out — it cancels the task, then *awaits* that cancellation to actually finish.
`_fetch_one`'s `finally: await page.close()` had no timeout of its own; if `close()`
ever stalled on an unresponsive CDP round-trip (e.g. a page whose navigation never
resolved cleanly), the domain-level `asyncio.wait_for` in `cli.py` would sit there
waiting for that cleanup to finish, no matter what `DOMAIN_TIMEOUT_S` said. Same class
of gap on `browser.new_context()`, `context.new_page()`, `context.close()`,
`browser.close()`, `playwright.stop()` — none of these accept a native `timeout=` kwarg
(confirmed against installed Playwright source, not memory), so nothing bounded them.
Separately, a nonexistent domain's `ERR_NAME_NOT_RESOLVED` was going through the same
3-attempt retry-with-backoff loop as a transient network error — pointless, since
retrying a DNS failure can't fix it, and it turns one bad domain into ~10-20s of pure
waste per candidate URL.

**Fix.** Added `fetcher._bounded()` — wraps any Playwright call with no native timeout
in `asyncio.wait_for` and converts our own timeout into a normal `PlaywrightError` (a
real external cancellation is `CancelledError`, a `BaseException`, so it's never
swallowed here). Applied to `chromium.launch()` (native `timeout=` now passed, 30s),
`new_context`, `new_page`, `page.close()`, and all three ops in `close_browser()` — each
wrapped in its own try/except in `close_browser()` too, so one stuck context can't
prevent closing the rest. `_goto_with_retries`'s retry predicate now excludes DNS
resolution failures (`_is_retryable_navigation_error`) — everything else (timeouts,
429, 5xx) still retries exactly as before.

**Verification.** `example.invalid` via the CLI: 240s (hung, pre-fix) → 0.6s (post-fix,
was 8.5s pre-review-session before retries got involved). Added
`tests/test_dns_failure_timeout.py`: one test, one event loop, 5 sequential attempts
against a nonexistent domain, each wrapped in `asyncio.wait_for(timeout=15)`. First cut
used `@pytest.mark.parametrize` for the 5 attempts and hung on attempt 2 — a *different*
bug: pytest-asyncio gives each test function its own fresh event loop by default, and
`fetcher.py`'s module-global Playwright driver is bound to whichever loop created it;
reusing it from a second loop hangs. Not a production bug (cli.py's `asyncio.run` is one
loop for the whole process) — a test-design pitfall specific to per-function loop
scope. Rewrote as a single test looping internally instead, which also matches how the
real CLI actually behaves across multiple domains. Ran the whole file 5 separate times
(fresh process each time): 0.98s, 0.96s, 0.95s, 0.92s, 0.94s — all pass, comfortably
under the 15s budget every time. Confirmed a real domain (vercel.com) still fetches
normally after all this (19.1s, 7 pages, `ok`). Full suite: 10 passed.

**Timing profile.** Added per-page `PROFILE` debug logging to `fetcher.py` (`goto=`,
`networkidle=` + whether it hit its cap, `scroll=`, plus jitter delays) — see
resilience.md's new "Profiling" section. Ran `python -m enrich baseten.co vapi.ai
--debug` (7 pages each):

| | vapi.ai (37.8s total) | baseten.co (43.1s total) |
|---|---|---|
| networkidle wait | 23.73s (63%) | 28.01s (65%) |
| jitter (our own delay) | 6.54s (17%) | 6.67s (15%) |
| goto (actual page load) | 3.79s (10%) | 6.87s (16%) |
| scroll | 0.07s (0.2%) | 0.09s (0.2%) |
| unaccounted (content/title RPCs, new_page/close, discovery httpx, launch) | ~3.7s (10%) | ~1.5s (3%) |

Per-page `networkidle` values cluster at 3-5s — right up against the current 5s cap —
for 12 of 13 pages fetched. These are modern marketing sites: analytics beacons, chat
widgets, and similar background polling mean the page essentially never goes truly
idle, so we're paying close to the full cap on almost every page, not the (rare) fast
case. `networkidle` is the dominant cost by a wide margin — roughly two-thirds of total
per-domain time on both sites.

**Proposal (not implemented — numbers only, per instructions):**
1. **Shorter networkidle cap (2s).** Highest leverage, lowest risk. We only need
   settled DOM for cleaned markdown, not a fully quiet network — `domcontentloaded` (our
   `goto` wait condition) already gets usable content well before "idle." Rough
   estimate at a 2s cap, assuming most pages still hit the (lower) ceiling since true
   idle rarely happens on these sites: vapi.ai 37.8s → ~28s (-26%), baseten.co 43.1s →
   ~29s (-32%).
2. **Fetch 2 subpages concurrently per domain.** Second-highest leverage. Subpage
   fetching (excluding home) is currently fully sequential; the goto+networkidle
   portion of that (≈22.25s of vapi.ai's 37.8s) could roughly halve under 2-way
   concurrency in the same `BrowserContext`. Medium effort/risk: needs per-page error
   isolation so one slow/failing page doesn't stall its partner, and jitter probably
   needs rethinking for a concurrent batch (staggering 2 at a time still avoids a full
   6-way burst, so likely keep it, possibly shrunk).
3. **Skip scroll on non-team pages — data doesn't support this.** Scroll costs 0.07s
   and 0.09s *combined across all 7 pages* on each site (<0.2% of total time). This is
   not a meaningful lever; flagging so it doesn't get implemented for imagined savings
   that the profile shows don't exist.
4. **Not asked for, but the same class of low-risk win as #1: shrink jitter.**
   `random.uniform(0.5, 1.5)` (avg 1.0s) fires before every one of 6 subpages —
   6.5-6.7s of guaranteed, fully-in-our-control overhead per domain, second only to
   networkidle. Even halving the range (`0.2-0.6s`) would save ~3s/domain for free,
   independent of #1/#2, at effectively zero added bot-detection risk (still polite,
   still staggered).

Combining #1 and #4 alone (both trivial, low-risk, no architecture change) gets an
estimated ~35-40% reduction in per-domain fetch time with no concurrency changes. #2 is
the bigger structural win but the one worth reviewing most carefully before building.

2026-09-16 — M1 follow-up, #1 + #4 implemented (#2 deferred to tech-debt.md, #3
rejected) — `fetcher._settle_page` no longer just waits on `networkidle` up to 5s. It
now races the native `networkidle` event against a new stable-text poll
(`_wait_for_stable_text`: every 0.25s, `document.body.innerText.length` via
`page.evaluate`; done once it's >1000 chars and unchanged across two consecutive
polls) using `asyncio.wait(..., return_when=FIRST_COMPLETED)`, capped at
`NETWORKIDLE_CAP_S=2.0` either way — whichever finishes first wins, the loser is
cancelled (and that cancellation is itself bounded via `_bounded`, per the hang fix
above — didn't want to reintroduce the exact class of bug just fixed). `--debug` logs
which exit fired (`idle`/`stable_text`/`cap`) per page. Jitter dropped from
`uniform(0.5, 1.5)` to `uniform(0.2, 0.6)`.

**Before/after** (`python -m enrich baseten.co vapi.ai --debug`, same two sites as the
profiling run):

| | vapi.ai | baseten.co |
|---|---|---|
| before | 37.8s | 43.1s |
| after | **14.5s** | **17.3s** |
| change | **-61.6%** | **-59.9%** |

Better than the ~35-40% estimated — the stable-text poll turned out to fire almost
immediately on real content: 12 of 13 pages across both sites exited via
`stable_text` in under ~1.1s (most well under that); only 2 pages (`baseten.co/talk-to-us`,
`baseten.co/company` — both still-animating on load) hit the 2s cap, which is still
less than half the old 5s cap. Full pytest suite (10 tests) still green — nothing in
the existing coverage exercised `_settle_page`'s internals directly, so this was
verified live rather than by a new unit test; a fake-page unit test for the race logic
itself would need a stubbed Playwright `Page`, which felt like more scaffolding than
the fix warranted given time. Updated `discovery-and-cleaning.md`'s Fetching section
and `resilience.md`'s Profiling section (field names changed:
`networkidle=`/`timeout=` → `settle=`/`exit=`) to match. Logged the deferred
concurrent-subpage-fetch idea in `tech-debt.md` with these same numbers, per
instruction. `ruff check`/`format`: clean.

2026-09-16 — M2: cleaner.py, wired into pipeline.py — Built `cleaner.py` per
discovery-and-cleaning.md's Cleaning section: strip
script/style/noscript/svg/canvas/iframe/form/header/nav/footer + `[aria-hidden=true]` +
cookie banners (class/id substring match), `trafilatura.extract(...,
output_format="markdown", include_links=True, favor_recall=True)` with a
`soup.get_text("\n")` fallback under 200 chars, collapse-blank-lines +
cross-page-line-dedup (first occurrence wins, in kind-priority order:
leadership/team/about first — same list used for both the dedup order and the final
`cleaned` list order), per-page truncation to `MAX_CHARS_PER_PAGE` with a
`[...truncated...]` marker. Email/LinkedIn harvesting needed no new code — it already
runs on raw HTML in `fetcher.py`/`discovery.py`, well before this node exists in the
pipeline, so the doc's "keep footer text only for extracting emails/links" exception is
satisfied by *ordering*, not by anything cleaner.py does.

**Token counts.** `tiktoken` was already a transitive dependency (via
`langchain-openai`); added it to `requirements.txt` explicitly since we now import it
directly. Uses `cl100k_base` — OpenAI's encoding, not DeepSeek's (no public
`tiktoken`-compatible DeepSeek encoding exists), so `raw_tokens`/`clean_tokens` are a
consistent approximation for the reduction-% metric, not what DeepSeek will actually
bill. Falls back to `len(text)//4` if `tiktoken` ever fails to import;
`cleaner.TOKEN_COUNT_METHOD` records which one ran.

**TEAM CARDS.** `_extract_team_cards` runs on the un-stripped soup (before
`_strip_boilerplate`) for `team`/`leadership`-kind pages only: h3/h4 heading as the
name, first short (≤80 char) sibling text within 3 hops as the role, first
`linkedin.com/in/` href found nearby. Added `team_cards: list[TeamCard]` to
`DomainState` (and to schemas.md — a real addition, not diagnostic-only, since `extract`
in M3 will actually consume it as its own prompt block).

**Debug output.** `cli.py`'s `debug_sink` shape changed from a flat
`dict[str, list]` (M1 review's `considered_links` only) to `dict[str, dict[str, list]]`
so it can carry both `considered_links` and `cleaned` per domain — the cleaned markdown
never touches `DomainResult` (disk-facing, no raw/cleaned content by design), so this
is the only path from `state["cleaned"]` to anything `cli.py` can act on. `--debug`
writes `debug/<domain>/<kind>-<slug>.md`, each with a one-line HTML comment header
(`raw_tokens=`/`clean_tokens=`) then the markdown. `pipeline._to_page_record` now looks
up each page's `CleanPage` by URL to fill `PageRecord.raw_tokens`/`clean_tokens`
(previously always `None`, TODO M2).

**Summary table.** Added `raw tok` / `clean tok` / `reduction` columns (per domain +
TOTAL), computed by summing `PageRecord.raw_tokens`/`clean_tokens` across a domain's
pages.

**Verification** (`python -m enrich postman.com supabase.com vapi.ai --debug`):

| domain | status | pages | raw tok | clean tok | reduction |
|---|---|---|---|---|---|
| postman.com | ok | 7 | 842,785 | 6,260 | 99% |
| supabase.com | ok | 7 | 1,259,048 | 5,407 | 100% |
| vapi.ai | ok | 7 | 1,186,861 | 2,818 | 100% |
| **TOTAL** | | 21 | 3,288,694 | 14,485 | **100%** |

All 3 `ok` (home + subpage fetched, per M1's interim status rule — no extraction yet).
`debug/<domain>/` populated for all three (spot-checked `postman.com/home-home.md`:
100,150 raw tokens → 73 clean tokens, and the content is exactly what the homepage
visibly says, nothing else; `postman.com/pricing-pricing.md` correctly hits the
`MAX_CHARS_PER_PAGE` cap and ends with `[...truncated...]`). The reduction-% numbers
look almost suspiciously perfect (99-100%) but that's genuinely what "modern SPA
homepage ships a 300KB+ JS bundle as `raw` HTML, renders a two-sentence hero" looks like
in tokens — this is the concrete evidence for AGENTS.md rule 4 ("No raw HTML to the LLM.
Ever."). `--debug`'s candidates-considered tables stayed reasonable on real sites too
(131/85/~120 rows for supabase/vapi/postman respectively — homepage link counts, not an
explosion) after the M1-review aggregation fix. Added `tests/test_cleaner.py` (6 tests:
script/svg/nav removal, card-grid fallback, token-count shrink, skip-non-ok-pages,
team-card extraction, team-cards-only-for-team/leadership-kind) and
`tests/test_emails.py` (6 tests — first pass used `example.com` as both the test
domain *and* the email domain, which collided with `EMAIL_JUNK_SUBSTRINGS`'s own
literal-placeholder-domain check and failed 4/6 tests; switched to `acme-corp.io`).
Full suite: 22 passed. `ruff check`/`format`: clean on `enrich/`+`tests/`.
