"""The export job, run by the worker (`REQ-EXP-007`, `REQ-EXP-008`, `DEC-21`).

Claim an export, build its document from the version, render it in its theme,
store the bytes, mark it ready. One export per call, so the worker can
interleave exports with research steps and document extraction.

**The row is the job.** `status`, `attempts` and `lease_expires_at` on
`exports` are the whole state machine: pending is claimable, running is
claimable once its lease lapses (the worker rendering it died), and three
attempts end in `failed` with a message for the reader and a retry offered.

**Nothing here can call a model** (`REQ-EXP-004`, `REQ-EXP-005 AC-2`). The
processor holds a session factory and a store; the renderers hold a document
and a theme.

**Deleted research is never rendered, and never left behind.** The claim skips
exports of deleted sessions, and if the research is deleted while an export is
rendering, the bytes just stored are removed again rather than orphaned
(`REQ-EXP-008 AC-3`).
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from dataclasses import dataclass
from typing import Final, Protocol, final
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from scrapr_core.db.base import utcnow
from scrapr_core.db.enums import ExportFormat, ExportStatus
from scrapr_core.db.models import Export, ResearchSession, ResearchVersion
from scrapr_core.export.document import ExportSourceError, build_document
from scrapr_core.export.pdf import render_pdf
from scrapr_core.export.slides import render_pptx
from scrapr_core.export.themes import theme_for

__all__ = [
    "CONTENT_TYPES",
    "EXPORT_LEASE_SECONDS",
    "EXPORT_MAX_ATTEMPTS",
    "ArtifactStore",
    "ExportProcessor",
    "ProcessedExport",
    "export_key",
]

logger = logging.getLogger(__name__)

EXPORT_LEASE_SECONDS: Final = 300
"""Five minutes, the research step lease. A render that takes longer than this
has hung; another worker takes it over."""

EXPORT_MAX_ATTEMPTS: Final = 3
"""The research step limit (`STEP_MAX_ATTEMPTS`), for the same reason: a third
failure is a defect, not bad luck."""

CONTENT_TYPES: Final[dict[ExportFormat, str]] = {
    ExportFormat.PDF: "application/pdf",
    ExportFormat.PPTX: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}

FAILED_MESSAGE: Final = "This export could not be generated. Try again, and the report will be rendered afresh."
UNEXPORTABLE_MESSAGE: Final = "This version has no finished report to export."


class ArtifactStore(Protocol):
    """What the processor needs from object storage."""

    def put(self, storage_key: str, data: bytes, content_type: str) -> None: ...

    def delete(self, storage_key: str) -> None: ...


@final
@dataclass(frozen=True, slots=True)
class ProcessedExport:
    export_id: UUID
    ready: bool
    reason: str | None = None
    render_ms: int | None = None
    size_bytes: int | None = None


def export_key(session_id: UUID, export_id: UUID, export_format: ExportFormat) -> str:
    """Where an artifact lives. Two UUIDs, never a user-supplied name
    (`REQ-EXP-008 AC-1`)."""
    return f"exports/{session_id}/{export_id}.{export_format.value}"


class ExportProcessor:
    """Claims and renders one export at a time."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        store: ArtifactStore,
        *,
        lease_seconds: int = EXPORT_LEASE_SECONDS,
        max_attempts: int = EXPORT_MAX_ATTEMPTS,
    ) -> None:
        self._session_factory = session_factory
        self._store = store
        self._lease = dt.timedelta(seconds=lease_seconds)
        self._max_attempts = max_attempts

    def run_one(self, *, now: dt.datetime | None = None) -> ProcessedExport | None:
        """Process one export, or return `None` if none is waiting."""
        claimed = self._claim(now or utcnow())
        if claimed is None:
            return None
        export_id, attempts = claimed
        if attempts > self._max_attempts:
            self._fail(export_id, FAILED_MESSAGE)
            return ProcessedExport(export_id, ready=False, reason="attempts exhausted")

        started = time.perf_counter()
        try:
            with self._session_factory() as session:
                export = session.get(Export, export_id)
                if export is None:  # pragma: no cover - claimed rows exist
                    return None
                version = session.get(ResearchVersion, export.version_id)
                if version is None:  # pragma: no cover - enforced by the foreign key
                    return None
                document = build_document(session, version.id)
                theme = theme_for(export.theme)
                data = (
                    render_pdf(document, theme)
                    if export.format is ExportFormat.PDF
                    else render_pptx(document, theme)
                )
                key = export_key(version.session_id, export.id, export.format)
                content_type = CONTENT_TYPES[export.format]
        except ExportSourceError as exc:
            # Permanent: retrying renders the same absence of a report.
            logger.info("export %s cannot be produced: %s", export_id, exc)
            self._fail(export_id, UNEXPORTABLE_MESSAGE)
            return ProcessedExport(export_id, ready=False, reason="unexportable")
        except Exception as exc:
            logger.exception("export %s failed on attempt %s", export_id, attempts)
            self._release(export_id, attempts, type(exc).__name__)
            return ProcessedExport(export_id, ready=False, reason=type(exc).__name__)

        render_ms = int((time.perf_counter() - started) * 1000)
        try:
            self._store.put(key, data, content_type)
        except Exception as exc:
            logger.warning("export %s could not be stored: %s", export_id, type(exc).__name__)
            self._release(export_id, attempts, "storage")
            return ProcessedExport(export_id, ready=False, reason="storage", render_ms=render_ms)

        if not self._mark_ready(export_id, key, len(data)):
            # The research was deleted while this rendered. The row is gone or
            # hidden; the object must not outlive it.
            try:
                self._store.delete(key)
            except Exception:
                logger.warning("could not remove the artifact of deleted export %s", export_id)
            return ProcessedExport(export_id, ready=False, reason="deleted", render_ms=render_ms)

        return ProcessedExport(export_id, ready=True, render_ms=render_ms, size_bytes=len(data))

    # ------------------------------------------------------------------

    def _claim(self, now: dt.datetime) -> tuple[UUID, int] | None:
        with self._session_factory() as session:
            export = session.execute(
                select(Export)
                .join(ResearchVersion, ResearchVersion.id == Export.version_id)
                .join(ResearchSession, ResearchSession.id == ResearchVersion.session_id)
                .where(
                    ResearchSession.deleted_at.is_(None),
                    or_(
                        Export.status == ExportStatus.PENDING,
                        (Export.status == ExportStatus.RUNNING) & (Export.lease_expires_at < now),
                    ),
                )
                .order_by(Export.created_at)
                .limit(1)
                .with_for_update(skip_locked=True, of=Export)
            ).scalar_one_or_none()
            if export is None:
                return None
            export.status = ExportStatus.RUNNING
            export.attempts += 1
            export.lease_expires_at = now + self._lease
            session.commit()
            return export.id, export.attempts

    def _fail(self, export_id: UUID, message: str) -> None:
        with self._session_factory() as session:
            export = session.get(Export, export_id)
            if export is not None:
                export.status = ExportStatus.FAILED
                export.error = message
                export.lease_expires_at = None
                export.completed_at = utcnow()
                session.commit()

    def _release(self, export_id: UUID, attempts: int, cause: str) -> None:
        """A transient failure: back to the queue, or failed if out of attempts."""
        if attempts >= self._max_attempts:
            self._fail(export_id, FAILED_MESSAGE)
            return
        with self._session_factory() as session:
            export = session.get(Export, export_id)
            if export is not None:
                export.status = ExportStatus.PENDING
                export.lease_expires_at = None
                session.commit()

    def _mark_ready(self, export_id: UUID, key: str, size: int) -> bool:
        with self._session_factory() as session:
            row = session.execute(
                select(Export, ResearchSession.deleted_at)
                .join(ResearchVersion, ResearchVersion.id == Export.version_id)
                .join(ResearchSession, ResearchSession.id == ResearchVersion.session_id)
                .where(Export.id == export_id)
                .with_for_update(of=Export)
            ).one_or_none()
            if row is None or row[1] is not None:
                return False
            export = row[0]
            export.status = ExportStatus.READY
            export.storage_key = key
            export.size_bytes = size
            export.error = None
            export.lease_expires_at = None
            export.completed_at = utcnow()
            session.commit()
            return True
