"""cost.py: PRICING lookup by DeepSeek model id; unknown model warns, does not crash."""

from __future__ import annotations

import logging

from enrich.cost import PRICING, TAVILY_USD_PER_CALL, UsageEvent, summarize_usage


def test_deepseek_flash_is_in_pricing_table() -> None:
    assert "deepseek-flash" in PRICING
    usd_in, usd_out = PRICING["deepseek-flash"]
    assert usd_in > 0 and usd_out > 0


def test_known_model_costs_extraction() -> None:
    usage = summarize_usage(
        [
            UsageEvent(
                component="extraction",
                model="deepseek-flash",
                input_tokens=1_000_000,
                output_tokens=1_000_000,
            )
        ]
    )
    usd_in, usd_out = PRICING["deepseek-flash"]
    assert usage.est_cost_usd == round(usd_in + usd_out, 6)
    assert usage.by_component["extraction"] == usage.est_cost_usd
    assert usage.llm_calls == 1


def test_unknown_model_warns_and_costs_zero(caplog: logging.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="enrich.cost")
    usage = summarize_usage(
        [
            UsageEvent(
                component="extraction",
                model="not-a-real-model",
                input_tokens=1000,
                output_tokens=100,
            )
        ]
    )
    assert usage.est_cost_usd == 0.0
    assert usage.input_tokens == 1000
    assert any("not-a-real-model" in rec.message for rec in caplog.records)


def test_tavily_basic_search_costs_by_call() -> None:
    usage = summarize_usage(
        [UsageEvent(component="search", search_calls=2, estimated=True)]
    )
    assert usage.search_calls == 2
    assert usage.est_cost_usd == 2 * TAVILY_USD_PER_CALL
    assert usage.by_component["search"] == usage.est_cost_usd
