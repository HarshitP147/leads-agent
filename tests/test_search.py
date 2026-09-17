"""Tavily search enrichment: accept only corroborated people and literal emails."""

from __future__ import annotations

from typing import Self

import pytest

from enrich import search
from enrich.config import Settings
from enrich.cost import UsageEvent
from enrich.models import ContactEmail, Leader, LLMExtraction


def _leader(name: str = "Ankit Sobti") -> Leader:
    return Leader(
        name=name,
        title="Co-founder",
        source_url="https://postman.com/company/about-postman",
        verified=True,
    )


def _outcome(results: list[search.TavilyResult]) -> search.SearchOutcome:
    return search.SearchOutcome(
        results=results,
        event=UsageEvent(component="search", search_calls=1),
    )


def test_known_leader_gets_only_corroborated_profile_url() -> None:
    results = [
        search.TavilyResult(
            title="Ankit Sobti posted on LinkedIn",
            url="https://www.linkedin.com/posts/ankit-sobti_post-id",
            content="Ankit Sobti Co-Founder, Postman",
        ),
        search.TavilyResult(
            title="Ankit Sobti",
            url="https://www.linkedin.com/in/ankit-sobti",
            content="# Ankit Sobti\nPostman\nSan Francisco Bay Area",
        ),
    ]
    enriched = search._enrich_known_leader(_leader(), _outcome(results), {"postman"})
    assert enriched.linkedin_url == "https://www.linkedin.com/in/ankit-sobti"
    assert enriched.linkedin_source == "search"


def test_wrong_company_role_evidence_does_not_enrich_leader() -> None:
    results = [
        search.TavilyResult(
            title="Ankit Sobti",
            url="https://www.linkedin.com/in/ankit-sobti",
            content="Ankit Sobti\nPostman",
        ),
        search.TavilyResult(
            title="Ankit Sobti posted on LinkedIn",
            url="https://www.linkedin.com/posts/ankit-sobti_post-id",
            content="Ankit Sobti Co-Founder at Another Company",
        ),
    ]
    enriched = search._enrich_known_leader(_leader(), _outcome(results), {"postman"})
    assert enriched.linkedin_url is None
    assert enriched.linkedin_source is None


def test_profile_identity_prevents_cross_assignment_and_company_substrings() -> None:
    results = [
        search.TavilyResult(
            title="Jordan D.",
            url="https://www.linkedin.com/in/jordandearsley",
            content="Nikhil Gupta, Co-Founder and CTO of Vapi. # Jordan D. Vapi",
        ),
        search.TavilyResult(
            title="Nikhil Gupta",
            url="https://www.linkedin.com/in/nikhilro",
            content="# Nikhil Gupta\nVapi (YC W21)",
        ),
        search.TavilyResult(
            title="Conference guest",
            url="https://www.linkedin.com/posts/conference_guest",
            content="Davit Baghdasaryan Co-Founder & CEO of Krisp at VapiCon.",
        ),
    ]
    aliases = {"vapi"}
    assert search._profile_url("Nikhil Gupta", results, aliases) == (
        "https://www.linkedin.com/in/nikhilro"
    )
    assert "davit baghdasaryan" not in search._role_evidence(results, aliases)


class _FakeTavilyClient:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def search(self, query: str, **kwargs: object) -> dict:
        if kwargs["include_domains"] == ["linkedin.com"]:
            return {
                "results": [
                    {
                        "title": "Ant Wilson's Post - LinkedIn",
                        "url": "https://www.linkedin.com/posts/ant-wilson_post-id",
                        "content": (
                            "Ant Wilson Co-Founder & CTO at Supabase. "
                            "Paul Copplestone CEO @ supabase.com."
                        ),
                    },
                    {
                        "title": "Ant Wilson",
                        "url": "https://uk.linkedin.com/in/ant-wilson-46179937",
                        "content": "# Ant Wilson\nSupabase\nSingapore",
                    },
                    {
                        "title": "Paul Copplestone",
                        "url": "https://www.linkedin.com/in/paulcopplestone",
                        "content": "# Paul Copplestone\nSupabase\nUnited States",
                    },
                ]
            }
        return {
            "results": [
                {
                    "title": "Contact Us | Supabase",
                    "url": "https://supabase.com/contact-us",
                    "content": "legal@supabase.com privacy@supabase.com",
                    "raw_content": (
                        "security@supabase.com abuse@supabase.io "
                        "no-reply@example.com vendor@unrelated.test"
                    ),
                },
                {
                    "title": "Copied contact list",
                    "url": "https://unrelated.test/supabase",
                    "content": "fake@supabase.com",
                },
            ]
        }


def _supabase_state() -> dict:
    extraction = LLMExtraction(
        company_name="Supabase",
        overview="Supabase is a Postgres platform. It provides backend services.",
        target_audience="Developers.",
        industries=["software"],
        self_confidence=0.7,
    )
    return {
        "domain": "supabase.com",
        "extraction": extraction,
        "leaders": [],
        "contact_emails": [
            ContactEmail(
                email="existing@supabase.com",
                purpose="other",
                source_url="https://supabase.com/",
            )
        ],
    }


@pytest.mark.asyncio
async def test_no_leaders_discovers_supabase_founders_and_public_emails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(search, "AsyncTavilyClient", _FakeTavilyClient)
    monkeypatch.setattr(
        search, "get_settings", lambda: Settings(tavily_api_key="test-key")
    )

    update = await search.search_linkedin(_supabase_state())

    by_name = {leader.name: leader for leader in update["leaders"]}
    assert set(by_name) == {"Ant Wilson", "Paul Copplestone"}
    assert by_name["Ant Wilson"].linkedin_url == (
        "https://uk.linkedin.com/in/ant-wilson-46179937"
    )
    assert all(leader.linkedin_source == "search" for leader in by_name.values())

    emails = {item.email: item.purpose for item in update["contact_emails"]}
    assert emails == {
        "existing@supabase.com": "other",
        "legal@supabase.com": "other",
        "privacy@supabase.com": "privacy",
        "security@supabase.com": "security",
        "abuse@supabase.io": "security",
    }
    assert len(update["usage_events"]) == 3
    assert update["errors"] == []


@pytest.mark.asyncio
async def test_missing_key_skips_without_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(search, "get_settings", lambda: Settings(tavily_api_key=None))
    update = await search.search_linkedin(_supabase_state())
    assert update == {"route_log": ["search_linkedin:skipped_no_tavily_key"]}


class _FailingClient:
    async def search(self, query: str, **kwargs: object) -> dict:
        raise TimeoutError("search provider timed out")


@pytest.mark.asyncio
async def test_one_search_failure_becomes_an_error_and_usage_event() -> None:
    outcome = await search._search(
        _FailingClient(),
        "query",
        include_domains=["linkedin.com"],
    )
    assert outcome.results == []
    assert outcome.error is not None
    assert outcome.error.kind == "search_error"
    assert outcome.event.search_calls == 1
