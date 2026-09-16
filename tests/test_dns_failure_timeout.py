"""Resilience regression test: a nonexistent domain must fail fast, not hang.

Needs network (a real DNS lookup for a domain that doesn't exist) — unlike the rest of
the suite, which stubs the fetcher. Guards against a real bug: `asyncio.wait_for` does
not hard-kill a task on timeout, it cancels it and then *awaits* the cancellation to
finish — so an unbounded `page.close()` inside a cancelled task's `finally` block could
block that cancellation from ever completing, meaning DOMAIN_TIMEOUT_S wasn't actually
being enforced. See build-plan.md's Progress Log for the investigation. Fixed by
bounding every Playwright op with no native `timeout=` kwarg (fetcher.py's `_bounded`)
and by not retrying a DNS resolution failure (permanent, retrying can't fix it).
"""

from __future__ import annotations

import asyncio

import pytest

from enrich import pipeline
from enrich.config import Settings

NONEXISTENT_DOMAIN = "this-domain-should-never-exist-abcxyz123456.invalid"
BUDGET_S = 15


@pytest.mark.asyncio
async def test_nonexistent_domain_fails_within_budget_five_times_in_a_row() -> None:
    """One event loop, five sequential attempts, reusing the shared browser each time —
    this is how the real CLI actually behaves across multiple domains in one run
    (`asyncio.run(run_all(...))` is a single loop for the whole process). Splitting
    this into 5 separate `@pytest.mark.parametrize` cases was tried first and hung: by
    default pytest-asyncio gives each test function its own fresh event loop, but
    fetcher.py's shared Playwright driver is bound to whichever loop first created it —
    reusing it from a second, different loop hangs. That's a test-design pitfall, not
    the production bug; cli.py only ever runs one loop per process."""
    settings = Settings()
    try:
        for attempt in range(5):
            result = await asyncio.wait_for(
                pipeline.run_pipeline(NONEXISTENT_DOMAIN, settings), timeout=BUDGET_S
            )
            assert result.status == "failed", (
                f"attempt {attempt}: status={result.status}"
            )
            assert any(e.kind == "dns_error" for e in result.errors), (
                f"attempt {attempt}: errors={result.errors}"
            )
    finally:
        await pipeline.fetcher.close_browser()
