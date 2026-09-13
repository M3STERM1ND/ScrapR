"""Uploads: registering them, limiting them, and moving them through states.

**Limits live here, not in a route** (`REQ-DOC-010 AC-1`, `DEC-13`). They are
checked twice — once when a presigned URL is issued against the size the client
*declares*, and again at completion against the size the object *has*. A
presigned URL is a write grant, not a size limit, so a client that understates
its file gets a signature it can use and a row it never receives.

**Counting is a query, never a cached column.** An `upload_count` field on the
session would be a second source of truth that drifts the first time a delete
misses it, and the limit it guards is the one an attacker probes.

**Deletion is soft here and hard in storage** (`REQ-SEC-008 AC-2`). The row
keeps `deleted_at` so an in-flight run holding an `upload_id` still resolves a
citation it already wrote, while the bytes and the searchable chunks are gone.
`models/documents.py` documents that window as the expected state between the
two steps.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from typing import final
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from scrapr_core.db.base import utcnow
from scrapr_core.db.enums import UploadState
from scrapr_core.db.models import Upload, UploadChunk
from scrapr_core.domain.json import JsonMapping

__all__ = ["LimitBreach", "UploadRepository"]


@final
@dataclass(frozen=True, slots=True)
class LimitBreach:
    """Which limit was hit, in words a user can act on.

    `REQ-SEC-010` requires errors reaching a user be sanitized, and this text is
    written to be shown: it names the limit and the actual number, and nothing
    about storage, keys or infrastructure.
    """

    code: str
    message: str


@final
@dataclass(frozen=True, slots=True)
class SessionTotals:
    """How much of a session's allowance is already spent."""

    count: int
    total_bytes: int


class UploadRepository:
    """Reads and writes for one research session's uploads."""

    def __init__(
        self,
        session: Session,
        *,
        max_upload_bytes: int,
        max_uploads_per_session: int,
        max_session_upload_bytes: int,
    ) -> None:
        self._session = session
        self._max_bytes = max_upload_bytes
        self._max_count = max_uploads_per_session
        self._max_total = max_session_upload_bytes

    # ------------------------------------------------------------------
    # Limits — `DEC-13`
    # ------------------------------------------------------------------

    def check_limits(
        self,
        session_id: UUID,
        size_bytes: int,
        *,
        replacing: UUID | None = None,
    ) -> LimitBreach | None:
        """Whether one more file of this size is allowed. `None` means yes.

        Called at presign with the declared size and again at completion with
        the stored one, which is the whole of `REQ-DOC-010 AC-1`. It returns a
        value rather than raising so each caller can answer in its own terms.

        `replacing` is the upload being re-checked. At completion the row
        already exists and its declared size is already in the session total,
        so without this the file would be counted twice and a second check that
        should pass would fail.
        """
        if size_bytes <= 0:
            return LimitBreach("empty_file", "That file is empty.")

        if size_bytes > self._max_bytes:
            return LimitBreach(
                "file_too_large",
                f"That file is {_megabytes(size_bytes)}. The limit is "
                f"{_megabytes(self._max_bytes)} per file.",
            )

        live = self.live_totals(session_id, replacing=replacing)

        if live.count >= self._max_count:
            return LimitBreach(
                "too_many_files",
                f"You have already attached {live.count} files. The limit is "
                f"{self._max_count} for one research session.",
            )

        if live.total_bytes + size_bytes > self._max_total:
            remaining = max(0, self._max_total - live.total_bytes)
            return LimitBreach(
                "session_full",
                f"That would take this session past its "
                f"{_megabytes(self._max_total)} total. There is "
                f"{_megabytes(remaining)} left.",
            )

        return None

    def live_totals(
        self, session_id: UUID, *, replacing: UUID | None = None
    ) -> SessionTotals:
        """How many live uploads a session has, and how large they are.

        `PENDING` rows count. A row exists before its bytes do, so excluding
        pending ones would let a client presign its way past the count limit by
        never completing — the cheapest possible way to defeat `DEC-13`.

        `replacing` leaves one upload out, for a caller re-checking a row that
        is already counted.
        """
        query = select(
            func.count(Upload.id),
            func.coalesce(func.sum(Upload.size_bytes), 0),
        ).where(Upload.session_id == session_id, Upload.deleted_at.is_(None))

        if replacing is not None:
            query = query.where(Upload.id != replacing)

        row = self._session.execute(query).one()
        return SessionTotals(count=int(row[0]), total_bytes=int(row[1]))

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def create_pending(
        self,
        *,
        session_id: UUID,
        filename: str,
        content_type: str,
        size_bytes: int,
        storage_key: str,
    ) -> Upload:
        """Record an upload the client is about to make.

        `sha256` is empty until completion: the hash is of bytes that do not
        exist yet, and inventing one now would put a value in the one column
        whose entire purpose is integrity.
        """
        upload = Upload(
            session_id=session_id,
            filename=filename,
            content_type=content_type,
            size_bytes=size_bytes,
            storage_key=storage_key,
            sha256="",
            processing_state=UploadState.PENDING,
            error=None,
        )
        self._session.add(upload)
        self._session.flush()
        return upload

    def mark_uploaded(self, upload: Upload, *, size_bytes: int) -> Upload:
        """The bytes arrived; extraction has not run yet.

        The declared size is overwritten by the real one, read back from
        storage. The row must describe the object, not the client's claim about
        it — that is the whole point of checking twice (`REQ-DOC-010 AC-1`).

        `sha256` stays empty here. The API never reads the file (§10), so it
        cannot hash it, and accepting a hash from the client would put a value
        in an integrity column that the client is free to make up. The worker
        fills it in when it reads the bytes to extract them.
        """
        upload.size_bytes = size_bytes
        upload.processing_state = UploadState.PROCESSING
        upload.error = None
        self._session.flush()
        return upload

    def claim_for_extraction(self) -> Upload | None:
        """Take the next upload awaiting extraction, or `None`.

        `FOR UPDATE SKIP LOCKED`, the same mechanism `run_steps` uses: two
        workers polling at once must not both read and extract one file. The
        lock is held for the length of the caller's transaction rather than
        recorded in a column, because extraction of a file capped at 25 MB
        (`DEC-13`) is seconds — a lease column would be state to expire and
        reclaim for a window shorter than the poll interval.
        """
        return self._session.execute(
            select(Upload)
            .where(
                Upload.deleted_at.is_(None),
                Upload.processing_state == UploadState.PROCESSING,
            )
            .order_by(Upload.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).scalar_one_or_none()

    def mark_ready(
        self,
        upload: Upload,
        chunks: Sequence[tuple[int, str, JsonMapping]],
        *,
        sha256: str = "",
        content_type: str = "",
    ) -> Upload:
        """Store the extracted chunks and open the document for retrieval.

        Refuses to mark a document ready with nothing in it (`REQ-DOC-004
        AC-3`). A `ready` upload with zero chunks is indistinguishable
        downstream from a document that genuinely said nothing about the
        subject, and only one of those is something the reader needs told.
        """
        if not chunks:
            return self.mark_failed(upload, "No text could be read from this file.")

        # Idempotent: a step is re-run after a crash, and a second extraction
        # must replace the first rather than collide with the unique index on
        # `(upload_id, ordinal)`.
        self._session.execute(
            delete(UploadChunk).where(UploadChunk.upload_id == upload.id)
        )

        for ordinal, text, locator in chunks:
            self._session.add(
                UploadChunk(
                    upload_id=upload.id,
                    ordinal=ordinal,
                    text=text,
                    locator=locator,
                )
            )

        # Both are facts about the bytes and only knowable to whoever read
        # them. `content_type` in particular is what the file *is* rather than
        # what the client declared at presign, which is what `DEC-14` requires
        # the column to hold.
        if sha256:
            upload.sha256 = sha256
        if content_type:
            upload.content_type = content_type

        upload.processing_state = UploadState.READY
        upload.error = None
        self._session.flush()
        return upload

    def mark_failed(self, upload: Upload, reason: str) -> Upload:
        """Extraction did not produce a usable document (`REQ-DOC-004 AC-3`).

        `reason` is shown to the user, so it says what happened to their file
        and never what happened to our infrastructure (`REQ-SEC-010`).
        """
        upload.processing_state = UploadState.FAILED
        upload.error = reason
        self._session.flush()
        return upload

    def soft_delete(self, upload: Upload, *, now: dt.datetime | None = None) -> Upload:
        """Mark the row deleted. The caller removes the object.

        Chunks go immediately rather than with the row: they are the searchable
        copy of the content, and leaving them would mean a deleted document
        could still be retrieved as evidence — which is what `REQ-SEC-008` is
        actually about.
        """
        self._session.execute(
            delete(UploadChunk).where(UploadChunk.upload_id == upload.id)
        )
        upload.deleted_at = now or utcnow()
        self._session.flush()
        return upload

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get(self, session_id: UUID, upload_id: UUID) -> Upload | None:
        """One upload, scoped to its session.

        The session is part of the lookup rather than a check afterwards, so
        there is no ordering in which a caller reads someone else's row first
        (implementation plan §4.3).
        """
        return self._session.execute(
            select(Upload).where(
                Upload.id == upload_id,
                Upload.session_id == session_id,
                Upload.deleted_at.is_(None),
            )
        ).scalar_one_or_none()

    def list_for_session(self, session_id: UUID) -> Sequence[Upload]:
        """Every live upload, oldest first."""
        return (
            self._session.execute(
                select(Upload)
                .where(Upload.session_id == session_id, Upload.deleted_at.is_(None))
                .order_by(Upload.created_at)
            )
            .scalars()
            .all()
        )

    def pending_extraction(self, session_id: UUID) -> Sequence[Upload]:
        """Uploads whose bytes have arrived but whose text has not been read.

        This is what the extraction step consumes. `PENDING` rows are excluded
        deliberately: their bytes may never arrive, and reading a key that does
        not exist would fail an upload the user has not finished making.
        """
        return (
            self._session.execute(
                select(Upload)
                .where(
                    Upload.session_id == session_id,
                    Upload.deleted_at.is_(None),
                    Upload.processing_state == UploadState.PROCESSING,
                )
                .order_by(Upload.created_at)
            )
            .scalars()
            .all()
        )

    def has_ready_documents(self, session_id: UUID) -> bool:
        """Whether retrieval should ask the documents category at all.

        The orchestrator calls this once per run. Without it every area of
        every session would query an empty index, and the documents tool would
        report `not_found` into runs that have no documents — a gap in the
        report where there is no gap.
        """
        return (
            self._session.execute(
                select(Upload.id)
                .where(
                    Upload.session_id == session_id,
                    Upload.deleted_at.is_(None),
                    Upload.processing_state == UploadState.READY,
                )
                .limit(1)
            ).first()
            is not None
        )

    def chunk_count(self, upload_id: UUID) -> int:
        return int(
            self._session.execute(
                select(func.count(UploadChunk.id)).where(
                    UploadChunk.upload_id == upload_id
                )
            ).scalar_one()
        )


def _megabytes(size_bytes: int) -> str:
    """A size a person can read. Never raw bytes, never six decimal places."""
    megabytes = size_bytes / (1024 * 1024)
    if megabytes < 0.1:
        return "under 0.1 MB"
    return f"{megabytes:.1f} MB"
