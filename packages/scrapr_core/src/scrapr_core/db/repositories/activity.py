"""Appending to the activity timeline.

`seq` is monotonic per session and is the resume key for both the Phase 1 poll
(`?after={seq}`) and the Phase 3 SSE upgrade (implementation plan §6.3). It is
derived as `max(seq) + 1` rather than kept in a counter column, and
`unique (session_id, seq)` is what makes that safe: two writers racing collide
and one retries, instead of both believing they own the same number.

`label` is user-facing and nothing else (`REQ-ACT-003`). The place that rule is
kept is here, at the writer: anything diagnostic belongs in `tool_invocations`,
which is never rendered.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from scrapr_core.db.enums import ActivityStatus
from scrapr_core.db.models import ActivityEvent

__all__ = ["ActivityRepository"]


class ActivityRepository:
    """Writes and reads the ordered activity timeline for one session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def append(
        self,
        session_id: UUID,
        label: str,
        status: ActivityStatus,
        *,
        version_id: UUID | None = None,
        tool_category: str | None = None,
    ) -> ActivityEvent:
        """Add an event, taking the next sequence number for the session."""
        event = ActivityEvent(
            session_id=session_id,
            version_id=version_id,
            seq=self._next_seq(session_id),
            label=label,
            status=status,
            tool_category=tool_category,
        )
        self._session.add(event)
        self._session.flush()
        return event

    def since(self, session_id: UUID, after: int = 0) -> Sequence[ActivityEvent]:
        """Events newer than a sequence number, in order.

        The whole transport story rests on this shape: polling and streaming ask
        the same question, so switching between them changes no schema, no route
        and no client state model.
        """
        statement = (
            select(ActivityEvent)
            .where(ActivityEvent.session_id == session_id, ActivityEvent.seq > after)
            .order_by(ActivityEvent.seq)
        )
        return self._session.execute(statement).scalars().all()

    def _next_seq(self, session_id: UUID) -> int:
        current = self._session.execute(
            select(func.max(ActivityEvent.seq)).where(
                ActivityEvent.session_id == session_id
            )
        ).scalar()
        return 1 if current is None else current + 1
