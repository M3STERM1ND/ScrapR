"""Deletion and the purge sweep (`REQ-SEC-008`, `DEC-17`, `DEC-18`).

Real commits, because the property under test is what is left in the database
after the sweep's own transactions — a rolled-back session would prove nothing
about rows another transaction can see.

The headline test runs the full pipeline first, so the session being purged has
every kind of row the schema can hold beneath it: questions, sources, evidence,
claims, sections, activity, runs and steps. A purge tested against a session
with one version and nothing else would pass while leaving the rest behind.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from uuid import UUID

import pytest
from pipeline_support import fixture_registry, run_pipeline, scripted_provider
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from scrapr_core.db.base import utcnow
from scrapr_core.db.enums import (
    ExportFormat,
    ExportStatus,
    ExportTheme,
    RunStatus,
    StepStatus,
    UploadState,
)
from scrapr_core.db.models import (
    AnonymousSession,
    Base,
    Export,
    ResearchRun,
    ResearchSession,
    RunStep,
    Upload,
    UserSession,
)
from scrapr_core.db.repositories import (
    AccountRepository,
    AnonymousSessionRepository,
    ResearchRepository,
    RunRepository,
)
from scrapr_core.db.repositories.anonymous_sessions import ANONYMOUS_LIFETIME
from scrapr_core.domain.ownership import OwnerContext
from scrapr_core.lifecycle import PURGE_GRACE, delete_research, purge
from scrapr_core.orchestrator.pipeline import STAGES

pytestmark = pytest.mark.integration

AREAS = (("Financial performance", ("What is Acme's revenue?",), ("web_search",)),)

# Tables that must hold nothing of a purged session. Identity tables are
# excluded: the anonymous owner outlives its research until it lapses.
RESEARCH_TABLES = sorted(
    name
    for name in Base.metadata.tables
    if name
    not in {"users", "anonymous_sessions", "user_sessions", "rate_limit_counters"}
)


class RecordingStore:
    """Stands in for object storage, remembering what it was asked to delete."""

    def __init__(self, failing: bool = False) -> None:
        self.deleted: list[str] = []
        self.failing = failing

    def delete(self, storage_key: str) -> None:
        if self.failing:
            raise RuntimeError("storage unreachable")
        self.deleted.append(storage_key)


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


def start(session_factory: sessionmaker[Session], objective: str = "How is Acme Corp doing?") -> tuple[UUID, UUID]:
    """A research session with a queued run. Returns (anonymous id, session id)."""
    with session_factory() as session:
        anonymous = AnonymousSessionRepository(session).issue().session
        research = ResearchRepository(session, OwnerContext.for_anonymous(anonymous.id))
        created = research.create_session(objective=objective)
        version = research.open_version(created.id)
        assert version is not None
        RunRepository(session).create_run(created.id, version.id, STAGES)
        session.commit()
        return anonymous.id, created.id


def attach_files(session_factory: sessionmaker[Session], research_id: UUID) -> list[str]:
    """One upload and one export artifact, so storage has something to lose."""
    with session_factory() as session:
        research = session.get(ResearchSession, research_id)
        assert research is not None and research.current_version_id is not None
        session.add(
            Upload(
                session_id=research_id,
                filename="notes.txt",
                content_type="text/plain",
                size_bytes=10,
                storage_key=f"uploads/{research_id}/a.txt",
                sha256="",
                processing_state=UploadState.READY,
            )
        )
        session.add(
            Export(
                version_id=research.current_version_id,
                format=ExportFormat.PDF,
                theme=ExportTheme.MINIMAL,
                status=ExportStatus.READY,
                storage_key=f"exports/{research_id}/b.pdf",
            )
        )
        session.commit()
    return [f"exports/{research_id}/b.pdf", f"uploads/{research_id}/a.txt"]


def rows_for(session: Session, research_id: UUID) -> dict[str, int]:
    """How many rows each research table still holds, across the whole table.

    Whole-table counts are the strict version of "rows for this session": the
    tests that use it purge the only session there is.
    """
    return {
        # Table names come from `Base.metadata`, never from input.
        name: int(session.execute(text(f"SELECT count(*) FROM {name}")).scalar_one())  # noqa: S608
        for name in RESEARCH_TABLES
    }


def delete_as_owner(
    session_factory: sessionmaker[Session],
    anonymous_id: UUID,
    research_id: UUID,
    store: RecordingStore,
    now: dt.datetime | None = None,
) -> None:
    with session_factory() as session:
        found = ResearchRepository(
            session, OwnerContext.for_anonymous(anonymous_id)
        ).get_session(research_id)
        assert found is not None
        delete_research(session, found, store, now=now)
        session.commit()


# --------------------------------------------------------------------------
# Deleting
# --------------------------------------------------------------------------


def test_deletion_hides_research_at_once(session_factory: sessionmaker[Session]) -> None:
    anonymous_id, research_id = start(session_factory)

    delete_as_owner(session_factory, anonymous_id, research_id, RecordingStore())

    with session_factory() as session:
        research = ResearchRepository(session, OwnerContext.for_anonymous(anonymous_id))
        assert research.get_session(research_id) is None
        assert research.list_sessions() == []
        assert research.list_versions(research_id) == []


def test_deletion_stops_the_run(session_factory: sessionmaker[Session]) -> None:
    """No worker may pick up a step of deleted research."""
    anonymous_id, research_id = start(session_factory)

    delete_as_owner(session_factory, anonymous_id, research_id, RecordingStore())

    with session_factory() as session:
        run = session.execute(select(ResearchRun)).scalar_one()
        assert run.status is RunStatus.FAILED
        statuses = set(session.execute(select(RunStep.status)).scalars())
        assert statuses == {StepStatus.DEAD}


def test_deletion_removes_every_stored_file(session_factory: sessionmaker[Session]) -> None:
    """`REQ-SEC-008 AC-2`, `REQ-EXP-008 AC-3`: uploads and export artifacts."""
    anonymous_id, research_id = start(session_factory)
    keys = attach_files(session_factory, research_id)
    store = RecordingStore()

    delete_as_owner(session_factory, anonymous_id, research_id, store)

    assert sorted(store.deleted) == keys


def test_a_storage_failure_does_not_undo_the_deletion(
    session_factory: sessionmaker[Session],
) -> None:
    anonymous_id, research_id = start(session_factory)
    attach_files(session_factory, research_id)

    delete_as_owner(session_factory, anonymous_id, research_id, RecordingStore(failing=True))

    with session_factory() as session:
        row = session.get(ResearchSession, research_id)
        assert row is not None and row.deleted_at is not None


def test_deleting_twice_keeps_the_first_tombstone(session_factory: sessionmaker[Session]) -> None:
    anonymous_id, research_id = start(session_factory)
    first = utcnow() - dt.timedelta(minutes=5)
    delete_as_owner(session_factory, anonymous_id, research_id, RecordingStore(), now=first)

    with session_factory() as session:
        row = session.get(ResearchSession, research_id)
        assert row is not None
        delete_research(session, row, RecordingStore())
        session.commit()
        assert row.deleted_at == first


# --------------------------------------------------------------------------
# Purging
# --------------------------------------------------------------------------


async def test_the_purge_removes_every_row_of_a_complete_version(
    session_factory: sessionmaker[Session],
) -> None:
    """`REQ-SEC-008 AC-1`: versions, evidence, conversation, uploads and exports."""
    anonymous_id, research_id = start(session_factory)
    await run_pipeline(
        session_factory,
        fixture_registry(),
        scripted_provider("Acme Corp", ("What is Acme's revenue?",), AREAS),
    )
    attach_files(session_factory, research_id)

    with session_factory() as session:
        before = rows_for(session, research_id)
    # The fixture really did produce a report worth purging.
    assert before["claims"] and before["evidence"] and before["sources"]
    assert before["activity_events"] and before["run_steps"]

    deleted_at = utcnow() - PURGE_GRACE - dt.timedelta(minutes=1)
    delete_as_owner(session_factory, anonymous_id, research_id, RecordingStore(), now=deleted_at)

    store = RecordingStore()
    report = purge(session_factory, store)

    assert report.purged_sessions == 1
    with session_factory() as session:
        assert all(count == 0 for count in rows_for(session, research_id).values()), rows_for(
            session, research_id
        )
    # Objects were asked to go again before the rows that name them did.
    assert len(store.deleted) == 2


def test_the_purge_waits_out_the_grace_period(session_factory: sessionmaker[Session]) -> None:
    """A worker may still hold a step; rows stay until it cannot."""
    anonymous_id, research_id = start(session_factory)
    delete_as_owner(session_factory, anonymous_id, research_id, RecordingStore())

    report = purge(session_factory, RecordingStore())

    assert report.purged_sessions == 0
    with session_factory() as session:
        assert session.get(ResearchSession, research_id) is not None


def test_the_purge_leaves_everyone_elses_research_alone(
    session_factory: sessionmaker[Session],
) -> None:
    anonymous_id, doomed = start(session_factory, "Research the owner deletes")
    _, kept = start(session_factory, "Research somebody else keeps")
    delete_as_owner(
        session_factory,
        anonymous_id,
        doomed,
        RecordingStore(),
        now=utcnow() - PURGE_GRACE - dt.timedelta(minutes=1),
    )

    purge(session_factory, RecordingStore())

    with session_factory() as session:
        assert session.get(ResearchSession, doomed) is None
        survivor = session.get(ResearchSession, kept)
        assert survivor is not None and survivor.deleted_at is None
        assert session.execute(select(func.count(RunStep.id))).scalar_one() == len(STAGES)


def test_rows_wait_for_storage_that_refused(session_factory: sessionmaker[Session]) -> None:
    """The rows are the only record of which keys still need deleting."""
    anonymous_id, research_id = start(session_factory)
    attach_files(session_factory, research_id)
    delete_as_owner(
        session_factory,
        anonymous_id,
        research_id,
        RecordingStore(failing=True),
        now=utcnow() - PURGE_GRACE - dt.timedelta(minutes=1),
    )

    report = purge(session_factory, RecordingStore(failing=True))

    assert report.deferred_sessions == 1
    with session_factory() as session:
        assert session.get(ResearchSession, research_id) is not None

    assert purge(session_factory, RecordingStore()).purged_sessions == 1


def test_lapsed_anonymous_research_is_expired_then_purged(
    session_factory: sessionmaker[Session],
) -> None:
    """`DEC-17`: expired anonymous research reaches the same purge."""
    anonymous_id, research_id = start(session_factory)
    lapse = utcnow() + ANONYMOUS_LIFETIME + dt.timedelta(minutes=1)

    first = purge(session_factory, RecordingStore(), now=lapse)
    assert first.expired_sessions == 1
    with session_factory() as session:
        row = session.get(ResearchSession, research_id)
        assert row is not None and row.deleted_at is not None

    second = purge(session_factory, RecordingStore(), now=lapse + PURGE_GRACE + dt.timedelta(minutes=1))
    assert second.purged_sessions == 1
    assert second.anonymous_sessions_removed == 1
    with session_factory() as session:
        assert session.get(ResearchSession, research_id) is None
        assert session.get(AnonymousSession, anonymous_id) is None


def test_a_live_anonymous_session_is_left_alone(session_factory: sessionmaker[Session]) -> None:
    anonymous_id, research_id = start(session_factory)

    report = purge(session_factory, RecordingStore(), now=utcnow() + dt.timedelta(days=29))

    assert report.expired_sessions == 0
    with session_factory() as session:
        assert session.get(AnonymousSession, anonymous_id) is not None
        row = session.get(ResearchSession, research_id)
        assert row is not None and row.deleted_at is None


def test_dead_account_sessions_are_cleared(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        accounts = AccountRepository(session)
        user = accounts.create("reader@example.com", "a long enough password")
        old = accounts.issue_session(user, now=utcnow() - dt.timedelta(days=40))
        live = accounts.issue_session(user)
        session.commit()
        live_id = live.session.id
        assert old.session.id != live_id

    report = purge(session_factory, None)

    assert report.account_sessions_removed == 1
    with session_factory() as session:
        remaining = session.execute(select(UserSession.id)).scalars().all()
        assert remaining == [live_id]
