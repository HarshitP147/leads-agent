"""Usage events -> tokens + USD per domain; pricing table.

See docs/design-docs/cost-tracking.md.
"""

from __future__ import annotations

from pydantic import BaseModel

# PRICING: {model_id: (usd_per_mtok_input, usd_per_mtok_output)}. Fill in from providers'
# official pricing pages before M6 and note the date checked here.
PRICING: dict[str, tuple[float, float]] = {}
TAVILY_USD_PER_CALL: float = 0.0


class UsageEvent(BaseModel):
    component: str  # "extraction" | "navigation" | "search"
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    search_calls: int = 0
    estimated: bool = False


def summarize_usage(events: list[UsageEvent]):
    """Roll up UsageEvents into a models.Usage. Unknown model -> cost 0, never a crash."""
    raise NotImplementedError
