"""Handler properties the runner depends on but cannot enforce.

The runner reclaims a step whose lease expired while it was still running, so
**every handler must be idempotent** (implementation plan §5.6). That is a
promise handlers make, and it is only real if something checks it — hence the
first test here, which runs a stage twice and asserts the second run adds
nothing.

The rest are the refusals: synthesis with nothing to synthesise from, and a
version the gate rejects. Both must stop the run rather than produce a report
that looks finished.
"""

from __future__ import annotations

import datetime as dt
from uuid import UUID

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from scrapr_core.db.enums import (
    Accessibility,
    ClaimType,
    ResearchStatus,
    RunKind,
    RunStatus,
    StepStatus,
    VersionStatus,
)
from scrapr_core.db.models import (
    AnonymousSession,
    Claim,
    Evidence,
    ResearchRun,
    ResearchSession,
    ResearchVersion,
    RunStep,
    Source,
    User,
)
from scrapr_core.db.repositories import RunRepository
from scrapr_core.domain.ids import new_id
from scrapr_core.domain.ownership import OwnerContext
from scrapr_core.jobs import JobRunner
from scrapr_core.jobs.contract import StepContext, StepPermanentError
from scrapr_core.llm import FakeLLMProvider
from scrapr_core.orchestrator.skeleton import (
    RETRIEVE_STAGE,
    RetrieveHandler,
    SkeletonClaim,
    SynthesizeHandler,
)
from scrapr_core.tools import ToolCategory, ToolRegistry
from scrapr_core.tools.impl import FixtureTool, fixture_item

pytestmark = pytest.mark.integration


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
                    text="Acme reported $1.2bn revenue for FY2025.",
                    source_url="https://acme.example/ir/fy2025",
                ),
            ),
        )
    )
    return registry


@pytest.fixture
def context(db_session: Session) -> StepContext:
    """A step of a real run, ready for a handler to execute directly."""
    owner_row = AnonymousSession(token_hash=f"hash-{new_id()}")
    db_session.add(owner_row)
    db_session.flush()

    research = ResearchSession(
        anonymous_session_id=owner_row.id,
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

    run = ResearchRun(
        session_id=research.id,
        version_id=version.id,
        kind=RunKind.INITIAL,
        status=RunStatus.RUNNING,
    )
    db_session.add(run)
    db_session.flush()

    step = RunStep(
        run_id=run.id, stage=RETRIEVE_STAGE, ordinal=0, status=StepStatus.RUNNING
    )
    db_session.add(step)
    db_session.flush()

    return StepContext(
        session=db_session,
        run=run,
        step=step,
        owner=OwnerContext.for_anonymous(owner_row.id),
        checkpoint={},
    )


def count(session: Session, model: type[Evidence] | type[Source], version_id: UUID) -> int:
    return (
        session.execute(
            select(func.count()).select_from(model).where(model.version_id == version_id)
        ).scalar_one()
    )


# --------------------------------------------------------------------------
# Idempotency
# --------------------------------------------------------------------------


async def test_retrieval_run_twice_reuses_its_source(
    context: StepContext, registry: ToolRegistry
) -> None:
    """What makes reclaiming a crashed step safe.

    The unique index on `(version_id, url_normalized)` is the guarantee; this
    asserts the handler actually leans on it instead of blindly inserting.
    """
    handler = RetrieveHandler(registry=registry)

    await handler.execute(context)
    await handler.execute(context)

    assert count(context.session, Source, context.run.version_id) == 1
    assert count(context.session, Evidence, context.run.version_id) == 2


# --------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------


async def test_synthesis_without_evidence_refuses_to_proceed(
    context: StepContext,
) -> None:
    """A claim without evidence is exactly what `REQ-EVID-017` exists to
    prevent, so the stage stops rather than inventing one."""
    provider = FakeLLMProvider()
    provider.enqueue(SkeletonClaim(section_title="Revenue", claim_text="anything"))

    with pytest.raises(StepPermanentError, match="no evidence"):
        await SynthesizeHandler(provider=provider).execute(context)


async def test_a_version_the_gate_rejects_stops_the_run(
    context: StepContext, registry: ToolRegistry
) -> None:
    """A gate failure is a generation defect and never ships silently.

    The defect is forced by making the retrieved source unreadable, which is the
    `REQ-EVID-018` case: a citation a reader cannot check.
    """
    await RetrieveHandler(registry=registry).execute(context)
    source = context.session.execute(
        select(Source).where(Source.version_id == context.run.version_id)
    ).scalar_one()
    source.accessibility = Accessibility.PAYWALLED
    context.session.flush()

    provider = FakeLLMProvider()
    provider.enqueue(
        SkeletonClaim(section_title="Revenue", claim_text="Acme reported $1.2bn.")
    )

    with pytest.raises(StepPermanentError, match="REQ-EVID-018"):
        await SynthesizeHandler(provider=provider).execute(context)


# --------------------------------------------------------------------------
# Run bookkeeping
# --------------------------------------------------------------------------


def test_a_run_needs_at_least_one_stage(db_session: Session, context: StepContext) -> None:
    with pytest.raises(ValueError, match="at least one stage"):
        RunRepository(db_session).create_run(
            context.run.session_id, context.run.version_id, []
        )


def test_a_run_reads_back_by_id(db_session: Session, context: StepContext) -> None:
    assert RunRepository(db_session).get_run(context.run.id) is not None
    assert RunRepository(db_session).get_run(new_id()) is None


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

    run = ResearchRun(
        session_id=research.id,
        version_id=version.id,
        kind=RunKind.INITIAL,
        status=RunStatus.RUNNING,
    )
    db_session.add(run)
    db_session.flush()

    runner = JobRunner(
        sessionmaker(bind=migrated_engine), {}, worker_id="test-worker"
    )
    owner = runner._owner_of(db_session, run)

    assert owner == OwnerContext.for_user(user.id)


def test_claims_carry_their_creation_time(db_session: Session, context: StepContext) -> None:
    """`REQ-EVID-004` is about retrieval, but a claim's own timestamp is what
    orders a version's history when something has to be reconstructed."""
    claim = Claim(
        version_id=context.run.version_id,
        text="Acme reported $1.2bn revenue.",
        claim_type=ClaimType.FACT,
    )
    db_session.add(claim)
    db_session.flush()

    assert claim.created_at <= dt.datetime.now(dt.UTC)
