"""Generated PDF and PPTX artifacts.

An export is scoped to a version, which is what makes `NFR-REL-003` — retry an
export without re-running research — fall out of the schema rather than needing
a mechanism. The version is immutable, so regenerating from it always produces
the same content.

`REQ-EXP-005` forbids an export containing anything absent from the workspace
version. The FK is the enforcement point: there is no path from an export to
content that is not reachable from its `version_id`.
"""

from __future__ import annotations

import datetime as dt
from uuid import UUID

from sqlalchemy import ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from scrapr_core.db.base import Base, CreatedAt, UuidPk
from scrapr_core.db.enums import ExportFormat, ExportStatus, ExportTheme, pg_enum

__all__ = ["Export"]


class Export(Base):
    """One export job and, once ready, the artifact it produced."""

    __tablename__ = "exports"
    __table_args__ = (Index("ix_exports_version_id", "version_id"),)

    id: Mapped[UuidPk]

    version_id: Mapped[UUID] = mapped_column(ForeignKey("research_versions.id"))

    format: Mapped[ExportFormat] = mapped_column(pg_enum(ExportFormat, "export_format"))
    theme: Mapped[ExportTheme] = mapped_column(pg_enum(ExportTheme, "export_theme"))
    """One of the six predefined themes (`REQ-EXP-003`); the visual definitions
    themselves are `OPEN-22`."""

    status: Mapped[ExportStatus] = mapped_column(pg_enum(ExportStatus, "export_status"))

    storage_key: Mapped[str | None]
    """Set when `status` is `ready`. Served as a signed, short-lived URL
    (`REQ-EXP-008`) rather than a public one."""

    error: Mapped[str | None]

    created_at: Mapped[CreatedAt]
    completed_at: Mapped[dt.datetime | None]
