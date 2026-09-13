"""Ownership: accounts, their sessions, and anonymous sessions.

`OPEN-17` split in two (implementation plan §8). The identity half — a row that
can own research and isolate it — was built in Phase 0. The semantics half is
`DEC-17`: an anonymous session lives 30 days past its last activity, expires,
and may be claimed by an account while it is still valid. `expires_at` and the
`claimed_*` columns existed from the baseline migration precisely so that
decision would be a behaviour change rather than a reshaping.

`DEC-16` closed `OPEN-11`: accounts are first-party email and password, with
server-side sessions. `users.password_hash` holds an Argon2id hash and
`user_sessions` holds the hash of each session token — never the token.
"""

from __future__ import annotations

import datetime as dt
from uuid import UUID

from sqlalchemy import ForeignKey, Index
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column

from scrapr_core.db.base import Base, CreatedAt, Json, UuidPk

__all__ = ["AnonymousSession", "User", "UserSession"]


class User(Base):
    """An account (`REQ-DATA-001`)."""

    __tablename__ = "users"

    id: Mapped[UuidPk]

    email: Mapped[str] = mapped_column(CITEXT, unique=True)
    """Case-insensitive by storage type, so two accounts cannot differ only in
    capitalisation."""

    auth_ref: Mapped[str | None]
    """Identifier at an external auth provider. `DEC-16` chose first-party
    credentials, so this stays null; it is kept so adding a provider later is
    a column that already exists rather than a migration."""

    password_hash: Mapped[str | None]
    """Argon2id, in the library's encoded form (parameters included), so a
    change of cost parameters is detected per row and rehashed on sign-in.
    Never the password, in any form that can be reversed (`REQ-AUTH-009
    AC-1`)."""

    preferences: Mapped[Json] = mapped_column(default=dict)
    usage_meta: Mapped[Json] = mapped_column(default=dict)

    created_at: Mapped[CreatedAt]
    deleted_at: Mapped[dt.datetime | None]


class UserSession(Base):
    """One signed-in browser (`DEC-16`).

    The token lives only in an `HttpOnly`, `Secure`, `SameSite=Lax` cookie; the
    row keeps its SHA-256. Signing out sets `revoked_at` rather than deleting,
    so a replayed cookie is refused for the reason it actually fails.
    """

    __tablename__ = "user_sessions"
    __table_args__ = (
        Index("ix_user_sessions_user_id", "user_id"),
        # The purge removes dead sessions by expiry.
        Index("ix_user_sessions_expires_at", "expires_at"),
    )

    id: Mapped[UuidPk]

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    token_hash: Mapped[str] = mapped_column(unique=True)

    created_at: Mapped[CreatedAt]
    last_seen_at: Mapped[CreatedAt]

    expires_at: Mapped[dt.datetime]
    """Absolute: 30 days from sign-in, never extended (`DEC-16`)."""

    revoked_at: Mapped[dt.datetime | None]


class AnonymousSession(Base):
    """An unauthenticated owner of research (`REQ-AUTH-001`, `REQ-AUTH-002`).

    The carrier is an opaque, high-entropy token in an `HttpOnly`, `Secure`,
    `SameSite=Lax` cookie. **Only the hash is stored**, so disclosure of the
    database does not hand over the ability to impersonate a session.
    """

    __tablename__ = "anonymous_sessions"
    __table_args__ = (Index("ix_anonymous_sessions_expires_at", "expires_at"),)

    id: Mapped[UuidPk]

    token_hash: Mapped[str] = mapped_column(unique=True)
    """Hash of the session token. The token itself is never persisted."""

    created_at: Mapped[CreatedAt]
    last_seen_at: Mapped[CreatedAt]

    expires_at: Mapped[dt.datetime | None]
    """30 days after `last_seen_at` (`DEC-17`). Null only on rows written before
    Phase 5, which are read as expiring 30 days after their last activity."""

    claimed_by_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    """Set by the single transactional claim operation (`REQ-AUTH-004`)."""

    claimed_at: Mapped[dt.datetime | None]
