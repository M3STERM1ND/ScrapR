"""The planned questions, and the evidence that answers them.

`DEC-04 §3.1`: stage 2's questions are **persisted rows, not in-memory objects**,
because termination is defined over them and `REQ-AGENT-005 AC-2` requires the
criteria to be inspectable after the run. This is the decision's only schema
consequence.

They are `version_id`-scoped like every other research artefact (§4.1), so the
question set a version was judged against is preserved exactly as it was, and an
update plans its own.

`question_evidence` is a join table rather than a column on `evidence`, because
one extracted fact can answer more than one question — a revenue figure speaks
to both "what are the revenue trends" and "how is it valued" — and the coverage
count in `DEC-04 §3.2` is defined over "sources yielding evidence addressing
it", which is a relation, not an attribute.
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum, unique
from uuid import UUID

from sqlalchemy import ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from scrapr_core.db.base import Base, CreatedAt, Json, JsonList, UuidPk
from scrapr_core.db.enums import pg_enum

__all__ = ["QuestionEvidence", "QuestionState", "ResearchQuestion"]


@unique
class QuestionState(StrEnum):
    """Where a question stands (`DEC-04 §3.1`)."""

    OPEN = "open"
    """Not yet answered to the coverage bar. Keeps the area researching."""

    RESOLVED = "resolved"
    """Enough distinct, accessible, sufficiently authoritative sources
    (`DEC-04 §3.2`)."""

    UNANSWERABLE = "unanswerable"
    """Terminal and honest. The tools were exhausted and returned failures, or
    every source found was paywalled, blocked or failed (`DEC-04 §3.3`). It
    resolves the area for termination and becomes an `uncertainty` claim in the
    report — it is never a form of success."""


class ResearchQuestion(Base):
    """One question the plan committed to answering."""

    __tablename__ = "research_questions"
    __table_args__ = (
        UniqueConstraint("version_id", "ordering"),
        Index("ix_research_questions_version_id", "version_id"),
        # The sufficiency query filters open questions per area, every round.
        Index("ix_research_questions_version_id_resolution_state", "version_id", "resolution_state"),
    )

    id: Mapped[UuidPk]

    version_id: Mapped[UUID] = mapped_column(ForeignKey("research_versions.id"))

    area_name: Mapped[str]
    """The area this question belongs to. Denormalised on purpose: an area is a
    grouping the plan invented, not an entity with a life of its own, and a
    table for it would buy nothing the name does not already give."""

    text: Mapped[str]

    ordering: Mapped[int]
    """Plan order. Areas are assessed in plan order (`DEC-04 §5`), so this is
    the sequence the budget is spent in, not a display preference."""

    resolution_state: Mapped[QuestionState] = mapped_column(
        pg_enum(QuestionState, "question_state")
    )

    tool_categories: Mapped[JsonList] = mapped_column(default=list)
    """Which categories the plan chose for this question's area. Recorded so a
    later reader can see what was tried, and by category rather than by query
    (`REQ-AGENT-003 AC-3`)."""

    unanswerable_reason: Mapped[Json | None]
    """Why it could not be answered: the failing kinds and the sources involved
    (`DEC-04 §3.3`). Null unless `resolution_state` is `unanswerable`."""

    resolved_at: Mapped[dt.datetime | None]

    created_at: Mapped[CreatedAt]


class QuestionEvidence(Base):
    """Which evidence addresses which question.

    The input to the coverage count: distinct accessible sources, each yielding
    at least one evidence row here, with at least one above `lower` tier
    (`DEC-04 §3.2`).
    """

    __tablename__ = "question_evidence"
    __table_args__ = (Index("ix_question_evidence_evidence_id", "evidence_id"),)

    question_id: Mapped[UUID] = mapped_column(
        ForeignKey("research_questions.id", ondelete="CASCADE"), primary_key=True
    )
    evidence_id: Mapped[UUID] = mapped_column(
        ForeignKey("evidence.id", ondelete="CASCADE"), primary_key=True
    )
