"""Persisting the follow-up conversation (`REQ-CONV-007`).

**Messages belong to the session, not to a process.** `REQ-CONV-001 AC-3` and
`REQ-CONV-007 AC-2` both require the conversation survive a reload and come
back when the research is reopened, so every turn is a row before it is
rendered and nothing is held in memory between requests.

**A version-scoped answer against a session-scoped conversation.** The message
records which version it was answered from, because a follow-up asked before an
Update Research was answered from different evidence than the same question
asked after — and `REQ-VER-002` makes the earlier version immutable, so the
answer stays checkable against what it actually read.

Sequence numbers are allocated inside the transaction that writes the message.
The unique constraint on `(session_id, seq)` is what makes two concurrent
questions fail loudly rather than silently interleave.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from scrapr_core.db.enums import MessageRole
from scrapr_core.db.models import ConversationMessage, MessageEvidence
from scrapr_core.domain.json import JsonMapping

__all__ = ["ConversationRepository"]


class ConversationRepository:
    """Reads and writes conversation turns."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def append(
        self,
        session_id: UUID,
        version_id: UUID,
        role: MessageRole,
        content: str,
        *,
        evidence_ids: Sequence[UUID] = (),
        context_ref: JsonMapping | None = None,
    ) -> ConversationMessage:
        """Write one turn, with the evidence it cited.

        `REQ-CONV-008 AC-1`: factual statements in answers link to evidence,
        and the linkage is a row rather than a marker in the text — so a
        citation survives the prose being re-rendered, and so the same
        inspection surface a claim uses can serve an answer.
        """
        message = ConversationMessage(
            session_id=session_id,
            version_id=version_id,
            seq=self._next_seq(session_id),
            role=role,
            content=content,
            context_ref=dict(context_ref) if context_ref else None,
        )
        self._session.add(message)
        self._session.flush()

        for evidence_id in dict.fromkeys(evidence_ids):
            self._session.add(
                MessageEvidence(message_id=message.id, evidence_id=evidence_id)
            )
        self._session.flush()
        return message

    def for_session(self, session_id: UUID) -> Sequence[ConversationMessage]:
        """The whole conversation, oldest first (`REQ-CONV-007 AC-2`)."""
        return (
            self._session.execute(
                select(ConversationMessage)
                .where(ConversationMessage.session_id == session_id)
                .order_by(ConversationMessage.seq)
            )
            .scalars()
            .all()
        )

    def recent_turns(self, session_id: UUID, limit: int = 6) -> list[tuple[str, str]]:
        """The last few turns, oldest first, for context.

        Bounded because the whole transcript would grow the prompt without
        bound and push the evidence — the part that actually grounds the answer
        — further from the model's attention.
        """
        rows = (
            self._session.execute(
                select(ConversationMessage)
                .where(ConversationMessage.session_id == session_id)
                .order_by(ConversationMessage.seq.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return [(row.role.value, row.content) for row in reversed(rows)]

    def evidence_ids_for(self, message_id: UUID) -> Sequence[UUID]:
        return (
            self._session.execute(
                select(MessageEvidence.evidence_id).where(
                    MessageEvidence.message_id == message_id
                )
            )
            .scalars()
            .all()
        )

    def _next_seq(self, session_id: UUID) -> int:
        """One past the highest, or one.

        Read inside the writing transaction. The unique constraint on
        `(session_id, seq)` is the real guard: two questions asked at once
        collide loudly instead of quietly taking the same number.
        """
        highest = self._session.execute(
            select(func.max(ConversationMessage.seq)).where(
                ConversationMessage.session_id == session_id
            )
        ).scalar_one_or_none()
        return (highest or 0) + 1
