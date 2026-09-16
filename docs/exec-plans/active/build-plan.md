# Exec Plan — Lead Enrichment Agent

Owner: Harshit. Deadline: night of 18 Sep 2026. Target submit: 18 Sep afternoon.
When all milestones are done, move this file to `exec-plans/completed/`.

Work milestones in order. Each ends with a runnable state and a Progress Log entry.

## Day 1 — 16 Sep (tonight)

### M0 · Scaffold
- [x] Confirm `.venv` Python >= 3.11; recreate if not
- [x] `pip install -r requirements.txt`; resolve conflicts; `playwright install chromium`
- [x] Run the verify-before-use checklist in `docs/references/stack.md`; log findings
- [x] Create `enrich/` package per ARCHITECTURE.md with empty typed stubs; `python -m enrich --help` works
- [x] `config.py` loads `.env`; `models.py` + `state.py` from schemas.md
- [x] `git init`, first commit

### M1 · Fetch
- [ ] `fetcher.py`: shared browser, per-domain context, resource blocking, timeouts, retries
- [ ] Bot-wall detection + one reload
- [ ] Smoke: fetch the 3 homepages, print status/title/len

### M2 · Discover + clean
- [ ] robots/sitemap via httpx; anchor scoring; guesses; page cap
- [ ] Email + LinkedIn link harvesting from every page
- [ ] `cleaner.py` with fallback + token counts; TEAM CARDS extraction
- [ ] `--debug` writes cleaned markdown; eyeball all 3 domains
- [ ] Tests: discovery, cleaner, emails, bot_wall

## Day 2 — 17 Sep

### M3 · Graph + extraction
- [ ] `graph.py` with linear path first (no fallback/search yet)
- [ ] `extractor.py` with `include_raw=True`, repair retry, usage events
- [ ] End-to-end on 3 domains producing `output.json`

### M4 · Trust layer
- [ ] `verify.py` + tests
- [ ] `scoring.py` + tests
- [ ] Status rule (ok/partial/failed) in `finalize`

### M5 · Resilience
- [ ] Node-level error capture everywhere; domain + run level wrappers
- [ ] Incremental output writes
- [ ] Run the full failure matrix in QUALITY.md; fix until all pass
- [ ] test_graph_failure.py

### M6 · Bonuses
- [ ] `search.py` (Tavily) + `route_after_verify`
- [ ] `navigator.py` (Browser Use) + `route_after_discover`, capped, costed
- [ ] `cost.py` pricing (check official pages, note date) + rich summary table

## Day 3 — 18 Sep (morning)

### M7 · Ship
- [ ] README: what/why, graph diagram (mermaid), setup (.env), run, sample output excerpt,
      design decisions (deterministic-first, verification, confidence formula), limitations
- [ ] Final clean run → commit `output.json`
- [ ] `ruff`, `pytest` green; remove dead code; push to GitHub (public)
- [ ] Human: record Loom (structure 30s → live run 60s → output + confidence 45s → failure demo 20s)
- [ ] Human: send email (repo, Loom, LinkedIn, explicit "Yes" to the 40% operations question)

## Decisions

- 16 Sep: LangGraph pipeline; Playwright deterministic path first; Browser Use only as capped fallback.
- 16 Sep: Leaders must be grounded in page text or search results; unverified are dropped.
- 16 Sep: Confidence = documented weighted blend, not raw LLM self-rating.

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
