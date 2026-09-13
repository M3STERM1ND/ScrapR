"""Turning a stored file into searchable text (`REQ-DOC-003`, `REQ-DOC-004`).

One upload at a time, claimed with `FOR UPDATE SKIP LOCKED` so two workers
polling at once cannot both read the same file. The whole operation is a single
transaction: the row is claimed, the bytes are read, the chunks are written, and
the state moves — or nothing happens and the next poll tries again.

**Failure is a state with a reason, never an empty document** (`AC-3`). A
scanned PDF, a corrupt archive, a file whose bytes are not the type its name
claims: each becomes a `failed` upload carrying a sentence the user can read.
The alternative — a `ready` upload with no chunks — is indistinguishable
downstream from a document that simply did not mention the subject, and the user
would never learn their file was unusable.

**The type is re-derived from the bytes here** (`DEC-14`). What the client
declared at presign was signed into the URL so the object could be stored, but
it is still a claim. This is the first moment anyone can check it, and a
mismatch is a rejection rather than a silent relabel.

**Nothing extracted is trusted** (`REQ-DOC-009`). The text goes into
`upload_chunks` as data, and the only thing that ever reads it is the documents
tool, which wraps it in `Untrusted` before it can reach a model.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import final
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from scrapr_core.config import Settings
from scrapr_core.db.repositories.uploads import UploadRepository
from scrapr_core.storage.extract import (
    ACCEPTED_TYPES,
    ExtractionError,
    accepted_types_message,
    detect_content_type,
    extract_chunks,
)
from scrapr_core.storage.objects import ObjectStore, StorageError

__all__ = ["DocumentProcessor", "ProcessedUpload"]

logger = logging.getLogger(__name__)


@final
@dataclass(frozen=True, slots=True)
class ProcessedUpload:
    """What one extraction did, for the caller's log."""

    upload_id: UUID
    ready: bool
    chunks: int
    reason: str = ""


@final
class DocumentProcessor:
    """Extracts one queued upload per call."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        store: ObjectStore,
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._store = store
        self._settings = settings

    def run_one(self) -> ProcessedUpload | None:
        """Process the next waiting upload, or return `None` if there is none.

        Synchronous, because every part of it is: a database transaction, a
        single object read, and CPU-bound parsing. Wrapping it in `async` would
        buy nothing and would let it block the event loop it pretended not to.
        """
        with self._session_factory.begin() as session:
            uploads = _repository(session, self._settings)
            upload = uploads.claim_for_extraction()
            if upload is None:
                return None

            upload_id = upload.id
            filename = upload.filename
            storage_key = upload.storage_key

            try:
                data = self._store.read(storage_key)
            except StorageError as exc:
                # Logged with the key, shown without it: `REQ-SEC-010` keeps
                # infrastructure detail out of anything a user reads.
                logger.warning("could not read %s: %s", storage_key, exc)
                uploads.mark_failed(
                    upload, "This file could not be read back after uploading."
                )
                return ProcessedUpload(upload_id, ready=False, chunks=0, reason="unreadable")

            detected = detect_content_type(data, filename)
            if detected is None or detected not in ACCEPTED_TYPES:
                uploads.mark_failed(
                    upload,
                    "This file is not one of the types ScrapR can read. "
                    f"Accepted: {accepted_types_message()}.",
                )
                return ProcessedUpload(upload_id, ready=False, chunks=0, reason="rejected_type")

            try:
                extracted = extract_chunks(data, detected)
            except ExtractionError as exc:
                # `ExtractionError` messages are written for the user: each one
                # says what happened to *their* file and names no library.
                uploads.mark_failed(upload, str(exc))
                return ProcessedUpload(upload_id, ready=False, chunks=0, reason="extract_failed")

            if not extracted:
                uploads.mark_failed(
                    upload,
                    "No text could be read from this file. If it is a scan or "
                    "a photo, ScrapR cannot read the words in it yet.",
                )
                return ProcessedUpload(upload_id, ready=False, chunks=0, reason="no_text")

            uploads.mark_ready(
                upload,
                [(chunk.ordinal, chunk.text, chunk.locator) for chunk in extracted],
                sha256=hashlib.sha256(data).hexdigest(),
                content_type=detected,
            )
            return ProcessedUpload(upload_id, ready=True, chunks=len(extracted))


def _repository(session: Session, settings: Settings) -> UploadRepository:
    return UploadRepository(
        session,
        max_upload_bytes=settings.max_upload_bytes,
        max_uploads_per_session=settings.max_uploads_per_session,
        max_session_upload_bytes=settings.max_session_upload_bytes,
    )
