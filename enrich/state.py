"""LangGraph state for one domain's pipeline run.

`errors`, `usage_events` and `route_log` use additive reducers so every node can append
without clobbering what earlier nodes wrote (see docs/ARCHITECTURE.md, Node contract).
"""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from enrich.cleaner import CleanPage
from enrich.cost import UsageEvent
from enrich.discovery import CandidateLink, FoundEmail, FoundLink
from enrich.fetcher import FetchedPage
from enrich.models import (
    ConfidenceBreakdown,
    ContactEmail,
    ErrorRecord,
    Leader,
    LLMExtraction,
)


class DomainState(TypedDict, total=False):
    domain: str
    base_url: str
    home: FetchedPage | None
    candidates: list[CandidateLink]
    pages: list[FetchedPage]  # raw fetch results (html kept in memory only)
    cleaned: list[CleanPage]
    candidate_emails: list[FoundEmail]
    linkedin_links: list[FoundLink]
    extraction: LLMExtraction | None
    leaders: list[Leader]
    contact_emails: list[ContactEmail]
    confidence: ConfidenceBreakdown | None
    errors: Annotated[list[ErrorRecord], operator.add]
    usage_events: Annotated[list[UsageEvent], operator.add]
    route_log: Annotated[list[str], operator.add]
    started_at: float
