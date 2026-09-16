"""Pydantic models for LLM-facing extraction and disk-facing output.

Two layers, per docs/design-docs/schemas.md:
- LLM-facing (`LLMExtraction` & friends): what we ask the model to fill in.
- Output-facing (`DomainResult` & friends): what we write to `output.json`,
  after verification, search enrichment, and scoring.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# --- LLM-facing -------------------------------------------------------------


class LLMLeader(BaseModel):
    name: str = Field(description="Full name exactly as written on the page.")
    title: str | None = Field(
        None, description="Role/title exactly as written, e.g. 'Co-founder & CEO'."
    )
    linkedin_url: str | None = Field(
        None,
        description="Only if a linkedin.com URL for this person appears in the provided content.",
    )
    source_url: str = Field(
        description="URL of the page (from the provided PAGE headers) where this person appears."
    )
    evidence: str = Field(
        description="Short verbatim snippet (<=20 words) from the page containing the name."
    )


class LLMEmail(BaseModel):
    email: str = Field(
        description="Must be one of the CANDIDATE EMAILS provided. Do not invent emails."
    )
    purpose: Literal[
        "general",
        "sales",
        "support",
        "press",
        "security",
        "careers",
        "privacy",
        "other",
    ]


class LLMExtraction(BaseModel):
    company_name: str
    overview: str = Field(
        description="Exactly two sentences on what the company does and its main product."
    )
    target_audience: str = Field(
        description="Who the product is built for, one sentence, e.g. 'Developers building backend applications'."
    )
    industries: list[str] = Field(default_factory=list, max_length=5)
    contact_emails: list[LLMEmail] = Field(default_factory=list)
    leaders: list[LLMLeader] = Field(
        default_factory=list,
        description="Only people explicitly shown as founders, executives, or leadership. Empty list if none are named.",
    )
    self_confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="How complete and well-supported this extraction is given the content.",
    )
    missing_info_notes: str | None = Field(None, description="What could not be found.")


# --- Output-facing ------------------------------------------------------------


class Leader(BaseModel):
    name: str
    title: str | None = None
    linkedin_url: str | None = None
    linkedin_source: Literal["website", "search"] | None = None
    source_url: str
    verified: bool  # name grounded in page text


class ContactEmail(BaseModel):
    email: str
    purpose: str
    source_url: str


class PageRecord(BaseModel):
    url: str
    kind: Literal[
        "home", "about", "team", "company", "contact", "pricing", "leadership", "other"
    ]
    status: Literal["ok", "not_found", "blocked", "timeout", "error"]
    http_status: int | None = None
    raw_tokens: int | None = None
    clean_tokens: int | None = None
    discovered_by: Literal["seed", "sitemap", "anchor", "guess", "agent"]


class ErrorRecord(BaseModel):
    stage: str  # node name
    kind: str  # timeout | bot_wall | http_404 | llm_error | parse_error | ...
    message: str
    url: str | None = None


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    llm_calls: int = 0
    search_calls: int = 0
    est_cost_usd: float = 0.0
    by_component: dict[str, float] = Field(default_factory=dict)


class ConfidenceBreakdown(BaseModel):
    score: float
    components: dict[str, float]


class CompanyProfile(BaseModel):
    company_name: str
    overview: str
    target_audience: str
    industries: list[str]
    contact_emails: list[ContactEmail]
    leaders: list[Leader]


class DomainResult(BaseModel):
    domain: str
    status: Literal["ok", "partial", "failed"]
    profile: CompanyProfile | None
    confidence: ConfidenceBreakdown
    pages: list[PageRecord]
    errors: list[ErrorRecord]
    usage: Usage
    duration_s: float
    scraped_at: str  # ISO-8601 UTC
