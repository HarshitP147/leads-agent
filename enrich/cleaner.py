"""HTML -> cleaned markdown, with fallback and token counts.

See docs/design-docs/discovery-and-cleaning.md (Cleaning).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

if TYPE_CHECKING:
    from enrich.state import DomainState


class CleanPage(BaseModel):
    url: str
    kind: Literal[
        "home", "about", "team", "company", "contact", "pricing", "leadership", "other"
    ]
    markdown: str
    raw_tokens: int
    clean_tokens: int


async def clean_pages(state: DomainState) -> dict:
    """Node: strip boilerplate, convert to markdown, truncate to MAX_CHARS_PER_PAGE."""
    raise NotImplementedError
