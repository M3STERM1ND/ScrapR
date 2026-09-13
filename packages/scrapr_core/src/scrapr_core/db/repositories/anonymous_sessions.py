"""Issuing and resolving anonymous session tokens.

The carrier is an opaque, high-entropy token in an `HttpOnly`, `Secure`,
`SameSite=Lax` cookie (implementation plan §8). **Only its hash is stored**, so
disclosure of the database does not hand over the ability to impersonate a
session.

**SHA-256, not a password KDF.** A slow hash exists to make guessing a
*low-entropy* human-chosen secret expensive. This token is 256 bits from
`secrets`, so there is nothing to guess and the only property needed is that the
stored value cannot be reversed. Argon2 here would buy nothing and cost a
verification per request.

**Lifetime is `DEC-17`.** A session lives 30 days past its last activity. An
expired session, or one an account has claimed, resolves to nobody: its research
becomes unreachable at that moment rather than when the purge gets to it.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import secrets
from typing import Final, NamedTuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from scrapr_core.db.base import utcnow
from scrapr_core.db.models import AnonymousSession
from scrapr_core.domain.ownership import OwnerContext

__all__ = [
    "ANONYMOUS_LIFETIME",
    "TOUCH_INTERVAL",
    "AnonymousSessionRepository",
    "IssuedSession",
    "hash_session_token",
    "is_valid",
]

TOKEN_BYTES = 32
"""256 bits. Unguessable by any margin that matters, and short enough for a
cookie."""

ANONYMOUS_LIFETIME: Final = dt.timedelta(days=30)
"""`DEC-17`: how long an anonymous session survives without activity."""

TOUCH_INTERVAL: Final = dt.timedelta(hours=1)
"""How stale `last_seen_at` may be before a request refreshes it.

Refreshing on every request would turn every read into a write, and the
lifetime is measured in days: an hour of imprecision in a thirty-day window is
not something anyone can observe.
"""


def hash_session_token(token: str) -> str:
    """Hash a session token for storage and lookup."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class IssuedSession(NamedTuple):
    """A newly created session and the one time its token is ever available."""

    session: AnonymousSession
    token: str
    """Returned to the caller to set as a cookie. Never persisted, never
    logged, and unrecoverable afterwards."""


class AnonymousSessionRepository:
    """Creates anonymous owners and resolves a token back to one."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def issue(self, *, now: dt.datetime | None = None) -> IssuedSession:
        """Mint a new anonymous session and return its token exactly once."""
        moment = now or utcnow()
        token = secrets.token_urlsafe(TOKEN_BYTES)
        row = AnonymousSession(
            token_hash=hash_session_token(token),
            created_at=moment,
            last_seen_at=moment,
            expires_at=moment + ANONYMOUS_LIFETIME,
        )
        self._session.add(row)
        self._session.flush()
        return IssuedSession(session=row, token=token)

    def resolve(self, token: str) -> AnonymousSession | None:
        """Find the row a token identifies, valid or not, or `None`.

        The raw lookup. Anything deciding *ownership* uses `resolve_valid`,
        because a row existing is not the same as it still owning anything.
        """
        return self._session.execute(
            select(AnonymousSession).where(
                AnonymousSession.token_hash == hash_session_token(token)
            )
        ).scalar_one_or_none()

    def resolve_valid(
        self, token: str, *, now: dt.datetime | None = None
    ) -> AnonymousSession | None:
        """The session a token identifies, if it may still own research.

        Returns `None` rather than raising: an absent, expired or claimed
        cookie is the ordinary case for a returning visitor, not an error.
        """
        row = self.resolve(token)
        if row is None or not is_valid(row, now=now):
            return None
        return row

    def touch(self, session: AnonymousSession, *, now: dt.datetime | None = None) -> bool:
        """Record activity and slide the expiry (`DEC-17`).

        Returns whether anything was written, which is also whether the cookie
        needs re-issuing with a fresh `Max-Age`. Rate-limited by
        `TOUCH_INTERVAL` so a burst of reads is not a burst of writes.
        """
        moment = now or utcnow()
        if moment - session.last_seen_at < TOUCH_INTERVAL:
            return False
        session.last_seen_at = moment
        session.expires_at = moment + ANONYMOUS_LIFETIME
        self._session.flush()
        return True

    def owner_context(
        self, token: str, *, now: dt.datetime | None = None
    ) -> OwnerContext | None:
        """Resolve a token straight to the value repositories are scoped by."""
        row = self.resolve_valid(token, now=now)
        return None if row is None else OwnerContext.for_anonymous(row.id)


def is_valid(row: AnonymousSession, *, now: dt.datetime | None = None) -> bool:
    """Whether an anonymous session still owns its research.

    A row written before `DEC-17` has no `expires_at`; it is read as expiring a
    lifetime after its last activity, which is exactly what migration `0005`
    backfilled, so the two cannot disagree.
    """
    if row.claimed_at is not None:
        return False
    expires = row.expires_at or (row.last_seen_at + ANONYMOUS_LIFETIME)
    return (now or utcnow()) < expires
