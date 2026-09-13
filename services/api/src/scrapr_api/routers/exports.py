"""Exports: request, list, fetch, retry (`REQ-EXP-001..010`, `DEC-21`).

**The request returns at once** (`REQ-EXP-007 AC-1`): it writes an `exports`
row and the worker renders it. Progress is the row's status, polled by the
export panel (`AC-2`); failure carries a message and a retry route (`AC-3`).

**The file is never served from here.** A ready export's `GET` carries a
five-minute signed URL to object storage, issued only after the ownership
chain from export to version to research to owner has been walked in the query
(`REQ-EXP-008`). Anonymous research exports exactly as account research does
(`REQ-EXP-010`): ownership is the anonymous session, and nothing asks for an
account.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status

from scrapr_api.deps import CurrentOwner, DbSession, Research
from scrapr_api.errors import ApiError
from scrapr_api.routers.uploads import get_object_store
from scrapr_api.schemas import ExportOut, ExportRequest
from scrapr_core.db.enums import ExportStatus, VersionStatus
from scrapr_core.db.models import Export
from scrapr_core.db.repositories.exports import ExportRepository
from scrapr_core.storage.objects import StorageError

__all__ = ["research_exports", "router"]

research_exports = APIRouter(
    prefix="/v1/research/{session_id}/versions/{version_number}/exports", tags=["exports"]
)
router = APIRouter(prefix="/v1/exports", tags=["exports"])


def _not_found() -> ApiError:
    return ApiError(status.HTTP_404_NOT_FOUND, "not_found", "That export does not exist.")


def _out(export: Export, version_number: int, download_url: str | None = None) -> ExportOut:
    return ExportOut(
        id=export.id,
        version_number=version_number,
        format=export.format,
        theme=export.theme,
        status=export.status,
        error=export.error,
        size_bytes=export.size_bytes,
        created_at=export.created_at,
        completed_at=export.completed_at,
        download_url=download_url,
    )


@research_exports.post("", status_code=status.HTTP_202_ACCEPTED)
def request_export(
    session_id: UUID,
    version_number: int,
    body: ExportRequest,
    research: Research,
    owner: CurrentOwner,
    session: DbSession,
) -> ExportOut:
    """Queue a PDF or PowerPoint of one version in one theme.

    Asking again for the same version, format and theme returns the export
    already queued or ready rather than rendering it twice: the version cannot
    change, so neither can the file.
    """
    version = research.get_version(session_id, version_number)
    if version is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "not_found", "That research does not exist.")
    if version.closed_at is None or version.status is VersionStatus.FAILED:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "version_not_ready",
            "Only a finished report can be exported. Wait for the research to complete.",
        )

    export, _ = ExportRepository(session, owner).request(version, body.format, body.theme)
    return _out(export, version.version_number)


@research_exports.get("")
def list_exports(
    session_id: UUID,
    version_number: int,
    research: Research,
    owner: CurrentOwner,
    session: DbSession,
) -> list[ExportOut]:
    """Every export of one version, newest first (export version tracking)."""
    version = research.get_version(session_id, version_number)
    if version is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "not_found", "That research does not exist.")
    return [
        _out(export, version.version_number)
        for export in ExportRepository(session, owner).for_version(version.id)
    ]


@router.get("/{export_id}")
def get_export(export_id: UUID, owner: CurrentOwner, session: DbSession) -> ExportOut:
    """One export; with a short-lived download link once it is ready."""
    exports = ExportRepository(session, owner)
    export = exports.get(export_id)
    if export is None:
        raise _not_found()

    version_number = exports.version_number(export)
    url: str | None = None
    if export.status is ExportStatus.READY and export.storage_key:
        name = f"scrapr-research-v{version_number}-{export.theme.value}.{export.format.value}"
        try:
            url = get_object_store().presign_get(export.storage_key, download_name=name)
        except StorageError as exc:
            raise ApiError(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "storage_unavailable",
                "Downloads are temporarily unavailable. Try again in a moment.",
            ) from exc
    return _out(export, version_number, url)


@router.post("/{export_id}/retry", status_code=status.HTTP_202_ACCEPTED)
def retry_export(export_id: UUID, owner: CurrentOwner, session: DbSession) -> ExportOut:
    """Render a failed export again, without re-running research (`NFR-REL-003`)."""
    exports = ExportRepository(session, owner)
    export = exports.get(export_id)
    if export is None:
        raise _not_found()
    if export.status is not ExportStatus.FAILED:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "not_failed",
            "Only an export that failed can be retried.",
        )
    return _out(exports.retry(export), exports.version_number(export))
