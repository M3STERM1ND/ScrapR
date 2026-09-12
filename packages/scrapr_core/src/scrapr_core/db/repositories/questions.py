"""Reading and writing the planned questions, and counting their coverage.

The coverage query is the mechanical half of `DEC-04`: it reports, per question,
how many distinct accessible sources yielded evidence addressing it and what
authority those sources carried. It decides nothing. `CoverageGatePolicy` reads
these counts and applies the rule, so the query can be tested against fixture
rows and the policy against fixture counts, and neither has to mock the other.

**Distinctness is by normalised URL where there is one.** `DEC-04 §3.2` says
"distinct by `sources.url_normalized`", and for a source that has none — a
filing identified by accession number, an uploaded document — the identifier
stands in, and the row id after that. Two records of the same filing must not
count as two corroborating sources.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import final
from uuid import UUID

from sqlalchemy import Select, Text, case, func, select
from sqlalchemy.orm import Session

from scrapr_core.db.base import utcnow
from scrapr_core.db.enums import Accessibility, AuthorityTier
from scrapr_core.db.models import (
    Evidence,
    QuestionEvidence,
    QuestionState,
    ResearchQuestion,
    Source,
)
from scrapr_core.domain.json import JsonMapping

__all__ = ["QuestionCoverage", "QuestionRepository"]


@final
@dataclass(frozen=True, slots=True)
class QuestionCoverage:
    """What the evidence says about one question. Facts, not a verdict."""

    question_id: UUID
    distinct_sources: int
    """Distinct, accessible sources yielding at least one evidence row."""
    above_lower_sources: int
    """How many of those carry an authority tier above `lower`."""
    primary_sources: int
    """How many are `primary`. One of these is worth the pair (`DEC-04 §3.2`)."""


class QuestionRepository:
    """Persists planned questions and reports their coverage."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    def persist_plan(
        self,
        version_id: UUID,
        areas: Sequence[tuple[str, Sequence[str], Sequence[str]]],
    ) -> Sequence[ResearchQuestion]:
        """Write a plan's questions, in plan order.

        Takes plain tuples rather than the planner's Pydantic types: this layer
        stores rows, and giving it an import of the orchestrator would make the
        database depend on the pipeline that happens to fill it.
        """
        questions: list[ResearchQuestion] = []
        ordering = 0

        for area_name, texts, categories in areas:
            for text in texts:
                questions.append(
                    ResearchQuestion(
                        version_id=version_id,
                        area_name=area_name,
                        text=text,
                        ordering=ordering,
                        resolution_state=QuestionState.OPEN,
                        tool_categories=list(categories),
                    )
                )
                ordering += 1

        self._session.add_all(questions)
        self._session.flush()
        return questions

    def link_evidence(self, question_id: UUID, evidence_id: UUID) -> None:
        """Record that a piece of evidence addresses a question. Idempotent."""
        existing = self._session.get(QuestionEvidence, (question_id, evidence_id))
        if existing is not None:
            return
        self._session.add(
            QuestionEvidence(question_id=question_id, evidence_id=evidence_id)
        )
        self._session.flush()

    def mark_resolved(self, question: ResearchQuestion) -> None:
        question.resolution_state = QuestionState.RESOLVED
        question.resolved_at = utcnow()
        self._session.flush()

    def mark_unanswerable(
        self, question: ResearchQuestion, reason: JsonMapping
    ) -> None:
        """Terminal and honest (`DEC-04 §3.3`).

        The reason carries the failing kinds and the sources involved, because
        "could not be established" is a claim the report makes and a reader is
        entitled to know what was tried.
        """
        question.resolution_state = QuestionState.UNANSWERABLE
        question.unanswerable_reason = dict(reason)
        question.resolved_at = utcnow()
        self._session.flush()

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def for_version(self, version_id: UUID) -> Sequence[ResearchQuestion]:
        return self._ordered(self._base(version_id))

    def for_area(self, version_id: UUID, area_name: str) -> Sequence[ResearchQuestion]:
        return self._ordered(
            self._base(version_id).where(ResearchQuestion.area_name == area_name)
        )

    def open_for_area(
        self, version_id: UUID, area_name: str
    ) -> Sequence[ResearchQuestion]:
        return self._ordered(
            self._base(version_id).where(
                ResearchQuestion.area_name == area_name,
                ResearchQuestion.resolution_state == QuestionState.OPEN,
            )
        )

    def unresolved_for_version(
        self, version_id: UUID
    ) -> Sequence[ResearchQuestion]:
        """Open and unanswerable questions together.

        Both produce an `uncertainty` claim (`DEC-04 §6.3`): one because nothing
        was found in the effort available, the other because nothing could be.
        """
        return self._ordered(
            self._base(version_id).where(
                ResearchQuestion.resolution_state != QuestionState.RESOLVED
            )
        )

    def evidence_ids_for(self, question_id: UUID) -> Sequence[UUID]:
        """The evidence recorded as addressing one question.

        Synthesis uses it to tell the model which question each fact answers,
        so a report can group evidence by the enquiry it came from rather than
        by the order it happened to arrive in.
        """
        return (
            self._session.execute(
                select(QuestionEvidence.evidence_id).where(
                    QuestionEvidence.question_id == question_id
                )
            )
            .scalars()
            .all()
        )

    def coverage(self, version_id: UUID) -> dict[UUID, QuestionCoverage]:
        """Per-question source counts, for every question in the version.

        One query for the whole version rather than one per question: the gate
        runs after every retrieval round, and a per-question query would make
        termination cost grow with the size of the plan.

        Questions with no qualifying evidence are absent from the result, which
        the policy reads as zero.
        """
        # Distinctness key: normalised URL, then provider identifier, then the
        # row id. Two records of one filing must not corroborate each other.
        source_key = func.coalesce(
            Source.url_normalized, Source.identifier, func.cast(Source.id, Text)
        )

        statement = (
            select(
                ResearchQuestion.id,
                func.count(func.distinct(source_key)).label("distinct_sources"),
                func.count(
                    func.distinct(
                        case(
                            (Source.authority_tier != AuthorityTier.LOWER, source_key),
                        )
                    )
                ).label("above_lower"),
                func.count(
                    func.distinct(
                        case(
                            (Source.authority_tier == AuthorityTier.PRIMARY, source_key),
                        )
                    )
                ).label("primary"),
            )
            .join(QuestionEvidence, QuestionEvidence.question_id == ResearchQuestion.id)
            .join(Evidence, Evidence.id == QuestionEvidence.evidence_id)
            .join(Source, Source.id == Evidence.source_id)
            .where(
                ResearchQuestion.version_id == version_id,
                # An unreadable source corroborates nothing: `REQ-EVID-018`
                # forbids citing one, so it cannot count toward coverage either.
                Source.accessibility == Accessibility.ACCESSIBLE,
            )
            .group_by(ResearchQuestion.id)
        )

        return {
            row[0]: QuestionCoverage(
                question_id=row[0],
                distinct_sources=row[1],
                above_lower_sources=row[2],
                primary_sources=row[3],
            )
            for row in self._session.execute(statement).all()
        }

    # ------------------------------------------------------------------

    def _base(self, version_id: UUID) -> Select[tuple[ResearchQuestion]]:
        return select(ResearchQuestion).where(
            ResearchQuestion.version_id == version_id
        )

    def _ordered(
        self, statement: Select[tuple[ResearchQuestion]]
    ) -> Sequence[ResearchQuestion]:
        return (
            self._session.execute(statement.order_by(ResearchQuestion.ordering))
            .scalars()
            .all()
        )
