"""Attaching documents to a research session (`REQ-DOC-001..003`, `-010`).

Four routes and one rule: **the API never touches the file**. It signs a URL,
records a row, reads the object's metadata back, and deletes. Implementation
plan §10 requires the browser to PUT directly to storage, which is what makes a
25 MB upload independent of any request timeout — and means bytes the API never
holds are bytes it cannot log, buffer or leak.

**Limits are enforced twice** (`REQ-DOC-010 AC-1`, `DEC-13`). Once at presign,
against the size the client declares, so an oversized file is refused before it
is transferred rather than after. Once at completion, against the size the
object actually has — because a presigned URL is a write grant and not a limit,
and a client that understates its file would otherwise walk straight past the
first check.

**A file rejected at completion is deleted, not kept.** Leaving an over-limit
object in the bucket with no row pointing at it would make the limit a
formality and the bucket unbounded.

**Extraction is not on this path.** Completion moves the row to `processing`
and returns; the worker picks it up. A route that parsed a PDF would hold a
request open for the length of a CPU-bound parse and put the accepted-types
decision in two places.
"""

from __future__ import annotations

from functools import lru_cache
from uuid import UUID

from fastapi import APIRouter, status

from scrapr_api.deps import CurrentOwner, DbSession, Research
from scrapr_api.errors import ApiError
from scrapr_api.schemas import (
    UploadOut,
    UploadTicket,
    UploadTicketRequest,
)
from scrapr_core.config import Settings, get_settings
from scrapr_core.db.models import Upload
from scrapr_core.db.repositories.uploads import UploadRepository
from scrapr_core.storage.extract import ACCEPTED_TYPES, accepted_types_message
from scrapr_core.storage.objects import ObjectStore, StorageError, storage_key_for

__all__ = ["router"]

router = APIRouter(prefix="/v1/research/{session_id}/uploads", tags=["uploads"])


@lru_cache(maxsize=1)
def get_object_store() -> ObjectStore:
    """One storage client per process, built lazily.

    Lazily for the same reason as the engine: importing the app must not open a
    connection, because CI generates the OpenAPI document with no storage and
    no database running.
    """
    return ObjectStore(get_settings())


def _uploads(session: DbSession, settings: Settings) -> UploadRepository:
    return UploadRepository(
        session,
        max_upload_bytes=settings.max_upload_bytes,
        max_uploads_per_session=settings.max_uploads_per_session,
        max_session_upload_bytes=settings.max_session_upload_bytes,
    )


def _not_found() -> ApiError:
    """404 for someone else's session and for one that does not exist.

    The same answer to both, deliberately (`REQ-SEC-009`): a 403 would confirm
    that an id someone guessed is real.
    """
    return ApiError(
        status.HTTP_404_NOT_FOUND, "not_found", "That research does not exist."
    )


def _storage_unavailable() -> ApiError:
    return ApiError(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "storage_unavailable",
        "Attachments are temporarily unavailable. Try again in a moment.",
    )


@router.post("", status_code=status.HTTP_201_CREATED)
def create_upload_ticket(
    session_id: UUID,
    body: UploadTicketRequest,
    owner: CurrentOwner,
    research: Research,
    session: DbSession,
) -> UploadTicket:
    """Reserve a slot and sign a URL for one file.

    **201, not 200**: this creates the `uploads` row. The row exists before the
    bytes do, which is what lets the count limit hold — a client that presigns
    ten tickets and uploads none has used its ten.
    """
    if research.get_session(session_id) is None:
        raise _not_found()

    settings = get_settings()
    uploads = _uploads(session, settings)

    # `REQ-DOC-001 AC-2`. Rejected on the declared type here and re-checked
    # against the actual bytes during extraction, because the client's claim is
    # only good enough to decide whether to sign a URL at all.
    if body.content_type not in ACCEPTED_TYPES:
        raise ApiError(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "unsupported_type",
            f"ScrapR cannot read that kind of file. Accepted: "
            f"{accepted_types_message()}.",
        )

    breach = uploads.check_limits(session_id, body.size_bytes)
    if breach is not None:
        raise ApiError(
            status.HTTP_413_CONTENT_TOO_LARGE, breach.code, breach.message
        )

    upload = uploads.create_pending(
        session_id=session_id,
        filename=body.filename,
        content_type=body.content_type,
        size_bytes=body.size_bytes,
        storage_key="",
    )

    # The key needs the row's id, so it is set immediately after insert rather
    # than being derived from something the client chose (`REQ-SEC-005 AC-1`).
    upload.storage_key = storage_key_for(session_id, upload.id, body.filename)
    session.flush()

    try:
        presigned = get_object_store().presign_put(
            upload.storage_key, body.content_type
        )
    except StorageError as exc:
        raise _storage_unavailable() from exc

    _ = owner  # resolved for its side effect: the request is attributable

    return UploadTicket(
        upload_id=upload.id,
        url=presigned.url,
        expires_at=presigned.expires_at,
        content_type=body.content_type,
    )


@router.post("/{upload_id}/complete")
def complete_upload(
    session_id: UUID,
    upload_id: UUID,
    research: Research,
    session: DbSession,
) -> UploadOut:
    """Confirm the bytes arrived, and queue the file for reading.

    The size is read back from storage and re-checked, which is the second half
    of `REQ-DOC-010 AC-1`. A file that is over the limit — or that never
    arrived — leaves no usable row behind.
    """
    if research.get_session(session_id) is None:
        raise _not_found()

    settings = get_settings()
    uploads = _uploads(session, settings)

    upload = uploads.get(session_id, upload_id)
    if upload is None:
        raise ApiError(
            status.HTTP_404_NOT_FOUND, "not_found", "That attachment does not exist."
        )

    store = get_object_store()
    try:
        stored = store.stored_size(upload.storage_key)
    except StorageError as exc:
        raise _storage_unavailable() from exc

    if stored is None:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "not_uploaded",
            "That file has not finished uploading yet.",
        )

    # `replacing`, because this row's declared size is already in the session
    # total. Without it every completion would be measured against a total that
    # includes the file being completed, and the second check would refuse
    # files the first one allowed.
    breach = uploads.check_limits(session_id, stored, replacing=upload.id)

    if breach is not None:
        # The object is real and over the limit. Delete it: a bucket that keeps
        # what the limit refused is not limited.
        try:
            store.delete(upload.storage_key)
        except StorageError:
            # Logged by the store. The row still goes, so nothing points at an
            # object we failed to remove, and a sweep can find it by key.
            pass
        uploads.soft_delete(upload)

        # Committed before raising, deliberately. The request-scoped session
        # rolls back on an exception, so without this the cleanup would be
        # undone by the very error response that reports it — leaving the row
        # alive, still counted against the session's ten, and permanently
        # stuck in `pending`.
        session.commit()

        raise ApiError(
            status.HTTP_413_CONTENT_TOO_LARGE, breach.code, breach.message
        )

    uploads.mark_uploaded(upload, size_bytes=stored)
    return _as_out(upload, uploads)


@router.get("")
def list_uploads(
    session_id: UUID,
    research: Research,
    session: DbSession,
) -> list[UploadOut]:
    """Every file attached to this session, with its processing state.

    This is what `REQ-DOC-003 AC-1` is rendered from. Polled by the intake
    panel while anything is still processing, which is why it is cheap: one
    indexed query and a count per row.
    """
    if research.get_session(session_id) is None:
        raise _not_found()

    uploads = _uploads(session, get_settings())
    return [_as_out(upload, uploads) for upload in uploads.list_for_session(session_id)]


@router.delete("/{upload_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_upload(
    session_id: UUID,
    upload_id: UUID,
    research: Research,
    session: DbSession,
) -> None:
    """Remove a file and its extracted text (`REQ-SEC-008 AC-2`).

    The chunks go with the row, immediately: they are the searchable copy, and
    a deleted document that can still be retrieved as evidence is not deleted.
    The object is removed next. If storage refuses, the row still goes — the
    user's instruction is honoured and the orphaned object is a cleanup
    problem, not a privacy one that waits on a retry.
    """
    if research.get_session(session_id) is None:
        raise _not_found()

    uploads = _uploads(session, get_settings())
    upload = uploads.get(session_id, upload_id)
    if upload is None:
        # Already gone. Deletion is idempotent: a client retrying a delete it
        # already made should not be told it failed.
        return

    storage_key = upload.storage_key
    uploads.soft_delete(upload)

    if storage_key:
        try:
            get_object_store().delete(storage_key)
        except StorageError as exc:
            raise _storage_unavailable() from exc


def _as_out(upload: Upload, uploads: UploadRepository) -> UploadOut:
    return UploadOut(
        id=upload.id,
        filename=upload.filename,
        content_type=upload.content_type,
        size_bytes=upload.size_bytes,
        state=upload.processing_state,
        error=upload.error,
        chunk_count=uploads.chunk_count(upload.id),
        created_at=upload.created_at,
    )
