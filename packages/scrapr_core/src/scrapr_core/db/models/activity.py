"""The activity timeline the user watches while research runs.

`seq` is monotonic per session and is the resume key. That single choice is what
makes the transport decision cheap: Phase 1 polls `?after={seq}`, and Phase 3 can
switch the same endpoint to SSE with no schema change, no route change and no
new client state model (implementation plan §6.3).

`label` is user-facing text and nothing else. `REQ-ACT-003` forbids exposing
internal tool names or errors here, and the place that rule is kept is the
writer: anything diagnostic belongs in `tool_invocations`, which is never
rendered.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from scrapr_core.db.base import Base, CreatedAt, UuidPk
from scrapr_core.db.enums import ActivityStatus, pg_enum

__all__ = ["ActivityEvent"]


class ActivityEvent(Base):
    """One entry in the ordered timeline (`REQ-ACT-001`, `REQ-ACT-004`)."""

    __tablename__ = "activity_events"
    __table_args__ = (
        # Also the incremental-poll index: `WHERE session_id = $1 AND seq > $2`.
        UniqueConstraint("session_id", "seq"),
        Index("ix_activity_events_version_id", "version_id"),
    )

    id: Mapped[UuidPk]

    session_id: Mapped[UUID] = mapped_column(ForeignKey("research_sessions.id"))

    version_id: Mapped[UUID | None] = mapped_column(ForeignKey("research_versions.id"))
    """Null for events that precede a version existing."""

    seq: Mapped[int]

    label: Mapped[str]
    """User-meaningful, e.g. "Reviewing recent developments" (`REQ-ACT-002`)."""

    tool_category: Mapped[str | None]
    """Broad category only — "financial", "news" — never a provider or tool
    name (`REQ-ACT-003`)."""

    status: Mapped[ActivityStatus] = mapped_column(
        pg_enum(ActivityStatus, "activity_status")
    )

    created_at: Mapped[CreatedAt]
