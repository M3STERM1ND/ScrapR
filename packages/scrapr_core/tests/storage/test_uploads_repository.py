"""Upload limits and the state machine (`REQ-DOC-010`, `DEC-13`).

The limits are the security-relevant half of Phase 4, so they are tested at the
repository — the one place that enforces them — rather than only through the
routes. A route test proves one caller behaves; this proves there is no second
caller that can behave differently.
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from scrapr_core.db.enums import UploadState
from scrapr_core.db.models import AnonymousSession, ResearchSession, UploadChunk
from scrapr_core.db.repositories.uploads import UploadRepository

MB = 1024 * 1024


@pytest.fixture
def research_id(db_session: Session) -> Iterator[UUID]:
    anonymous = AnonymousSession(token_hash=f"uploads-{id(db_session)}")
    db_session.add(anonymous)
    db_session.flush()
    research = ResearchSession(
        anonymous_session_id=anonymous.id,
        objective="How did Acme Corp perform last year?",
        status="pending",
    )
    db_session.add(research)
    db_session.flush()
    yield research.id


@pytest.fixture
def uploads(db_session: Session) -> UploadRepository:
    return UploadRepository(
        db_session,
        max_upload_bytes=25 * MB,
        max_uploads_per_session=10,
        max_session_upload_bytes=100 * MB,
    )


def _add(uploads: UploadRepository, research_id: UUID, size: int, name: str = "a.txt"):
    return uploads.create_pending(
        session_id=research_id,
        filename=name,
        content_type="text/plain",
        size_bytes=size,
        storage_key=f"uploads/{research_id}/{name}",
    )


# --------------------------------------------------------------------------
# Limits
# --------------------------------------------------------------------------


def test_a_file_within_every_limit_is_allowed(
    uploads: UploadRepository, research_id: UUID
) -> None:
    assert uploads.check_limits(research_id, 5 * MB) is None


def test_an_oversized_file_is_refused_with_both_numbers(
    uploads: UploadRepository, research_id: UUID
) -> None:
    """`REQ-DOC-010 AC-2`: a clear message.

    Clear means it says what the file was and what the limit is. "Too large" on
    its own leaves the user guessing whether to shave a megabyte or split the
    document in four.
    """
    breach = uploads.check_limits(research_id, 30 * MB)
    assert breach is not None
    assert breach.code == "file_too_large"
    assert "30.0 MB" in breach.message
    assert "25.0 MB" in breach.message


def test_an_empty_file_is_refused(
    uploads: UploadRepository, research_id: UUID
) -> None:
    assert uploads.check_limits(research_id, 0) is not None


def test_the_count_limit_counts_pending_rows(
    uploads: UploadRepository, research_id: UUID
) -> None:
    """The cheapest way to defeat `DEC-13` is to presign and never complete.

    A row exists before its bytes do. If only completed uploads counted, a
    client could hold an unlimited number of signed URLs against one session.
    """
    for index in range(10):
        _add(uploads, research_id, 1024, f"file-{index}.txt")

    breach = uploads.check_limits(research_id, 1024)
    assert breach is not None
    assert breach.code == "too_many_files"
    assert "10" in breach.message


def test_the_session_total_is_enforced_and_reports_what_is_left(
    uploads: UploadRepository, research_id: UUID
) -> None:
    for index in range(4):
        _add(uploads, research_id, 24 * MB, f"big-{index}.txt")

    breach = uploads.check_limits(research_id, 24 * MB)
    assert breach is not None
    assert breach.code == "session_full"
    assert "100.0 MB" in breach.message
    assert "4.0 MB" in breach.message


def test_a_deleted_upload_frees_its_allowance(
    uploads: UploadRepository, research_id: UUID
) -> None:
    """Otherwise deleting a file to make room would not make room, and the
    limit would be a lifetime quota rather than a session one."""
    upload = _add(uploads, research_id, 90 * MB)
    assert uploads.check_limits(research_id, 20 * MB) is not None

    uploads.soft_delete(upload)
    assert uploads.check_limits(research_id, 20 * MB) is None


def test_recheck_excludes_the_row_being_completed(
    uploads: UploadRepository, research_id: UUID
) -> None:
    """`REQ-DOC-010 AC-1`'s second check must not count the file twice.

    Without `replacing`, completion measures the arriving file against a total
    that already includes it — and every upload past the halfway mark of the
    session budget would be refused after a successful transfer.
    """
    # Five 20 MB files fill the 100 MB session budget exactly.
    filled = [_add(uploads, research_id, 20 * MB, f"f-{n}.txt") for n in range(5)]

    # Completing the last of them: counted twice, it is 120 MB and refused.
    assert uploads.check_limits(research_id, 20 * MB) is not None
    # Counted once, it is the 100 MB that was allowed at presign.
    assert uploads.check_limits(research_id, 20 * MB, replacing=filled[-1].id) is None


# --------------------------------------------------------------------------
# State machine
# --------------------------------------------------------------------------


def test_a_new_upload_is_pending_with_no_hash(
    uploads: UploadRepository, research_id: UUID
) -> None:
    """The hash is of bytes that do not exist yet."""
    upload = _add(uploads, research_id, 1024)
    assert upload.processing_state is UploadState.PENDING
    assert upload.sha256 == ""


def test_completion_overwrites_the_declared_size(
    uploads: UploadRepository, research_id: UUID
) -> None:
    """The row must describe the object, not the client's claim about it."""
    upload = _add(uploads, research_id, 1024)
    uploads.mark_uploaded(upload, size_bytes=4096)

    assert upload.size_bytes == 4096
    assert upload.processing_state is UploadState.PROCESSING


def test_ready_stores_chunks_the_hash_and_the_real_type(
    db_session: Session, uploads: UploadRepository, research_id: UUID
) -> None:
    upload = _add(uploads, research_id, 1024)
    uploads.mark_uploaded(upload, size_bytes=1024)
    uploads.mark_ready(
        upload,
        [(0, "Revenue was $1.2bn.", {"line": 1})],
        sha256="a" * 64,
        content_type="text/markdown",
    )

    assert upload.processing_state is UploadState.READY
    assert upload.sha256 == "a" * 64
    # `DEC-14`: what the file is, not what the client declared at presign.
    assert upload.content_type == "text/markdown"
    assert uploads.chunk_count(upload.id) == 1


def test_ready_with_no_chunks_becomes_failed_instead(
    uploads: UploadRepository, research_id: UUID
) -> None:
    """`REQ-DOC-004 AC-3`. A ready upload with nothing in it is indistinguishable
    downstream from a document that said nothing about the subject, and only one
    of those is something the reader needs told."""
    upload = _add(uploads, research_id, 1024)
    uploads.mark_uploaded(upload, size_bytes=1024)
    uploads.mark_ready(upload, [])

    assert upload.processing_state is UploadState.FAILED
    assert upload.error


def test_re_extraction_replaces_chunks_rather_than_colliding(
    uploads: UploadRepository, research_id: UUID
) -> None:
    """A step is re-run after a crash. The unique index on `(upload_id,
    ordinal)` would otherwise turn a retry into an error."""
    upload = _add(uploads, research_id, 1024)
    uploads.mark_uploaded(upload, size_bytes=1024)

    uploads.mark_ready(upload, [(0, "first pass", {"line": 1})])
    uploads.mark_ready(upload, [(0, "second pass", {"line": 1}), (1, "more", {"line": 3})])

    assert uploads.chunk_count(upload.id) == 2


def test_deleting_removes_the_searchable_text_immediately(
    db_session: Session, uploads: UploadRepository, research_id: UUID
) -> None:
    """`REQ-SEC-008`. A deleted document that can still be retrieved as evidence
    is not deleted, whatever the object store says."""
    upload = _add(uploads, research_id, 1024)
    uploads.mark_uploaded(upload, size_bytes=1024)
    uploads.mark_ready(upload, [(0, "Revenue was $1.2bn.", {"line": 1})])

    uploads.soft_delete(upload)

    assert upload.deleted_at is not None
    assert (
        db_session.execute(
            select(UploadChunk).where(UploadChunk.upload_id == upload.id)
        ).first()
        is None
    )


def test_a_deleted_upload_is_not_listed_or_gettable(
    uploads: UploadRepository, research_id: UUID
) -> None:
    upload = _add(uploads, research_id, 1024)
    uploads.soft_delete(upload)

    assert uploads.list_for_session(research_id) == []
    assert uploads.get(research_id, upload.id) is None


def test_get_is_scoped_to_the_session_not_checked_after(
    db_session: Session, uploads: UploadRepository, research_id: UUID
) -> None:
    """Implementation plan §4.3. There must be no ordering in which a caller
    reads someone else's row first and filters afterwards."""
    other = ResearchSession(
        anonymous_session_id=db_session.execute(
            select(AnonymousSession.id)
        ).scalars().first(),
        objective="A different question entirely, at length.",
        status="pending",
    )
    db_session.add(other)
    db_session.flush()

    upload = _add(uploads, research_id, 1024)
    assert uploads.get(other.id, upload.id) is None


# --------------------------------------------------------------------------
# What the orchestrator asks
# --------------------------------------------------------------------------


def test_has_ready_documents_is_false_until_something_is_ready(
    uploads: UploadRepository, research_id: UUID
) -> None:
    """The orchestrator appends the documents category on this answer.

    False for a pending or failed upload: retrieval against an index with
    nothing in it costs a tool call per question and reports `not_found` as a
    gap in a report that has no gap.
    """
    assert uploads.has_ready_documents(research_id) is False

    upload = _add(uploads, research_id, 1024)
    assert uploads.has_ready_documents(research_id) is False

    uploads.mark_uploaded(upload, size_bytes=1024)
    assert uploads.has_ready_documents(research_id) is False

    uploads.mark_ready(upload, [(0, "text", {"line": 1})])
    assert uploads.has_ready_documents(research_id) is True

    uploads.soft_delete(upload)
    assert uploads.has_ready_documents(research_id) is False


def test_claim_for_extraction_only_takes_uploaded_files(
    uploads: UploadRepository, research_id: UUID
) -> None:
    """A `PENDING` row's bytes may never arrive; reading its key would fail an
    upload the user has not finished making."""
    _add(uploads, research_id, 1024, "pending.txt")
    assert uploads.claim_for_extraction() is None

    waiting = _add(uploads, research_id, 1024, "waiting.txt")
    uploads.mark_uploaded(waiting, size_bytes=1024)

    claimed = uploads.claim_for_extraction()
    assert claimed is not None
    assert claimed.id == waiting.id
