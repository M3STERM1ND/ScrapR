"""Fixed-window rate limiting, counted in Postgres (`REQ-AUTH-009 AC-3`, `REQ-SEC-010`).

**Fixed windows, not a sliding log.** A sliding log is more exact at the window
edge and costs a row per request. A fixed window costs one row per key per
window and lets a burst of at most twice the limit straddle a boundary, which
for "how many times may one address try a password" is a difference nobody can
exploit into anything.

**Counted with one upsert.** `INSERT ... ON CONFLICT DO UPDATE ... RETURNING`
increments and reads in a single statement, so two concurrent requests cannot
both read `limit - 1` and both proceed.

**The subject is hashed before it is a key.** Keys are addresses and emails,
and this table is the kind that gets read in a debugging session. A digest is
enough to count by and useless for anything else.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import math
from dataclasses import dataclass
from typing import final

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from scrapr_core.db.base import utcnow
from scrapr_core.db.models import RateLimitCounter

__all__ = ["RateDecision", "RateLimit", "RateLimiter"]


@final
@dataclass(frozen=True, slots=True)
class RateLimit:
    """A named allowance: `limit` actions per `window_seconds`."""

    scope: str
    limit: int
    window_seconds: int

    def __post_init__(self) -> None:
        if self.limit < 1 or self.window_seconds < 1:
            raise ValueError("a rate limit needs a positive limit and window")


@final
@dataclass(frozen=True, slots=True)
class RateDecision:
    """Whether one action may proceed, and when to try again if not."""

    allowed: bool
    count: int
    retry_after_seconds: int = 0


class RateLimiter:
    """Counts actions against limits in the caller's transaction.

    The caller commits. That is deliberate and it has a sharp edge: a request
    that fails *after* counting and rolls back also un-counts. Callers that
    count failures — sign-in, above all — must commit the count before they
    decide, or a wrong password would never be counted at all.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def hit(
        self, rule: RateLimit, subject: str, *, now: dt.datetime | None = None
    ) -> RateDecision:
        """Count one action by `subject` under `rule`, and say if it is allowed."""
        moment = now or utcnow()
        epoch = moment.timestamp()
        start = math.floor(epoch / rule.window_seconds) * rule.window_seconds
        window_start = dt.datetime.fromtimestamp(start, tz=dt.UTC)

        digest = hashlib.sha256(subject.encode("utf-8")).hexdigest()
        key = f"{rule.scope}:{digest}"

        statement = (
            insert(RateLimitCounter)
            .values(key=key, window_start=window_start, count=1)
            .on_conflict_do_update(
                index_elements=[RateLimitCounter.key, RateLimitCounter.window_start],
                set_={"count": RateLimitCounter.count + 1},
            )
            .returning(RateLimitCounter.count)
        )
        count = int(self._session.execute(statement).scalar_one())

        if count <= rule.limit:
            return RateDecision(allowed=True, count=count)

        retry_after = max(1, math.ceil(start + rule.window_seconds - epoch))
        return RateDecision(allowed=False, count=count, retry_after_seconds=retry_after)

    def purge_before(self, cutoff: dt.datetime) -> int:
        """Drop windows that ended before `cutoff`. Returns rows removed."""
        result = self._session.execute(
            delete(RateLimitCounter).where(RateLimitCounter.window_start < cutoff)
        )
        return int(getattr(result, "rowcount", 0) or 0)
