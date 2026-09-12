"""The Phase 1 pipeline, end to end through the runner.

> **Exit (PRD):** one query reliably becomes a useful source-backed report.

Everything below the HTTP layer is real: real steps claimed from `run_steps`,
real repositories, real database, real validation gate. Only the providers are
fixtures, because `OPEN-04..09` name none yet.

The cases that matter most are the ones where the run does not go well. A report
that is complete when the research was complete is the easy half; the half worth
testing is what the system says when an area could not be researched, when a
question cannot be answered, and when nothing could be found at all.
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID

import pytest
from pipeline_support import (
    HIRING_TEXT,
    REVENUE_TEXT,
    fixture_registry,
    run_pipeline,
    scripted_provider,
    search_tool,
)
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from scrapr_core.db.enums import (
    ActivityStatus,
    ClaimType,
    ResearchStatus,
)
from scrapr_core.db.models import (
    Base,
    Claim,
    Evidence,
    QuestionState,
    ReportSection,
    ResearchQuestion,
    ResearchSession,
    Source,
)
from scrapr_core.db.repositories import (
    ActivityRepository,
    AnonymousSessionRepository,
    ResearchRepository,
    RunRepository,
)
from scrapr_core.domain.ownership import OwnerContext
from scrapr_core.llm import FakeLLMProvider
from scrapr_core.orchestrator.pipeline import STAGES
from scrapr_core.synthesis import validate_version
from scrapr_core.tools import ToolCategory, ToolRegistry
from scrapr_core.tools.impl import FailingFixtureTool

pytestmark = pytest.mark.integration

OBJECTIVE = "How is Acme Corp performing, and is it hiring?"
QUESTION_REVENUE = "What is Acme's revenue?"
QUESTION_HIRING = "How many roles is Acme hiring for?"

AREAS = (
    ("Financial performance", (QUESTION_REVENUE,), ("web_search",)),
    ("Hiring", (QUESTION_HIRING,), ("news",)),
)


def provider_for() -> FakeLLMProvider:
    return scripted_provider("Acme Corp", (QUESTION_REVENUE, QUESTION_HIRING), AREAS)


@pytest.fixture
def registry() -> ToolRegistry:
    return fixture_registry()


@pytest.fixture
def session_factory(migrated_engine: Engine) -> sessionmaker[Session]:
    """Real sessions: the runner commits between claiming and executing."""
    return sessionmaker(bind=migrated_engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
def _empty_afterwards(session_factory: sessionmaker[Session]) -> Iterator[None]:
    yield
    tables = ", ".join(sorted(Base.metadata.tables))
    with session_factory() as session:
        session.execute(text(f"TRUNCATE {tables} CASCADE"))
        session.commit()


def start_research(session_factory: sessionmaker[Session]) -> UUID:
    """Everything `POST /v1/research` does, minus the HTTP."""
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


# --------------------------------------------------------------------------
# The exit condition
# --------------------------------------------------------------------------


async def test_one_query_becomes_a_source_backed_report(
    session_factory: sessionmaker[Session], registry: ToolRegistry
) -> None:
    session_id = start_research(session_factory)
    provider = provider_for()

    await run_pipeline(session_factory, registry, provider)

    with session_factory() as session:
        research = session.get(ResearchSession, session_id)
        assert research is not None
        # `REQ-AGENT-001 AC-2`: the subject the header shows.
        assert research.subject == "Acme Corp"

        version_id = research.current_version_id
        assert version_id is not None

        sections = (
            session.execute(
                select(ReportSection)
                .where(ReportSection.version_id == version_id)
                .order_by(ReportSection.ordering)
            )
            .scalars()
            .all()
        )
        assert sections[0].is_executive_summary

        claims = (
            session.execute(select(Claim).where(Claim.version_id == version_id))
            .scalars()
            .all()
        )
        assert claims
        assert all(claim.section_id is not None for claim in claims)

        # The invariant the product exists for.
        assert validate_version(session, version_id).passed


async def test_the_plan_is_persisted_as_questions(
    session_factory: sessionmaker[Session], registry: ToolRegistry
) -> None:
    """`DEC-04 §3.1`. Termination is defined over these rows, so they have to
    outlive the process that planned them."""
    start_research(session_factory)

    await run_pipeline(session_factory, registry, provider_for())

    with session_factory() as session:
        questions = (
            session.execute(select(ResearchQuestion).order_by(ResearchQuestion.ordering))
            .scalars()
            .all()
        )

    assert [q.text for q in questions] == [QUESTION_REVENUE, QUESTION_HIRING]
    assert {q.area_name for q in questions} == {"Financial performance", "Hiring"}
    assert all(q.tool_categories for q in questions)


async def test_evidence_is_tied_to_sources_and_questions(
    session_factory: sessionmaker[Session], registry: ToolRegistry
) -> None:
    """`REQ-EVID-001 AC-2`: no evidence without a source."""
    start_research(session_factory)

    await run_pipeline(session_factory, registry, provider_for())

    with session_factory() as session:
        evidence = session.execute(select(Evidence)).scalars().all()
        sources = session.execute(select(Source)).scalars().all()

    assert evidence
    source_ids = {source.id for source in sources}
    assert all(row.source_id in source_ids for row in evidence)
    assert all(source.retrieved_at.tzinfo is not None for source in sources)


async def test_activity_narrates_the_stages_as_they_happen(
    session_factory: sessionmaker[Session], registry: ToolRegistry
) -> None:
    """`REQ-ACT-001`, `REQ-ACT-002`: plain language, in order, with status."""
    session_id = start_research(session_factory)

    await run_pipeline(session_factory, registry, provider_for())

    with session_factory() as session:
        events = ActivityRepository(session).since(session_id)

    labels = [event.label for event in events]
    assert "Understanding the objective" in labels
    assert "Identifying research areas" in labels
    assert "Building the report" in labels
    assert any("Researching financial performance" == label for label in labels)
    assert events[-1].status is ActivityStatus.COMPLETE
    assert [event.seq for event in events] == sorted(event.seq for event in events)


async def test_activity_records_the_category_not_the_tool(
    session_factory: sessionmaker[Session], registry: ToolRegistry
) -> None:
    """`REQ-ACT-003`, `REQ-AGENT-003 AC-3`. A provider name in the timeline is
    an internal detail leaking into a user-facing surface."""
    session_id = start_research(session_factory)

    await run_pipeline(session_factory, registry, provider_for())

    with session_factory() as session:
        events = ActivityRepository(session).since(session_id)

    categories = {event.tool_category for event in events if event.tool_category}
    assert categories <= {c.value for c in ToolCategory}
    assert not any("fixture" in (event.label or "") for event in events)


async def test_the_run_completes_and_the_version_closes(
    session_factory: sessionmaker[Session], registry: ToolRegistry
) -> None:
    session_id = start_research(session_factory)

    await run_pipeline(session_factory, registry, provider_for())

    with session_factory() as session:
        research = session.get(ResearchSession, session_id)
        assert research is not None
        assert research.status in {ResearchStatus.COMPLETE, ResearchStatus.PARTIAL}


# --------------------------------------------------------------------------
# When it does not go well
# --------------------------------------------------------------------------


async def test_a_dead_category_leaves_the_other_area_intact(
    session_factory: sessionmaker[Session]
) -> None:
    """`REQ-AGENT-009 AC-1`. One dead provider is not a failed run."""
    registry = ToolRegistry()
    registry.register(search_tool(texts=(REVENUE_TEXT, HIRING_TEXT)))
    registry.register(
        FailingFixtureTool(name="broken_news", category=ToolCategory.NEWS)
    )
    registry.freeze()
    start_research(session_factory)

    await run_pipeline(session_factory, registry, provider_for())

    with session_factory() as session:
        evidence = session.execute(select(Evidence)).scalars().all()
        questions = session.execute(select(ResearchQuestion)).scalars().all()

    # The financial area still produced evidence.
    assert evidence
    # And the hiring question is recorded as unanswerable rather than left to
    # look like nobody asked (`DEC-04 §3.3`).
    hiring = next(q for q in questions if q.text == QUESTION_HIRING)
    assert hiring.resolution_state is QuestionState.UNANSWERABLE
    assert hiring.unanswerable_reason is not None


async def test_an_unanswerable_question_is_stated_in_the_report(
    session_factory: sessionmaker[Session]
) -> None:
    """`REQ-SYNTH-010`, `DEC-04 §6.3`, and the gate rule that enforces it: the
    report says what could not be established rather than omitting it."""
    registry = ToolRegistry()
    registry.register(search_tool(texts=(REVENUE_TEXT, HIRING_TEXT)))
    registry.register(
        FailingFixtureTool(name="broken_news", category=ToolCategory.NEWS)
    )
    registry.freeze()
    session_id = start_research(session_factory)

    await run_pipeline(session_factory, registry, provider_for())

    with session_factory() as session:
        research = session.get(ResearchSession, session_id)
        assert research is not None
        version_id = research.current_version_id
        assert version_id is not None

        claims = (
            session.execute(select(Claim).where(Claim.version_id == version_id))
            .scalars()
            .all()
        )
        uncertainties = [c for c in claims if c.claim_type is ClaimType.UNCERTAINTY]

        assert any(QUESTION_HIRING in claim.text for claim in uncertainties)
        assert validate_version(session, version_id).passed


async def test_a_version_with_gaps_closes_as_partial(
    session_factory: sessionmaker[Session]
) -> None:
    """`REQ-AGENT-009 AC-4`: research that did not cover everything must not
    close as though it had."""
    registry = ToolRegistry()
    registry.register(search_tool(texts=(REVENUE_TEXT, HIRING_TEXT)))
    registry.register(
        FailingFixtureTool(name="broken_news", category=ToolCategory.NEWS)
    )
    registry.freeze()
    session_id = start_research(session_factory)

    await run_pipeline(session_factory, registry, provider_for())

    with session_factory() as session:
        research = session.get(ResearchSession, session_id)
        assert research is not None
        version = research.current_version_id
        assert version is not None
        status = session.execute(
            select(ResearchSession.status).where(ResearchSession.id == session_id)
        ).scalar_one()

    assert status is ResearchStatus.PARTIAL


async def test_a_failed_area_is_named_in_the_report(
    session_factory: sessionmaker[Session]
) -> None:
    """`REQ-AGENT-009 AC-2`: the report states which areas could not be
    researched. In the report, not in a log line nobody reads."""
    registry = ToolRegistry()
    registry.register(search_tool(texts=(REVENUE_TEXT, HIRING_TEXT)))
    registry.register(
        FailingFixtureTool(name="broken_news", category=ToolCategory.NEWS)
    )
    registry.freeze()
    session_id = start_research(session_factory)

    await run_pipeline(session_factory, registry, provider_for())

    with session_factory() as session:
        research = session.get(ResearchSession, session_id)
        assert research is not None
        version_id = research.current_version_id
        assert version_id is not None

        claims = (
            session.execute(select(Claim).where(Claim.version_id == version_id))
            .scalars()
            .all()
        )
        sections = (
            session.execute(
                select(ReportSection)
                .where(ReportSection.version_id == version_id)
                .order_by(ReportSection.ordering)
            )
            .scalars()
            .all()
        )

    named = [
        claim.text
        for claim in claims
        if claim.claim_type is ClaimType.UNCERTAINTY and "Hiring" in claim.text
    ]
    assert named, f"no claim names the failed area: {[c.text for c in claims]}"

    # It belongs to the summary, because learning halfway down that an area was
    # never covered is learning it too late (`AC-4`).
    summary = sections[0]
    assert summary.is_executive_summary
    summary_claims = {
        claim.id for claim in claims if claim.section_id == summary.id
    }
    gap_claim = next(c for c in claims if c.text in named)
    assert gap_claim.id in summary_claims
    assert gap_claim.is_important
