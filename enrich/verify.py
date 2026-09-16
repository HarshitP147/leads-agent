"""Name/email/LinkedIn grounding checks: drop unverified people (AGENTS.md rule 5).

See docs/design-docs/extraction-and-verification.md.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from enrich.state import DomainState


async def verify(state: DomainState) -> dict:
    """Node: keep only leaders/emails grounded in fetched page text."""
    raise NotImplementedError
