"""Browser Use fallback: agentic navigation when deterministic discovery finds too little.

Returns extra candidate URLs only — never final data. See
docs/design-docs/browser-use-fallback.md and the Browser Use findings in
docs/references/stack.md (LLM lives at `browser_use.llm.<provider>`, `max_steps` is a
`.run()` kwarg, token usage needs `Agent(calculate_cost=True)` and reads from
`history.usage`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from enrich.state import DomainState


async def agentic_navigate(state: DomainState) -> dict:
    """Node: ask Browser Use to find team/about/leadership URLs, capped by max_steps."""
    raise NotImplementedError
