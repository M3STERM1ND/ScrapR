"""Ownership: accounts and anonymous sessions.

`OPEN-17` (anonymous session semantics) splits in two, and only one half is a
Phase 5 decision (implementation plan §8):

* **Identity** — a row that can own research and isolate it. Built now; nothing
  in a token hash and a timestamp is a product choice.
* **Semantics** — lifetime, expiry, deletion on expiry, and the window in which
  an anonymous session may be claimed by a new account. Deferred.

`expires_at` and the `claimed_*` columns therefore exist from the baseline
migration but are written by nothing and enforced by nothing until Phase 5, so
resolving `OPEN-17` becomes a behaviour change rather than a migration.

`users.auth_ref` is shaped by `OPEN-11` and is free text for the same reason.
"""

from __future__ import annotations

import datetime as dt
from uuid import UUID

from sqlalchemy import ForeignKey
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column

from scrapr_core.db.base import Base, CreatedAt, Json, UuidPk

__all__ = ["AnonymousSession", "User"]


class User(Base):
    """An account. Rows appear only in Phase 5; the column set is settled now."""

    __tablename__ = "users"

    id: Mapped[UuidPk]

    email: Mapped[str] = mapped_column(CITEXT, unique=True)
    """Case-insensitive by storage type, so two accounts cannot differ only in
    capitalisation."""

    auth_ref: Mapped[str | None]
    """Identifier at the auth provider. Shape pending `OPEN-11`."""

    preferences: Mapped[Json] = mapped_column(default=dict)
    usage_meta: Mapped[Json] = mapped_column(default=dict)

    created_at: Mapped[CreatedAt]
    deleted_at: Mapped[dt.datetime | None]


class AnonymousSession(Base):
    """An unauthenticated owner of research (`REQ-AUTH-001`, `REQ-AUTH-002`).

    The carrier is an opaque, high-entropy token in an `HttpOnly`, `Secure`,
    `SameSite=Lax` cookie. **Only the hash is stored**, so disclosure of the
    database does not hand over the ability to impersonate a session.
    """

    __tablename__ = "anonymous_sessions"

    id: Mapped[UuidPk]

    token_hash: Mapped[str] = mapped_column(unique=True)
    """Hash of the session token. The token itself is never persisted."""

    created_at: Mapped[CreatedAt]
    last_seen_at: Mapped[CreatedAt]

    expires_at: Mapped[dt.datetime | None]
    """Nullable until `OPEN-17` sets a lifetime. Unenforced before Phase 5."""

    claimed_by_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    """Set by the single transactional claim operation (`REQ-AUTH-004`)."""

    claimed_at: Mapped[dt.datetime | None]
