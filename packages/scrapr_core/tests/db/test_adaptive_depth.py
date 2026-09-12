"""The adaptive-depth eval: a narrow objective must cost less than a broad one.

`DEC-04 §8` says `REQ-AGENT-004 AC-1` is "verified by an eval comparing
tool-call totals for a narrow and a broad objective", and `§12` warns that a
regression in question quality is *a termination defect before it is a planning
defect* — so this is load-bearing, not a nice-to-have.

**What this eval can and cannot prove.** It measures the machinery: given a
narrow interpretation and a broad one, the pipeline plans fewer areas, allocates
fewer rounds, and spends measurably fewer tool calls. It cannot prove that a
*model* turns a narrow objective into few questions and a broad one into many —
that half needs a real provider and belongs to a golden-objective set once
`OPEN-04` closes. The interpretations here are therefore fixed, which makes the
result a statement about the allocation rule rather than about model quality.

That split matters: if this test ever passes while real runs cost the same for
both, the fault is in question decomposition, and `§12` says to read it that way
rather than as a budget problem.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from uuid import UUID

import pytest
from pipeline_support import REVENUE_TEXT, run_pipeline, scripted_provider, search_tool
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from scrapr_core.db.models import Base, Evidence, ResearchQuestion
from scrapr_core.db.repositories import (
    AnonymousSessionRepository,
    ResearchRepository,
    RunRepository,
)
from scrapr_core.domain.ownership import OwnerContext
from scrapr_core.orchestrator.pipeline import STAGES
from scrapr_core.orchestrator.sufficiency import (
    AREA_ROUNDS_CAP,
    AREA_ROUNDS_FLOOR,
    area_rounds,
)
from scrapr_core.tools import ToolCategory, ToolRegistry
from scrapr_core.tools.impl import FixtureTool

pytestmark = pytest.mark.integration

NARROW_OBJECTIVE = "What was Acme's FY2025 revenue?"
NARROW_QUESTIONS = ("What was Acme's revenue in FY2025?",)
NARROW_AREAS = (("Revenue", NARROW_QUESTIONS, ("web_search",)),)

BROAD_OBJECTIVE = (
    "Analyse Acme as a company, as an investment, and as a place to work."
)
BROAD_QUESTIONS = (
    "What are Acme's revenue and margin trends?",
    "How is Acme valued against its peers?",
    "What is Acme's competitive position?",
    "What do employees report about working at Acme?",
    "How fast is Acme hiring?",
    "What recent developments affect Acme?",
)
# Area sizes deliberately straddle an allocation boundary. With
# `QUESTIONS_PER_ROUND = 2`, areas of one and two questions both draw a single
# round, so a plan whose areas are all that size would show no variation and
# would say nothing about `AC-2`. A broad objective realistically produces
# uneven areas, and this is what that looks like: 3, 1, 1, 1.
BROAD_AREAS = (
    ("Financial performance", BROAD_QUESTIONS[:3], ("web_search",)),
    ("Competitive position", BROAD_QUESTIONS[3:4], ("web_search",)),
    ("Working there", BROAD_QUESTIONS[4:5], ("news",)),
    ("Recent developments", BROAD_QUESTIONS[5:], ("news",)),
)


class CountingTool:
    """A fixture tool that records how many times it was actually called.

    Counting at the tool rather than at the registry is deliberate: a cache hit
    costs nothing, and the number that matters for `NFR-COST-001` is the number
    of calls a provider would have billed for.
    """

    def __init__(self, inner: FixtureTool) -> None:
        self._inner = inner
        self.calls = 0

    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def category(self) -> ToolCategory:
        return self._inner.category

    async def invoke(self, request: object) -> object:
        self.calls += 1
        return await self._inner.invoke(request)  # type: ignore[arg-type]


@pytest.fixture
def session_factory(migrated_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=migrated_engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
def _empty_afterwards(session_factory: sessionmaker[Session]) -> Iterator[None]:
    yield
    tables = ", ".join(sorted(Base.metadata.tables))
    with session_factory() as session:
        session.execute(text(f"TRUNCATE {tables} CASCADE"))
        session.commit()


def counting_registry() -> tuple[ToolRegistry, list[CountingTool]]:
    """Two categories, each counting its own calls.

    The fixtures return a single source, so no question ever reaches
    `MIN_SOURCES_PER_QUESTION`. That is on purpose: every area runs to its
    allocation, which is exactly the quantity this eval is measuring.
    """
    registry = ToolRegistry()
    counters = [
        CountingTool(search_tool(texts=(REVENUE_TEXT,))),
        CountingTool(
            search_tool(
                name="fixture_news",
                category=ToolCategory.NEWS,
                texts=(REVENUE_TEXT,),
                host="press.example",
            )
        ),
    ]
    for counter in counters:
        registry.register(counter)
    registry.freeze()
    return registry, counters


async def measure(
    session_factory: sessionmaker[Session],
    objective: str,
    questions: Sequence[str],
    areas: Sequence[tuple[str, Sequence[str], Sequence[str]]],
) -> tuple[int, int, int]:
    """Run one objective end to end. Returns tool calls, questions, evidence."""
    with session_factory() as session:
        owner = OwnerContext.for_anonymous(
            AnonymousSessionRepository(session).issue().session.id
        )
        research = ResearchRepository(session, owner)
        created = research.create_session(objective=objective)
        version = research.open_version(created.id)
        assert version is not None
        RunRepository(session).create_run(created.id, version.id, STAGES)
        session.commit()
        version_id: UUID = version.id

    registry, counters = counting_registry()
    provider = scripted_provider("Acme Corp", questions, areas)
    await run_pipeline(session_factory, registry, provider)

    with session_factory() as session:
        planned = len(
            session.execute(
                select(ResearchQuestion.id).where(
                    ResearchQuestion.version_id == version_id
                )
            )
            .scalars()
            .all()
        )
        evidence = len(
            session.execute(
                select(Evidence.id).where(Evidence.version_id == version_id)
            )
            .scalars()
            .all()
        )

    return sum(counter.calls for counter in counters), planned, evidence


# --------------------------------------------------------------------------
# The eval
# --------------------------------------------------------------------------


async def test_a_narrow_objective_costs_measurably_less_than_a_broad_one(
    session_factory: sessionmaker[Session],
) -> None:
    """`REQ-AGENT-004 AC-1`, the way `DEC-04 §8` asks for it: tool-call totals.

    "Measurably" is the point. A narrow run that costs 90% of a broad one would
    pass a simple inequality while meaning the depth rule does nothing, so the
    assertion is a ratio, not a comparison.
    """
    narrow_calls, narrow_questions, _ = await measure(
        session_factory, NARROW_OBJECTIVE, NARROW_QUESTIONS, NARROW_AREAS
    )
    broad_calls, broad_questions, _ = await measure(
        session_factory, BROAD_OBJECTIVE, BROAD_QUESTIONS, BROAD_AREAS
    )

    assert narrow_questions < broad_questions
    assert narrow_calls < broad_calls
    assert narrow_calls * 2 <= broad_calls, (
        f"narrow spent {narrow_calls} calls against broad's {broad_calls}: "
        "the depth rule is not separating them"
    )


async def test_the_broad_objective_gathers_more_evidence(
    session_factory: sessionmaker[Session],
) -> None:
    """Effort has to buy something. A broad run that spends more and returns the
    same evidence would be burning budget rather than researching more."""
    _, _, narrow_evidence = await measure(
        session_factory, NARROW_OBJECTIVE, NARROW_QUESTIONS, NARROW_AREAS
    )
    _, _, broad_evidence = await measure(
        session_factory, BROAD_OBJECTIVE, BROAD_QUESTIONS, BROAD_AREAS
    )

    assert broad_evidence > narrow_evidence


async def test_depth_varies_between_areas_of_one_run(
    session_factory: sessionmaker[Session],
) -> None:
    """`REQ-AGENT-004 AC-2`: depth varies *within* a run, not just between runs.

    The three-question area draws more rounds than the one-question areas in the
    same plan, which is the structural claim `DEC-04 §4.1` makes.
    """
    with session_factory() as session:
        owner = OwnerContext.for_anonymous(
            AnonymousSessionRepository(session).issue().session.id
        )
        research = ResearchRepository(session, owner)
        created = research.create_session(objective=BROAD_OBJECTIVE)
        version = research.open_version(created.id)
        assert version is not None
        RunRepository(session).create_run(created.id, version.id, STAGES)
        session.commit()
        version_id = version.id

    registry, _ = counting_registry()
    await run_pipeline(
        session_factory,
        registry,
        scripted_provider("Acme Corp", BROAD_QUESTIONS, BROAD_AREAS),
    )

    with session_factory() as session:
        questions = (
            session.execute(
                select(ResearchQuestion).where(
                    ResearchQuestion.version_id == version_id
                )
            )
            .scalars()
            .all()
        )

    per_area: dict[str, int] = {}
    for question in questions:
        per_area[question.area_name] = per_area.get(question.area_name, 0) + 1

    allocations = {name: area_rounds(count) for name, count in per_area.items()}
    assert len(set(allocations.values())) > 1, (
        f"every area drew the same allocation: {allocations}"
    )


# --------------------------------------------------------------------------
# The allocation rule itself, without a database
# --------------------------------------------------------------------------


def test_a_fixed_count_is_never_the_stopping_rule() -> None:
    """`REQ-AGENT-004 AC-3`. The cap bounds effort; it does not decide when
    research is done, and reaching it yields `ceiling_reached`."""
    assert area_rounds(1) == AREA_ROUNDS_FLOOR
    assert area_rounds(100) == AREA_ROUNDS_CAP
    assert area_rounds(6) > area_rounds(2)


def test_allocation_is_monotonic_in_question_count() -> None:
    """More to answer never buys less effort. A non-monotonic rule would make
    a broader area cheaper than a narrow one at some size, which is the bug this
    catches."""
    previous = 0
    for count in range(0, 20):
        allocated = area_rounds(count)
        assert allocated >= previous
        previous = allocated
