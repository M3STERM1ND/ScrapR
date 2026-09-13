"""Ownership-scoped access to exports (`REQ-EXP-006..008`, `REQ-SEC-002`).

An export belongs to a version, which belongs to a research session, which
belongs to an owner. Every read here walks that chain in the query and filters
on the `OwnerContext` at its end, the same single enforcement point
`ResearchRepository` uses (implementation plan §4.3). An export owned by
somebody else reads as absent.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from scrapr_core.db.base import utcnow
from scrapr_core.db.enums import ExportFormat, ExportStatus, ExportTheme
from scrapr_core.db.models import Export, ResearchSession, ResearchVersion
from scrapr_core.domain.ownership import OwnerContext

__all__ = ["ExportRepository"]

REUSABLE: tuple[ExportStatus, ...] = (ExportStatus.PENDING, ExportStatus.RUNNING, ExportStatus.READY)
"""States in which asking again returns the export already in hand. A version is
immutable and rendering is deterministic, so a second identical request would
only produce the same file twice."""


class ExportRepository:
    """Requests, reads and retries exports within one owner's scope."""

    def __init__(self, session: Session, owner: OwnerContext) -> None:
        self._session = session
        self._owner = owner

    def _owned(self, statement: Select[tuple[Export]]) -> Select[tuple[Export]]:
        scoped = (
            statement.join(ResearchVersion, ResearchVersion.id == Export.version_id)
            .join(ResearchSession, ResearchSession.id == ResearchVersion.session_id)
            .where(ResearchSession.deleted_at.is_(None))
        )
        if self._owner.anonymous_session_id is not None:
            return scoped.where(
                ResearchSession.anonymous_session_id == self._owner.anonymous_session_id
            )
        return scoped.where(ResearchSession.owner_user_id == self._owner.user_id)

    def get(self, export_id: UUID) -> Export | None:
        return self._session.execute(
            self._owned(select(Export)).where(Export.id == export_id)
        ).scalar_one_or_none()

    def for_version(self, version_id: UUID) -> Sequence[Export]:
        """Every export of one owned version, newest first."""
        return (
            self._session.execute(
                self._owned(select(Export))
                .where(Export.version_id == version_id)
                .order_by(Export.created_at.desc())
            )
            .scalars()
            .all()
        )

    def request(
        self, version: ResearchVersion, export_format: ExportFormat, theme: ExportTheme
    ) -> tuple[Export, bool]:
        """Queue an export, or return the identical one already queued or ready.

        Returns the export and whether it is new. The caller has already
        resolved `version` through an ownership-scoped read; this re-checks
        nothing about ownership because there is nothing left to check.
        """
        existing = self._session.execute(
            self._owned(select(Export)).where(
                Export.version_id == version.id,
                Export.format == export_format,
                Export.theme == theme,
                Export.status.in_(REUSABLE),
            )
            .order_by(Export.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if existing is not None:
            return existing, False

        created = Export(
            version_id=version.id,
            format=export_format,
            theme=theme,
            status=ExportStatus.PENDING,
            attempts=0,
        )
        self._session.add(created)
        self._session.flush()
        return created, True

    def retry(self, export: Export) -> Export:
        """Put a failed export back in the queue (`REQ-EXP-007 AC-3`).

        Research is not re-run (`NFR-REL-003`): the version is unchanged, and
        the export is rendered from it again from scratch.
        """
        if export.status is ExportStatus.FAILED:
            export.status = ExportStatus.PENDING
            export.attempts = 0
            export.error = None
            export.lease_expires_at = None
            export.created_at = utcnow()
            self._session.flush()
        return export

    def version_number(self, export: Export) -> int:
        version = self._session.get(ResearchVersion, export.version_id)
        return version.version_number if version else 0
