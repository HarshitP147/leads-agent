# Core Beliefs

These are the principles every change is checked against.

1. **Deterministic first, agentic when it earns its cost.** Playwright + sitemap + link
   scoring handles the common case cheaply and repeatably. Browser Use only runs when
   deterministic discovery comes back thin. Every agentic step is capped and costed.
2. **Trust nothing the LLM says about facts it can't point to.** The LLM classifies and
   summarises; grounding checks decide what ships. A shorter, true output beats a
   fuller, invented one.
3. **Extract what regex can extract.** Emails and LinkedIn URLs come from the DOM
   (`mailto:`, `href`, regex). The LLM only labels them.
4. **Failure is data.** Every domain returns a result with `status` and `errors[]`.
   Partial success is reported honestly as `partial`.
5. **Tokens are a budget.** Cleaned markdown only, per-page character caps, and
   before/after token counts logged so savings are demonstrable.
6. **Confidence is computed, not vibed.** The LLM's self-rating is one input to a
   documented formula, never the whole score.
7. **Provenance on everything.** Each extracted item carries the `source_url` it came from.
8. **Legible to a reviewer in 3 minutes.** Small modules, obvious names, a README
   diagram, and a run summary table.
9. **Be a polite scraper.** Realistic UA, per-domain page cap, small delays, no login
   walls, no LinkedIn fetching.
