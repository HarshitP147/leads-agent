# Schemas

Two layers of models:

- **LLM-facing** (`LLMExtraction`): what the model is asked to fill. Kept flat and
  simple because every field costs tokens and strictness.
- **Output-facing** (`DomainResult`): what we write to disk, after verification,
  search enrichment, and scoring.

Field `description=`s matter: they are part of the prompt the model sees.

## LLM-facing (`models.py`)

```python
from typing import Literal
from pydantic import BaseModel, Field, HttpUrl, EmailStr

class LLMLeader(BaseModel):
    name: str = Field(description="Full name exactly as written on the page.")
    title: str | None = Field(None, description="Role/title exactly as written, e.g. 'Co-founder & CEO'.")
    linkedin_url: str | None = Field(None, description="Only if a linkedin.com URL for this person appears in the provided content.")
    source_url: str = Field(description="URL of the page (from the provided PAGE headers) where this person appears.")
    evidence: str = Field(description="Short verbatim snippet (<=20 words) from the page containing the name.")

class LLMEmail(BaseModel):
    email: str = Field(description="Must be one of the CANDIDATE EMAILS provided. Do not invent emails.")
    purpose: Literal["general", "sales", "support", "press", "security", "careers", "privacy", "other"]

class LLMExtraction(BaseModel):
    company_name: str
    overview: str = Field(description="Exactly two sentences on what the company does and its main product.")
    target_audience: str = Field(description="Who the product is built for, one sentence, e.g. 'Developers building backend applications'.")
    industries: list[str] = Field(default_factory=list, max_length=5)
    contact_emails: list[LLMEmail] = Field(default_factory=list)
    leaders: list[LLMLeader] = Field(default_factory=list, description="Only people explicitly shown as founders, executives, or leadership. Empty list if none are named.")
    self_confidence: float = Field(ge=0.0, le=1.0, description="How complete and well-supported this extraction is given the content.")
    missing_info_notes: str | None = Field(None, description="What could not be found.")
```

## Output-facing (`models.py`)

```python
class Leader(BaseModel):
    name: str
    title: str | None = None
    linkedin_url: str | None = None
    linkedin_source: Literal["website", "search"] | None = None
    source_url: str
    verified: bool                         # name grounded in page text

class ContactEmail(BaseModel):
    email: str
    purpose: str
    source_url: str

class PageRecord(BaseModel):
    url: str
    kind: Literal["home", "about", "team", "company", "contact", "pricing", "leadership", "other"]
    status: Literal["ok", "not_found", "blocked", "timeout", "error"]
    http_status: int | None = None
    raw_tokens: int | None = None
    clean_tokens: int | None = None
    discovered_by: Literal["seed", "sitemap", "anchor", "guess", "agent"]

class ErrorRecord(BaseModel):
    stage: str                             # node name
    kind: str                              # timeout | bot_wall | http_404 | llm_error | parse_error | ...
    message: str
    url: str | None = None

class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    llm_calls: int = 0
    search_calls: int = 0
    est_cost_usd: float = 0.0
    by_component: dict[str, float] = {}    # {"extraction": 0.01, "navigation": 0.004, ...}

class ConfidenceBreakdown(BaseModel):
    score: float
    components: dict[str, float]           # see confidence-scoring.md

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
    scraped_at: str                        # ISO-8601 UTC
```

Status rule: `failed` if no profile; `partial` if profile exists but any page was
blocked/timeout or leaders is empty; else `ok`.

**Interim rule (M1–M2, until `extract` lands in M3):** there is no `profile` yet, so the
rule above can't apply. Until then, status reflects fetch health only: `failed` if the
homepage itself failed to fetch; `ok` if the homepage fetched and at least one subpage
also fetched successfully; `partial` if the homepage fetched but no subpage did (empty
candidate list, all subpages 404/blocked/errored, etc). `# TODO M3`: swap back to the
real profile-based rule above once `extract`/`verify` exist.

## Graph state (`state.py`)

```python
import operator
from typing import Annotated, TypedDict

class DomainState(TypedDict, total=False):
    domain: str
    base_url: str
    home: FetchedPage | None
    candidates: list[CandidateLink]
    pages: list[FetchedPage]              # raw fetch results (html kept in memory only)
    cleaned: list[CleanPage]              # url, kind, markdown, token counts
    candidate_emails: list[FoundEmail]    # from regex/mailto, with source_url
    linkedin_links: list[FoundLink]       # linkedin.com/in/... hrefs with anchor text + source_url
    extraction: LLMExtraction | None
    leaders: list[Leader]
    contact_emails: list[ContactEmail]
    confidence: ConfidenceBreakdown | None
    errors: Annotated[list[ErrorRecord], operator.add]
    usage_events: Annotated[list[UsageEvent], operator.add]
    route_log: Annotated[list[str], operator.add]
    started_at: float
```

`FetchedPage`, `CandidateLink`, `CleanPage`, `FoundEmail`, `FoundLink`, `UsageEvent` are
small internal dataclasses/models defined next to the module that produces them.
Never put raw HTML into `DomainResult`.
