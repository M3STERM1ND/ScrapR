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

Lifetime, expiry and the claim eligibility window are `OPEN-17` and deliberately
absent: `expires_at` stays null and nothing reads it until Phase 5.
"""

from __future__ import annotations

import hashlib
import secrets
from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from scrapr_core.db.base import utcnow
from scrapr_core.db.models import AnonymousSession
from scrapr_core.domain.ownership import OwnerContext

__all__ = ["AnonymousSessionRepository", "IssuedSession", "hash_session_token"]

TOKEN_BYTES = 32
"""256 bits. Unguessable by any margin that matters, and short enough for a
cookie."""


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

    def issue(self) -> IssuedSession:
        """Mint a new anonymous session and return its token exactly once."""
        token = secrets.token_urlsafe(TOKEN_BYTES)
        row = AnonymousSession(token_hash=hash_session_token(token))
        self._session.add(row)
        self._session.flush()
        return IssuedSession(session=row, token=token)

    def resolve(self, token: str) -> AnonymousSession | None:
        """Find the session a token identifies, or `None`.

        Returns `None` rather than raising: an absent or stale cookie is the
        ordinary case for a first-time visitor, not an error.
        """
        return self._session.execute(
            select(AnonymousSession).where(
                AnonymousSession.token_hash == hash_session_token(token)
            )
        ).scalar_one_or_none()

    def touch(self, session: AnonymousSession) -> None:
        """Record activity. What, if anything, this affects is `OPEN-17`."""
        session.last_seen_at = utcnow()
        self._session.flush()

    def owner_context(self, token: str) -> OwnerContext | None:
        """Resolve a token straight to the value repositories are scoped by."""
        row = self.resolve(token)
        return None if row is None else OwnerContext.for_anonymous(row.id)
