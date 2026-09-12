"""The durable step machine's own behaviour (§5.6).

Tested against trivial handlers rather than the real pipeline, because these are
properties of the runner: leases, retries, ordering, checkpoints and what a
poisoned step does to a run. Using real stages here would make a retry test fail
whenever synthesis changed, which is how a suite stops being read.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from uuid import UUID

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from scrapr_core.db.enums import (
    ResearchStatus,
    RunStatus,
    StepStatus,
    VersionStatus,
)
from scrapr_core.db.models import (
    Base,
    ResearchRun,
    ResearchSession,
    ResearchVersion,
    User,
)
from scrapr_core.db.repositories import (
    AnonymousSessionRepository,
    ResearchRepository,
    RunRepository,
)
from scrapr_core.domain.ids import new_id
from scrapr_core.domain.json import JsonMapping
from scrapr_core.domain.ownership import OwnerContext
from scrapr_core.jobs import JobRunner
from scrapr_core.jobs.contract import StepContext, StepPermanentError

pytestmark = pytest.mark.integration

FIRST = "first"
SECOND = "second"
STAGES = (FIRST, SECOND)


class Recording:
    """A handler that records what it did and returns a checkpoint."""

    def __init__(self, note: str = "done") -> None:
        self.note = note
        self.calls = 0

    async def execute(self, context: StepContext) -> JsonMapping:
        self.calls += 1
        return {"note": self.note, "attempt": context.step.attempt}


class Failing:
    """A handler that always raises an ordinary error, so the runner retries."""

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, context: StepContext) -> JsonMapping:
        self.calls += 1
        raise RuntimeError("upstream hiccup")


class Refusing:
    """A handler that raises the permanent kind, which must not be retried."""

    async def execute(self, context: StepContext) -> JsonMapping:
        raise StepPermanentError("this input can never work")


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


def start(session_factory: sessionmaker[Session]) -> tuple[UUID, UUID]:
    """A research session with a two-step run queued. Returns session and run."""
    with session_factory() as session:
        owner = OwnerContext.for_anonymous(
            AnonymousSessionRepository(session).issue().session.id
        )
        research = ResearchRepository(session, owner)
        created = research.create_session(objective="Acme competitive position")
        version = research.open_version(created.id)
        assert version is not None
        run = RunRepository(session).create_run(created.id, version.id, STAGES)
        session.commit()
        return created.id, run.id


def runner(
    session_factory: sessionmaker[Session], **handlers: object
) -> JobRunner:
    return JobRunner(
        session_factory,
        handlers,  # type: ignore[arg-type]
        worker_id="runner-test",
    )


def steps(session_factory: sessionmaker[Session], run_id: UUID):  # type: ignore[no-untyped-def]
    with session_factory() as session:
        return RunRepository(session).steps(run_id)


# --------------------------------------------------------------------------
# Ordering and completion
# --------------------------------------------------------------------------


async def test_steps_run_in_order(session_factory: sessionmaker[Session]) -> None:
    _, run_id = start(session_factory)
    first, second = Recording("one"), Recording("two")

    await runner(session_factory, first=first, second=second).run_until_idle()

    recorded = steps(session_factory, run_id)
    assert [step.status for step in recorded] == [
        StepStatus.COMPLETE,
        StepStatus.COMPLETE,
    ]
    assert [step.checkpoint["note"] for step in recorded] == ["one", "two"]


async def test_a_completed_run_closes_its_version(
    session_factory: sessionmaker[Session]
) -> None:
    session_id, run_id = start(session_factory)

    await runner(
        session_factory, first=Recording(), second=Recording()
    ).run_until_idle()

    with session_factory() as session:
        run = session.get(ResearchRun, run_id)
        assert run is not None
        version = session.get(ResearchVersion, run.version_id)
        research = session.get(ResearchSession, session_id)

    assert run.status is RunStatus.COMPLETE
    assert version is not None and version.status is VersionStatus.COMPLETE
    assert version.closed_at is not None
    assert research is not None and research.status is ResearchStatus.COMPLETE


async def test_a_handler_that_marked_the_version_partial_is_not_overruled(
    session_factory: sessionmaker[Session]
) -> None:
    """A handler that judged the research incomplete knows something the step
    machine does not: every step ran, and the result is still short
    (`REQ-AGENT-009`)."""

    class MarksPartial:
        async def execute(self, context: StepContext) -> JsonMapping:
            version = context.session.get(ResearchVersion, context.run.version_id)
            assert version is not None
            version.status = VersionStatus.PARTIAL
            context.session.flush()
            return {}

    session_id, run_id = start(session_factory)

    await runner(
        session_factory, first=Recording(), second=MarksPartial()
    ).run_until_idle()

    with session_factory() as session:
        run = session.get(ResearchRun, run_id)
        version = session.get(ResearchVersion, run.version_id) if run else None
        research = session.get(ResearchSession, session_id)

    assert version is not None and version.status is VersionStatus.PARTIAL
    assert run is not None and run.status is RunStatus.PARTIAL
    assert research is not None and research.status is ResearchStatus.PARTIAL


async def test_a_run_in_flight_reads_as_running(
    session_factory: sessionmaker[Session]
) -> None:
    session_id, _ = start(session_factory)

    await runner(
        session_factory, first=Recording(), second=Recording()
    ).run_one()

    with session_factory() as session:
        research = session.get(ResearchSession, session_id)

    assert research is not None and research.status is ResearchStatus.RUNNING


async def test_an_idle_runner_finds_nothing(
    session_factory: sessionmaker[Session]
) -> None:
    assert await runner(session_factory, first=Recording()).run_one() is False


# --------------------------------------------------------------------------
# Failure
# --------------------------------------------------------------------------


async def test_a_transient_failure_is_retried_then_poisoned(
    session_factory: sessionmaker[Session]
) -> None:
    """Two retries cover a provider blip. A third failure is a defect, and the
    step stops rather than cycling (§5.6)."""
    _, run_id = start(session_factory)
    flaky = Failing()

    await runner(session_factory, first=flaky, second=Recording()).run_until_idle()

    recorded = steps(session_factory, run_id)
    assert flaky.calls == 3
    assert recorded[0].status is StepStatus.DEAD
    assert recorded[0].attempt == 3
    # The second step never ran: steps of one run execute in order.
    assert recorded[1].status is StepStatus.PENDING


async def test_a_permanent_failure_is_not_retried(
    session_factory: sessionmaker[Session]
) -> None:
    """Burning two more attempts on an input that can never work only delays
    the same answer."""
    _, run_id = start(session_factory)

    await runner(
        session_factory, first=Refusing(), second=Recording()
    ).run_until_idle()

    recorded = steps(session_factory, run_id)
    assert recorded[0].status is StepStatus.DEAD
    assert recorded[0].attempt == 1
    assert "never work" in (recorded[0].error or "")


async def test_a_poisoned_step_fails_the_run_visibly(
    session_factory: sessionmaker[Session]
) -> None:
    """`REQ-AGENT-009 AC-3`: never an empty report presented as complete."""
    session_id, run_id = start(session_factory)

    await runner(
        session_factory, first=Refusing(), second=Recording()
    ).run_until_idle()

    with session_factory() as session:
        run = session.get(ResearchRun, run_id)
        research = session.get(ResearchSession, session_id)
        version = session.get(ResearchVersion, run.version_id) if run else None

    assert run is not None and run.status is RunStatus.FAILED
    assert run.termination_reason is not None
    assert version is not None and version.status is VersionStatus.FAILED
    assert research is not None and research.status is ResearchStatus.FAILED


async def test_a_missing_handler_poisons_its_step_immediately(
    session_factory: sessionmaker[Session]
) -> None:
    """A stage nothing can execute is a wiring error, not bad luck."""
    _, run_id = start(session_factory)

    await runner(session_factory).run_until_idle()

    recorded = steps(session_factory, run_id)
    assert recorded[0].status is StepStatus.DEAD
    assert recorded[0].attempt == 1
    assert "no handler registered" in (recorded[0].error or "")


# --------------------------------------------------------------------------
# Leases
# --------------------------------------------------------------------------


async def test_an_expired_lease_makes_a_step_claimable_again(
    session_factory: sessionmaker[Session]
) -> None:
    """A worker that dies mid-step must not strand the run. Idempotency is what
    makes reclaiming safe, which is why every handler owes it."""
    _, run_id = start(session_factory)

    with session_factory() as session:
        step = RunRepository(session).steps(run_id)[0]
        step.status = StepStatus.RUNNING
        step.lease_owner = "worker-that-died"
        step.lease_expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)
        session.commit()

    executed = await runner(
        session_factory, first=Recording(), second=Recording()
    ).run_until_idle()

    assert executed == 2
    assert all(step.status is StepStatus.COMPLETE for step in steps(session_factory, run_id))


async def test_a_live_lease_is_left_alone(
    session_factory: sessionmaker[Session]
) -> None:
    """The other half of the rule: a step someone is working on is not stolen,
    or two workers would duplicate its side effects."""
    _, run_id = start(session_factory)

    with session_factory() as session:
        step = RunRepository(session).steps(run_id)[0]
        step.status = StepStatus.RUNNING
        step.lease_owner = "another-worker"
        step.lease_expires_at = dt.datetime.now(dt.UTC) + dt.timedelta(minutes=5)
        session.commit()

    executed = await runner(
        session_factory, first=Recording(), second=Recording()
    ).run_until_idle()

    assert executed == 0


async def test_a_completed_step_releases_its_lease(
    session_factory: sessionmaker[Session]
) -> None:
    _, run_id = start(session_factory)

    await runner(
        session_factory, first=Recording(), second=Recording()
    ).run_until_idle()

    for step in steps(session_factory, run_id):
        assert step.lease_owner is None
        assert step.lease_expires_at is None


# --------------------------------------------------------------------------
# Ownership
# --------------------------------------------------------------------------


async def test_a_worker_adopts_the_owner_of_the_research_it_runs(
    migrated_engine: Engine, db_session: Session
) -> None:
    """`REQ-SEC-011`: a worker goes through the same enforcement point a request
    does, including for account-owned research once accounts exist."""
    user = User(email=f"{new_id()}@example.com")
    db_session.add(user)
    db_session.flush()

    research = ResearchSession(
        owner_user_id=user.id,
        objective="Acme competitive position",
        status=ResearchStatus.RUNNING,
    )
    db_session.add(research)
    db_session.flush()
    version = ResearchVersion(
        session_id=research.id, version_number=1, status=VersionStatus.BUILDING
    )
    db_session.add(version)
    db_session.flush()
    run = RunRepository(db_session).create_run(research.id, version.id, STAGES)

    built = JobRunner(
        sessionmaker(bind=migrated_engine), {}, worker_id="runner-test"
    )
    owner = built._owner_of(db_session, run)

    assert owner == OwnerContext.for_user(user.id)


async def test_a_run_reads_back_by_id(
    session_factory: sessionmaker[Session]
) -> None:
    _, run_id = start(session_factory)

    with session_factory() as session:
        assert RunRepository(session).get_run(run_id) is not None
        assert RunRepository(session).get_run(new_id()) is None


def test_a_run_needs_at_least_one_stage(db_session: Session) -> None:
    owner = OwnerContext.for_anonymous(
        AnonymousSessionRepository(db_session).issue().session.id
    )
    research = ResearchRepository(db_session, owner)
    created = research.create_session(objective="Acme")
    version = research.open_version(created.id)
    assert version is not None

    with pytest.raises(ValueError, match="at least one stage"):
        RunRepository(db_session).create_run(created.id, version.id, [])


def test_the_step_query_orders_by_ordinal(db_session: Session) -> None:
    owner = OwnerContext.for_anonymous(
        AnonymousSessionRepository(db_session).issue().session.id
    )
    research = ResearchRepository(db_session, owner)
    created = research.create_session(objective="Acme")
    version = research.open_version(created.id)
    assert version is not None
    run = RunRepository(db_session).create_run(
        created.id, version.id, ("a", "b", "c")
    )

    recorded = RunRepository(db_session).steps(run.id)

    assert [step.stage for step in recorded] == ["a", "b", "c"]
    assert [step.ordinal for step in recorded] == [0, 1, 2]
    assert db_session.execute(select(ResearchRun.id)).scalars().all() == [run.id]
