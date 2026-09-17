"""Baseline linear orchestrator (M1-M5): plain async fetch -> discover -> clean ->
extract -> verify -> score -> finalize. No LangGraph, no Browser Use — those are the
bonus orchestrator (`graph.py` + `navigator.py`, M8/M9). See docs/ARCHITECTURE.md.

Nothing on this run path may import `graph` or `navigator` — that's the whole point of
keeping the baseline and the bonus orchestrator decoupled.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Literal

from enrich import cleaner, discovery, extractor, fetcher, scoring, verify
from enrich.config import Settings, get_settings
from enrich.cost import UsageEvent
from enrich.fetcher import FetchedPage
from enrich.models import (
    CompanyProfile,
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


def _status(
    state: DomainState, profile: CompanyProfile | None
) -> Literal["ok", "partial", "failed"]:
    """schemas.md: `failed` if no profile; `partial` if profile exists but any page was
    blocked/timeout or leaders is empty; else `ok`."""
    if profile is None:
        return "failed"
    pages = state.get("pages", [])
    if any(page.status in ("blocked", "timeout") for page in pages):
        return "partial"
    if not state.get("leaders"):
        return "partial"
    return "ok"


def _profile_from_state(state: DomainState) -> CompanyProfile | None:
    extraction = state.get("extraction")
    if extraction is None:
        return None
    return CompanyProfile(
        company_name=extraction.company_name,
        overview=extraction.overview,
        target_audience=extraction.target_audience,
        industries=list(extraction.industries),
        contact_emails=state.get("contact_emails", []),
        leaders=state.get("leaders", []),
    )


def _usage_from_events(events: list[UsageEvent]) -> Usage:
    """Token rollup only — USD stays 0 until M6 fills `cost.py` pricing."""
    return Usage(
        input_tokens=sum(event.input_tokens for event in events),
        output_tokens=sum(event.output_tokens for event in events),
        llm_calls=sum(1 for event in events if event.component == "extraction"),
        search_calls=sum(event.search_calls for event in events),
    )


def finalize(state: DomainState, *, started: float) -> DomainResult:
    cleaned_by_url = {c.url: c for c in state.get("cleaned", [])}
    pages = [_to_page_record(p, cleaned_by_url) for p in state.get("pages", [])]
    profile = _profile_from_state(state)
    confidence = state.get("confidence") or ConfidenceBreakdown(
        score=0.0, components={}
    )
    return DomainResult(
        domain=state.get("domain", ""),
        status=_status(state, profile),
        profile=profile,
        confidence=confidence,
        pages=pages,
        errors=state.get("errors", []),
        usage=_usage_from_events(state.get("usage_events", [])),
        duration_s=time.monotonic() - started,
        scraped_at=datetime.now(UTC).isoformat(),
    )


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
        await _run_stages(state, debug_sink=debug_sink, domain=domain)
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

    return finalize(state, started=started)


async def _run_stages(
    state: DomainState,
    *,
    debug_sink: dict[str, dict[str, list]] | None,
    domain: str,
) -> None:
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
        _merge(state, await extractor.extract(state))
        _merge(state, await verify.verify(state))
        # TODO M7 (bonus): search_linkedin
    # Hard fetch_home failure skips the LLM (ARCHITECTURE.md route_after_fetch_home)
    # but still scores — no extraction → 0.0, homepage-failed cap ≤ 0.2.
    _merge(state, await scoring.score(state))
