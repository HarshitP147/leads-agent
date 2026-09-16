"""robots/sitemap parsing + anchor scoring -> ranked candidate links; email/LinkedIn harvest.

See docs/design-docs/discovery-and-cleaning.md (Discovery).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

if TYPE_CHECKING:
    from enrich.state import DomainState


class CandidateLink(BaseModel):
    url: str
    kind: Literal[
        "about", "team", "company", "contact", "pricing", "leadership", "other"
    ]
    score: float
    discovered_by: Literal["sitemap", "anchor", "guess"]
    anchor_text: str | None = None


class FoundEmail(BaseModel):
    email: str
    source_url: str


class FoundLink(BaseModel):
    url: str
    anchor_text: str | None
    source_url: str


async def discover_links(state: DomainState) -> dict:
    """Node: rank candidate subpages and harvest emails/LinkedIn links from the homepage."""
    raise NotImplementedError
