"""Build prompt from cleaned pages; structured LLM call; usage capture.

See docs/design-docs/extraction-and-verification.md. Uses
`with_structured_output(LLMExtraction, include_raw=True)`, which returns a dict with
`raw`/`parsed`/`parsing_error` (verified in docs/references/stack.md).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from enrich.state import DomainState


async def extract(state: DomainState) -> dict:
    """Node: run the structured extraction call, with one repair retry on parse failure."""
    raise NotImplementedError
