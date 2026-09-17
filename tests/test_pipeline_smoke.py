"""M1 smoke test: run_pipeline must return a DomainResult, never raise, even when a
stage blows up. No network — the fetcher is stubbed."""

from __future__ import annotations

import pytest

from enrich import pipeline
from enrich.config import Settings
from enrich.fetcher import FetchedPage
from enrich.models import (
    ConfidenceBreakdown,
    DomainResult,
    Leader,
    LLMExtraction,
    LLMLeader,
)


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


def _ok_subpage() -> FetchedPage:
    return FetchedPage(
        url="https://example.com/about",
        requested_url="https://example.com/about",
        kind="about",
        discovered_by="anchor",
        status="ok",
        http_status=200,
        html="<html><body>about us</body></html>",
        title="About",
    )


@pytest.mark.asyncio
async def test_run_pipeline_ok_when_home_and_a_subpage_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = _ok_home()
    subpage = _ok_subpage()

    async def fake_fetch_home(state: dict) -> dict:
        return {"home": home, "pages": [home], "base_url": home.url}

    async def fake_discover_links(state: dict) -> dict:
        return {"candidates": [], "candidate_emails": [], "linkedin_links": []}

    async def fake_fetch_subpages(state: dict) -> dict:
        return {
            "pages": [*state["pages"], subpage],
            "candidate_emails": [],
            "linkedin_links": [],
        }

    async def fake_extract(state: dict) -> dict:
        return {
            "extraction": LLMExtraction(
                company_name="Example",
                overview="Example builds widgets. Developers use them.",
                target_audience="Developers.",
                industries=["software"],
                leaders=[
                    LLMLeader(
                        name="Ada Example",
                        title="CEO",
                        linkedin_url=None,
                        source_url=subpage.url,
                        evidence="Ada Example",
                    )
                ],
                self_confidence=0.8,
            )
        }

    async def fake_verify(state: dict) -> dict:
        return {
            "leaders": [
                Leader(
                    name="Ada Example",
                    title="CEO",
                    source_url=subpage.url,
                    verified=True,
                )
            ],
            "contact_emails": [],
        }

    async def fake_score(state: dict) -> dict:
        return {"confidence": ConfidenceBreakdown(score=0.72, components={})}

    async def fake_search(state: dict) -> dict:
        return {}

    monkeypatch.setattr(pipeline.fetcher, "fetch_home", fake_fetch_home)
    monkeypatch.setattr(pipeline.discovery, "discover_links", fake_discover_links)
    monkeypatch.setattr(pipeline.fetcher, "fetch_subpages", fake_fetch_subpages)
    monkeypatch.setattr(pipeline.extractor, "extract", fake_extract)
    monkeypatch.setattr(pipeline.verify, "verify", fake_verify)
    monkeypatch.setattr(pipeline.search, "search_linkedin", fake_search)
    monkeypatch.setattr(pipeline.scoring, "score", fake_score)

    result = await pipeline.run_pipeline("example.com", Settings())

    assert isinstance(result, DomainResult)
    assert result.domain == "example.com"
    assert result.status == "ok"  # profile + leaders, no blocked/timeout
    assert result.profile is not None
    assert result.profile.company_name == "Example"
    assert result.errors == []
    assert len(result.pages) == 2


@pytest.mark.asyncio
async def test_run_pipeline_partial_when_only_home_fetches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = _ok_home()

    async def fake_fetch_home(state: dict) -> dict:
        return {"home": home, "pages": [home], "base_url": home.url}

    async def fake_discover_links(state: dict) -> dict:
        return {"candidates": [], "candidate_emails": [], "linkedin_links": []}

    async def fake_fetch_subpages(state: dict) -> dict:
        return {"pages": state["pages"], "candidate_emails": [], "linkedin_links": []}

    async def fake_extract(state: dict) -> dict:
        return {
            "extraction": LLMExtraction(
                company_name="Example",
                overview="Example builds widgets. Developers use them.",
                target_audience="Developers.",
                industries=["software"],
                self_confidence=0.4,
            )
        }

    async def fake_verify(state: dict) -> dict:
        return {"leaders": [], "contact_emails": []}

    async def fake_score(state: dict) -> dict:
        return {"confidence": ConfidenceBreakdown(score=0.3, components={})}

    async def fake_search(state: dict) -> dict:
        return {}

    monkeypatch.setattr(pipeline.fetcher, "fetch_home", fake_fetch_home)
    monkeypatch.setattr(pipeline.discovery, "discover_links", fake_discover_links)
    monkeypatch.setattr(pipeline.fetcher, "fetch_subpages", fake_fetch_subpages)
    monkeypatch.setattr(pipeline.extractor, "extract", fake_extract)
    monkeypatch.setattr(pipeline.verify, "verify", fake_verify)
    monkeypatch.setattr(pipeline.search, "search_linkedin", fake_search)
    monkeypatch.setattr(pipeline.scoring, "score", fake_score)

    result = await pipeline.run_pipeline("example.com", Settings())

    assert isinstance(result, DomainResult)
    assert result.status == "partial"  # profile exists but leaders is empty
    assert result.profile is not None
    assert result.profile.leaders == []
    assert result.errors == []
    assert len(result.pages) == 1
    assert result.pages[0].url == home.url


@pytest.mark.asyncio
async def test_run_pipeline_survives_a_stage_that_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def blows_up(state: dict) -> dict:
        raise RuntimeError("boom")

    monkeypatch.setattr(pipeline.fetcher, "fetch_home", blows_up)

    result = await pipeline.run_pipeline("example.com", Settings())

    assert isinstance(result, DomainResult)
    assert result.status == "failed"
    assert len(result.errors) == 1
    assert result.errors[0].kind == "internal"
    assert "boom" in result.errors[0].message
