"""Stage 7 — Assess sufficiency (`REQ-AGENT-004`, `REQ-AGENT-005`, `DEC-04`).

**Sufficiency is question coverage.** Not a model's opinion, and not an
iteration count. An area is done when every question the plan committed to is
either resolved by evidence or honestly marked unanswerable.

That choice is what makes three acceptance criteria structural rather than
aspirational:

* `REQ-AGENT-004 AC-2` — depth varies *within* one run, because the per-area
  ceiling is derived from that area's open question count, not fixed.
* `REQ-AGENT-004 AC-3` — no fixed count is the stopping rule; the cap yields
  `ceiling_reached`, which is never `sufficient`.
* `REQ-AGENT-005 AC-2` — the rationale is generated from the same counts that
  produced the verdict, so it cannot disagree with the decision it explains.

**`ceiling_reached` and `sufficient` are never collapsed.** A ceiling means the
questions are still open and the report owes the reader an uncertainty for each
one (`DEC-04 §6`). Calling that "sufficient" would be the single most damaging
lie the system could tell about its own work.

**No model call and no tool call happens here** (`DEC-04 §3.4`), so the gate
costs nothing against `NFR-COST-001` and can run after every round.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, final
from uuid import UUID

from scrapr_core.db.models import QuestionState, ResearchQuestion
from scrapr_core.db.repositories.questions import QuestionCoverage

__all__ = [
    "AREA_ROUNDS_CAP",
    "AREA_ROUNDS_FLOOR",
    "MIN_SOURCES_PER_QUESTION",
    "QUESTIONS_PER_ROUND",
    "AreaProgress",
    "CoverageGatePolicy",
    "SufficiencyVerdict",
    "TerminationPolicy",
    "area_rounds",
    "is_resolved",
]

# `DEC-04 §7`. Configuration, not constants scattered through the pipeline.
MIN_SOURCES_PER_QUESTION = 2
"""Distinct accessible sources needed to resolve a question. Drops to one where
that source is `primary`: a figure read straight from a filing does not need a
second-hand corroborator to be established."""

QUESTIONS_PER_ROUND = 2
"""Sets the slope of the per-area allocation."""

AREA_ROUNDS_FLOOR = 1
"""Every planned area gets at least one retrieval round."""

AREA_ROUNDS_CAP = 4
"""A ceiling, never the stopping rule."""

type Decision = Literal["continue", "sufficient", "ceiling_reached"]


@final
@dataclass(frozen=True, slots=True)
class SufficiencyVerdict:
    """The decision, and the reason it was reached (`REQ-AGENT-005 AC-2`)."""

    decision: Decision
    rationale: str
    area_name: str

    @property
    def is_terminal(self) -> bool:
        return self.decision != "continue"


@final
@dataclass(frozen=True, slots=True)
class AreaProgress:
    """What the current round changed, and what the area has spent.

    `new_sources_this_round` is the input to the no-progress rule, which is
    mandatory: it is what makes `REQ-AGENT-005 AC-1` hold without waiting to
    burn the whole ceiling, and the guard against an area whose queries have
    gone circular quietly consuming the run's budget (`DEC-04 §4.2`).
    """

    rounds_used: int
    new_sources_this_round: int
    budget_exhausted: bool = False


class TerminationPolicy(Protocol):
    """What decides whether an area keeps researching.

    A protocol because `DEC-04 §9.1` defers a model-based judge to V1.1: it
    satisfies this same interface, so adopting it later is a config change plus
    one class.
    """

    def assess(
        self,
        area_name: str,
        questions: Sequence[ResearchQuestion],
        coverage: Mapping[UUID, QuestionCoverage],
        progress: AreaProgress,
    ) -> SufficiencyVerdict: ...


def is_resolved(coverage: QuestionCoverage | None) -> bool:
    """Whether one question's evidence meets the bar (`DEC-04 §3.2`).

    Both conditions must hold — enough distinct accessible sources, and at least
    one above `lower` tier — except that a single `primary` source satisfies
    both at once.
    """
    if coverage is None:
        return False
    if coverage.primary_sources >= 1:
        return True
    return (
        coverage.distinct_sources >= MIN_SOURCES_PER_QUESTION
        and coverage.above_lower_sources >= 1
    )


def area_rounds(open_question_count: int) -> int:
    """How many retrieval rounds an area is allocated (`DEC-04 §4.1`).

    Derived, never constant. A two-question area draws materially less than a
    seven-question area within the same run, which is what makes adaptive depth
    a property of the design rather than a hope about the model.
    """
    if open_question_count <= 0:
        return AREA_ROUNDS_FLOOR
    rounds = -(-open_question_count // QUESTIONS_PER_ROUND)  # ceiling division
    return max(AREA_ROUNDS_FLOOR, min(rounds, AREA_ROUNDS_CAP))


@final
class CoverageGatePolicy:
    """The `DEC-04` implementation of `TerminationPolicy`."""

    def assess(
        self,
        area_name: str,
        questions: Sequence[ResearchQuestion],
        coverage: Mapping[UUID, QuestionCoverage],
        progress: AreaProgress,
    ) -> SufficiencyVerdict:
        """Decide whether this area continues, is done, or hit a ceiling.

        The order of the branches is the decision (`DEC-04 §3.4`), and it is not
        arbitrary: coverage is checked first so that an area which finished on
        the same round its budget ran out is reported as `sufficient` rather
        than as a ceiling. It did the work; the ceiling merely arrived too.
        """
        resolved, unanswerable, still_open = self._partition(questions, coverage)

        if not still_open:
            return SufficiencyVerdict(
                decision="sufficient",
                rationale=self._rationale(
                    questions, resolved, unanswerable, still_open, coverage, None
                ),
                area_name=area_name,
            )

        reason = self._ceiling_reason(progress, still_open)
        if reason is not None:
            return SufficiencyVerdict(
                decision="ceiling_reached",
                rationale=self._rationale(
                    questions, resolved, unanswerable, still_open, coverage, reason
                ),
                area_name=area_name,
            )

        return SufficiencyVerdict(
            decision="continue",
            rationale=self._rationale(
                questions, resolved, unanswerable, still_open, coverage, None
            ),
            area_name=area_name,
        )

    # ------------------------------------------------------------------

    @staticmethod
    def _partition(
        questions: Sequence[ResearchQuestion],
        coverage: Mapping[UUID, QuestionCoverage],
    ) -> tuple[
        list[ResearchQuestion], list[ResearchQuestion], list[ResearchQuestion]
    ]:
        """Split into resolved, unanswerable and still open.

        A question already marked `unanswerable` resolves the *area* without
        pretending to answer the question: it is terminal and honest, and it
        still produces an uncertainty claim in the report (`DEC-04 §3.3`).
        """
        resolved: list[ResearchQuestion] = []
        unanswerable: list[ResearchQuestion] = []
        still_open: list[ResearchQuestion] = []

        for question in questions:
            if question.resolution_state is QuestionState.UNANSWERABLE:
                unanswerable.append(question)
            elif question.resolution_state is QuestionState.RESOLVED or is_resolved(
                coverage.get(question.id)
            ):
                resolved.append(question)
            else:
                still_open.append(question)

        return resolved, unanswerable, still_open

    @staticmethod
    def _ceiling_reason(
        progress: AreaProgress, still_open: Sequence[ResearchQuestion]
    ) -> str | None:
        """Which ceiling stops this area, if any."""
        if progress.budget_exhausted:
            return "run budget exhausted"
        if progress.rounds_used > 0 and progress.new_sources_this_round == 0:
            # Mandatory rail: a round that added no new distinct source will not
            # do better on the next attempt, and the area would otherwise spend
            # its whole allocation confirming that.
            return "a round added no new sources"
        if progress.rounds_used >= area_rounds(len(still_open)):
            return f"area allocation exhausted at {progress.rounds_used} rounds"
        return None

    @staticmethod
    def _rationale(
        questions: Sequence[ResearchQuestion],
        resolved: Sequence[ResearchQuestion],
        unanswerable: Sequence[ResearchQuestion],
        still_open: Sequence[ResearchQuestion],
        coverage: Mapping[UUID, QuestionCoverage],
        ceiling: str | None,
    ) -> str:
        """Generated from the same counts that produced the verdict.

        Never written by a model (`DEC-04 §3.5`), so it cannot drift from the
        decision it explains.
        """
        parts = [f"{len(resolved)} of {len(questions)} questions resolved"]

        if ceiling:
            parts.append(ceiling)

        if still_open:
            worst = still_open[0]
            found = coverage.get(worst.id)
            if found is None:
                detail = "no accessible sources"
            elif found.above_lower_sources == 0:
                detail = f"{found.distinct_sources} accessible source(s), all lower-tier"
            else:
                detail = f"{found.distinct_sources} accessible source(s)"
            parts.append(f'"{worst.text}" has {detail}')

        if unanswerable:
            parts.append(f"{len(unanswerable)} unanswerable")

        return "; ".join(parts)
