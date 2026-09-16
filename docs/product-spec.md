# Product Spec — Autonomous Lead Enrichment Agent

Source: SoftwareBrio "AI Engineer Intern — Practical Take-Home Assignment".

## Input / Output

- **Input:** list of domains via CLI args (also accept `--file domains.txt`).
- **Output:** `output.json` — a JSON array, one `DomainResult` per domain, in input order.
  Schema in `design-docs/schemas.md`. Optional `--csv` flattens to `output.csv`.

## Required capabilities → where implemented

| Requirement | Implementation | Doc |
|---|---|---|
| Fetch homepage, discover /about /team /company /contact /pricing | `fetcher.py`, `discovery.py` | discovery-and-cleaning.md |
| Handle JS-rendered content with a headless browser | Playwright (async Chromium) | discovery-and-cleaning.md |
| No raw HTML to the LLM; strip CSS/SVG/scripts/nav | `cleaner.py` | discovery-and-cleaning.md |
| LLM with strict structured outputs | LangChain `with_structured_output(PydanticModel)` | extraction-and-verification.md |
| Fields: overview (2 sentences), ICP, contact emails, leadership (name, role, LinkedIn), confidence 0–1 | `models.py`, `extractor.py`, `scoring.py` | schemas.md, confidence-scoring.md |
| 404s, bot blockers, timeouts, missing elements; never crash midway | per-node try/except, statuses | resilience.md |

## Bonus (we are doing all three)

| Bonus | Implementation |
|---|---|
| Search for founder LinkedIn URLs | `search.py` (Tavily), conditional graph edge |
| Agentic framework / dynamic navigation | LangGraph pipeline + Browser Use fallback navigator |
| Cost tracking per domain | `cost.py`, `usage` block in each result + run summary table |

## Rubric → what to show

| Criterion | Weight | Evidence we provide |
|---|---|---|
| Agent & scraping architecture | 30% | Graph diagram in README, sitemap + link scoring, deterministic-first / agentic-fallback |
| LLM & structured output | 25% | Pydantic schemas, token reduction numbers (raw vs cleaned), name verification |
| Error handling & resilience | 20% | `status` + `errors[]` per domain, bot-wall detection, failure demo in Loom |
| Code quality & docs | 15% | Small typed modules, tests, README with `.env` setup |
| Loom | 10% | 2–3 min: structure → live run → output → one graceful failure |

## Deliverables (human does the last two)

1. GitHub repo: code, `requirements.txt`, `README.md`, `.env.example`.
2. `output.json` from the 3 test domains, committed.
3. Loom 2–3 min (human records).
4. Email to support@softwarebrio.com, subject `[AI Intern Submission] - <Full Name>`,
   with repo link, Loom link, LinkedIn, and the explicit answer to the 40% operations
   question (human writes).

## Non-goals

- No web UI, no database, no Docker (optional only if time remains).
- No login-walled scraping (LinkedIn itself is never fetched; we only take URLs from
  site content or search results).
