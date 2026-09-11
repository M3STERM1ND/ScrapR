"""The Phase 0 exit condition, executed.

> A session and a version owned by an anonymous session. The local runner picks
> up `run_steps` and executes a two-stage no-op pipeline against a fixture tool
> and a fake LLM. One report section containing one evidence-backed, fact-typed
> claim persists, with its source and retrieval timestamp. The validation gate
> passes it. The version reads back, with the activity list showing the two
> stages.

Everything except the HTTP surface and the browser is here, which is the point:
the seams are what Phase 0 is for, and they are all crossed below.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from uuid import UUID

import pytest
from sqlalchemy import Engine, Select, select, text
from sqlalchemy.orm import Session, sessionmaker

from scrapr_core.db.enums import (
    ActivityStatus,
    ClaimType,
    RunStatus,
    StepStatus,
    VersionStatus,
)
from scrapr_core.db.models import (
    Base,
    Claim,
    Evidence,
    ReportSection,
    ResearchRun,
    Source,
)
from scrapr_core.db.repositories import (
    ActivityRepository,
    AnonymousSessionRepository,
    ResearchRepository,
    RunRepository,
)
from scrapr_core.domain.ownership import OwnerContext
from scrapr_core.jobs import JobRunner
from scrapr_core.llm import FakeLLMProvider
from scrapr_core.orchestrator.skeleton import (
    RETRIEVE_STAGE,
    SYNTHESIZE_STAGE,
    RetrieveHandler,
    SkeletonClaim,
    SynthesizeHandler,
)
from scrapr_core.synthesis import validate_version
from scrapr_core.tools import ToolCategory, ToolRegistry
from scrapr_core.tools.impl import FailingFixtureTool, FixtureTool, fixture_item

pytestmark = pytest.mark.integration

OBJECTIVE = "How is Acme Corp positioned against its competitors?"
RETRIEVED_TEXT = "Acme reported $1.2bn revenue for FY2025, up 18% year over year."
STAGES = (RETRIEVE_STAGE, SYNTHESIZE_STAGE)


@pytest.fixture
def session_factory(migrated_engine: Engine) -> sessionmaker[Session]:
    """Real sessions, not the rolled-back one.

    The runner commits between claiming and executing — that separation is the
    whole mechanism — so this suite cleans up after itself instead.
    """
    return sessionmaker(bind=migrated_engine, expire_on_commit=False)


@pytest.fixture
def registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        FixtureTool(
            name="fixture_search",
            category=ToolCategory.WEB_SEARCH,
            items=(
                fixture_item(
                    source_name="Acme FY2025 results",
                    text=RETRIEVED_TEXT,
                    source_url="https://acme.example/ir/fy2025",
                    published_at=dt.datetime(2026, 2, 1, tzinfo=dt.UTC),
                ),
            ),
        )
    )
    registry.freeze()
    return registry


@pytest.fixture
def provider() -> FakeLLMProvider:
    provider = FakeLLMProvider()
    provider.enqueue(
        SkeletonClaim(
            section_title="Revenue",
            claim_text="Acme reported $1.2bn revenue for FY2025.",
        )
    )
    return provider


def start_research(session_factory: sessionmaker[Session]) -> tuple[OwnerContext, str]:
    """Everything `POST /v1/research` will do, minus the HTTP."""
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
        return owner, str(created.id)


def make_runner(
    session_factory: sessionmaker[Session],
    registry: ToolRegistry,
    provider: FakeLLMProvider,
) -> JobRunner:
    return JobRunner(
        session_factory,
        {
            RETRIEVE_STAGE: RetrieveHandler(registry=registry),
            SYNTHESIZE_STAGE: SynthesizeHandler(provider=provider),
        },
        worker_id="test-worker",
    )


@pytest.fixture(autouse=True)
def _empty_afterwards(session_factory: sessionmaker[Session]) -> Iterator[None]:
    """Truncate everything once the test is done.

    The transaction-rollback fixture the other suites use cannot serve here: the
    runner commits between claiming a step and executing it, and that separation
    is the mechanism under test. `CASCADE` is what makes one statement enough
    despite the foreign keys, including the deliberate cycle between
    `research_sessions` and `research_versions`.
    """
    yield
    tables = ", ".join(sorted(Base.metadata.tables))
    with session_factory() as session:
        session.execute(text(f"TRUNCATE {tables} CASCADE"))
        session.commit()


# --------------------------------------------------------------------------
# The exit condition
# --------------------------------------------------------------------------


async def test_the_walking_skeleton_runs_end_to_end(
    session_factory: sessionmaker[Session],
    registry: ToolRegistry,
    provider: FakeLLMProvider,
) -> None:
    owner, session_id = start_research(session_factory)

    executed = await make_runner(session_factory, registry, provider).run_until_idle()

    assert executed == 2
    with session_factory() as session:
        research = ResearchRepository(session, owner)
        version = research.get_version(_uuid(session_id), 1)
        assert version is not None
        assert version.status is VersionStatus.COMPLETE
        assert version.closed_at is not None

        source = session.execute(
            select(Source).where(Source.version_id == version.id)
        ).scalar_one()
        assert source.url == "https://acme.example/ir/fy2025"
        assert source.retrieved_at.tzinfo is not None

        evidence = session.execute(
            select(Evidence).where(Evidence.version_id == version.id)
        ).scalar_one()
        assert evidence.content == RETRIEVED_TEXT

        section = session.execute(
            select(ReportSection).where(ReportSection.version_id == version.id)
        ).scalar_one()
        assert section.title == "Revenue"

        claim = session.execute(
            select(Claim).where(Claim.version_id == version.id)
        ).scalar_one()
        assert claim.claim_type is ClaimType.FACT
        assert claim.section_id == section.id

        assert validate_version(session, version.id).passed


async def test_the_activity_timeline_shows_both_stages(
    session_factory: sessionmaker[Session],
    registry: ToolRegistry,
    provider: FakeLLMProvider,
) -> None:
    """`REQ-ACT-001`, and the shape the Phase 1 poll reads: `?after={seq}`."""
    _, session_id = start_research(session_factory)

    await make_runner(session_factory, registry, provider).run_until_idle()

    with session_factory() as session:
        events = ActivityRepository(session).since(_uuid(session_id))

    labels = [(event.label, event.status) for event in events]
    assert labels == [
        ("Searching for information", ActivityStatus.IN_PROGRESS),
        ("Searching for information", ActivityStatus.COMPLETE),
        ("Building the report", ActivityStatus.IN_PROGRESS),
        ("Building the report", ActivityStatus.COMPLETE),
    ]
    assert [event.seq for event in events] == [1, 2, 3, 4]


async def test_polling_after_a_sequence_number_returns_only_newer_events(
    session_factory: sessionmaker[Session],
    registry: ToolRegistry,
    provider: FakeLLMProvider,
) -> None:
    _, session_id = start_research(session_factory)
    await make_runner(session_factory, registry, provider).run_until_idle()

    with session_factory() as session:
        later = ActivityRepository(session).since(_uuid(session_id), after=2)

    assert [event.seq for event in later] == [3, 4]


async def test_no_runnable_steps_means_the_runner_idles(
    session_factory: sessionmaker[Session],
    registry: ToolRegistry,
    provider: FakeLLMProvider,
) -> None:
    runner = make_runner(session_factory, registry, provider)

    assert await runner.run_one() is False


# --------------------------------------------------------------------------
# Failure behaviour
# --------------------------------------------------------------------------


async def test_a_failing_tool_stops_the_run_visibly(
    session_factory: sessionmaker[Session], provider: FakeLLMProvider
) -> None:
    """`REQ-AGENT-009 AC-3`: a run that could not do its work must not be
    presented as complete."""
    registry = ToolRegistry()
    registry.register(
        FailingFixtureTool(name="broken", category=ToolCategory.WEB_SEARCH)
    )
    owner, session_id = start_research(session_factory)

    await make_runner(session_factory, registry, provider).run_until_idle()

    with session_factory() as session:
        run = session.execute(_runs_for(session_id)).scalar_one()
        assert run.status is RunStatus.FAILED
        assert run.termination_reason is not None

        version = ResearchRepository(session, owner).get_version(_uuid(session_id), 1)
        assert version is not None
        assert version.status is VersionStatus.FAILED


async def test_a_missing_handler_poisons_its_step_immediately(
    session_factory: sessionmaker[Session],
) -> None:
    """A stage nothing can execute is a wiring error: retrying it twice more
    only delays the same answer."""
    _, session_id = start_research(session_factory)
    runner = JobRunner(session_factory, {}, worker_id="test-worker")

    await runner.run_until_idle()

    with session_factory() as session:
        run = session.execute(_runs_for(session_id)).scalar_one()
        steps = RunRepository(session).steps(run.id)

    assert steps[0].status is StepStatus.DEAD
    assert steps[0].attempt == 1
    assert "no handler registered" in (steps[0].error or "")


async def test_a_transient_failure_is_retried_then_poisoned(
    session_factory: sessionmaker[Session], registry: ToolRegistry
) -> None:
    """Two retries cover a provider blip. A third failure is a defect, and the
    step stops rather than cycling (implementation plan §5.6)."""
    attempts = 0

    class Flaky:
        async def execute(self, context: object) -> dict[str, str]:
            nonlocal attempts
            attempts += 1
            raise RuntimeError("upstream hiccup")

    _, session_id = start_research(session_factory)
    runner = JobRunner(
        session_factory, {RETRIEVE_STAGE: Flaky()}, worker_id="test-worker"
    )

    await runner.run_until_idle()

    assert attempts == 3
    with session_factory() as session:
        run = session.execute(_runs_for(session_id)).scalar_one()
        steps = RunRepository(session).steps(run.id)

    assert steps[0].status is StepStatus.DEAD
    assert steps[0].attempt == 3
    # The second stage never ran: steps of one run execute in order.
    assert steps[1].status is StepStatus.PENDING


async def test_a_step_checkpoints_before_it_completes(
    session_factory: sessionmaker[Session],
    registry: ToolRegistry,
    provider: FakeLLMProvider,
) -> None:
    """The checkpoint is what a reclaimed step resumes from, so it has to be
    what the handler actually produced (`NFR-REL-001`)."""
    _, session_id = start_research(session_factory)

    await make_runner(session_factory, registry, provider).run_until_idle()

    with session_factory() as session:
        run = session.execute(_runs_for(session_id)).scalar_one()
        steps = RunRepository(session).steps(run.id)

    assert steps[0].checkpoint["tool"] == "fixture_search"
    assert len(steps[0].checkpoint["evidence_ids"]) == 1
    assert "claim_id" in steps[1].checkpoint
    assert steps[0].lease_owner is None


async def test_an_expired_lease_makes_a_step_claimable_again(
    session_factory: sessionmaker[Session],
    registry: ToolRegistry,
    provider: FakeLLMProvider,
) -> None:
    """A worker that dies mid-step must not strand the run. Idempotency is what
    makes reclaiming safe, which is why every handler owes it."""
    _, session_id = start_research(session_factory)

    with session_factory() as session:
        run = session.execute(_runs_for(session_id)).scalar_one()
        step = RunRepository(session).steps(run.id)[0]
        step.status = StepStatus.RUNNING
        step.lease_owner = "worker-that-died"
        step.lease_expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)
        session.commit()

    executed = await make_runner(session_factory, registry, provider).run_until_idle()

    assert executed == 2
    with session_factory() as session:
        steps = RunRepository(session).steps(run.id)
    assert [step.status for step in steps] == [StepStatus.COMPLETE, StepStatus.COMPLETE]


async def test_a_live_lease_is_left_alone(
    session_factory: sessionmaker[Session],
    registry: ToolRegistry,
    provider: FakeLLMProvider,
) -> None:
    """The other half of the same rule: a step someone is working on is not
    stolen, or two workers would duplicate its side effects."""
    _, session_id = start_research(session_factory)

    with session_factory() as session:
        run = session.execute(_runs_for(session_id)).scalar_one()
        step = RunRepository(session).steps(run.id)[0]
        step.status = StepStatus.RUNNING
        step.lease_owner = "another-worker"
        step.lease_expires_at = dt.datetime.now(dt.UTC) + dt.timedelta(minutes=5)
        session.commit()

    executed = await make_runner(session_factory, registry, provider).run_until_idle()

    assert executed == 0


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _uuid(value: str) -> UUID:
    return UUID(value)


def _runs_for(session_id: str) -> Select[tuple[ResearchRun]]:
    return select(ResearchRun).where(ResearchRun.session_id == _uuid(session_id))
