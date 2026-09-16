# Browser Use Fallback (`navigator.py`)

## Why a fallback and not the main scraper

Browser Use calls an LLM on every navigation step. As the primary scraper it would be
slow, costly (hurting the token-optimisation story), and nondeterministic (risky for a
live demo). As a fallback it adds real value on sites where the team/about page is hidden
behind JS menus or not linked plainly.

## Trigger

`route_after_discover` sends the state here when `BROWSER_USE_ENABLED` and either:
- no candidate of kind `leadership`, `team`, or `about` was found; or
- homepage clean text < 800 chars (likely an unrendered SPA).

## Task

Narrow, URL-returning task only. The agent never produces final profile data.

```
Go to https://{domain}. Find the page(s) listing the company's leadership, founders,
or team, and the About page. Do not log in, submit forms, or leave {domain} and its
subdomains. Return up to 3 URLs as JSON: {"urls": [{"url": "...", "kind": "team|about|leadership"}]}.
If nothing exists, return {"urls": []}.
```

Use Browser Use's structured output support if the installed version provides it;
otherwise parse the final result text defensively (regex for URLs, filter to same site).

## Limits

- `max_steps = BROWSER_USE_MAX_STEPS` (default 8).
- Hard wall clock: `asyncio.wait_for(..., 90s)`.
- Model: `NAVIGATION_MODEL` (cheap/fast).
- Headless, own browser session (do not share our Playwright context; simpler and isolated).
- Any exception → `ErrorRecord(stage="agentic_navigate", ...)`, return no URLs, continue.
- Returned URLs are validated (same site, http(s)) and added to `candidates` with
  `discovered_by="agent"`, then fetched by our normal `fetch_subpages`.

## Cost

Browser Use does its own LLM calls, which LangChain callbacks will **not** see. Capture
usage from Browser Use's history/usage objects (check the installed API; it has changed
across versions) and emit a `UsageEvent(component="navigation", ...)`. If exact usage is
unavailable in the installed version, estimate and mark `estimated=True`. Document the
outcome in `docs/references/stack.md`.

## Integration warning

Recent Browser Use versions ship their own LLM wrappers (not LangChain chat models).
Verify in the installed package which chat class to pass before writing code.
