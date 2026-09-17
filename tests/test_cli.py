"""Domain input hygiene: normalize/validate/dedupe before any fetching, and fail-fast
config validation. No network."""

from __future__ import annotations

import pytest

from enrich.cli import _prepare_domains, normalize_domain
from enrich.config import Settings, validate_settings


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("supabase.com", "supabase.com"),
        ("https://Supabase.com/pricing/", "supabase.com"),
        ("SUPABASE.COM", "supabase.com"),
        ("www.supabase.com", "supabase.com"),
        ("supabase.com/", "supabase.com"),
        ("supabase.com?utm_source=x", "supabase.com"),
        ("http://supabase.com", "supabase.com"),
    ],
)
def test_normalize_domain_accepts_and_strips(raw: str, expected: str) -> None:
    assert normalize_domain(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "localhost",
        "127.0.0.1",
        "http://127.0.0.1:8000",
        "10.0.0.5",
        "192.168.1.1",
        "ftp://example.com",
        "file:///etc/passwd",
        "",
        "   ",
    ],
)
def test_normalize_domain_rejects_unsafe_input(raw: str) -> None:
    assert normalize_domain(raw) is None


def test_prepare_domains_dedupes_case_insensitively_and_normalizes() -> None:
    valid, invalid = _prepare_domains(
        ["supabase.com", "Supabase.com", "https://SUPABASE.com/docs"]
    )
    assert valid == ["supabase.com"]
    assert invalid == []


def test_prepare_domains_keeps_first_seen_order_for_distinct_domains() -> None:
    valid, invalid = _prepare_domains(["vapi.ai", "supabase.com", "postman.com"])
    assert valid == ["vapi.ai", "supabase.com", "postman.com"]
    assert invalid == []


def test_prepare_domains_invalid_entry_becomes_a_failed_result_not_a_crash() -> None:
    valid, invalid = _prepare_domains(["supabase.com", "localhost"])
    assert valid == ["supabase.com"]
    assert len(invalid) == 1
    assert invalid[0].domain == "localhost"
    assert invalid[0].status == "failed"
    assert invalid[0].errors[0].kind == "invalid_domain"


def test_validate_settings_reports_missing_provider_key() -> None:
    settings = Settings(llm_provider="deepseek", deepseek_api_key=None)
    error = validate_settings(settings)
    assert error is not None
    assert "DEEPSEEK_API_KEY" in error
    assert ".env.example" in error


def test_validate_settings_reports_malformed_base_url() -> None:
    settings = Settings(
        llm_provider="deepseek",
        deepseek_api_key="sk-test",
        deepseek_base_url="not-a-url",
    )
    error = validate_settings(settings)
    assert error is not None
    assert "DEEPSEEK_BASE_URL" in error


def test_validate_settings_passes_with_a_key_and_no_base_url() -> None:
    settings = Settings(llm_provider="deepseek", deepseek_api_key="sk-test")
    assert validate_settings(settings) is None
