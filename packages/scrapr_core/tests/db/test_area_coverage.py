"""What an area actually reached, and what the report says about it.

Three defects lived in the gap between the research loop and the report, and
each one was invisible to the suite that shipped with them. They share a shape:
the run knew something true about its own coverage and then failed to carry it
anywhere a reader could see.

* **The reservation was sized per round, and spent per question.** A round
  retrieves for every open question, so it costs questions by categories; the
  reservation granted rounds by categories. Any area of two or more questions
  therefore could not afford its own first round, and the questions past the
  cap got no tool call, no failure, and no mention.
* **`_area_outcomes` built every outcome with empty `failures` and
  `skipped_categories`.** Two of the four sentences in `outcome._gaps` were
  unreachable in production as a result — including the one that would have
  disclosed the defect above.
* **`unanswerable` counted as outstanding when the run was summarised**, while
  `CoverageGatePolicy` counted it as terminal. An area that concluded honestly
  was reported as having run out of road, and the run's termination reason came
  out `ceiling` instead of `sufficiency`.

These are integration tests on purpose. Each defect was already covered at the
unit level — `test_outcome.py` passes a `skipped_categories` list by hand and
asserts the right sentence — and passed while the path was dead. Only a run
through the real loop, the real rows and the real gate can tell the difference.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import final
from uuid import UUID

import pytest
from pipeline_support import (
    HIRING_TEXT,
    REVENUE_TEXT,
    run_pipeline,
    scripted_provider,
    search_tool,
)
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from scrapr_core.db.enums import ClaimType, TerminationReason
from scrapr_core.db.models import (
    Base,
    Claim,
    QuestionState,
    ResearchQuestion,
    ResearchRun,
    ResearchSession,
)
from scrapr_core.db.repositories import (
    AnonymousSessionRepository,
    ResearchRepository,
    RunRepository,
)
from scrapr_core.domain.ownership import OwnerContext
from scrapr_core.llm import FakeLLMProvider
from scrapr_core.orchestrator.pipeline import STAGES
from scrapr_core.tools import ToolCategory, ToolRegistry
from scrapr_core.tools.contract import ToolOutcome, ToolRequest
from scrapr_core.tools.impl import FailingFixtureTool, FixtureTool

pytestmark = pytest.mark.integration

OBJECTIVE = "How is Acme Corp performing across the board?"

# Four questions in one area. Three is already enough to break the old
# arithmetic; four keeps the fixture clear of `QUESTIONS_PER_ROUND` boundaries
# so a failure reads as the defect rather than as an off-by-one.
FOUR_QUESTIONS = (
    "What is Acme's revenue?",
    "How many roles is Acme hiring for?",
    "Who are Acme's competitors?",
    "What are Acme's main risks?",
)

ONE_AREA = (("Company overview", FOUR_QUESTIONS, ("web_search",)),)


@final
@dataclass
class CountingTool:
    """A fixture tool that records which queries it was actually asked.

    The assertion that matters is not how much evidence came back; it is
    whether a planned question ever reached a provider at all. A question
    skipped for want of reservation looks exactly like a question whose
    retrieval found nothing, and only the call log tells them apart.
    """

    inner: FixtureTool
    queries: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.inner.name

    @property
    def category(self) -> ToolCategory:
        return self.inner.category

    async def invoke(self, request: ToolRequest) -> ToolOutcome:
        self.queries.append(str(request.params.get("query", "")))
        return await self.inner.invoke(request)


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


def start_research(session_factory: sessionmaker[Session]) -> UUID:
    with session_factory() as session:
        owner = OwnerContext.for_anonymous(
            AnonymousSessionRepository(session).issue().session.id
        )
        research = ResearchRepository(session, owner)
        created = research.create_session(objective=OBJECTIVE)
        version = research.open_version(created.id)
        assert version is not None
        RunRepository(session).create_run(created.id, version.id, STAGES)
        session.commit()
        return created.id


def provider_for(
    questions: Sequence[str] = FOUR_QUESTIONS,
    areas: Sequence[tuple[str, Sequence[str], Sequence[str]]] = ONE_AREA,
) -> FakeLLMProvider:
    return scripted_provider("Acme Corp", questions, areas)


# --------------------------------------------------------------------------
# The reservation covers what a round actually costs
# --------------------------------------------------------------------------


async def test_every_planned_question_reaches_a_provider(
    session_factory: sessionmaker[Session],
) -> None:
    """The defect in one assertion.

    Four questions in one area, one category, a provider that always answers.
    Sized per round, the area was granted two calls for a round that costs
    four, so the last two questions were never asked at all.
    """
    counting = CountingTool(inner=search_tool(texts=(REVENUE_TEXT, HIRING_TEXT)))
    registry = ToolRegistry()
    registry.register(counting)
    registry.freeze()
    start_research(session_factory)

    await run_pipeline(session_factory, registry, provider_for())

    asked = "\n".join(counting.queries)
    for question in FOUR_QUESTIONS:
        assert question in asked, (
            f"{question!r} was planned and never retrieved for; the area could "
            "not afford its own first round"
        )


async def test_an_affordable_area_resolves_all_of_its_questions(
    session_factory: sessionmaker[Session],
) -> None:
    """The consequence the reader would have seen.

    Every question here is answerable by the fixture, so a run that asked all
    four resolves all four. Before the fix two stayed `open` — and the report
    carried two uncertainties describing research that was never attempted.
    """
    registry = ToolRegistry()
    registry.register(search_tool(texts=(REVENUE_TEXT, HIRING_TEXT)))
    registry.freeze()
    start_research(session_factory)

    await run_pipeline(session_factory, registry, provider_for())

    with session_factory() as session:
        questions = session.execute(select(ResearchQuestion)).scalars().all()

    assert len(questions) == len(FOUR_QUESTIONS)
    unresolved = [q.text for q in questions if q.resolution_state is QuestionState.OPEN]
    assert not unresolved, f"still open after a full run: {unresolved}"


# --------------------------------------------------------------------------
# What the area could not reach reaches the report
# --------------------------------------------------------------------------


async def test_an_area_that_lost_a_source_says_so_in_the_report(
    session_factory: sessionmaker[Session],
) -> None:
    """`REQ-AGENT-009 AC-2` for the partial case.

    One category answers and one fails, so the area *is* researched but not
    wholly. That sentence exists in `outcome._gaps` and was unreachable: the
    failure never left the research step, so every rebuilt outcome claimed a
    clean run.
    """
    registry = ToolRegistry()
    registry.register(search_tool(texts=(REVENUE_TEXT, HIRING_TEXT)))
    registry.register(
        FailingFixtureTool(name="broken_news", category=ToolCategory.NEWS)
    )
    registry.freeze()
    session_id = start_research(session_factory)

    # One area, both categories: the area survives on web search while news
    # fails under it.
    areas = (("Company overview", FOUR_QUESTIONS, ("web_search", "news")),)
    await run_pipeline(session_factory, registry, provider_for(areas=areas))

    with session_factory() as session:
        research = session.get(ResearchSession, session_id)
        assert research is not None
        claims = (
            session.execute(
                select(Claim).where(Claim.version_id == research.current_version_id)
            )
            .scalars()
            .all()
        )

    disclosed = [
        claim.text
        for claim in claims
        if claim.claim_type is ClaimType.UNCERTAINTY
        and "could not be read" in claim.text
    ]
    assert disclosed, (
        "an area lost a source and the report never mentioned it; "
        f"claims were {[c.text for c in claims]}"
    )


async def test_the_disclosure_names_no_provider(
    session_factory: sessionmaker[Session],
) -> None:
    """`REQ-SEC-010`, `REQ-ACT-003`: the gap is a sentence, not a stack trace.

    The marker carrying the failure into the report is internal, so this is
    the test that keeps it internal.
    """
    registry = ToolRegistry()
    registry.register(search_tool(texts=(REVENUE_TEXT, HIRING_TEXT)))
    registry.register(
        FailingFixtureTool(name="broken_news", category=ToolCategory.NEWS)
    )
    registry.freeze()
    session_id = start_research(session_factory)

    areas = (("Company overview", FOUR_QUESTIONS, ("web_search", "news")),)
    await run_pipeline(session_factory, registry, provider_for(areas=areas))

    with session_factory() as session:
        research = session.get(ResearchSession, session_id)
        assert research is not None
        claims = (
            session.execute(
                select(Claim).where(Claim.version_id == research.current_version_id)
            )
            .scalars()
            .all()
        )

    for claim in claims:
        assert "broken_news" not in claim.text
        assert "fixture" not in claim.text.casefold()


# --------------------------------------------------------------------------
# Unanswerable is a conclusion, not a ceiling
# --------------------------------------------------------------------------


async def test_an_unanswerable_area_terminates_by_sufficiency(
    session_factory: sessionmaker[Session],
) -> None:
    """`REQ-AGENT-005 AC-4`, `DEC-04 §3.3`.

    The news area can never be answered, so its question is marked
    `unanswerable` — terminal, and honest. `CoverageGatePolicy` calls that area
    sufficient. The run summary called it a ceiling, because it counted
    anything short of `resolved` as still open, and the two have to agree.

    On its own this assertion also passes against the unfixed code, which wrote
    no reason at all and inherited `sufficiency` from the runner's default. It
    is the ceiling test below that separates a decision from a default; the two
    only mean something as a pair.
    """
    registry = ToolRegistry()
    registry.register(search_tool(texts=(REVENUE_TEXT, HIRING_TEXT)))
    registry.register(
        FailingFixtureTool(name="broken_news", category=ToolCategory.NEWS)
    )
    registry.freeze()
    session_id = start_research(session_factory)

    areas = (
        ("Financial performance", (FOUR_QUESTIONS[0],), ("web_search",)),
        ("Hiring", (FOUR_QUESTIONS[1],), ("news",)),
    )
    await run_pipeline(
        session_factory,
        registry,
        provider_for(questions=FOUR_QUESTIONS[:2], areas=areas),
    )

    with session_factory() as session:
        run = session.execute(
            select(ResearchRun).where(ResearchRun.session_id == session_id)
        ).scalar_one()
        questions = session.execute(select(ResearchQuestion)).scalars().all()

    hiring = next(q for q in questions if q.text == FOUR_QUESTIONS[1])
    assert hiring.resolution_state is QuestionState.UNANSWERABLE

    assert run.termination_reason is TerminationReason.SUFFICIENCY, (
        "an area that concluded honestly was recorded as having hit a ceiling"
    )


async def test_a_run_that_hit_a_ceiling_records_a_ceiling(
    session_factory: sessionmaker[Session],
) -> None:
    """`REQ-AGENT-005 AC-4`. The column existed and nothing ever wrote it.

    This is the discriminating half of the pair above. `JobRunner._finish_run`
    defers to the handler and defaults to `sufficiency` only when the handler
    recorded nothing — and the handler recorded nothing, so *every* run was
    stamped `sufficiency` by that default. A run reporting the honest outcome
    and a run reporting nothing at all were indistinguishable in the column
    meant to tell them apart, and only a genuine ceiling shows the difference.

    A single-source provider is what forces it: `MIN_SOURCES_PER_QUESTION` is
    two, so the question can never resolve, and the no-progress rule ends the
    area with the question still open.
    """
    registry = ToolRegistry()
    registry.register(search_tool(texts=(REVENUE_TEXT,)))
    registry.freeze()
    session_id = start_research(session_factory)

    await run_pipeline(session_factory, registry, provider_for())

    with session_factory() as session:
        run = session.execute(
            select(ResearchRun).where(ResearchRun.session_id == session_id)
        ).scalar_one()
        questions = session.execute(select(ResearchQuestion)).scalars().all()

    assert any(q.resolution_state is QuestionState.OPEN for q in questions), (
        "the fixture was meant to leave a question unresolvable"
    )
    assert run.termination_reason is TerminationReason.CEILING, (
        "the run stopped at a ceiling and recorded it as sufficiency"
    )
