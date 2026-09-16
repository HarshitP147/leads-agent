"""Baseline linear orchestrator (M1-M5): plain async fetch -> discover -> clean ->
extract -> verify -> score -> finalize. No LangGraph, no Browser Use — those are the
bonus orchestrator (`graph.py` + `navigator.py`, M8/M9). See docs/ARCHITECTURE.md.

Nothing on this run path may import `graph` or `navigator` — that's the whole point of
keeping the baseline and the bonus orchestrator decoupled.

M1-M2 status: `fetch_home -> discover_links -> fetch_subpages -> clean_pages` run.
`extract`, `verify`, `search_linkedin`, `score` are TODOs, skipped entirely (not called,
not stubbed-and-raising) so the run path never hits a `NotImplementedError`. Because no
extraction has happened yet, `profile` is always `None`, so the real profile-based status
rule (schemas.md: "failed if no profile") can't apply yet either — `_interim_status`
implements the fetch-health-only stand-in documented in schemas.md's "Interim rule"
until `extract`/`verify` land in M3.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Literal

from enrich import cleaner, discovery, fetcher
from enrich.config import Settings, get_settings
from enrich.fetcher import FetchedPage
from enrich.models import (
    ConfidenceBreakdown,
    DomainResult,
    ErrorRecord,
    PageRecord,
    Usage,
)
from enrich.state import DomainState

logger = logging.getLogger(__name__)

_ADDITIVE_KEYS = ("errors", "usage_events", "route_log")


def _merge(state: DomainState, update: dict) -> None:
    """Apply a stage's partial update in place. `errors`/`usage_events`/`route_log` are
    additive (mirrors the `Annotated[list, operator.add]` reducers in state.py, which a
    real graph engine would apply automatically); every other key is a plain overwrite,
    since each stage already returns the full accumulated list for those (see
    fetcher.fetch_subpages, which reads `state.get("pages", [])` before appending)."""
    for key, value in update.items():
        if key in _ADDITIVE_KEYS:
            state[key] = [*state.get(key, []), *value]  # type: ignore[literal-required]
        else:
            state[key] = value  # type: ignore[literal-required]


def _to_page_record(
    page: FetchedPage, cleaned_by_url: dict[str, cleaner.CleanPage]
) -> PageRecord:
    clean_page = cleaned_by_url.get(page.url)
    return PageRecord(
        url=page.url,
        kind=page.kind,
        status=page.status,
        http_status=page.http_status,
        raw_tokens=clean_page.raw_tokens if clean_page else None,
        clean_tokens=clean_page.clean_tokens if clean_page else None,
        discovered_by=page.discovered_by,
    )


def _interim_status(state: DomainState) -> Literal["ok", "partial", "failed"]:
    """# TODO M3: replace with the real profile-based rule (schemas.md, "Status rule")
    once `extract`/`verify` exist. Until then: `failed` if the homepage itself failed;
    `ok` if the homepage fetched and at least one subpage also fetched successfully;
    `partial` if the homepage fetched but no subpage did."""
    home = state.get("home")
    if home is None or home.status != "ok":
        return "failed"
    subpages = state.get("pages", [])[1:]  # pages[0] is always home (see fetch_home)
    if any(p.status == "ok" for p in subpages):
        return "ok"
    return "partial"


async def run_pipeline(
    domain: str,
    settings: Settings | None = None,
    *,
    debug: bool = False,
    debug_sink: dict[str, dict[str, list]] | None = None,
) -> DomainResult:
    """Run the baseline stages for one domain. Never raises — any exception escaping a
    stage becomes a `failed` DomainResult with an `ErrorRecord(kind="internal")`; this
    is the domain-level last line of defence from docs/design-docs/resilience.md.

    `debug_sink`, if given, is populated with `debug_sink[domain] = {"considered_links":
    [...], "cleaned": [...]}` — diagnostic data `cli.py` prints/writes separately under
    `--debug`, since none of it belongs in the returned `DomainResult`."""
    settings = settings or get_settings()
    started = time.monotonic()
    state: DomainState = {"domain": domain, "started_at": started}

    try:
        _merge(state, await fetcher.fetch_home(state))
        home = state.get("home")

        if home is not None and home.status == "ok":
            _merge(state, await discovery.discover_links(state))
            # TODO M9 (bonus): agentic_navigate fallback when discovery comes back thin.
            _merge(state, await fetcher.fetch_subpages(state))
            _merge(state, await cleaner.clean_pages(state))
            if debug_sink is not None:
                debug_sink[domain] = {
                    "considered_links": state.get("considered_links", []),
                    "cleaned": state.get("cleaned", []),
                }
        # else: hard fetch_home failure — skip straight to finalize, don't call the LLM
        # on nothing (docs/ARCHITECTURE.md, route_after_fetch_home).

        # TODO M3: extract, verify
        # TODO M7 (bonus): search_linkedin
        # TODO M4: score
    except Exception as exc:
        if debug:
            logger.exception("domain %s: pipeline stage raised", domain)
        else:
            logger.error("domain %s failed: %s: %s", domain, type(exc).__name__, exc)
        _merge(
            state,
            {
                "errors": [
                    ErrorRecord(stage="pipeline", kind="internal", message=str(exc))
                ]
            },
        )

    cleaned_by_url = {c.url: c for c in state.get("cleaned", [])}
    pages = [_to_page_record(p, cleaned_by_url) for p in state.get("pages", [])]

    return DomainResult(
        domain=domain,
        status=_interim_status(state),
        profile=None,
        confidence=ConfidenceBreakdown(score=0.0, components={}),
        pages=pages,
        errors=state.get("errors", []),
        usage=Usage(),
        duration_s=time.monotonic() - started,
        scraped_at=datetime.now(UTC).isoformat(),
    )
