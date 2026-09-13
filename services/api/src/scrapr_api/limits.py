"""Abuse controls on the expensive routes (`REQ-SEC-010`, `DEC-23`, `DEC-24`).

Two checks, both before any work is queued:

* **`enforce_action`** counts the request against every limit its subjects
  have — anonymous session, account, client address — and refuses with a `429`
  if any is spent. Counts are committed first, so a refused request stays
  counted.
* **`admit_research`** refuses new research with a `503` when the queue of
  pending runs is past `TBD-13`. Shedding at the door is honest; accepting a
  run nobody will reach for an hour is not.

Both answer in the one error envelope, saying what to do next and nothing about
limits, counters or infrastructure (`REQ-SEC-010 AC-5`).

Account usage is recorded as the request passes (`REQ-OBS-008`): what the
account has done, and when it was last active, on the user row where abuse
review can read it.
"""

from __future__ import annotations

from fastapi import Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from scrapr_api.deps import client_address
from scrapr_api.errors import ApiError
from scrapr_core.config import get_settings
from scrapr_core.db.base import utcnow
from scrapr_core.db.enums import RunStatus
from scrapr_core.db.models import ResearchRun, User
from scrapr_core.db.repositories.rate_limits import RateLimit, RateLimiter
from scrapr_core.domain.ownership import OwnerContext
from scrapr_core.security.limits import Action

__all__ = ["admit_research", "enforce_action", "record_account_usage"]


def enforce_action(
    session: Session, request: Request, owner: OwnerContext, action: Action
) -> None:
    """Count one `action` against every subject it applies to, or refuse.

    The owner, not the cookies: a first request mints its anonymous session in
    the same request, and that new session is the subject it is counted under.
    """
    limiter = RateLimiter(session)
    subjects: list[tuple[tuple[RateLimit, ...], str]] = []
    if owner.user_id is not None:
        subjects.append((action.account, f"user:{owner.user_id}"))
    else:
        subjects.append((action.anonymous, f"anonymous:{owner.anonymous_session_id}"))
    subjects.append((action.address, f"address:{client_address(request)}"))

    worst_retry = 0
    for rules, subject in subjects:
        for rule in rules:
            decision = limiter.hit(rule, subject)
            if not decision.allowed:
                worst_retry = max(worst_retry, decision.retry_after_seconds)
    # Committed before deciding: the request session rolls back on the error
    # raised below, and a refused request must stay counted.
    session.commit()

    if worst_retry:
        raise ApiError(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "rate_limited",
            _MESSAGES.get(action.name, "Too many requests. Wait a while and try again."),
            headers={"Retry-After": str(worst_retry)},
        )


_MESSAGES = {
    "research": (
        "You have started a lot of research recently. Wait a while before starting more."
    ),
    "question": "Too many questions in a short time. Wait a few minutes and ask again.",
    "upload": "Too many files attached recently. Wait a while and try again.",
    "export": "Too many exports requested recently. Wait a while and try again.",
}


def admit_research(session: Session) -> None:
    """Refuse new research when the queue is past `TBD-13` (`NFR-SCALE-002`)."""
    pending = session.execute(
        select(func.count(ResearchRun.id)).where(ResearchRun.status == RunStatus.PENDING)
    ).scalar_one()
    if pending >= get_settings().queue_shed_threshold:
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "busy",
            "ScrapR is handling a lot of research right now. Try again in a few minutes.",
            headers={"Retry-After": "120"},
        )


def record_account_usage(user: User | None, key: str) -> None:
    """Add one to an account's usage counter (`REQ-OBS-008`).

    A new dict is assigned rather than the old one mutated: the column is
    JSONB, and SQLAlchemy only notices a change it can see.
    """
    if user is None:
        return
    usage = dict(user.usage_meta or {})
    usage[key] = int(usage.get(key, 0)) + 1
    usage["last_active_at"] = utcnow().isoformat()
    user.usage_meta = usage
