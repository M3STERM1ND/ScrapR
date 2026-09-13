"""Fixed-window rate limit counters, kept in Postgres.

`REQ-AUTH-009 AC-3` and `REQ-SEC-010` need limits that hold across processes.
The API is a set of serverless instances (`DEC-03`), so a counter in process
memory would give every instance its own allowance and limit nothing. Postgres
is already the one thing every instance shares, and an upsert on a composite
key is a single round trip.

`key` is a digest, never a raw address or email: this table is read casually
while debugging, and a list of who tried to sign in as whom does not belong in
it.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Index
from sqlalchemy.orm import Mapped, mapped_column

from scrapr_core.db.base import Base

__all__ = ["RateLimitCounter"]


class RateLimitCounter(Base):
    """How many times one key acted within one window."""

    __tablename__ = "rate_limit_counters"
    __table_args__ = (
        # The purge drops windows that can no longer be read.
        Index("ix_rate_limit_counters_window_start", "window_start"),
    )

    key: Mapped[str] = mapped_column(primary_key=True)
    """`<scope>:<sha256>`, e.g. `signin-email:ab12...`."""

    window_start: Mapped[dt.datetime] = mapped_column(primary_key=True)

    count: Mapped[int] = mapped_column(default=0)
