"""QUALITY.md: a fetcher stub that raises still yields a failed DomainResult.
Also a 404 subpage must not crash the run."""

from __future__ import annotations

import pytest

from enrich import pipeline
from enrich.config import Settings
from enrich.fetcher import FetchedPage
from enrich.models import DomainResult, ErrorRecord


def _ok_home() -> FetchedPage:
    return FetchedPage(
        url="https://example.com",
        requested_url="https://example.com",
        kind="home",
        discovered_by="seed",
        status="ok",
        http_status=200,
        html="<html><body>hi</body></html>",
        title="Example",
    )


@pytest.mark.asyncio
async def test_pipeline_with_raising_fetcher_returns_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def blows_up(state: dict) -> dict:
        raise RuntimeError("boom")

    monkeypatch.setattr(pipeline.fetcher, "fetch_home", blows_up)
    result = await pipeline.run_pipeline("example.com", Settings())
    assert isinstance(result, DomainResult)
    assert result.status == "failed"
    assert result.profile is None
    assert result.errors[0].kind == "internal"


@pytest.mark.asyncio
async def test_404_subpage_does_not_crash_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = _ok_home()
    missing = FetchedPage(
        url="https://example.com/team",
        requested_url="https://example.com/team",
        kind="team",
        discovered_by="guess",
        status="not_found",
        http_status=404,
        html=None,
        title="Not Found",
    )

    async def fake_fetch_home(state: dict) -> dict:
        return {"home": home, "pages": [home], "base_url": home.url}

    async def fake_discover(state: dict) -> dict:
        return {"candidates": [], "candidate_emails": [], "linkedin_links": []}

    async def fake_subpages(state: dict) -> dict:
        return {
            "pages": [*state["pages"], missing],
            "candidate_emails": [],
            "linkedin_links": [],
        }

    async def fake_extract(state: dict) -> dict:
        return {"extraction": None, "route_log": ["extract:skipped"]}

    async def fake_verify(state: dict) -> dict:
        return {"leaders": [], "contact_emails": []}

    async def fake_score(state: dict) -> dict:
        from enrich.models import ConfidenceBreakdown

        return {"confidence": ConfidenceBreakdown(score=0.0, components={})}

    monkeypatch.setattr(pipeline.fetcher, "fetch_home", fake_fetch_home)
    monkeypatch.setattr(pipeline.discovery, "discover_links", fake_discover)
    monkeypatch.setattr(pipeline.fetcher, "fetch_subpages", fake_subpages)
    monkeypatch.setattr(pipeline.extractor, "extract", fake_extract)
    monkeypatch.setattr(pipeline.verify, "verify", fake_verify)
    monkeypatch.setattr(pipeline.scoring, "score", fake_score)

    result = await pipeline.run_pipeline("example.com", Settings())
    assert result.status == "failed"  # no profile
    assert any(
        page.status == "not_found" and page.http_status == 404 for page in result.pages
    )


@pytest.mark.asyncio
async def test_bot_walled_home_skips_llm_and_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blocked = FetchedPage(
        url="https://walled.example",
        requested_url="https://walled.example",
        kind="home",
        discovered_by="seed",
        status="blocked",
        http_status=403,
        html="<html>Just a moment...</html>",
        title="Just a moment...",
    )
    extract_calls = {"n": 0}

    async def fake_fetch_home(state: dict) -> dict:
        return {
            "home": blocked,
            "pages": [blocked],
            "errors": [
                ErrorRecord(
                    stage="fetcher", kind="bot_wall", message="bot wall detected"
                )
            ],
        }

    async def fake_extract(state: dict) -> dict:
        extract_calls["n"] += 1
        return {}

    monkeypatch.setattr(pipeline.fetcher, "fetch_home", fake_fetch_home)
    monkeypatch.setattr(pipeline.extractor, "extract", fake_extract)

    result = await pipeline.run_pipeline("walled.example", Settings())
    assert result.status == "failed"
    assert extract_calls["n"] == 0
    assert any(e.kind == "bot_wall" for e in result.errors)


@pytest.mark.asyncio
async def test_missing_tavily_key_notes_skip_in_route_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = _ok_home()

    async def fake_fetch_home(state: dict) -> dict:
        return {"home": home, "pages": [home], "base_url": home.url}

    async def fake_discover(state: dict) -> dict:
        return {"candidates": [], "candidate_emails": [], "linkedin_links": []}

    async def fake_subpages(state: dict) -> dict:
        return {"pages": state["pages"], "candidate_emails": [], "linkedin_links": []}

    async def noop(state: dict) -> dict:
        return {}

    monkeypatch.setattr(pipeline.fetcher, "fetch_home", fake_fetch_home)
    monkeypatch.setattr(pipeline.discovery, "discover_links", fake_discover)
    monkeypatch.setattr(pipeline.fetcher, "fetch_subpages", fake_subpages)
    monkeypatch.setattr(pipeline.cleaner, "clean_pages", noop)
    monkeypatch.setattr(pipeline.extractor, "extract", noop)
    monkeypatch.setattr(pipeline.verify, "verify", noop)
    monkeypatch.setattr(pipeline.scoring, "score", noop)
    monkeypatch.setattr(
        pipeline.search,
        "get_settings",
        lambda: Settings(tavily_api_key=None),
    )

    state: dict = {"domain": "example.com", "started_at": 0.0}
    await pipeline._run_stages(state, debug_sink=None, domain="example.com")
    assert "search_linkedin:skipped_no_tavily_key" in state.get("route_log", [])
