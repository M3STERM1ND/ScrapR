"""Accounts and the sessions that sign them in (`DEC-16`, `REQ-AUTH-003`).

Three rules this module keeps, each because getting it wrong is silent:

* **The token is never stored.** A session row holds the SHA-256 of a 256-bit
  token, the same reasoning as anonymous sessions: nothing to guess, so the only
  property needed is that the stored value cannot be replayed.
* **Sign-in failure has one shape.** Unknown email and wrong password both
  return `None` after a full Argon2 verification, so neither the response nor
  its timing says which emails have accounts.
* **Expiry is absolute.** A session is good for 30 days from sign-in and no
  request extends it (`DEC-16`). Sliding expiry is right for anonymous research,
  where the session is the only key; for an account, signing in again costs one
  form and a stolen cookie should not live forever.
"""

from __future__ import annotations

import datetime as dt
import re
import secrets
from dataclasses import dataclass
from typing import Final, final
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from scrapr_core.db.base import utcnow
from scrapr_core.db.models import User, UserSession
from scrapr_core.db.repositories.anonymous_sessions import (
    TOKEN_BYTES,
    TOUCH_INTERVAL,
    hash_session_token,
)
from scrapr_core.security.passwords import hash_password, verify_password

__all__ = [
    "ACCOUNT_SESSION_LIFETIME",
    "AccountRepository",
    "EmailTakenError",
    "IssuedAccountSession",
    "normalize_email",
]

ACCOUNT_SESSION_LIFETIME: Final = dt.timedelta(days=30)
"""`DEC-16`: absolute, from sign-in."""

EMAIL_MAX_LENGTH: Final = 254
"""RFC 5321's path limit, less the angle brackets."""

_EMAIL_SHAPE: Final = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
"""Deliberately loose. The only reliable email validator is sending an email,
which V1 cannot do (`DEC-16`), and a strict pattern mostly rejects real
addresses with plus signs and new top-level domains."""


class EmailTakenError(ValueError):
    """An account already exists for that email."""


@final
@dataclass(frozen=True, slots=True)
class IssuedAccountSession:
    """A new sign-in and the one time its token is available."""

    session: UserSession
    token: str


def normalize_email(raw: str) -> str | None:
    """The canonical form of an email, or `None` if it cannot be one.

    Lower-cased as well as stored in `citext`: the column already compares
    case-insensitively, and lower-casing on the way in means the address shown
    back to the user is one consistent spelling rather than whichever they
    typed first.
    """
    email = raw.strip().lower()
    if not email or len(email) > EMAIL_MAX_LENGTH or not _EMAIL_SHAPE.match(email):
        return None
    return email


class AccountRepository:
    """Creates accounts, checks credentials, and issues and resolves sessions."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Accounts
    # ------------------------------------------------------------------

    def create(self, email: str, password: str) -> User:
        """Register an account. Raises `EmailTakenError` on a duplicate.

        The uniqueness check is the database's, not a prior `SELECT`: two
        sign-ups racing on one address both pass a read and only one can pass
        a unique index. A savepoint keeps the collision from poisoning the
        caller's transaction.
        """
        normalized = normalize_email(email)
        if normalized is None:
            raise ValueError("That does not look like an email address.")

        user = User(email=normalized, password_hash=hash_password(password))
        try:
            with self._session.begin_nested():
                self._session.add(user)
                self._session.flush()
        except IntegrityError as exc:
            raise EmailTakenError(normalized) from exc
        return user

    def get(self, user_id: UUID) -> User | None:
        user = self._session.get(User, user_id)
        if user is None or user.deleted_at is not None:
            return None
        return user

    def authenticate(self, email: str, password: str) -> User | None:
        """The account these credentials open, or `None`.

        Always pays for one verification, found or not. A password hashed under
        older parameters is rehashed now, while the plaintext is in hand — the
        only moment that is possible.
        """
        normalized = normalize_email(email)
        user = (
            self._session.execute(
                select(User).where(User.email == normalized, User.deleted_at.is_(None))
            ).scalar_one_or_none()
            if normalized is not None
            else None
        )

        check = verify_password(user.password_hash if user else None, password)
        if user is None or not check.valid:
            return None

        if check.needs_rehash:
            user.password_hash = hash_password(password)
            self._session.flush()
        return user

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    def issue_session(
        self, user: User, *, now: dt.datetime | None = None
    ) -> IssuedAccountSession:
        """Sign a browser in. A new token every time, never a reused one."""
        moment = now or utcnow()
        token = secrets.token_urlsafe(TOKEN_BYTES)
        row = UserSession(
            user_id=user.id,
            token_hash=hash_session_token(token),
            created_at=moment,
            last_seen_at=moment,
            expires_at=moment + ACCOUNT_SESSION_LIFETIME,
        )
        self._session.add(row)
        self._session.flush()
        return IssuedAccountSession(session=row, token=token)

    def resolve_session(
        self, token: str, *, now: dt.datetime | None = None
    ) -> User | None:
        """The account a session token signs in, if the session is still good."""
        moment = now or utcnow()
        row = self._session.execute(
            select(UserSession).where(UserSession.token_hash == hash_session_token(token))
        ).scalar_one_or_none()

        if row is None or row.revoked_at is not None or row.expires_at <= moment:
            return None

        user = self.get(row.user_id)
        if user is None:
            return None

        if moment - row.last_seen_at >= TOUCH_INTERVAL:
            # Diagnostic only: expiry is absolute and this never extends it.
            row.last_seen_at = moment
            self._session.flush()
        return user

    def revoke_session(self, token: str, *, now: dt.datetime | None = None) -> None:
        """Sign a browser out. Idempotent: an unknown token is already out."""
        row = self._session.execute(
            select(UserSession).where(UserSession.token_hash == hash_session_token(token))
        ).scalar_one_or_none()
        if row is not None and row.revoked_at is None:
            row.revoked_at = now or utcnow()
            self._session.flush()
