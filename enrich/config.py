"""Frozen runtime settings, loaded from `.env` / the environment."""

from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", frozen=True
    )

    # --- LLM for extraction ---
    llm_provider: Literal["deepseek", "anthropic", "openai"] = "deepseek"
    extraction_model: str | None = None
    navigation_model: str | None = None
    deepseek_api_key: str | None = None
    deepseek_base_url: str | None = None
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None

    # --- Search (bonus: LinkedIn lookup) ---
    tavily_api_key: str | None = None

    # --- Runtime ---
    max_concurrent_domains: int = 3
    page_timeout_s: int = 25
    domain_timeout_s: int = 240
    max_pages_per_domain: int = 6
    max_chars_per_page: int = 12000
    browser_use_enabled: bool = True
    browser_use_max_steps: int = 8
    headless: bool = True


def get_settings() -> Settings:
    return Settings()


PROVIDER_KEY_FIELD: dict[str, str] = {
    "deepseek": "deepseek_api_key",
    "anthropic": "anthropic_api_key",
    "openai": "openai_api_key",
}
PROVIDER_KEY_ENV: dict[str, str] = {
    "deepseek": "DEEPSEEK_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
}


def validate_settings(settings: Settings) -> str | None:
    """One-line, user-facing config error, or None if settings are usable. Checked once
    at CLI startup, before any fetching (fail fast rather than burning a homepage fetch
    per domain only to have every `extract` stage die on the same missing key)."""
    env_var = PROVIDER_KEY_ENV[settings.llm_provider]
    key = getattr(settings, PROVIDER_KEY_FIELD[settings.llm_provider]) or ""
    if not key.strip():
        return (
            f"{env_var} is not set (required for LLM_PROVIDER={settings.llm_provider}). "
            "Copy .env.example to .env and fill it in."
        )
    base_url = (settings.deepseek_base_url or "").strip()
    if base_url and not base_url.startswith(("http://", "https://")):
        return (
            f"DEEPSEEK_BASE_URL is malformed ({base_url!r}) — it must start with "
            "http:// or https://. See .env.example."
        )
    return None
