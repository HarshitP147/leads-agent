"""test_verify.py per QUALITY.md: invented name dropped; middle-name match; bad
LinkedIn URL nulled. No network."""

from __future__ import annotations

import pytest

from enrich.cleaner import CleanPage, TeamCard
from enrich.discovery import FoundEmail, FoundLink
from enrich.models import LLMEmail, LLMExtraction, LLMLeader
from enrich.verify import verify

PAGE_URL = "https://acme-corp.io/team"


def _clean(text: str, *, url: str = PAGE_URL) -> CleanPage:
    return CleanPage(
        url=url, kind="team", markdown=text, raw_tokens=100, clean_tokens=20
    )


def _leader(
    name: str,
    *,
    title: str | None = "CEO",
    linkedin_url: str | None = None,
    source_url: str = PAGE_URL,
) -> LLMLeader:
    return LLMLeader(
        name=name,
        title=title,
        linkedin_url=linkedin_url,
        source_url=source_url,
        evidence=name,
    )


def _extraction(
    leaders: list[LLMLeader], emails: list[LLMEmail] | None = None
) -> LLMExtraction:
    return LLMExtraction(
        company_name="Acme",
        overview="Acme builds widgets. Developers use them daily.",
        target_audience="Developers building backends.",
        industries=["software"],
        contact_emails=emails or [],
        leaders=leaders,
        self_confidence=0.8,
    )


def _state(
    extraction: LLMExtraction,
    markdown: str,
    *,
    emails: list[FoundEmail] | None = None,
    links: list[FoundLink] | None = None,
    cards: list[TeamCard] | None = None,
) -> dict:
    return {
        "domain": "acme-corp.io",
        "cleaned": [_clean(markdown)],
        "candidate_emails": emails or [],
        "linkedin_links": links or [],
        "team_cards": cards or [],
        "extraction": extraction,
        "errors": [],
    }


@pytest.mark.asyncio
async def test_invented_name_is_dropped() -> None:
    extraction = _extraction(
        [_leader("Jane Founder"), _leader("John Fakeperson")],
    )
    state = _state(extraction, "The team is led by Jane Founder, our CEO.")

    update = await verify(state)

    names = [leader.name for leader in update["leaders"]]
    assert names == ["Jane Founder"]
    assert all(leader.verified for leader in update["leaders"])
    kinds = [err.kind for err in update["errors"]]
    assert kinds == ["unverified_person"]
    assert "John Fakeperson" in update["errors"][0].message


@pytest.mark.asyncio
async def test_middle_name_matches_first_and_last() -> None:
    extraction = _extraction([_leader("Paul James Copplestone")])
    state = _state(extraction, "Paul Copplestone is co-founder and CEO.")

    update = await verify(state)

    assert [leader.name for leader in update["leaders"]] == ["Paul James Copplestone"]
    assert update["leaders"][0].verified is True
    assert update["errors"] == []


@pytest.mark.asyncio
async def test_bad_linkedin_url_is_nulled() -> None:
    good = "https://www.linkedin.com/in/jane-founder"
    extraction = _extraction(
        [
            _leader("Jane Founder", linkedin_url="https://linkedin.com/company/acme"),
            _leader("Ada Example", linkedin_url=good),
        ]
    )
    state = _state(
        extraction,
        "Jane Founder and Ada Example run the company.",
        links=[FoundLink(url=good, anchor_text="Ada Example", source_url=PAGE_URL)],
    )

    update = await verify(state)

    by_name = {leader.name: leader for leader in update["leaders"]}
    assert by_name["Jane Founder"].linkedin_url is None
    assert by_name["Jane Founder"].linkedin_source is None
    assert by_name["Ada Example"].linkedin_url == good
    assert by_name["Ada Example"].linkedin_source == "website"


VAPI_HOME = "https://vapi.ai/"
VAPI_TESTIMONIAL_MD = """
Vapi is a platform for voice agents.

"Vapi helped us ship voice agents in a week."
Jason Mitura
VP of Software Development

"The product is incredible."
Alejandro Maza, Chief Product & AI Officer, Kavak
"""


def _vapi_state(leaders: list[LLMLeader]) -> dict:
    extraction = LLMExtraction(
        company_name="Vapi",
        overview="Vapi builds voice agents. Developers use them.",
        target_audience="Developers.",
        industries=["software"],
        leaders=leaders,
        self_confidence=0.7,
    )
    return {
        "domain": "vapi.ai",
        "cleaned": [
            CleanPage(
                url=VAPI_HOME,
                kind="home",
                markdown=VAPI_TESTIMONIAL_MD,
                raw_tokens=200,
                clean_tokens=80,
            )
        ],
        "candidate_emails": [],
        "linkedin_links": [],
        "team_cards": [],
        "extraction": extraction,
        "errors": [],
    }


@pytest.mark.asyncio
async def test_vapi_testimonial_jason_mitura_is_dropped() -> None:
    extraction_leaders = [
        _leader(
            "Jason Mitura",
            title="VP of Software Development",
            source_url=VAPI_HOME,
        )
    ]
    # evidence is the quoted homepage snippet
    extraction_leaders[0].evidence = '"Vapi helped us ship voice agents in a week."'
    update = await verify(_vapi_state(extraction_leaders))
    assert update["leaders"] == []
    assert update["errors"][0].kind == "unverified_person"
    assert "Jason Mitura" in update["errors"][0].message


@pytest.mark.asyncio
async def test_vapi_kavak_cpo_alejandro_maza_is_dropped() -> None:
    update = await verify(
        _vapi_state(
            [
                _leader(
                    "Alejandro Maza",
                    title="Chief Product & AI Officer, Kavak",
                    source_url=VAPI_HOME,
                )
            ]
        )
    )
    assert update["leaders"] == []
    assert update["errors"][0].kind == "unverified_person"
    assert "Alejandro Maza" in update["errors"][0].message


@pytest.mark.asyncio
async def test_candidate_email_ignored_by_llm_is_still_kept() -> None:
    extraction = _extraction(
        [_leader("Jane Founder")],
        emails=[LLMEmail(email="sales@acme-corp.io", purpose="sales")],
    )
    state = _state(
        extraction,
        "Jane Founder is CEO.",
        emails=[
            FoundEmail(
                email="sales@acme-corp.io", source_url="https://acme-corp.io/contact"
            ),
            FoundEmail(email="hello@acme-corp.io", source_url="https://acme-corp.io"),
        ],
    )

    update = await verify(state)

    by_addr = {item.email: item for item in update["contact_emails"]}
    assert by_addr["sales@acme-corp.io"].purpose == "sales"
    assert by_addr["hello@acme-corp.io"].purpose == "other"
    assert by_addr["hello@acme-corp.io"].source_url == "https://acme-corp.io"
