"""Runtime configuration, read from the environment.

One settings object for the whole system. The API, the worker and Alembic all
read the same values from the same place, so a developer machine and CI cannot
disagree about which database is being migrated.

Provider settings arrive as their questions close. `DEC-06` closed `OPEN-04`,
`DEC-07` closed `OPEN-05..09`, and `DEC-12` and `DEC-13` closed `OPEN-10` and
`OPEN-19`, so the keys for those six providers plus storage and the upload
limits are all here.

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

    allow_fixtures: bool = Field(
        default=False,
        alias="SCRAPR_ALLOW_FIXTURES",
        description=(
            "Opt in to canned fixture tools for categories with no provider. "
            "Honoured only outside production and only when no real AI provider "
            "is configured: a run a real model reads is real research, and a "
            "fixture in it is fabricated evidence."
        ),
    )

    web_origins: str = Field(
        default="http://localhost:3000",
        alias="WEB_ORIGINS",
        description=(
            "Comma-separated origins allowed to call the API with credentials. "
            "Explicit origins only: the session cookie means a wildcard would "
            "let any site on the internet read a user's research."
        ),
    )

    trust_proxy_headers: bool = Field(
        default=False,
        alias="TRUST_PROXY_HEADERS",
        description=(
            "Read the client address from X-Forwarded-For. True only behind a "
            "proxy that sets it (Vercel does); otherwise any client could name "
            "a fresh address per request and escape every rate limit."
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

    retrieval_cache_ttl_seconds: float = Field(
        default=900.0,
        alias="RETRIEVAL_CACHE_TTL_SECONDS",
        gt=0,
        description=(
            "How long an identical retrieval is reused within one run "
            "(`REQ-TOOL-013 AC-4`, `DEC-19`). The cache never outlives its run, "
            "so Update Research always re-fetches."
        ),
    )

    # ------------------------------------------------------------------
    # Operating limits — `DEC-23`, `DEC-24`, `DEC-25` (Phase 8)
    # ------------------------------------------------------------------

    run_cost_ceiling_micros: int = Field(
        default=2_000_000, alias="RUN_COST_CEILING_MICROS", gt=0,
        description="TBD-10: the most one research run may spend, in millionths of a dollar.",
    )
    update_cost_ceiling_micros: int = Field(
        default=1_500_000, alias="UPDATE_COST_CEILING_MICROS", gt=0,
        description="TBD-11: the most one Update Research run may spend.",
    )
    queue_shed_threshold: int = Field(
        default=100, alias="QUEUE_SHED_THRESHOLD", gt=0,
        description="TBD-13: pending runs beyond which new research is refused with 503.",
    )
    activity_heartbeat_seconds: float = Field(
        default=20.0, alias="ACTIVITY_HEARTBEAT_SECONDS", gt=0,
        description=(
            "How long research may go without a timeline event before the step "
            "repeats its progress line. Below TBD-05's 30 seconds on purpose."
        ),
    )

    # ------------------------------------------------------------------
    # Object storage — `DEC-12`, closing `OPEN-10`
    # ------------------------------------------------------------------

    storage_endpoint_url: str = Field(
        default="http://localhost:9000",
        alias="STORAGE_ENDPOINT_URL",
        description=(
            "S3 API endpoint. MinIO locally, empty for real AWS S3, the R2 "
            "account endpoint in production. The client is the same either "
            "way, which is the whole point of `DEC-12`."
        ),
    )
    storage_bucket: str = Field(default="scrapr-uploads", alias="STORAGE_BUCKET")
    storage_access_key: str = Field(default="scrapr", alias="STORAGE_ACCESS_KEY")
    storage_secret_key: str = Field(
        default="scrapr_local_dev_only", alias="STORAGE_SECRET_KEY"
    )
    storage_region: str = Field(default="auto", alias="STORAGE_REGION")
    """`auto` is what R2 expects; MinIO ignores it. A real AWS bucket needs its
    actual region."""

    # ------------------------------------------------------------------
    # Upload limits — `DEC-13`, closing `OPEN-19`
    # ------------------------------------------------------------------

    max_upload_bytes: int = Field(default=25 * 1024 * 1024, alias="MAX_UPLOAD_BYTES")
    max_uploads_per_session: int = Field(default=10, alias="MAX_UPLOADS_PER_SESSION")
    max_session_upload_bytes: int = Field(
        default=100 * 1024 * 1024, alias="MAX_SESSION_UPLOAD_BYTES"
    )
    """Deliberately less than `max_upload_bytes` times the count: the per-file
    limit is what one document plausibly is, the session limit is what will be
    processed for one run (`DEC-13`)."""

    def production_problems(self) -> list[str]:
        """What in this configuration would break a production guarantee.

        `REQ-SEC-003`: storage retrieval and the browser's origin are over TLS.
        `REQ-SEC-004` asks for encryption at rest, which is the database and
        storage providers' property rather than something a URL can prove — but
        a database connection that does not insist on TLS carries the data in
        the clear on its way there, and that *is* checkable.

        Empty outside production. Returned as a list, not raised, so a caller
        can report every problem at once rather than one per deploy.
        """
        if self.scrapr_env != "production":
            return []

        problems: list[str] = []
        if self.storage_endpoint_url and not self.storage_endpoint_url.startswith("https://"):
            problems.append("STORAGE_ENDPOINT_URL must use https:// in production")
        if any(not origin.startswith("https://") for origin in self.allowed_origins):
            problems.append("every WEB_ORIGINS entry must use https:// in production")
        if not any(
            f"sslmode={mode}" in self.database_url for mode in ("require", "verify-ca", "verify-full")
        ):
            problems.append("DATABASE_URL must set sslmode=require (or stricter) in production")
        if "local_dev_only" in self.storage_secret_key or "local_dev_only" in self.database_url:
            problems.append("the docker-compose development credentials must not reach production")
        if self.allow_fixtures:
            problems.append("SCRAPR_ALLOW_FIXTURES must not be set in production")
        return problems

    @property
    def fixtures_permitted(self) -> bool:
        """Whether fixture tools may stand in for unserved categories.

        Three conditions, all required. The opt-in is explicit because the
        NVIDIA run showed what the implicit default did: a real model read a
        canned "$1.2bn revenue" item and reported it as a Reuters filing. The
        model check is what makes that impossible even with the flag set by
        mistake — fixtures exist to exercise plumbing without spending money,
        and a run with a real model is spending money on research.
        """
        return (
            self.allow_fixtures
            and self.scrapr_env != "production"
            and not self.has_ai_provider
        )

    def api_privilege_problems(self) -> list[str]:
        """Credentials the API process holds but never uses (`REQ-SEC-006`).

        Only research retrieves, so only the worker needs data-provider keys.
        In production an API holding one refuses to start: a credential a
        process does not use is only a credential it can leak.
        """
        if self.scrapr_env != "production":
            return []
        held = [
            name
            for name, value in (
                ("TAVILY_API_KEY", self.tavily_api_key),
                ("FMP_API_KEY", self.fmp_api_key),
                ("ADZUNA_APP_ID", self.adzuna_app_id),
                ("ADZUNA_APP_KEY", self.adzuna_app_key),
                ("SEC_EDGAR_USER_AGENT", self.sec_edgar_user_agent),
            )
            if value.strip()
        ]
        return [f"the API must not hold {name}; it belongs to the worker only" for name in held]

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
