"""Runtime configuration, read from the environment.

One settings object for the whole system. The API, the worker and Alembic all
read the same values from the same place, so a developer machine and CI cannot
disagree about which database is being migrated.

Provider settings arrive as their questions close. `DEC-06` closed `OPEN-04`
and `DEC-07` closed `OPEN-05..09`, so the keys for those six are here.
`OPEN-10` (object storage) is still open and has nothing to configure.

**Every provider key is optional, and that is deliberate.** A missing key means
that category does not register, and the run reports it as a gap the same way
it reports any unserved category — degraded and named, never silent. The one
exception is the AI provider: a worker with no model cannot do research at all,
so `services/worker` refuses to start rather than pretending.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from scrapr_core.llm.anthropic_provider import (
    MODEL_CHEAP,
    MODEL_DEEP,
    MODEL_STANDARD,
)

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

    # ------------------------------------------------------------------
    # AI provider — `DEC-06`, closing `OPEN-04`
    # ------------------------------------------------------------------

    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")

    model_cheap: str = Field(default=MODEL_CHEAP, alias="SCRAPR_MODEL_CHEAP")
    model_standard: str = Field(default=MODEL_STANDARD, alias="SCRAPR_MODEL_STANDARD")
    model_deep: str = Field(default=MODEL_DEEP, alias="SCRAPR_MODEL_DEEP")
    """The `DEC-06 §1` tier table, as configuration. Overriding a row changes
    which model serves a tier without touching a stage."""

    # ------------------------------------------------------------------
    # Data providers — `DEC-07`, closing `OPEN-05..09`
    # ------------------------------------------------------------------

    tavily_api_key: str = Field(default="", alias="TAVILY_API_KEY")
    """Serves two categories, web search and news (`DEC-07 §3.1`). One absent
    key therefore removes both, which `DEC-07 §8` records as the cost of that
    choice."""

    fmp_api_key: str = Field(default="", alias="FMP_API_KEY")

    adzuna_app_id: str = Field(default="", alias="ADZUNA_APP_ID")
    adzuna_app_key: str = Field(default="", alias="ADZUNA_APP_KEY")

    sec_edgar_user_agent: str = Field(
        default="",
        alias="SEC_EDGAR_USER_AGENT",
        description=(
            "EDGAR requires a descriptive User-Agent with contact details and "
            "rejects requests without one. It needs no key, so this string is "
            "what gates the filings category instead."
        ),
    )

    @property
    def has_ai_provider(self) -> bool:
        """Whether a real model can be reached.

        Read by the worker, which must refuse to start without one rather than
        fall back to the deterministic stand-in: serving that to users would
        mean presenting research nobody did.
        """
        return bool(self.anthropic_api_key.strip())

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
