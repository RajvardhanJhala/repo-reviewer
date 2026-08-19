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
    dry_run: bool = True

    # App
    log_level: str = "INFO"

    @property
    def allowed_repos(self) -> set[str]:
        return {r.strip() for r in self.github_allowed_repos.split(",") if r.strip()}

    # Router lanes: which model handles which kind of work.
    # "quality" -> long context / careful reasoning (PR review, synthesis)
    # "fast"    -> short iterative agent-loop calls (planner, Q&A tools, classification)
    quality_model: str = Field(default="gemini/gemini-2.5-flash")
    fast_model: str = Field(default="groq/llama-3.3-70b-versatile")
    fallback_models: list[str] = Field(
        default=[
            "openrouter/deepseek/deepseek-chat-v3-0324:free",
            "cerebras/llama-3.3-70b",
        ]
    )


settings = Settings()
