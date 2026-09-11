"""User-provided documents and their extracted chunks.

Uploads are session-scoped, not version-scoped, and that is deliberate: the file
is something the *user* supplied once, and re-uploading it for each version
would be absurd. What is version-scoped is the `sources` row that points at it,
which is how a document's retrieval timestamp still differs per version
(`REQ-VER-003 AC-2`).

`storage_key` names an object in whatever store `OPEN-10` selects. Nothing here
depends on which one: the column is a key, not a URL, so a provider change is a
config change.

`upload_chunks` carries **no embedding column**. Whether retrieval over
documents needs vector search at all is `OPEN-12`, and adding a `vector` column
means installing pgvector — a real infrastructure commitment that must not be
made accidentally by a baseline migration.
"""

from __future__ import annotations

import datetime as dt
from uuid import UUID

from sqlalchemy import BigInteger, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from scrapr_core.db.base import Base, CreatedAt, Json, UuidPk
from scrapr_core.db.enums import UploadState, pg_enum

__all__ = ["Upload", "UploadChunk"]


class Upload(Base):
    """One uploaded file and its processing state (`REQ-DOC-002..004`)."""

    __tablename__ = "uploads"
    __table_args__ = (Index("ix_uploads_session_id", "session_id"),)

    id: Mapped[UuidPk]

    session_id: Mapped[UUID] = mapped_column(ForeignKey("research_sessions.id"))

    filename: Mapped[str]
    content_type: Mapped[str]
    """Validated against an allowlist at the boundary (`REQ-SEC-005`); stored as
    what the file actually is, not what the client asserted."""

    size_bytes: Mapped[int] = mapped_column(BigInteger)

    storage_key: Mapped[str]
    """Object key in the store `OPEN-10` selects."""

    sha256: Mapped[str]
    """Content hash: integrity, and the basis for recognising a re-upload."""

    processing_state: Mapped[UploadState] = mapped_column(
        pg_enum(UploadState, "upload_state")
    )

    error: Mapped[str | None]
    """Why extraction failed, in terms safe to show the user (`REQ-DOC-005`)."""

    created_at: Mapped[CreatedAt]
    deleted_at: Mapped[dt.datetime | None]
    """`REQ-SEC-008` requires deletion to remove the stored object too, so the
    row outliving the file is the expected state between those two steps."""


class UploadChunk(Base):
    """One extracted span of a document, with a locator back into the original.

    The locator is what lets a citation point at page 14 rather than at "the
    PDF" (`REQ-DOC-006`).
    """

    __tablename__ = "upload_chunks"
    __table_args__ = (
        UniqueConstraint("upload_id", "ordinal"),
        Index("ix_upload_chunks_upload_id", "upload_id"),
    )

    id: Mapped[UuidPk]

    upload_id: Mapped[UUID] = mapped_column(
        ForeignKey("uploads.id", ondelete="CASCADE")
    )

    ordinal: Mapped[int]

    text: Mapped[str]
    """Extracted content. Untrusted in origin: a document is as capable of
    carrying an injection payload as a web page (`REQ-SEC-013`)."""

    locator: Mapped[Json]
    """Page, sheet, cell range — whatever the format supports."""
