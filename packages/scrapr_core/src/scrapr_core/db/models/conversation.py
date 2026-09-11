"""Follow-up conversation, grounded in the version it is about.

Messages carry both `session_id` and `version_id`. The session is what the user
sees as one continuous conversation; the version is what the answer was grounded
in, and `REQ-CONV-004` requires an answer be traceable to the evidence that
existed when it was given. Keeping only the session would silently re-point old
answers at newer evidence.

`seq` is unique per session and monotonic, the same shape `activity_events`
uses, so history pages and any future streaming transport both resume from a
number rather than a timestamp.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from scrapr_core.db.base import Base, CreatedAt, Json, UuidPk
from scrapr_core.db.enums import MessageRole, pg_enum

__all__ = ["ConversationMessage", "MessageEvidence"]


class ConversationMessage(Base):
    """One turn of the follow-up conversation (`REQ-CONV-007`)."""

    __tablename__ = "conversation_messages"
    __table_args__ = (
        UniqueConstraint("session_id", "seq"),
        Index("ix_conversation_messages_version_id", "version_id"),
    )

    id: Mapped[UuidPk]

    session_id: Mapped[UUID] = mapped_column(ForeignKey("research_sessions.id"))
    version_id: Mapped[UUID] = mapped_column(ForeignKey("research_versions.id"))

    seq: Mapped[int]
    role: Mapped[MessageRole] = mapped_column(pg_enum(MessageRole, "message_role"))

    content: Mapped[str]

    context_ref: Mapped[Json | None]
    """What the user was pointing at when they asked — a claim, a section, a
    visualization (`REQ-CONV-002`)."""

    created_at: Mapped[CreatedAt]


class MessageEvidence(Base):
    """Citations on an agent answer (`REQ-CONV-004`).

    An answer citing nothing is as unacceptable as a fact claim citing nothing;
    this is the same linkage, applied to conversation.
    """

    __tablename__ = "message_evidence"
    __table_args__ = (Index("ix_message_evidence_evidence_id", "evidence_id"),)

    message_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversation_messages.id", ondelete="CASCADE"), primary_key=True
    )
    evidence_id: Mapped[UUID] = mapped_column(
        ForeignKey("evidence.id", ondelete="CASCADE"), primary_key=True
    )
