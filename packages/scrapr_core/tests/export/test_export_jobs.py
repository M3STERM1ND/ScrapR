"""Building a document from a real version, and the export job (`REQ-EXP-005..008`, `DEC-21`).

Integration: the pipeline runs first, against the disputing fixtures, so the
version being exported has claims, evidence, sources and a conflict. Then the
processor renders it the way the worker does, with storage replaced by a
recorder.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from uuid import UUID

import pytest
from pipeline_support import disputed_registry, disputing_provider, run_pipeline
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from scrapr_core.db.base import utcnow
from scrapr_core.db.enums import ExportFormat, ExportStatus, ExportTheme, VersionStatus
from scrapr_core.db.models import Base, Claim, Export, ResearchSession, ResearchVersion
from scrapr_core.db.repositories import (
    AnonymousSessionRepository,
    ResearchRepository,
    RunRepository,
)
from scrapr_core.db.repositories.exports import ExportRepository
from scrapr_core.domain.ownership import OwnerContext
from scrapr_core.export import ExportProcessor, ExportSourceError, build_document
from scrapr_core.export.jobs import FAILED_MESSAGE, export_key
from scrapr_core.orchestrator.pipeline import STAGES

pytestmark = pytest.mark.integration


class RecordingStore:
    def __init__(self, failing_puts: int = 0) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.deleted: list[str] = []
        self.failing_puts = failing_puts

    def put(self, storage_key: str, data: bytes, content_type: str) -> None:
        if self.failing_puts:
            self.failing_puts -= 1
            raise RuntimeError("storage unavailable")
        self.objects[storage_key] = (data, content_type)

    def delete(self, storage_key: str) -> None:
        self.deleted.append(storage_key)
        self.objects.pop(storage_key, None)


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


async def researched(session_factory: sessionmaker[Session]) -> tuple[OwnerContext, UUID, UUID]:
    """A finished version with a conflict in it. Returns (owner, session, version)."""
    with session_factory() as session:
        owner = OwnerContext.for_anonymous(AnonymousSessionRepository(session).issue().session.id)
        research = ResearchRepository(session, owner)
        created = research.create_session(objective="What is Acme Corp's revenue?")
        version = research.open_version(created.id)
        assert version is not None
        RunRepository(session).create_run(created.id, version.id, STAGES)
        session.commit()
        session_id, version_id = created.id, version.id

    from pipeline_support import cite_everything

    await run_pipeline(session_factory, disputed_registry(), disputing_provider(), synthesis=cite_everything)
    return owner, session_id, version_id


def queue(session_factory: sessionmaker[Session], owner: OwnerContext, version_id: UUID,
          export_format: ExportFormat = ExportFormat.PDF) -> UUID:
    with session_factory() as session:
        version = session.get(ResearchVersion, version_id)
        assert version is not None
        export, created = ExportRepository(session, owner).request(version, export_format, ExportTheme.INVESTOR)
        session.commit()
        assert created
        return export.id


# --------------------------------------------------------------------------
# The document — `REQ-EXP-005`
# --------------------------------------------------------------------------


async def test_the_document_holds_exactly_the_versions_claims(session_factory: sessionmaker[Session]) -> None:
    """`REQ-EXP-005 AC-1`: every claim in the export exists in the version, and
    every claim in the version is in the export."""
    _, _, version_id = await researched(session_factory)

    with session_factory() as session:
        document = build_document(session, version_id)
        stored = {claim.text for claim in session.execute(select(Claim).where(Claim.version_id == version_id)).scalars()}

    exported = {claim.text for section in document.sections for claim in section.claims}
    assert exported == stored
    assert document.version_number == 1
    assert document.references and [ref.number for ref in document.references] == list(
        range(1, len(document.references) + 1)
    )
    # Every citation marker resolves to a numbered source (`REQ-EXP-009 AC-1`).
    numbers = {ref.number for ref in document.references}
    assert all(set(claim.references) <= numbers for section in document.sections for claim in section.claims)
    # The disagreement the fixtures planted is carried (`REQ-EXP-009 AC-3`).
    assert any(claim.conflicts for section in document.sections for claim in section.claims)


async def test_an_unfinished_version_cannot_be_exported(session_factory: sessionmaker[Session]) -> None:
    _, session_id, _ = await researched(session_factory)
    with session_factory() as session:
        building = ResearchVersion(session_id=session_id, version_number=2, status=VersionStatus.BUILDING)
        session.add(building)
        session.commit()

        with pytest.raises(ExportSourceError):
            build_document(session, building.id)


# --------------------------------------------------------------------------
# The job — `REQ-EXP-006..008`, `NFR-REL-003`
# --------------------------------------------------------------------------


async def test_an_export_is_rendered_stored_and_bound_to_its_version(
    session_factory: sessionmaker[Session],
) -> None:
    owner, session_id, version_id = await researched(session_factory)
    pdf_id = queue(session_factory, owner, version_id)
    deck_id = queue(session_factory, owner, version_id, ExportFormat.PPTX)
    store = RecordingStore()
    processor = ExportProcessor(session_factory, store)

    first, second = processor.run_one(), processor.run_one()
    assert processor.run_one() is None

    assert first is not None and first.ready and second is not None and second.ready
    with session_factory() as session:
        for export_id, export_format in ((pdf_id, ExportFormat.PDF), (deck_id, ExportFormat.PPTX)):
            export = session.get(Export, export_id)
            assert export is not None
            # `REQ-EXP-006 AC-1`: format, theme, version, time, file reference.
            assert export.status is ExportStatus.READY
            assert export.version_id == version_id
            assert export.completed_at is not None and export.size_bytes
            assert export.storage_key == export_key(session_id, export_id, export_format)
            data, content_type = store.objects[export.storage_key]
            assert len(data) == export.size_bytes
            assert content_type.startswith("application/")
    assert store.objects[export_key(session_id, pdf_id, ExportFormat.PDF)][0].startswith(b"%PDF-")
    assert store.objects[export_key(session_id, deck_id, ExportFormat.PPTX)][0].startswith(b"PK")


async def test_asking_twice_returns_the_same_export(session_factory: sessionmaker[Session]) -> None:
    owner, _, version_id = await researched(session_factory)
    first = queue(session_factory, owner, version_id)
    with session_factory() as session:
        version = session.get(ResearchVersion, version_id)
        assert version is not None
        again, created = ExportRepository(session, owner).request(version, ExportFormat.PDF, ExportTheme.INVESTOR)
    assert again.id == first and not created


async def test_a_transient_failure_is_retried_then_failed_with_a_message(
    session_factory: sessionmaker[Session],
) -> None:
    """`REQ-EXP-007 AC-3`: failure surfaces, and a retry renders it without research."""
    owner, _, version_id = await researched(session_factory)
    export_id = queue(session_factory, owner, version_id)
    store = RecordingStore(failing_puts=3)
    processor = ExportProcessor(session_factory, store)

    for _ in range(3):
        outcome = processor.run_one()
        assert outcome is not None and not outcome.ready

    with session_factory() as session:
        export = session.get(Export, export_id)
        assert export is not None and export.status is ExportStatus.FAILED
        assert export.error == FAILED_MESSAGE
        ExportRepository(session, owner).retry(export)
        session.commit()

    outcome = processor.run_one()
    assert outcome is not None and outcome.ready
    with session_factory() as session:
        runs = session.execute(text("SELECT count(*) FROM research_runs")).scalar_one()
    assert runs == 1  # `NFR-REL-003`: no research re-ran


async def test_a_dead_workers_export_is_taken_over_after_its_lease(
    session_factory: sessionmaker[Session],
) -> None:
    owner, _, version_id = await researched(session_factory)
    export_id = queue(session_factory, owner, version_id)
    with session_factory() as session:
        export = session.get(Export, export_id)
        assert export is not None
        export.status = ExportStatus.RUNNING
        export.attempts = 1
        export.lease_expires_at = utcnow() + dt.timedelta(minutes=5)
        session.commit()

    processor = ExportProcessor(session_factory, RecordingStore())
    assert processor.run_one() is None  # still leased
    outcome = processor.run_one(now=utcnow() + dt.timedelta(minutes=6))
    assert outcome is not None and outcome.ready


async def test_exports_of_deleted_research_are_not_rendered(session_factory: sessionmaker[Session]) -> None:
    owner, session_id, version_id = await researched(session_factory)
    queue(session_factory, owner, version_id)
    with session_factory() as session:
        research = session.get(ResearchSession, session_id)
        assert research is not None
        research.deleted_at = utcnow()
        session.commit()

    assert ExportProcessor(session_factory, RecordingStore()).run_one() is None


async def test_an_artifact_is_removed_if_its_research_is_deleted_mid_render(
    session_factory: sessionmaker[Session],
) -> None:
    """`REQ-EXP-008 AC-3`: nothing outlives the research it came from."""
    owner, session_id, version_id = await researched(session_factory)
    export_id = queue(session_factory, owner, version_id)

    class DeletingStore(RecordingStore):
        def put(self, storage_key: str, data: bytes, content_type: str) -> None:
            super().put(storage_key, data, content_type)
            with session_factory() as session:
                research = session.get(ResearchSession, session_id)
                assert research is not None
                research.deleted_at = utcnow()
                session.commit()

    store = DeletingStore()
    outcome = ExportProcessor(session_factory, store).run_one()

    assert outcome is not None and not outcome.ready and outcome.reason == "deleted"
    assert store.objects == {}
    assert store.deleted == [export_key(session_id, export_id, ExportFormat.PDF)]


async def test_an_export_is_isolated_to_its_owner(session_factory: sessionmaker[Session]) -> None:
    """`REQ-EXP-008 AC-2`."""
    owner, _, version_id = await researched(session_factory)
    export_id = queue(session_factory, owner, version_id)
    with session_factory() as session:
        stranger = OwnerContext.for_anonymous(AnonymousSessionRepository(session).issue().session.id)
        session.commit()
        assert ExportRepository(session, stranger).get(export_id) is None
        assert ExportRepository(session, owner).get(export_id) is not None
