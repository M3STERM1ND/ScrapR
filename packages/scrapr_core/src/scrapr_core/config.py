"""Runtime configuration, read from the environment.

One settings object for the whole system. The API, the worker and Alembic all
read the same values from the same place, so a developer machine and CI cannot
disagree about which database is being migrated.

Provider settings are deliberately absent: `OPEN-04..10` are unresolved and
Phase 0 runs on fixtures and a fake LLM, so there is nothing real to configure
yet. They arrive as their questions close.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["Settings", "get_settings"]


class Settings(BaseSettings):
    """Environment-backed configuration. See `.env.example` for the local set."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(
        default="postgresql+psycopg://scrapr:scrapr_local_dev_only@localhost:5432/scrapr",
        alias="DATABASE_URL",
        description="SQLAlchemy URL. The psycopg (v3) driver, not psycopg2.",
    )

    scrapr_env: Literal["local", "preview", "production"] = Field(
        default="local", alias="SCRAPR_ENV"
    )

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    web_origins: str = Field(
        default="http://localhost:3000",
        alias="WEB_ORIGINS",
        description=(
            "Comma-separated origins allowed to call the API with credentials. "
            "Explicit origins only: the session cookie means a wildcard would "
            "let any site on the internet read a user's research."
        ),
    )

    @property
    def allowed_origins(self) -> list[str]:
        """`web_origins` split and cleaned."""
        return [origin.strip() for origin in self.web_origins.split(",") if origin.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, read once.

    Cached because settings are immutable for the life of a process; tests that
    need different values call `get_settings.cache_clear()`.
    """
    return Settings()
