"""The termination conformance test (`DEC-04 §10.3`).

This test was skipped while `OPEN-13` was open. `DEC-04` closed it, so the skip
is gone and this is **required**. It is a pure unit test with no model in the
loop: fixture the coverage counts, assert the verdict.

The seven cases `DEC-04 §10.3` names are each below, marked. They are the ones
where being wrong is expensive: every one of them is a way for an area to stop
researching while the report still claims to have covered it, or to keep
researching after there is nothing left to find.
"""

from __future__ import annotations

from uuid import UUID

import pytest

from scrapr_core.db.models import QuestionState, ResearchQuestion
from scrapr_core.db.repositories.questions import QuestionCoverage
from scrapr_core.domain.ids import new_id
from scrapr_core.orchestrator.sufficiency import (
    AREA_ROUNDS_CAP,
    AREA_ROUNDS_FLOOR,
    AreaProgress,
    CoverageGatePolicy,
    SufficiencyVerdict,
    area_rounds,
    is_resolved,
)

AREA = "Financial performance"


def question(text: str = "What is revenue?", state: QuestionState = QuestionState.OPEN) -> ResearchQuestion:
    return ResearchQuestion(
        id=new_id(),
        version_id=new_id(),
        area_name=AREA,
        text=text,
        ordering=0,
        resolution_state=state,
        tool_categories=[],
    )


def covered(
    question_id: UUID,
    *,
    sources: int = 0,
    above_lower: int = 0,
    primary: int = 0,
) -> QuestionCoverage:
    return QuestionCoverage(
        question_id=question_id,
        distinct_sources=sources,
        above_lower_sources=above_lower,
        primary_sources=primary,
    )


def assess(
    questions: list[ResearchQuestion],
    coverage: dict[UUID, QuestionCoverage] | None = None,
    *,
    rounds_used: int = 0,
    new_sources: int = 3,
    budget_exhausted: bool = False,
) -> SufficiencyVerdict:
    return CoverageGatePolicy().assess(
        AREA,
        questions,
        coverage or {},
        AreaProgress(
            rounds_used=rounds_used,
            new_sources_this_round=new_sources,
            budget_exhausted=budget_exhausted,
        ),
    )


# --------------------------------------------------------------------------
# The seven cases DEC-04 §10.3 requires
# --------------------------------------------------------------------------


def test_every_question_resolved_is_sufficient() -> None:
    """Case 1. The only route to `sufficient` that means what it says."""
    first, second = question("What is revenue?"), question("What is margin?")

    verdict = assess(
        [first, second],
        {
            first.id: covered(first.id, sources=2, above_lower=1),
            second.id: covered(second.id, sources=3, above_lower=2),
        },
    )

    assert verdict.decision == "sufficient"
    assert "2 of 2 questions resolved" in verdict.rationale


def test_one_source_short_keeps_going() -> None:
    """Case 2. One corroborating source is not corroboration."""
    only = question()

    verdict = assess([only], {only.id: covered(only.id, sources=1, above_lower=1)})

    assert verdict.decision == "continue"
    assert "1 accessible source(s)" in verdict.rationale


def test_only_lower_tier_sources_keep_going() -> None:
    """Case 3. Two forum posts are two forum posts, not an established fact."""
    only = question()

    verdict = assess([only], {only.id: covered(only.id, sources=3, above_lower=0)})

    assert verdict.decision == "continue"
    assert "all lower-tier" in verdict.rationale


def test_a_single_primary_source_resolves_a_question() -> None:
    """Case 4. A figure read straight from a filing does not need a second-hand
    corroborator to be established (`DEC-04 §3.2`)."""
    only = question()

    verdict = assess(
        [only], {only.id: covered(only.id, sources=1, above_lower=1, primary=1)}
    )

    assert verdict.decision == "sufficient"


def test_a_round_adding_no_new_sources_ends_the_area() -> None:
    """Case 5. The mandatory rail (`DEC-04 §4.2`): this is what makes research
    terminate without waiting to burn the whole ceiling, and the guard against
    an area whose queries have gone circular."""
    only = question()

    verdict = assess([only], rounds_used=1, new_sources=0)

    assert verdict.decision == "ceiling_reached"
    assert "no new sources" in verdict.rationale


def test_allocation_exhausted_is_a_ceiling_never_sufficiency() -> None:
    """Case 6. The distinction `REQ-AGENT-004 AC-3` turns on. Collapsing these
    two would be the most damaging lie the system could tell about its work."""
    only = question()

    verdict = assess([only], rounds_used=AREA_ROUNDS_CAP + 1)

    assert verdict.decision == "ceiling_reached"
    assert verdict.decision != "sufficient"
    assert "allocation exhausted" in verdict.rationale


def test_all_questions_unanswerable_resolves_the_area() -> None:
    """Case 7. Terminal and honest: it resolves the area for termination and
    becomes an uncertainty claim in the report, never a form of success."""
    first = question("What is segment revenue?", QuestionState.UNANSWERABLE)
    second = question("What is segment margin?", QuestionState.UNANSWERABLE)

    verdict = assess([first, second])

    assert verdict.decision == "sufficient"
    assert "2 unanswerable" in verdict.rationale
    assert "0 of 2 questions resolved" in verdict.rationale


# --------------------------------------------------------------------------
# The allocation
# --------------------------------------------------------------------------


def test_a_small_area_is_allocated_less_than_a_large_one() -> None:
    """`REQ-AGENT-004 AC-2`: depth varies within one run by construction."""
    assert area_rounds(2) < area_rounds(7)


@pytest.mark.parametrize(
    ("open_questions", "expected"),
    [(0, AREA_ROUNDS_FLOOR), (1, 1), (2, 1), (3, 2), (4, 2), (20, AREA_ROUNDS_CAP)],
)
def test_allocation_is_derived_and_clamped(open_questions: int, expected: int) -> None:
    assert area_rounds(open_questions) == expected


def test_the_run_budget_ends_an_area_wherever_it_stands() -> None:
    """`DEC-04 §5`: whichever counter binds, binds, and the area stops."""
    only = question()

    verdict = assess([only], budget_exhausted=True)

    assert verdict.decision == "ceiling_reached"
    assert "run budget exhausted" in verdict.rationale


def test_budget_exhaustion_does_not_override_completed_work() -> None:
    """An area that finished on the same round the budget ran out did the work;
    the ceiling merely arrived at the same time."""
    only = question()

    verdict = assess(
        [only],
        {only.id: covered(only.id, primary=1, sources=1, above_lower=1)},
        budget_exhausted=True,
    )

    assert verdict.decision == "sufficient"


def test_the_first_round_is_never_cut_short_by_the_no_progress_rule() -> None:
    """Zero new sources before any round has run is the starting state, not a
    signal that the area has gone circular."""
    only = question()

    verdict = assess([only], rounds_used=0, new_sources=0)

    assert verdict.decision == "continue"


# --------------------------------------------------------------------------
# Resolution, in isolation
# --------------------------------------------------------------------------


def test_a_question_with_no_evidence_is_not_resolved() -> None:
    assert not is_resolved(None)


@pytest.mark.parametrize(
    ("sources", "above_lower", "primary", "resolved"),
    [
        (0, 0, 0, False),
        (1, 1, 0, False),  # one source is not corroboration
        (2, 0, 0, False),  # two lower-tier sources are not authority
        (2, 1, 0, True),
        (1, 1, 1, True),  # a primary source stands alone
        (5, 0, 0, False),
    ],
)
def test_the_resolution_bar(
    sources: int, above_lower: int, primary: int, resolved: bool
) -> None:
    coverage = covered(
        new_id(), sources=sources, above_lower=above_lower, primary=primary
    )

    assert is_resolved(coverage) is resolved


def test_a_question_marked_resolved_stays_resolved() -> None:
    """Persisted state wins over a recount: a question resolved in an earlier
    round does not reopen because this round's query returned less."""
    already = question(state=QuestionState.RESOLVED)

    verdict = assess([already])

    assert verdict.decision == "sufficient"


# --------------------------------------------------------------------------
# The rationale
# --------------------------------------------------------------------------


def test_the_rationale_names_the_question_holding_the_area_open() -> None:
    """`REQ-AGENT-005 AC-2`. It is generated from the counts that produced the
    verdict, so it cannot disagree with the decision it explains."""
    blocked = question("What is FY2025 segment revenue?")

    verdict = assess([blocked])

    assert "FY2025 segment revenue" in verdict.rationale
    assert "no accessible sources" in verdict.rationale


def test_a_verdict_knows_whether_it_ends_the_area() -> None:
    only = question()

    assert not assess([only]).is_terminal
    assert assess([only], budget_exhausted=True).is_terminal
