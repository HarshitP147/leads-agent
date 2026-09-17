"""Tavily enrichment for LinkedIn profiles, missing leaders, and public emails.

Search results are evidence, not truth: every person needs a direct LinkedIn ``/in/``
result plus nearby target-company leadership evidence. Emails must occur literally in
an indexed page on the target company's site. LinkedIn pages are never fetched.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field
from tavily import AsyncTavilyClient

from enrich.config import get_settings
from enrich.cost import UsageEvent
from enrich.discovery import _is_junk_email
from enrich.models import ContactEmail, ErrorRecord, Leader
from enrich.verify import LINKEDIN_RE

if TYPE_CHECKING:
    from enrich.state import DomainState

logger = logging.getLogger(__name__)

SEARCH_DEPTH = "basic"
SEARCH_TIMEOUT_S = 30
MAX_RESULTS = 10
MAX_LEADER_LOOKUPS = 5
MAX_DISCOVERED_LEADERS = 5

_NAME_TOKEN = r"[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’\-]+"
_PERSON_NAME = rf"{_NAME_TOKEN}(?:[ \t]+{_NAME_TOKEN}){{1,3}}?"
_ROLE_TERM = r"(?i:co[- ]?founder|founder|ceo|cto|coo|cfo|president)"
_ROLE_BLOCK = rf"{_ROLE_TERM}(?:\s*(?:&|and|/|,)\s*{_ROLE_TERM}){{0,2}}"
_PERSON_ROLE_RE = re.compile(
    rf"(?P<name>{_PERSON_NAME})\s*(?:['’]s)?\s*[,|.()\n\-]*\s*"
    rf"(?P<title>{_ROLE_BLOCK})\s*(?:(?i:at|of)|@|,)\s*",
)
_EMAIL_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.IGNORECASE)
_BAD_NAME_WORDS = frozenset(
    {"linkedin", "profile", "view", "today", "image", "company", "founder", "ceo"}
)


class TavilyResult(BaseModel):
    """Subset of Tavily's result schema used by deterministic validators."""

    model_config = ConfigDict(extra="ignore")

    title: str = ""
    url: str
    content: str = ""
    raw_content: str | None = None
    score: float | None = Field(default=None, ge=0.0, le=1.0)


class TavilyResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    results: list[TavilyResult] = Field(default_factory=list)


@dataclass(frozen=True)
class SearchOutcome:
    results: list[TavilyResult]
    event: UsageEvent
    error: ErrorRecord | None = None


@dataclass(frozen=True)
class RoleEvidence:
    name: str
    title: str
    source_url: str


def _normalise(value: str) -> str:
    nfkd = unicodedata.normalize("NFKD", value)
    plain = "".join(char for char in nfkd if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", plain.casefold()).strip()


def _company_aliases(company_name: str, domain: str) -> set[str]:
    host = (urlparse(f"https://{domain}").hostname or domain).removeprefix("www.")
    stem = host.split(".")[0]
    company = _normalise(company_name)
    return {alias for alias in (company, _normalise(stem)) if alias}


def _result_text(result: TavilyResult, *, raw: bool = False) -> str:
    values = [result.title, result.content]
    if raw and result.raw_content:
        values.append(result.raw_content)
    return "\n".join(value for value in values if value)


def _has_company(text: str, aliases: set[str]) -> bool:
    normalised = _normalise(text)
    padded = f" {normalised} "
    return any(f" {alias} " in padded for alias in aliases)


def _valid_person_name(name: str) -> bool:
    tokens = _normalise(name).split()
    if not 2 <= len(tokens) <= 4 or any(tok in _BAD_NAME_WORDS for tok in tokens):
        return False
    midpoint = len(tokens) // 2
    return len(tokens) % 2 != 0 or tokens[:midpoint] != tokens[midpoint:]


def _role_evidence(
    results: list[TavilyResult], aliases: set[str]
) -> dict[str, RoleEvidence]:
    found: dict[str, RoleEvidence] = {}
    for result in results:
        text = _result_text(result)
        for match in _PERSON_ROLE_RE.finditer(text):
            name = match.group("name").strip(" ,.-\n")
            company_window = text[match.end() : match.end() + 100]
            if not _valid_person_name(name) or not _has_company(
                company_window, aliases
            ):
                continue
            key = _normalise(name)
            found.setdefault(
                key,
                RoleEvidence(
                    name=name,
                    title=match.group("title").strip(),
                    source_url=result.url,
                ),
            )
    return found


def _name_in_text(name: str, text: str) -> bool:
    target = _normalise(name)
    haystack = _normalise(text)
    if target in haystack:
        return True
    tokens = target.split()
    return len(tokens) >= 3 and f"{tokens[0]} {tokens[-1]}" in haystack


def _profile_url(
    name: str, results: list[TavilyResult], aliases: set[str]
) -> str | None:
    for result in results:
        if not LINKEDIN_RE.match(result.url):
            continue
        evidence = _result_text(result)
        slug = _normalise(urlparse(result.url).path.rsplit("/", 1)[-1]).replace(" ", "")
        compact_name = _normalise(name).replace(" ", "")
        identity_matches = _name_in_text(name, result.title) or compact_name in slug
        if identity_matches and _has_company(evidence, aliases):
            return result.url
    return None


def _profile_name(result: TavilyResult, aliases: set[str]) -> str | None:
    if not LINKEDIN_RE.match(result.url) or not _has_company(
        result.content[:300], aliases
    ):
        return None
    match = re.match(rf"(?P<name>{_PERSON_NAME})(?:\s*[|\-]|$)", result.title)
    if match is None:
        return None
    name = match.group("name")
    return name if _valid_person_name(name) else None


async def _search(
    client: AsyncTavilyClient,
    query: str,
    *,
    include_domains: list[str],
    raw_content: bool = False,
) -> SearchOutcome:
    event = UsageEvent(component="search", search_calls=1, estimated=True)
    try:
        response = await client.search(
            query,
            search_depth=SEARCH_DEPTH,
            max_results=MAX_RESULTS,
            include_domains=include_domains,
            include_domains_mode="filter",
            include_raw_content="text" if raw_content else False,
            timeout=SEARCH_TIMEOUT_S,
        )
        results = TavilyResponse.model_validate(response).results
        return SearchOutcome(results=results, event=event)
    except Exception as exc:  # noqa: BLE001 — one failed search must not end a domain
        message = f"{type(exc).__name__}: {exc}".splitlines()[0][:200]
        return SearchOutcome(
            results=[],
            event=event,
            error=ErrorRecord(stage="search", kind="search_error", message=message),
        )


def _leader_query(leader: Leader, company_name: str) -> str:
    role = leader.title or "founder or executive"
    return f'LinkedIn profile for "{leader.name}", {role} at "{company_name}"'


def _discovery_query(company_name: str) -> str:
    return f'"{company_name}" founders co-founders CEO CTO LinkedIn profiles'


def _enrich_known_leader(
    leader: Leader, outcome: SearchOutcome, aliases: set[str]
) -> Leader:
    roles = _role_evidence(outcome.results, aliases)
    if _normalise(leader.name) not in roles:
        return leader
    url = _profile_url(leader.name, outcome.results, aliases)
    if url is None:
        return leader
    return leader.model_copy(update={"linkedin_url": url, "linkedin_source": "search"})


async def _enrich_known_leaders(
    client: AsyncTavilyClient,
    leaders: list[Leader],
    company_name: str,
    aliases: set[str],
) -> tuple[list[Leader], list[SearchOutcome]]:
    missing = [leader for leader in leaders if not leader.linkedin_url][
        :MAX_LEADER_LOOKUPS
    ]
    tasks = [
        _search(
            client,
            _leader_query(leader, company_name),
            include_domains=["linkedin.com"],
        )
        for leader in missing
    ]
    outcomes = await asyncio.gather(*tasks) if tasks else []
    by_name = {
        _normalise(leader.name): _enrich_known_leader(leader, outcome, aliases)
        for leader, outcome in zip(missing, outcomes, strict=True)
    }
    enriched = [by_name.get(_normalise(leader.name), leader) for leader in leaders]
    return enriched, list(outcomes)


async def _discover_leaders(
    client: AsyncTavilyClient, company_name: str, aliases: set[str]
) -> tuple[list[Leader], list[SearchOutcome]]:
    outcome = await _search(
        client,
        _discovery_query(company_name),
        include_domains=["linkedin.com"],
    )
    outcomes = [outcome]
    roles = _role_evidence(outcome.results, aliases)
    missing_roles = [
        name
        for result in outcome.results
        if (name := _profile_name(result, aliases)) is not None
        and _normalise(name) not in roles
    ][:3]
    fallback_tasks = [
        _search(
            client,
            f'LinkedIn profile for "{name}", founder or executive at "{company_name}"',
            include_domains=["linkedin.com"],
        )
        for name in missing_roles
    ]
    if fallback_tasks:
        outcomes.extend(await asyncio.gather(*fallback_tasks))
    combined = [result for item in outcomes for result in item.results]
    roles = _role_evidence(combined, aliases)
    leaders: list[Leader] = []
    for evidence in roles.values():
        url = _profile_url(evidence.name, combined, aliases)
        if url is None:
            continue
        leaders.append(
            Leader(
                name=evidence.name,
                title=evidence.title,
                linkedin_url=url,
                linkedin_source="search",
                source_url=evidence.source_url,
                verified=True,
            )
        )
        if len(leaders) >= MAX_DISCOVERED_LEADERS:
            break
    return leaders, outcomes


def _is_target_url(url: str, domain: str) -> bool:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    target = domain.lower().removeprefix("www.")
    return host == target or host.endswith(f".{target}")


def _company_email(address: str, domain: str, aliases: set[str]) -> bool:
    email_domain = address.rsplit("@", 1)[-1].casefold()
    target = domain.casefold().removeprefix("www.")
    if email_domain == target or email_domain.endswith(f".{target}"):
        return True
    email_stem = email_domain.split(".")[0]
    return any(email_stem == alias.replace(" ", "") for alias in aliases)


def _email_purpose(address: str) -> str:
    local = address.split("@", 1)[0].casefold()
    if "sales" in local:
        return "sales"
    if any(word in local for word in ("support", "help")):
        return "support"
    if any(word in local for word in ("career", "jobs", "talent", "hiring")):
        return "careers"
    if "press" in local or "media" in local or local == "pr":
        return "press"
    if "privacy" in local:
        return "privacy"
    if any(word in local for word in ("security", "abuse")):
        return "security"
    if any(word in local for word in ("contact", "hello", "info")):
        return "general"
    return "other"


def _emails_from_results(
    results: list[TavilyResult], domain: str, aliases: set[str]
) -> list[ContactEmail]:
    found: dict[str, ContactEmail] = {}
    for result in results:
        if not _is_target_url(result.url, domain):
            continue
        for match in _EMAIL_RE.finditer(_result_text(result, raw=True)):
            address = match.group(0).casefold().rstrip(".")
            if _is_junk_email(address) or not _company_email(address, domain, aliases):
                continue
            found.setdefault(
                address,
                ContactEmail(
                    email=address,
                    purpose=_email_purpose(address),
                    source_url=result.url,
                ),
            )
    return list(found.values())


async def _search_emails(
    client: AsyncTavilyClient,
    domain: str,
    company_name: str,
    aliases: set[str],
) -> tuple[list[ContactEmail], list[SearchOutcome]]:
    queries = (
        f'Public contact support and sales email addresses for "{company_name}" ({domain})',
        f'Public security privacy legal abuse press and careers emails for "{company_name}" ({domain})',
    )
    outcomes = await asyncio.gather(
        *(
            _search(client, query, include_domains=[domain], raw_content=True)
            for query in queries
        )
    )
    results = [result for item in outcomes for result in item.results]
    return _emails_from_results(results, domain, aliases), list(outcomes)


def _merge_emails(
    existing: list[ContactEmail], searched: list[ContactEmail]
) -> list[ContactEmail]:
    merged = {item.email.casefold(): item for item in existing}
    for item in searched:
        merged.setdefault(item.email.casefold(), item)
    return list(merged.values())


async def _run_searches(
    client: AsyncTavilyClient,
    state: DomainState,
    domain: str,
    company_name: str,
    aliases: set[str],
) -> tuple[list[Leader], list[ContactEmail], list[SearchOutcome]]:
    email_task = _search_emails(client, domain, company_name, aliases)
    if state.get("leaders"):
        leader_task = _enrich_known_leaders(
            client, state.get("leaders", []), company_name, aliases
        )
    else:
        leader_task = _discover_leaders(client, company_name, aliases)
    (leaders, leader_outcomes), (emails, email_outcomes) = await asyncio.gather(
        leader_task, email_task
    )
    return leaders, emails, [*leader_outcomes, *email_outcomes]


def _search_update(
    state: DomainState,
    leaders: list[Leader],
    emails: list[ContactEmail],
    outcomes: list[SearchOutcome],
) -> dict:
    return {
        "leaders": leaders,
        "contact_emails": _merge_emails(state.get("contact_emails", []), emails),
        "errors": [item.error for item in outcomes if item.error is not None],
        "usage_events": [item.event for item in outcomes],
        "route_log": [
            f"search_linkedin:leaders={len(leaders)} emails_added={len(emails)}"
        ],
    }


async def search_linkedin(state: DomainState) -> dict:
    """Enrich verified data with company-scoped Tavily evidence. Never raises."""
    started = time.monotonic()
    settings = get_settings()
    api_key = (settings.tavily_api_key or "").strip()
    if not api_key:
        logger.info(
            "domain=%s route_log=search_linkedin:skipped_no_tavily_key",
            state.get("domain", ""),
        )
        return {"route_log": ["search_linkedin:skipped_no_tavily_key"]}

    extraction = state.get("extraction")
    if extraction is None:
        return {"route_log": ["search_linkedin:skipped_no_extraction"]}

    domain = state.get("domain", "")
    aliases = _company_aliases(extraction.company_name, domain)
    try:
        async with AsyncTavilyClient(api_key=api_key) as client:
            leaders, emails, outcomes = await _run_searches(
                client, state, domain, extraction.company_name, aliases
            )
        logger.info(
            "search done domain=%s leaders=%d emails=%d calls=%d duration=%.2fs",
            domain,
            len(leaders),
            len(emails),
            len(outcomes),
            time.monotonic() - started,
        )
        return _search_update(state, leaders, emails, outcomes)
    except Exception as exc:  # noqa: BLE001 — stage-level last line of defence
        logger.error(
            "domain %s: search failed: %s: %s", domain, type(exc).__name__, exc
        )
        return {
            "errors": [
                ErrorRecord(stage="search", kind="search_error", message=str(exc)[:200])
            ],
            "route_log": ["search_linkedin:search_error"],
        }
