# AGENTS.md

This file is a **map**, not a manual. Keep it under ~100 lines. Detail lives in `docs/`.
When you learn something durable, write it into the right doc, not here.

## What this repo is

Autonomous Lead Enrichment Agent (take-home for SoftwareBrio, AI Engineer Intern).
Input: a list of company domains. Output: `output.json` with a structured, verified
profile per domain (overview, ICP, contact emails, leadership, confidence score).
Test targets: `postman.com`, `supabase.com`, `vapi.ai`.
**Hard deadline: night of 18 Sep 2026. Submission-ready target: afternoon of 18 Sep.**

## Where to look

| Need | Read |
|---|---|
| What we must deliver and how it's graded | `docs/product-spec.md` |
| System shape, graph nodes, state, module layout | `docs/ARCHITECTURE.md` |
| Non-negotiable engineering principles | `docs/design-docs/core-beliefs.md` |
| Pydantic models (output + graph state) | `docs/design-docs/schemas.md` |
| Fetching, link discovery, HTML cleaning | `docs/design-docs/discovery-and-cleaning.md` |
| When and how Browser Use is invoked | `docs/design-docs/browser-use-fallback.md` |
| LLM extraction + hallucination checks | `docs/design-docs/extraction-and-verification.md` |
| Confidence score formula | `docs/design-docs/confidence-scoring.md` |
| Timeouts, retries, bot walls, failure statuses | `docs/design-docs/resilience.md` |
| Token + cost accounting | `docs/design-docs/cost-tracking.md` |
| Pinned versions, library API gotchas | `docs/references/stack.md` |
| Current plan and progress | `docs/exec-plans/active/build-plan.md` |
| Definition of done, test matrix | `docs/QUALITY.md` |
| Known shortcuts to revisit | `docs/tech-debt.md` |

Design docs index: `docs/design-docs/index.md`.

## Commands

Uses `uv` (see `pyproject.toml` + `uv.lock`). **Never run a non-editable install of
this project** (`uv pip install .` / `pip install .` without `-e`) — it drops a real,
unrelated build of our own top-level `enrich/` package into `site-packages`, which can
shadow the local one depending on import order (this happened once; see
docs/references/stack.md Gotchas).

```bash
# setup — must be Python >= 3.11 (browser-use requires it)
uv sync                        # creates/updates .venv, editable-installs this project
uv run playwright install chromium
cp .env.example .env          # then fill keys

# run
uv run python -m enrich postman.com supabase.com vapi.ai --out output.json
uv run python -m enrich example.invalid --timeout 1   # failure-path demo

# checks
uv run pytest -q
uv run ruff check . && uv run ruff format --check .

# keep requirements.txt in sync with pyproject.toml/uv.lock (assignment deliverable —
# some reviewers won't have uv; regenerate after any dependency change)
uv export --no-hashes --no-emit-project -o requirements.txt
```

Plain-`pip` fallback (no `uv` installed) — same venv already exists at `.venv/`:

```bash
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

No install step for our own package needed either way — `pyproject.toml`'s
`pythonpath = ["."]` covers pytest, and `python -m enrich`/`uv run python -m enrich`
already puts the repo root on `sys.path` by itself. Just don't `pip install .`
(non-editable) — see the warning above.

## Working rules for agents

1. **Start every session** by reading `docs/exec-plans/active/build-plan.md`. Work the
   first unchecked milestone. Tick boxes and append to its Progress Log as you go.
2. **Never trust your memory of library APIs.** LangGraph, LangChain and Browser Use
   change fast. Inspect the installed package (`pip show`, `python -c "import x; help(...)"`,
   read source in `.venv`) before writing integration code. Record gotchas in
   `docs/references/stack.md`.
3. **A domain failing must never crash the run.** Nodes catch, record into
   `state.errors`, and continue. See `resilience.md`.
4. **No raw HTML to the LLM. Ever.** Only cleaned markdown, capped per page.
5. **No unverified people in output.** See `extraction-and-verification.md`.
6. **Small modules, type hints everywhere, no function over ~50 lines.**
7. **Explain as you go.** After finishing each module, write a 3–5 line "why it works
   this way" note in the Progress Log. The human owner must be able to defend every
   design choice in an interview.
8. Don't add dependencies without updating `requirements.txt` and `docs/references/stack.md`.
9. Don't commit `.env`, `.venv`, or `debug/` artifacts.
10. When a plan changes, update the doc first, then the code.

## Module map (details in ARCHITECTURE.md)

```
enrich/
  __main__.py   cli.py      config.py    models.py    state.py
  graph.py      fetcher.py  discovery.py cleaner.py   extractor.py
  verify.py     search.py   navigator.py scoring.py   cost.py   logging_setup.py
tests/
```
