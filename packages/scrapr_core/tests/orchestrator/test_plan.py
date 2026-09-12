"""Stage 2: the invariants the plan must hold (`REQ-AGENT-002`, `REQ-AGENT-003`).

The planner's judgement belongs to an eval set. What belongs here is what the
rest of the pipeline would break on: full question coverage, because termination
is measured against it, and categories that actually exist, because an area
pointed at a missing provider is a gap nobody would ever be told about.
"""

from __future__ import annotations

import pytest

from scrapr_core.llm import FakeLLMProvider
from scrapr_core.orchestrator.interpret import Interpretation
from scrapr_core.orchestrator.plan import (
    FALLBACK_AREA_NAME,
    MAX_AREAS,
    _PlanDraft,
    plan_research,
)
from scrapr_core.tools import ToolCategory

QUESTIONS = [
    "What are NVIDIA's revenue and margin trends?",
    "How is NVIDIA valued against its peers?",
    "What do employees report about working at NVIDIA?",
]

AVAILABLE = [
    ToolCategory.WEB_SEARCH,
    ToolCategory.FINANCIAL,
    ToolCategory.FILINGS,
    ToolCategory.JOBS,
]


def interpretation(questions: list[str] | None = None) -> Interpretation:
    return Interpretation(
        subject="NVIDIA Corporation",
        interpretation_note=None,
        questions=questions or QUESTIONS,
    )


def draft(*areas: tuple[str, list[str], list[str]]) -> _PlanDraft:
    return _PlanDraft(
        areas=[
            _PlanDraft.Area(name=name, questions=questions, tool_categories=categories)
            for name, questions, categories in areas
        ]
    )


async def run_plan(
    drafted: _PlanDraft,
    available: list[ToolCategory] | None = None,
    questions: list[str] | None = None,
):  # type: ignore[no-untyped-def]
    provider = FakeLLMProvider()
    provider.enqueue(drafted)
    return await plan_research(
        interpretation(questions), available or AVAILABLE, provider
    )


# --------------------------------------------------------------------------
# Coverage
# --------------------------------------------------------------------------


async def test_areas_carry_their_questions_and_categories() -> None:
    plan = await run_plan(
        draft(
            ("Financial performance", QUESTIONS[:2], ["financial", "filings"]),
            ("Working there", QUESTIONS[2:], ["jobs"]),
        )
    )

    assert [area.name for area in plan.areas] == [
        "Financial performance",
        "Working there",
    ]
    assert plan.areas[0].tool_categories == [
        ToolCategory.FINANCIAL,
        ToolCategory.FILINGS,
    ]
    assert plan.question_count == 3


async def test_a_dropped_question_is_replanned_not_lost() -> None:
    """Termination is coverage of the planned set, so a question the model
    forgets would be researched by nothing and reported as missing by nothing."""
    plan = await run_plan(draft(("Financial performance", QUESTIONS[:1], ["financial"])))

    assert sorted(plan.questions()) == sorted(QUESTIONS)
    assert plan.areas[-1].name == FALLBACK_AREA_NAME
    assert plan.areas[-1].questions == QUESTIONS[1:]


async def test_an_invented_question_is_discarded() -> None:
    """A model that adds a question would move the coverage target it is being
    measured against."""
    plan = await run_plan(
        draft(("Everything", [*QUESTIONS, "What is the CEO's favourite colour?"], ["web_search"]))
    )

    assert plan.questions() == QUESTIONS


async def test_a_question_claimed_twice_is_placed_once() -> None:
    plan = await run_plan(
        draft(
            ("Financial performance", QUESTIONS[:2], ["financial"]),
            ("Also financial", QUESTIONS[:2], ["filings"]),
        )
    )

    assert sorted(plan.questions()) == sorted(QUESTIONS)
    assert len(plan.questions()) == len(set(plan.questions()))


async def test_an_area_left_with_no_questions_is_dropped() -> None:
    """An empty area would still reserve budget and emit activity for work that
    does not exist."""
    plan = await run_plan(
        draft(
            ("Financial performance", QUESTIONS, ["financial"]),
            ("Leftovers", [], ["web_search"]),
        )
    )

    assert [area.name for area in plan.areas] == ["Financial performance"]


async def test_the_area_count_is_bounded() -> None:
    many = [f"Question {n}?" for n in range(MAX_AREAS + 3)]
    plan = await run_plan(
        draft(*[(f"Area {n}", [q], ["web_search"]) for n, q in enumerate(many)]),
        questions=many,
    )

    assert len(plan.areas) <= MAX_AREAS
    # Coverage still holds: the overflow lands in the fallback area rather than
    # being dropped along with the areas that did not fit.
    assert sorted(plan.questions()) == sorted(many)


# --------------------------------------------------------------------------
# Tool selection
# --------------------------------------------------------------------------


async def test_a_financial_area_can_select_financial_and_filings() -> None:
    """`REQ-AGENT-003 AC-1`, at the contract level: when those categories are
    named and registered, they survive into the plan."""
    plan = await run_plan(draft(("Financials", QUESTIONS, ["financial", "filings"])))

    assert ToolCategory.FINANCIAL in plan.areas[0].tool_categories
    assert ToolCategory.FILINGS in plan.areas[0].tool_categories


async def test_a_hiring_area_can_select_jobs() -> None:
    """`REQ-AGENT-003 AC-2`."""
    plan = await run_plan(draft(("Hiring", QUESTIONS, ["jobs"])))

    assert plan.areas[0].tool_categories == [ToolCategory.JOBS]


async def test_an_unregistered_category_is_dropped() -> None:
    """`OPEN-05..09` are open, so most categories have no provider yet. Planning
    an area against one would promise retrieval that cannot happen."""
    plan = await run_plan(
        draft(("News", QUESTIONS, ["news", "web_search"])),
        available=[ToolCategory.WEB_SEARCH],
    )

    assert plan.areas[0].tool_categories == [ToolCategory.WEB_SEARCH]


async def test_a_nonsense_category_name_is_dropped() -> None:
    plan = await run_plan(draft(("Odd", QUESTIONS, ["tarot", "web_search"])))

    assert plan.areas[0].tool_categories == [ToolCategory.WEB_SEARCH]


async def test_an_area_with_nothing_valid_falls_back_to_search() -> None:
    """An area with no tool cannot be retrieved at all, which is worse than one
    pointed at a general tool."""
    plan = await run_plan(draft(("Odd", QUESTIONS, ["tarot"])))

    assert plan.areas[0].tool_categories == [ToolCategory.WEB_SEARCH]


async def test_the_fallback_uses_what_exists_when_there_is_no_search() -> None:
    plan = await run_plan(
        draft(("Odd", QUESTIONS, ["tarot"])), available=[ToolCategory.FILINGS]
    )

    assert plan.areas[0].tool_categories == [ToolCategory.FILINGS]


async def test_a_repeated_category_is_kept_once() -> None:
    plan = await run_plan(draft(("Financials", QUESTIONS, ["financial", "financial"])))

    assert plan.areas[0].tool_categories == [ToolCategory.FINANCIAL]


async def test_planning_without_any_tool_refuses_to_pretend() -> None:
    """A run with no retrieval can produce no evidence. Failing here names the
    cause; failing later would look like research that went badly."""
    provider = FakeLLMProvider()

    with pytest.raises(ValueError, match="no tool categories registered"):
        await plan_research(interpretation(), [], provider)


# --------------------------------------------------------------------------
# The trust boundary
# --------------------------------------------------------------------------


async def test_the_interpretation_travels_as_material() -> None:
    provider = FakeLLMProvider()
    provider.enqueue(draft(("Financials", QUESTIONS, ["financial"])))

    await plan_research(interpretation(), AVAILABLE, provider)

    labels = [document.label for document in provider.calls[0].documents]
    assert labels == ["interpretation", "available tool categories"]
    assert "NVIDIA" in provider.calls[0].rendered


# --------------------------------------------------------------------------
# Areas nobody predicted (`REQ-AGENT-010`)
# --------------------------------------------------------------------------


async def test_an_area_nobody_anticipated_is_planned_like_any_other() -> None:
    """`AC-1`: a discovered area becomes a section without a code change.

    Area names are strings the planner chose, and nothing in the pipeline
    matches against a list of known ones. This test is what stops that becoming
    a static taxonomy later without somebody noticing.
    """
    plan = await run_plan(
        draft(("Regulatory exposure in the EU", QUESTIONS, ["web_search"]))
    )

    assert plan.areas[0].name == "Regulatory exposure in the EU"
    assert plan.areas[0].questions == QUESTIONS


async def test_a_discovered_area_gets_no_special_treatment() -> None:
    """`AC-2`: subject to the same evidence and citation rules as any other.

    It is planned by the same code path, so there is nowhere for an exemption
    to live — which is the point worth protecting.
    """
    plan = await run_plan(
        draft(
            ("Financial performance", QUESTIONS[:1], ["financial"]),
            ("Something nobody listed", QUESTIONS[1:], ["web_search"]),
        )
    )

    assert all(area.tool_categories for area in plan.areas)
    assert all(area.questions for area in plan.areas)
    assert sorted(plan.questions()) == sorted(QUESTIONS)
