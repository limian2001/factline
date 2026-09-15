"""Runtime configuration. Everything external is declared here, nothing is hardcoded."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings loaded from environment / .env.

    sec_user_agent is mandatory and validated: the SEC returns 403 for requests
    without a contact header, and that is the single most common first-run failure.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- SEC EDGAR -------------------------------------------------------
    sec_user_agent: str = Field(
        ...,
        description="SEC requires 'Name email@example.com'. Requests without it get 403.",
    )
    sec_rate_limit_rps: float = Field(
        8.0, description="SEC caps at 10 req/s per IP; we target 8 for headroom."
    )

    # --- Prices ----------------------------------------------------------
    tiingo_api_key: str | None = None

    # --- LLM -------------------------------------------------------------
    anthropic_api_key: str | None = None
    llm_model: str = "claude-sonnet-4-5"

    # --- Storage ---------------------------------------------------------
    data_dir: Path = Path("data")
    cache_ttl_hours: int = Field(
        24, description="companyfacts changes at most daily; 24h cache is safe."
    )

    @field_validator("sec_user_agent")
    @classmethod
    def _must_look_like_contact(cls, v: str) -> str:
        if "@" not in v or len(v.split()) < 2:
            raise ValueError(
                "SEC_USER_AGENT must be 'Your Name your@email.com' — "
                "the SEC blocks requests that do not identify a contact."
            )
        return v

    @property
    def raw_dir(self) -> Path:
        """Immutable landing zone. Written once, never edited."""
        return self.data_dir / "raw"

    @property
    def clean_dir(self) -> Path:
        """Derived, always safe to delete and rebuild from raw/."""
        return self.data_dir / "clean"

    @property
    def quarantine_dir(self) -> Path:
        """Records that failed validation. Never silently dropped."""
        return self.data_dir / "quarantine"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / ".httpcache"


def load_settings(**overrides: object) -> Settings:
    return Settings(**overrides)  # type: ignore[arg-type]
