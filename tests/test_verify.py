"""test_verify.py per QUALITY.md: invented name dropped; middle-name match; bad
LinkedIn URL nulled. No network."""

from __future__ import annotations

import pytest

from enrich.cleaner import CleanPage, TeamCard
from enrich.discovery import FoundEmail, FoundLink
from enrich.fetcher import FetchedPage
from enrich.models import LLMEmail, LLMExtraction, LLMLeader
from enrich.verify import two_sentences, verify

PAGE_URL = "https://acme-corp.io/team"


def _clean(text: str, *, url: str = PAGE_URL, kind: str = "team") -> CleanPage:
    return CleanPage(url=url, kind=kind, markdown=text, raw_tokens=100, clean_tokens=20)


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
    markdown_kind: str = "team",
) -> dict:
    return {
        "domain": "acme-corp.io",
        "cleaned": [_clean(markdown, kind=markdown_kind)],
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
async def test_personal_site_owner_on_homepage_is_kept() -> None:
    """harshit147.dev-style: title/H1 names the owner, extractor drops the H1."""
    home = "https://harshit147.dev/"
    extraction = LLMExtraction(
        company_name="Harshit Pandit",
        overview="Harshit builds software. This is a personal site.",
        target_audience="Hiring managers.",
        industries=["software"],
        leaders=[
            _leader("Harshit Pandit", title=None, source_url=home),
        ],
        self_confidence=0.6,
    )
    state = {
        "domain": "harshit147.dev",
        "cleaned": [
            CleanPage(
                url=home,
                kind="home",
                markdown="Hi, I'm Harshit. I build software.\n",
                raw_tokens=50,
                clean_tokens=20,
            )
        ],
        "pages": [
            FetchedPage(
                url=home,
                requested_url=home,
                kind="home",
                discovered_by="seed",
                status="ok",
                http_status=200,
                html="<h1>Harshit Pandit</h1>",
                title="Harshit Pandit",
            )
        ],
        "candidate_emails": [],
        "linkedin_links": [],
        "team_cards": [],
        "extraction": extraction,
        "errors": [],
    }
    update = await verify(state)
    assert [leader.name for leader in update["leaders"]] == ["Harshit Pandit"]
    assert update["errors"] == []


@pytest.mark.asyncio
async def test_homepage_professional_title_without_foreign_company_is_kept() -> None:
    home = "https://acme-corp.io/"
    extraction = _extraction(
        [_leader("Jane Founder", title="Software Engineer", source_url=home)]
    )
    state = _state(
        extraction,
        "Jane Founder builds developer tools at Acme.",
    )
    state["cleaned"] = [
        CleanPage(
            url=home,
            kind="home",
            markdown="Jane Founder builds developer tools at Acme.",
            raw_tokens=40,
            clean_tokens=20,
        )
    ]
    update = await verify(state)
    assert [leader.name for leader in update["leaders"]] == ["Jane Founder"]


@pytest.mark.asyncio
async def test_personal_site_author_on_homepage_is_kept() -> None:
    home = "https://technical-notes.dev/"
    extraction = _extraction([_leader("Avery Writer", title=None, source_url=home)])
    state = _state(extraction, "Technical Notes — written by Avery Writer.")
    state["domain"] = "technical-notes.dev"
    state["cleaned"] = [
        CleanPage(
            url=home,
            kind="home",
            markdown="Technical Notes — written by Avery Writer.",
            raw_tokens=20,
            clean_tokens=10,
        )
    ]
    update = await verify(state)
    assert [leader.name for leader in update["leaders"]] == ["Avery Writer"]


@pytest.mark.asyncio
async def test_team_page_member_without_title_is_kept() -> None:
    """Regression for a real test gap: commenting out the PREFERRED_LEADER_KINDS check
    in `_name_on_preferred_pages` (always returning False) still passed the whole suite
    before this test existed. A person named on a `team`-kind page with no professional
    title and no personal-site signal must be kept *because* they're on a preferred
    page — if that check is disabled, `_homepage_leader_allowed` would wrongly demand a
    title and drop them."""
    extraction = _extraction([_leader("Priya Ops", title=None)])
    state = _state(
        extraction, "Priya Ops is part of our operations team.", markdown_kind="team"
    )
    update = await verify(state)
    assert [leader.name for leader in update["leaders"]] == ["Priya Ops"]
    assert update["errors"] == []


@pytest.mark.asyncio
async def test_team_card_grounds_name_without_title() -> None:
    """Same gap, via the TEAM CARDS branch of `_name_on_preferred_pages` rather than
    cleaned-page markdown."""
    extraction = _extraction(
        [_leader("Priya Ops", title=None, source_url="https://acme-corp.io/")]
    )
    state = _state(
        extraction,
        "Meet the team.",
        markdown_kind="home",
        cards=[TeamCard(name="Priya Ops", role=None, source_url=PAGE_URL)],
    )
    update = await verify(state)
    assert [leader.name for leader in update["leaders"]] == ["Priya Ops"]


@pytest.mark.asyncio
async def test_homepage_only_member_without_title_is_dropped() -> None:
    """Contrast case for the two tests above: the same person with no title and no
    personal-site signal, found ONLY on the homepage (not a preferred kind, no team
    card), must still be dropped."""
    extraction = _extraction(
        [_leader("Priya Ops", title=None, source_url="https://acme-corp.io/")]
    )
    state = _state(
        extraction, "Priya Ops is part of our operations team.", markdown_kind="home"
    )
    update = await verify(state)
    assert update["leaders"] == []
    assert update["errors"][0].kind == "unverified_person"


@pytest.mark.asyncio
async def test_linkedin_kept_via_page_markdown_fallback() -> None:
    """`_linkedin_kept` has two acceptance paths: the URL is in `linkedin_links`, or it
    literally appears in some fetched page's cleaned markdown. Every other test in this
    file only exercises the first path (via `links=`); this one has no `linkedin_links`
    entry at all, so it can only pass through the markdown fallback."""
    url = "https://www.linkedin.com/in/jane-founder"
    extraction = _extraction([_leader("Jane Founder", linkedin_url=url)])
    state = _state(
        extraction,
        f"Jane Founder is CEO. Find her at {url}.",
    )
    update = await verify(state)
    assert update["leaders"][0].linkedin_url == url
    assert update["leaders"][0].linkedin_source == "website"


@pytest.mark.asyncio
async def test_title_naming_target_company_is_not_foreign() -> None:
    """`_foreign_company_in_title` must not drop a title just because it has a
    separator — only when the part *after* the separator names a company that is not
    the target. "CEO at Acme" (the target company itself) must be kept."""
    extraction = _extraction([_leader("Jane Founder", title="CEO at Acme")])
    state = _state(extraction, "Jane Founder, CEO at Acme, leads the company.")
    update = await verify(state)
    assert [leader.name for leader in update["leaders"]] == ["Jane Founder"]


@pytest.mark.asyncio
async def test_personal_site_subject_matches_via_company_name_alone() -> None:
    """`_personal_site_subject` has three independent acceptance paths (domain match,
    company_name match, owner-phrase match). This fixture defeats the first two ways to
    reach True by any means BUT the company-name match: the domain doesn't contain the
    person's name, and the markdown has no "I'm"/"about me"-style phrase."""
    home = "https://flagship-studio.example/"
    extraction = LLMExtraction(
        company_name="Jordan Solo",
        overview="Jordan Solo builds tools. This is a personal site.",
        target_audience="Visitors.",
        industries=["software"],
        leaders=[_leader("Jordan Solo", title=None, source_url=home)],
        self_confidence=0.6,
    )
    state = {
        "domain": "flagship-studio.example",
        "cleaned": [
            CleanPage(
                url=home,
                kind="home",
                markdown="Jordan Solo. Tools for developers.",
                raw_tokens=20,
                clean_tokens=10,
            )
        ],
        "candidate_emails": [],
        "linkedin_links": [],
        "team_cards": [],
        "extraction": extraction,
        "errors": [],
    }
    update = await verify(state)
    assert [leader.name for leader in update["leaders"]] == ["Jordan Solo"]


@pytest.mark.asyncio
async def test_personal_site_owner_marker_without_domain_or_company_match() -> None:
    """Isolates the owner-marker branch of `_personal_site_subject`: neither the domain
    nor `company_name` names the person, so only the "about me"-style phrase can allow
    them through."""
    home = "https://myportfolio.example/"
    extraction = LLMExtraction(
        company_name="My Portfolio",
        overview="A personal portfolio site. It showcases projects.",
        target_audience="Visitors.",
        industries=["software"],
        leaders=[_leader("Riley Quinn", title=None, source_url=home)],
        self_confidence=0.6,
    )
    state = {
        "domain": "myportfolio.example",
        "cleaned": [
            CleanPage(
                url=home,
                kind="home",
                markdown="About me: I'm Riley Quinn, a software engineer.",
                raw_tokens=20,
                clean_tokens=10,
            )
        ],
        "candidate_emails": [],
        "linkedin_links": [],
        "team_cards": [],
        "extraction": extraction,
        "errors": [],
    }
    update = await verify(state)
    assert [leader.name for leader in update["leaders"]] == ["Riley Quinn"]


def test_two_sentences_single_sentence_is_left_alone() -> None:
    assert two_sentences("Acme builds widgets.") == "Acme builds widgets."


def test_two_sentences_adds_missing_terminal_punctuation() -> None:
    result = two_sentences("Acme builds widgets. Developers use them daily")
    assert result == "Acme builds widgets. Developers use them daily."


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
