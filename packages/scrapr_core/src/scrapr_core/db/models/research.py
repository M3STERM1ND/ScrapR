"""Research sessions and their immutable versions.

**Everything downstream is scoped to `version_id`, not `session_id`** — the most
consequential schema choice in the project (implementation plan §4.1).

`REQ-VER-002 AC-2` requires a prior version's claims, evidence, sources *and
retrieval timestamps* to be unchanged by an update, while `REQ-VER-003 AC-2`
requires new retrieval timestamps on update. A source re-fetched during Update
Research is therefore a genuinely different record, not a mutation of an old one.
Version-scoping makes that immutability structural instead of a discipline
somebody has to remember.

The cost is storage duplicated across versions. Accepted: it is cheap, and it is
the difference between "explainable" and "we think it was probably this".
"""

from __future__ import annotations

import datetime as dt
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from scrapr_core.db.base import Base, CreatedAt, Json, UuidPk, utcnow
from scrapr_core.db.enums import ResearchStatus, VersionStatus, pg_enum

__all__ = ["ResearchSession", "ResearchVersion"]


class ResearchSession(Base):
    """One research objective and everything that has been produced for it."""

    __tablename__ = "research_sessions"
    __table_args__ = (
        # Exactly one owner, enforced by the database. This is what makes an
        # ownerless session unrepresentable, and it is why the anonymous
        # identity half of `OPEN-17` cannot wait for Phase 5 (§8).
        CheckConstraint(
            "num_nonnulls(owner_user_id, anonymous_session_id) = 1",
            name="one_owner",
        ),
        # History, newest first, for one account (`REQ-AUTH-005`).
        Index(
            "ix_research_sessions_owner_user_id_updated_at",
            "owner_user_id",
            text("updated_at DESC"),
        ),
        Index("ix_research_sessions_anonymous_session_id", "anonymous_session_id"),
    )

    id: Mapped[UuidPk]

    owner_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    anonymous_session_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("anonymous_sessions.id")
    )

    objective: Mapped[str]
    """What the user asked for, verbatim (`REQ-INPUT-001`)."""

    instructions: Mapped[str | None]
    context_url: Mapped[str | None]
    context_company: Mapped[str | None]
    context_ticker: Mapped[str | None]

    subject: Mapped[str | None]
    """The agent's interpretation of who or what is being researched
    (`REQ-AGENT-001 AC-2`). Null until the interpret stage runs."""

    subject_interpretation_note: Mapped[str | None]
    """How an ambiguous objective was read (`REQ-AGENT-001 AC-3`)."""

    status: Mapped[ResearchStatus] = mapped_column(
        pg_enum(ResearchStatus, "research_status")
    )

    current_version_id: Mapped[UUID | None] = mapped_column(
        # `use_alter` breaks the cycle with `research_versions.session_id`:
        # without it neither table can be created first.
        ForeignKey(
            "research_versions.id",
            use_alter=True,
            name="fk_research_sessions_current_version_id_research_versions",
        )
    )

    created_at: Mapped[CreatedAt]
    updated_at: Mapped[CreatedAt] = mapped_column(onupdate=utcnow)

    deleted_at: Mapped[dt.datetime | None]
    """Deletion semantics — soft or hard, and what happens to children — are
    `OPEN-24`. The column exists so the answer is not a migration."""


class ResearchVersion(Base):
    """One immutable snapshot of research for a session (`REQ-VER-002`).

    Nothing scoped to a version is updated after the version closes. "Closed" is
    `closed_at` being set; the runner writes it once, in the same transaction
    that flips `status` off `building`.
    """

    __tablename__ = "research_versions"
    __table_args__ = (
        UniqueConstraint("session_id", "version_number"),
        Index("ix_research_versions_session_id", "session_id"),
    )

    id: Mapped[UuidPk]

    session_id: Mapped[UUID] = mapped_column(ForeignKey("research_sessions.id"))

    version_number: Mapped[int]
    """1-based and contiguous per session (`REQ-VER-008`)."""

    status: Mapped[VersionStatus] = mapped_column(
        pg_enum(VersionStatus, "version_status")
    )

    created_at: Mapped[CreatedAt]
    closed_at: Mapped[dt.datetime | None]

    change_summary: Mapped[Json | None]
    """What's Changed against the previous version (`REQ-VER-006`). Null on the
    first version, where there is nothing to compare against."""

    previous_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("research_versions.id")
    )
