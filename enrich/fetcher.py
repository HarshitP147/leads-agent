"""Playwright page fetch: shared browser, per-domain context, retries, bot-wall detection.

See docs/design-docs/discovery-and-cleaning.md (Fetching) and
docs/design-docs/resilience.md (Request level) for the full contract.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

if TYPE_CHECKING:
    from enrich.state import DomainState


class FetchedPage(BaseModel):
    """Result of fetching one URL. `html` is kept in memory only — never persisted
    into `DomainResult` (see AGENTS.md rule: no raw HTML to the LLM, ever)."""

    url: str  # final URL after redirects
    requested_url: str
    kind: Literal[
        "home", "about", "team", "company", "contact", "pricing", "leadership", "other"
    ]
    discovered_by: Literal["seed", "sitemap", "anchor", "guess", "agent"]
    status: Literal["ok", "not_found", "blocked", "timeout", "error"]
    http_status: int | None = None
    html: str | None = None
    title: str | None = None


async def fetch_home(state: DomainState) -> dict:
    """Node: fetch the domain's homepage. Returns a partial DomainState update."""
    raise NotImplementedError


async def fetch_subpages(state: DomainState) -> dict:
    """Node: fetch the discovered candidate subpages sequentially, with jitter."""
    raise NotImplementedError
