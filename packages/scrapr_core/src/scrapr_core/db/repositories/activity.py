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

import datetime as dt
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import Engine, func, select
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
        """Add an event, taking the next sequence number for the session.

        **Committed on its own, at once, when writing against the engine.** A
        research step runs in one transaction for as long as it takes, and an
        event written inside it is invisible to the reader until the whole step
        commits — minutes of silence on screen while the timeline is, in the
        database, filling up. `REQ-ACT-001 AC-3` wants events *during*
        research and `NFR-PERF-003` caps the silent interval, so events are
        written in a short transaction of their own. The step's transaction
        still sees them: it reads committed rows, and `_next_seq` reads first.

        A session bound to a connection rather than an engine is a test holding
        one transaction open to roll back, and writes into it as before.
        """
        bind = self._session.get_bind()
        if isinstance(bind, Engine):
            with Session(bind=bind, expire_on_commit=False) as own:
                event = self._build(own, session_id, label, status, version_id, tool_category)
                own.add(event)
                own.commit()
            return event

        event = self._build(self._session, session_id, label, status, version_id, tool_category)
        self._session.add(event)
        self._session.flush()
        return event

    @staticmethod
    def _build(
        session: Session,
        session_id: UUID,
        label: str,
        status: ActivityStatus,
        version_id: UUID | None,
        tool_category: str | None,
    ) -> ActivityEvent:
        current = session.execute(
            select(func.max(ActivityEvent.seq)).where(ActivityEvent.session_id == session_id)
        ).scalar()
        return ActivityEvent(
            session_id=session_id,
            version_id=version_id,
            seq=1 if current is None else current + 1,
            label=label,
            status=status,
            tool_category=tool_category,
        )

    def last_event_at(self, session_id: UUID) -> dt.datetime | None:
        """When the session's timeline last moved, for the heartbeat (`TBD-05`)."""
        return self._session.execute(
            select(func.max(ActivityEvent.created_at)).where(ActivityEvent.session_id == session_id)
        ).scalar()

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

