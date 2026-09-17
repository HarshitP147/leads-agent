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
