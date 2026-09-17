"""Tavily search enrichment: accept only corroborated people and literal emails."""

from __future__ import annotations

import logging
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
async def test_missing_key_skips_without_error(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(search, "get_settings", lambda: Settings(tavily_api_key=None))
    caplog.set_level(logging.INFO, logger="enrich.search")
    update = await search.search_linkedin(_supabase_state())
    assert update == {"route_log": ["search_linkedin:skipped_no_tavily_key"]}
    assert "route_log=search_linkedin:skipped_no_tavily_key" in caplog.text


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


def test_email_from_unofficial_subdomain_is_rejected() -> None:
    """Regression for a live amazon.com run: search results included a
    sellercentral.amazon.com community-forum thread containing ~10 pasted addresses
    (one a bare `jeff@amazon.com`) that are not verifiable as genuine official Amazon
    contacts. A forum/community subdomain is not the target company's own published
    contact page, even though it shares the registrable domain."""
    results = [
        search.TavilyResult(
            title="Seller forum thread",
            url="https://sellercentral.amazon.com/seller-forums/discussions/t/123",
            content="",
            raw_content="jeff@amazon.com copyright@amazon.com",
        ),
        search.TavilyResult(
            title="Contact Amazon",
            url="https://www.amazon.com/gp/help/customer/contact-us",
            content="",
            raw_content="press@amazon.com",
        ),
    ]
    emails = search._emails_from_results(results, "amazon.com", {"amazon"})
    assert {item.email for item in emails} == {"press@amazon.com"}


class _CountingLinkedInClient:
    """Every call returns the same 3 named LinkedIn profiles that mention the target
    company but never satisfy `_role_evidence` (no role verb) — designed so the only
    thing that can end the fallback loop is the early-stop / budget logic under test,
    not a lucky match."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.no_role_results = [
            {
                "title": "Jordan Alpha",
                "url": "https://www.linkedin.com/in/jordan-alpha",
                "content": "Jordan Alpha works at Acme.",
            },
            {
                "title": "Taylor Beta",
                "url": "https://www.linkedin.com/in/taylor-beta",
                "content": "Taylor Beta works at Acme.",
            },
            {
                "title": "Casey Gamma",
                "url": "https://www.linkedin.com/in/casey-gamma",
                "content": "Casey Gamma works at Acme.",
            },
        ]

    async def search(self, query: str, **kwargs: object) -> dict:
        self.calls.append(query)
        return {"results": self.no_role_results}


@pytest.mark.asyncio
async def test_discover_leaders_stops_after_two_empty_queries() -> None:
    """A large budget is deliberately given so the early-stop rule — not the budget —
    is what ends the loop: 1 initial query + 1 fallback (both empty) must stop it
    before a 3rd, even though 2 more missing_roles candidates and 8 more budget units
    remain."""
    client = _CountingLinkedInClient()
    budget = search.SearchBudget(limit=10)

    await search._discover_leaders(client, "Acme", {"acme"}, budget)

    assert len(client.calls) == 2
    assert budget.remaining == 8


class _AlwaysHitLinkedInClient:
    """Every LinkedIn query returns a fresh, fully-corroborated role match — early-stop
    never fires — so only MAX_TAVILY_CALLS_PER_DOMAIN can be bounding the call count."""

    def __init__(self) -> None:
        self.linkedin_calls = 0
        self.email_calls = 0

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def search(self, query: str, **kwargs: object) -> dict:
        if kwargs.get("include_domains") == ["linkedin.com"]:
            self.linkedin_calls += 1
            n = self.linkedin_calls
            return {
                "results": [
                    {
                        "title": f"Jordan Founder{n}",
                        "url": f"https://www.linkedin.com/in/jordan-founder-{n}",
                        "content": f"Jordan Founder{n} Co-Founder & CEO at Acme.",
                    }
                ]
            }
        self.email_calls += 1
        return {"results": []}


@pytest.mark.asyncio
async def test_search_linkedin_never_exceeds_the_tavily_call_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """münchen.de and mercadolibre.com each burned 6 Tavily calls (1 discovery + 3
    fallback + 2 email) for zero accepted results before the shared budget existed.
    Here every query succeeds (so early-stop never triggers), proving the 3-call cap
    itself — not a lucky early stop — is what bounds the total."""
    client = _AlwaysHitLinkedInClient()
    monkeypatch.setattr(search, "AsyncTavilyClient", lambda api_key: client)
    monkeypatch.setattr(
        search, "get_settings", lambda: Settings(tavily_api_key="test-key")
    )
    extraction = LLMExtraction(
        company_name="Acme",
        overview="Acme builds widgets. It sells them online.",
        target_audience="Developers.",
        industries=["software"],
        self_confidence=0.5,
    )
    state = {"domain": "acme.example", "extraction": extraction, "leaders": []}

    await search.search_linkedin(state)

    assert (
        client.linkedin_calls + client.email_calls == search.MAX_TAVILY_CALLS_PER_DOMAIN
    )
