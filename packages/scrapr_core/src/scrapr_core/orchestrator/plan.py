"""Stage 2 — Plan the research (`REQ-AGENT-002`, `REQ-AGENT-003`).

In: the interpretation's questions. Out: areas, each carrying its questions and
the tool categories worth trying for it.

Two invariants this stage enforces in code rather than hoping the model keeps:

* **Every question lands in exactly one area.** Termination is measured as
  coverage of the planned question set (`DEC-04 §3`), so a question the plan
  quietly drops is a question nothing will ever research and nothing will ever
  report as missing. A question the model fails to place is placed by us, in a
  named fallback area, and the repair is recorded — visible repair beats silent
  loss.
* **Only registered categories are selected.** The model names a category; the
  registry decides whether one exists. Asking for `filings` when no filings
  provider is configured would plan an area that cannot be served, which is a
  gap the user would never be told about.

**The orchestrator selects a category, never a provider** (`REQ-TOOL-009 AC-2`),
and activity records the category rather than the query (`REQ-AGENT-003 AC-3`).
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, Field, field_validator

from scrapr_core.llm.contract import LLMProvider, ModelTier, UntrustedDocument
from scrapr_core.orchestrator.interpret import Interpretation
from scrapr_core.security.trust import SourceRef, Trusted, Untrusted
from scrapr_core.tools.contract import ToolCategory

__all__ = [
    "FALLBACK_AREA_NAME",
    "MAX_AREAS",
    "PlannedArea",
    "ResearchPlan",
    "plan_research",
]

MAX_AREAS = 8
"""A run's areas each reserve budget (`DEC-04 §5`), so the count is bounded for
the same reason question count is."""

FALLBACK_AREA_NAME = "Further questions"
"""Where questions the model failed to place end up. Named plainly because a
user may see it in the activity list."""

INSTRUCTION = Trusted(
    "You are planning research. The material lists a subject and the questions "
    "that need answering.\n\n"
    "Group the questions into research areas. An area is a coherent line of "
    "enquiry a person would investigate in one sitting — financial performance, "
    "competitive position, hiring and headcount, recent developments. Give each "
    "area a short name a non-technical reader would understand, because it is "
    "shown while the research runs.\n\n"
    "Assign every question to exactly one area. Do not invent questions and do "
    "not drop any.\n\n"
    "For each area, choose the tool categories worth trying, from the available "
    "list only. Financial performance wants financial data and company filings. "
    "Hiring wants job postings. Recent events want news. Anything else wants "
    "web search. Choose what the area actually needs, not everything available."
)


class PlannedArea(BaseModel):
    """One line of enquiry, with its questions and the tools worth trying."""

    name: str = Field(min_length=1, max_length=80)
    """User-meaningful (`REQ-ACT-002`): it becomes an activity label."""

    questions: list[str] = Field(min_length=1)
    tool_categories: list[ToolCategory] = Field(min_length=1)

    @field_validator("tool_categories")
    @classmethod
    def _unique_categories(cls, value: list[ToolCategory]) -> list[ToolCategory]:
        """Keep first occurrences. A repeated category would retrieve twice and
        bill the area's budget twice for the same result."""
        seen: set[ToolCategory] = set()
        unique: list[ToolCategory] = []
        for category in value:
            if category not in seen:
                seen.add(category)
                unique.append(category)
        return unique


class ResearchPlan(BaseModel):
    """The whole plan. Recorded, and what drives activity (`REQ-AGENT-002 AC-2`)."""

    areas: list[PlannedArea] = Field(min_length=1, max_length=MAX_AREAS)

    @property
    def question_count(self) -> int:
        return sum(len(area.questions) for area in self.areas)

    def questions(self) -> list[str]:
        return [q for area in self.areas for q in area.questions]


class _PlanDraft(BaseModel):
    """What the model returns, before the invariants are imposed.

    Deliberately looser than `ResearchPlan`: categories arrive as free strings
    so an unrecognised one can be dropped rather than failing the whole call,
    and area count is unbounded so the repair step can decide what to do.
    """

    class Area(BaseModel):
        name: str = Field(min_length=1, max_length=80)
        questions: list[str] = Field(default_factory=list)
        tool_categories: list[str] = Field(default_factory=list)

    areas: list[Area] = Field(default_factory=list)


def _resolve_categories(
    named: Sequence[str], available: Sequence[ToolCategory]
) -> list[ToolCategory]:
    """Keep the named categories that actually exist, in the order named."""
    allowed = set(available)
    resolved: list[ToolCategory] = []
    for name in named:
        try:
            category = ToolCategory(name.strip().lower())
        except ValueError:
            continue
        if category in allowed and category not in resolved:
            resolved.append(category)
    return resolved


def _default_category(available: Sequence[ToolCategory]) -> ToolCategory:
    """What an area falls back to when nothing it named is available.

    Web search if there is one, otherwise whatever exists: an area with no tool
    at all cannot be retrieved, and an unretrievable area is worse than one
    pointed at a general tool.
    """
    if ToolCategory.WEB_SEARCH in available:
        return ToolCategory.WEB_SEARCH
    return available[0]


def _impose_invariants(
    draft: _PlanDraft,
    interpretation: Interpretation,
    available: Sequence[ToolCategory],
) -> ResearchPlan:
    """Turn a draft into a plan that covers every question exactly once."""
    planned: list[PlannedArea] = []
    claimed: set[str] = set()

    for area in draft.areas[:MAX_AREAS]:
        questions = [
            question
            for question in (q.strip() for q in area.questions)
            # Only questions the interpretation actually asked, and only once:
            # a model that invents or repeats a question would otherwise move
            # the coverage target it is being measured against.
            if question in interpretation.questions and question not in claimed
        ]
        if not questions:
            continue
        claimed.update(questions)

        categories = _resolve_categories(area.tool_categories, available)
        planned.append(
            PlannedArea(
                name=area.name.strip(),
                questions=questions,
                tool_categories=categories or [_default_category(available)],
            )
        )

    unplaced = [q for q in interpretation.questions if q not in claimed]
    if unplaced:
        if len(planned) < MAX_AREAS:
            planned.append(
                PlannedArea(
                    name=FALLBACK_AREA_NAME,
                    questions=unplaced,
                    tool_categories=[_default_category(available)],
                )
            )
        else:
            # Already at the cap. Coverage wins over tidiness: the leftovers
            # join the last area rather than being dropped to make room for a
            # fallback that would not fit.
            last = planned[-1]
            planned[-1] = last.model_copy(
                update={"questions": [*last.questions, *unplaced]}
            )

    return ResearchPlan(areas=planned)


async def plan_research(
    interpretation: Interpretation,
    available_categories: Sequence[ToolCategory],
    provider: LLMProvider,
    tier: ModelTier = ModelTier.STANDARD,
) -> ResearchPlan:
    """Group questions into areas and choose tool categories for each.

    Raises:
        ValueError: if no tool category is available at all. A run with no
            retrieval configured cannot produce evidence, and pretending to plan
            one would only defer the failure to a point where it looks like the
            research went badly rather than like nothing was wired up.
    """
    if not available_categories:
        raise ValueError(
            "cannot plan research with no tool categories registered; "
            "a run with no retrieval can produce no evidence"
        )

    origin = SourceRef(kind="plan", locator=interpretation.subject)
    material = [
        UntrustedDocument(
            content=Untrusted(
                "Subject: "
                + interpretation.subject
                + "\n\nQuestions:\n"
                + "\n".join(f"- {q}" for q in interpretation.questions),
                origin,
            ),
            label="interpretation",
        ),
        UntrustedDocument(
            content=Untrusted(
                "\n".join(category.value for category in available_categories),
                origin,
            ),
            label="available tool categories",
        ),
    ]

    result = await provider.complete_structured(INSTRUCTION, material, _PlanDraft, tier)
    return _impose_invariants(result.value, interpretation, available_categories)
