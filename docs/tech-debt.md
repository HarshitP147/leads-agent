# Tech Debt Tracker

Shortcuts taken under the 48-hour deadline. Mention the important ones in the README
"Limitations & next steps" section.

| Item | Why deferred | Fix idea |
|---|---|---|
| Token counts approximate (chars/4) unless tiktoken installed | speed | provider tokenizer |
| No persistent cache of fetched pages | scope | on-disk cache keyed by URL+date |
| No CAPTCHA/stealth handling | ethics + scope | official APIs or data providers |
| Single LLM call per domain; large sites truncated | cost | map-reduce over pages |
| Subpage fetches are fully sequential within a domain (one page at a time in `fetch_subpages`), even after the 16 Sep settle-time fix cut per-domain time ~60% (vapi.ai 37.8s→14.5s, baseten.co 43.1s→17.3s). The remaining goto+settle time for the 6 subpages (excluding home) is still paid one-at-a-time — e.g. vapi.ai's post-fix run: 3.43s goto + 4.52s settle = 7.95s of sequential work that 2-way concurrency in the same `BrowserContext` could roughly halve. | reviewed and deferred (approved #1/#4, this one held back) rather than rejected — needs more design care: per-page error isolation so one slow/failing page doesn't stall its partner, and jitter likely needs rethinking for a concurrent batch (staggering 2 at a time still avoids a full 6-way burst) | `asyncio.gather` over 2-page batches in `fetch_subpages`, each page its own `_fetch_one` call, same shared `BrowserContext` |
