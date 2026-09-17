"""Deterministic confidence score formula.

See docs/design-docs/confidence-scoring.md.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from enrich.models import (
    ConfidenceBreakdown,
    ContactEmail,
    ErrorRecord,
    Leader,
    LLMExtraction,
)

if TYPE_CHECKING:
    from enrich.fetcher import FetchedPage
    from enrich.state import DomainState

logger = logging.getLogger(__name__)

W_FIELD = 0.30
W_LEADER = 0.20
W_SOURCE = 0.20
W_FETCH = 0.10
W_LLM = 0.20
BLOCKED_PENALTY = 0.10
UNVERIFIED_PENALTY = 0.05
UNVERIFIED_PENALTY_CAP = 0.15
HOME_FAIL_CAP = 0.20
SOURCE_GROUPS: tuple[tuple[str, ...], ...] = (
    ("about", "company"),
    ("team", "leadership"),
    ("contact",),
    ("pricing",),
)


def _field_coverage(
    extraction: LLMExtraction | None,
    emails: list[ContactEmail],
    leaders: list[Leader],
) -> float:
    if extraction is None:
        return 0.0
    flags = (
        bool(extraction.overview.strip()),
        bool(extraction.target_audience.strip()),
        bool(emails),
        bool(leaders),
        bool(extraction.industries),
    )
    return sum(flags) / len(flags)


def _leader_quality(leaders: list[Leader]) -> float:
    if not leaders:
        return 0.0
    scores = []
    for leader in leaders:
        value = 0.5 if leader.verified else 0.0
        if leader.title:
            value += 0.25
        if leader.linkedin_url:
            value += 0.25
        scores.append(value)
    return sum(scores) / len(scores)


def _source_coverage(pages: list[FetchedPage]) -> float:
    kinds_ok = {page.kind for page in pages if page.status == "ok"}
    hits = sum(1 for group in SOURCE_GROUPS if any(kind in kinds_ok for kind in group))
    return hits / len(SOURCE_GROUPS)


def _fetch_health(pages: list[FetchedPage]) -> float:
    if not pages:
        return 0.0
    return sum(1 for page in pages if page.status == "ok") / len(pages)


def _unverified_dropped(errors: list[ErrorRecord]) -> int:
    return sum(1 for err in errors if err.kind == "unverified_person")


def compute_confidence(state: DomainState) -> ConfidenceBreakdown:
    """Pure scorer: deterministic given the state. Used by `score` and unit tests."""
    extraction = state.get("extraction")
    if extraction is None:
        return ConfidenceBreakdown(score=0.0, components={"no_extraction": 1.0})

    pages = state.get("pages", [])
    leaders = state.get("leaders", [])
    emails = state.get("contact_emails", [])
    components = {
        "field_coverage": _field_coverage(extraction, emails, leaders),
        "leader_quality": _leader_quality(leaders),
        "source_coverage": _source_coverage(pages),
        "fetch_health": _fetch_health(pages),
        "llm_self": float(extraction.self_confidence),
    }
    score = (
        W_FIELD * components["field_coverage"]
        + W_LEADER * components["leader_quality"]
        + W_SOURCE * components["source_coverage"]
        + W_FETCH * components["fetch_health"]
        + W_LLM * components["llm_self"]
    )
    if any(page.status == "blocked" for page in pages):
        score -= BLOCKED_PENALTY
        components["blocked_penalty"] = BLOCKED_PENALTY
    dropped = _unverified_dropped(state.get("errors", []))
    if dropped:
        penalty = min(UNVERIFIED_PENALTY * dropped, UNVERIFIED_PENALTY_CAP)
        score -= penalty
        components["unverified_penalty"] = penalty
    home = state.get("home")
    if home is None or home.status != "ok":
        score = min(score, HOME_FAIL_CAP)
        components["home_fail_cap"] = HOME_FAIL_CAP
    return ConfidenceBreakdown(score=round(max(score, 0.0), 2), components=components)


async def score(state: DomainState) -> dict:
    """Node: compute the weighted confidence breakdown from verified data + page health."""
    started = time.monotonic()
    domain = state.get("domain", "")
    try:
        breakdown = compute_confidence(state)
        update: dict = {
            "confidence": breakdown,
            "route_log": [f"score:{breakdown.score:.2f}"],
        }
    except Exception as exc:  # noqa: BLE001 — stage must never raise
        logger.error("domain %s: score failed: %s: %s", domain, type(exc).__name__, exc)
        update = {
            "confidence": ConfidenceBreakdown(score=0.0, components={}),
            "errors": [ErrorRecord(stage="score", kind="internal", message=str(exc))],
            "route_log": ["score:internal"],
        }
    logger.info(
        "score done domain=%s duration=%.2fs", domain, time.monotonic() - started
    )
    return update
