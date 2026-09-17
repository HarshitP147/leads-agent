"""Usage events -> tokens + USD per domain; pricing table.

See docs/design-docs/cost-tracking.md.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel

from enrich.models import Usage

logger = logging.getLogger(__name__)

# Official DeepSeek API pricing (https://api-docs.deepseek.com/quick_start/pricing),
# checked 17 Sep 2026. Values are USD per 1M tokens, cache-MISS, peak-hour list
# price (upper bound of the published range). Off-peak miss is half of these
# (flash $0.15 in / $0.60 out; v4-pro $0.66 in / $1.98 out). Cache-hit is much
# cheaper ($0.006 / $0.044 peak) — we do not currently split hit vs miss in
# usage_metadata, so the estimate is conservative.
# Keys must match EXTRACTION_MODEL / ChatDeepSeek.model_name exactly.
PRICING: dict[str, tuple[float, float]] = {
    "deepseek-flash": (0.30, 1.20),
    "deepseek-v4-pro": (1.32, 3.96),
}
# Tavily official pricing (https://docs.tavily.com/guides/api-credits), checked
# 17 Sep 2026: basic search = 1 credit; pay-as-you-go = $0.008/credit. M7 only
# emits basic searches, so one search call is conservatively estimated at $0.008.
TAVILY_USD_PER_CALL: float = 0.008


class UsageEvent(BaseModel):
    component: str  # "extraction" | "navigation" | "search"
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    search_calls: int = 0
    estimated: bool = False


def _event_cost(event: UsageEvent) -> float:
    if event.search_calls:
        return event.search_calls * TAVILY_USD_PER_CALL
    if not event.model:
        return 0.0
    rates = PRICING.get(event.model)
    if rates is None:
        logger.warning(
            "unknown model %r has no PRICING entry; costing $0.00", event.model
        )
        return 0.0
    usd_in, usd_out = rates
    return (event.input_tokens / 1_000_000) * usd_in + (
        event.output_tokens / 1_000_000
    ) * usd_out


def summarize_usage(events: list[UsageEvent]) -> Usage:
    """Roll up UsageEvents into a models.Usage. Unknown model -> cost 0 + warning."""
    by_component: dict[str, float] = {}
    for event in events:
        by_component[event.component] = by_component.get(
            event.component, 0.0
        ) + _event_cost(event)
    rounded_components = {
        component: round(cost, 6) for component, cost in by_component.items()
    }
    return Usage(
        input_tokens=sum(event.input_tokens for event in events),
        output_tokens=sum(event.output_tokens for event in events),
        llm_calls=sum(1 for event in events if event.component == "extraction"),
        search_calls=sum(event.search_calls for event in events),
        est_cost_usd=round(sum(by_component.values()), 6),
        by_component=rounded_components,
    )
