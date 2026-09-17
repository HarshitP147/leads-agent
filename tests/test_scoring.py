"""test_scoring.py per QUALITY.md: empty → 0.0; perfect → ≥0.9; blocked penalty.
No network — hand-built states."""

from __future__ import annotations

from enrich.fetcher import FetchedPage
from enrich.models import ContactEmail, ErrorRecord, Leader, LLMExtraction
from enrich.scoring import (
    BLOCKED_PENALTY,
    UNVERIFIED_PENALTY_CAP,
    _fetch_health,
    _field_coverage,
    _leader_quality,
    _source_coverage,
    compute_confidence,
)


def _page(kind: str, *, status: str = "ok") -> FetchedPage:
    return FetchedPage(
        url=f"https://acme-corp.io/{kind}",
        requested_url=f"https://acme-corp.io/{kind}",
        kind=kind,  # type: ignore[arg-type]
        discovered_by="anchor",
        status=status,  # type: ignore[arg-type]
        http_status=200 if status == "ok" else 403,
        html=None,
        title=kind,
    )


def _extraction() -> LLMExtraction:
    return LLMExtraction(
        company_name="Acme",
        overview="Acme builds widgets. Developers use them daily.",
        target_audience="Developers building backends.",
        industries=["software"],
        self_confidence=1.0,
    )


def _leader() -> Leader:
    return Leader(
        name="Jane Founder",
        title="CEO",
        linkedin_url="https://www.linkedin.com/in/jane-founder",
        linkedin_source="website",
        source_url="https://acme-corp.io/team",
        verified=True,
    )


def _perfect_state(**overrides: object) -> dict:
    state = {
        "domain": "acme-corp.io",
        "home": _page("home"),
        "pages": [
            _page("home"),
            _page("about"),
            _page("team"),
            _page("contact"),
            _page("pricing"),
        ],
        "extraction": _extraction(),
        "leaders": [_leader()],
        "contact_emails": [
            ContactEmail(
                email="hello@acme-corp.io",
                purpose="general",
                source_url="https://acme-corp.io/contact",
            )
        ],
        "errors": [],
    }
    state.update(overrides)
    return state


def test_empty_state_scores_zero() -> None:
    breakdown = compute_confidence({"domain": "acme-corp.io"})
    assert breakdown.score == 0.0


def test_perfect_state_scores_at_least_0_9() -> None:
    breakdown = compute_confidence(_perfect_state())
    assert breakdown.score >= 0.9
    assert breakdown.score <= 1.0
    assert breakdown.components["field_coverage"] == 1.0
    assert breakdown.components["leader_quality"] == 1.0
    assert breakdown.components["source_coverage"] == 1.0
    assert breakdown.components["fetch_health"] == 1.0
    assert breakdown.components["llm_self"] == 1.0


def test_blocked_penalty_is_applied() -> None:
    # Extra blocked `other` page so the four source-kind buckets stay covered;
    # fetch_health still drops slightly, so we don't require an exact -0.10 delta.
    clean = compute_confidence(_perfect_state())
    pages = [*_perfect_state()["pages"], _page("other", status="blocked")]
    blocked = compute_confidence(_perfect_state(pages=pages))
    assert blocked.components["blocked_penalty"] == BLOCKED_PENALTY
    assert blocked.score < clean.score
    assert blocked.score <= round(clean.score - BLOCKED_PENALTY, 2)


def test_homepage_failed_caps_score() -> None:
    breakdown = compute_confidence(_perfect_state(home=_page("home", status="error")))
    assert breakdown.score <= 0.2
    assert breakdown.components["home_fail_cap"] == 0.2


def test_unverified_person_penalty() -> None:
    clean = compute_confidence(_perfect_state())
    errors = [
        ErrorRecord(
            stage="verify",
            kind="unverified_person",
            message="dropped unverified leader: John Fakeperson",
        )
    ]
    penalised = compute_confidence(_perfect_state(errors=errors))
    assert penalised.components["unverified_penalty"] == 0.05
    assert penalised.score == round(clean.score - 0.05, 2)


def test_unverified_person_penalty_is_capped() -> None:
    """UNVERIFIED_PENALTY_CAP (0.15) caps the penalty regardless of how many people were
    dropped — 4 drops at 0.05 each would be 0.20 uncapped, which must not happen."""
    errors = [
        ErrorRecord(stage="verify", kind="unverified_person", message=f"dropped {i}")
        for i in range(4)
    ]
    breakdown = compute_confidence(_perfect_state(errors=errors))
    assert breakdown.components["unverified_penalty"] == UNVERIFIED_PENALTY_CAP


def test_missing_home_key_caps_score_like_a_failed_home() -> None:
    """`home is None` (the key was never set) must cap the score the same way an
    explicit `status="error"` home page does — these are different states in
    `DomainState` (absent vs. present-but-failed) and both must hit the cap."""
    state = _perfect_state()
    del state["home"]
    breakdown = compute_confidence(state)
    assert breakdown.score <= 0.2
    assert breakdown.components["home_fail_cap"] == 0.2


def test_field_coverage_partial() -> None:
    extraction = LLMExtraction(
        company_name="Acme",
        overview="Acme builds widgets.",
        target_audience="",
        industries=[],
        self_confidence=0.5,
    )
    # overview present, target_audience empty, no emails, no leaders, no industries:
    # 1 of 5 flags true.
    assert _field_coverage(extraction, [], []) == 1 / 5


def test_leader_quality_partial_credit_per_leader() -> None:
    verified_no_extras = Leader(
        name="A", title=None, linkedin_url=None, source_url="https://x/", verified=True
    )
    unverified = Leader(
        name="B",
        title="CEO",
        linkedin_url="https://www.linkedin.com/in/b",
        source_url="https://x/",
        verified=False,
    )
    # verified_no_extras: 0.5; unverified (even with title+linkedin): 0.0 + 0.25 + 0.25.
    assert _leader_quality([verified_no_extras]) == 0.5
    assert _leader_quality([unverified]) == 0.5
    assert _leader_quality([verified_no_extras, unverified]) == 0.5


def test_source_coverage_partial_when_a_group_missing() -> None:
    pages = [_page("home"), _page("about"), _page("team")]  # no contact, no pricing
    assert _source_coverage(pages) == 2 / 4


def test_fetch_health_empty_pages_is_zero() -> None:
    assert _fetch_health([]) == 0.0


def test_fetch_health_partial_ratio() -> None:
    pages = [_page("home"), _page("about", status="blocked")]
    assert _fetch_health(pages) == 0.5
