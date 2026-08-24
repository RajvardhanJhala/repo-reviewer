"""Central configuration. Everything reads from .env via pydantic-settings.

Usage:
    from config import settings
    settings.gemini_api_key
"""
from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # LLM providers
    gemini_api_key: str = ""
    groq_api_key: str = ""
    openrouter_api_key: str = ""
    cerebras_api_key: str = ""

    # GitHub
    github_token: str = ""
    github_allowed_repos: str = ""  # comma-separated "owner/repo"
    github_webhook_secret: str = ""  # HMAC secret for the webhook receiver (Phase 4)
    dry_run: bool = True

    # App
    log_level: str = "INFO"

    @property
    def allowed_repos(self) -> set[str]:
        return {r.strip() for r in self.github_allowed_repos.split(",") if r.strip()}

    # Router lanes: which model handles which kind of work.
    # "quality" -> long context / careful reasoning (PR review, synthesis)
    # "fast"    -> short iterative agent-loop calls (planner, Q&A tools, classification)
    quality_model: str = Field(default="gemini/gemini-3.6-flash")
    fast_model: str = Field(default="groq/openai/gpt-oss-120b")
    # Ordered fallback chain, all verified live 2026-08-23 with JSON mode.
    # Deliberately spans three providers: a Gemini rate-limit (the Phase 5
    # outage) must not take out the whole chain. Cerebras is absent - its key
    # authenticates but inference returns "Payment required" (no free tier).
    fallback_models: list[str] = Field(
        default=[
            "gemini/gemini-3.5-flash",                            # same provider, separate quota
            "groq/openai/gpt-oss-120b",                           # different provider, fastest
            "openrouter/nvidia/nemotron-3-super-120b-a12b:free",  # third provider
            "groq/openai/gpt-oss-20b",                            # last resort
        ]
    )


settings = Settings()
