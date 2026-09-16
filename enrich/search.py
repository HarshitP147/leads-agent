"""Tavily LinkedIn lookups with result validation.

Uses `AsyncTavilyClient` (verified async-capable in docs/references/stack.md).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from enrich.state import DomainState


async def search_linkedin(state: DomainState) -> dict:
    """Node: look up LinkedIn URLs for verified leaders missing one."""
    raise NotImplementedError
