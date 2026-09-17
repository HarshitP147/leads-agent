"""Fetcher error taxonomy + guessed-URL abort. No network."""

from __future__ import annotations

import pytest
from playwright.async_api import Error as PlaywrightError

from enrich.discovery import CandidateLink
from enrich.fetcher import (
    _classify_navigation_error,
    _http_error_kind,
    _one_line,
    fetch_subpages,
)


def test_navigation_error_kinds() -> None:
    assert (
        _classify_navigation_error(PlaywrightError("net::ERR_NAME_NOT_RESOLVED"))
        == "dns_error"
    )
    assert (
        _classify_navigation_error(
            PlaywrightError("net::ERR_EMPTY_RESPONSE at https://x")
        )
        == "empty_response"
    )
    assert (
        _classify_navigation_error(PlaywrightError("net::ERR_CONNECTION_REFUSED"))
        == "empty_response"
    )


def test_http_error_kind_taxonomy() -> None:
    assert _http_error_kind(404) == "http_404"
    assert _http_error_kind(429) == "rate_limited"
    assert _http_error_kind(403) == "http_4xx"
    assert _http_error_kind(503) == "http_5xx"


def test_error_message_is_one_line() -> None:
    blob = "first line of the failure\nCall log:\n  - navigating to ...\n  - waiting"
    assert _one_line(blob) == "first line of the failure"
    assert "\n" not in _one_line(blob)


@pytest.mark.asyncio
async def test_two_empty_guesses_abort_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fake_fetch_one(domain, url, *, kind, discovered_by, timeout_s):
        calls.append(url)
        raise PlaywrightError(f"net::ERR_EMPTY_RESPONSE at {url}")

    monkeypatch.setattr("enrich.fetcher._fetch_one", fake_fetch_one)
    guesses = [
        CandidateLink(
            url=f"https://otel.ai/{path}",
            kind=kind,  # type: ignore[arg-type]
            score=1.0,
            discovered_by="guess",
        )
        for path, kind in (
            ("about", "about"),
            ("company", "company"),
            ("team", "team"),
            ("contact", "contact"),
            ("pricing", "pricing"),
        )
    ]
    state = {
        "domain": "otel.ai",
        "candidates": guesses,
        "pages": [],
        "candidate_emails": [],
        "linkedin_links": [],
    }
    result = await fetch_subpages(state)
    assert calls == ["https://otel.ai/about", "https://otel.ai/company"]
    kinds = [err.kind for err in result["errors"]]
    assert kinds.count("empty_response") >= 2
    assert any("skipped" in err.message for err in result["errors"])
