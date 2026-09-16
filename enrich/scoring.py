"""Deterministic confidence score formula.

See docs/design-docs/confidence-scoring.md.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from enrich.state import DomainState


async def score(state: DomainState) -> dict:
    """Node: compute the weighted confidence breakdown from verified data + page health."""
    raise NotImplementedError
