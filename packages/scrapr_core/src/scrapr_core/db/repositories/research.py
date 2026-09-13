"""Ownership-scoped access to research sessions and versions.

**Every query filters on the `OwnerContext`.** `REQ-SEC-002 AC-2` requires
server-side enforcement on every access, and implementation plan §4.3 puts it
here rather than in route handlers, because a rule applied in twenty handlers is
a rule that is missing from the twenty-first. `_owned()` is the single place
that filter is expressed, and every read in this module goes through it.

**A session owned by someone else reads as absent, not as forbidden.** Returning
404 rather than 403 means an identifier cannot be probed for existence
(`REQ-SEC-009`), and it keeps the caller's error handling honest: there is no
"found but refused" branch to get wrong.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from scrapr_core.db.base import utcnow
from scrapr_core.db.enums import ResearchStatus, VersionStatus
from scrapr_core.db.models import ResearchSession, ResearchVersion
from scrapr_core.domain.ownership import OwnerContext

__all__ = ["ResearchRepository"]


class ResearchRepository:
    """Reads and writes research, always within one owner's scope."""

    def __init__(self, session: Session, owner: OwnerContext) -> None:
        self._session = session
        self._owner = owner

    # ------------------------------------------------------------------
    # Scoping
    # ------------------------------------------------------------------

    def _owned(self, statement: Select[tuple[ResearchSession]]) -> Select[tuple[ResearchSession]]:
        """Restrict a select to the current owner. The one enforcement point."""
        if self._owner.anonymous_session_id is not None:
            return statement.where(
                ResearchSession.anonymous_session_id == self._owner.anonymous_session_id
            )
        return statement.where(ResearchSession.owner_user_id == self._owner.user_id)

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    def create_session(
        self,
        objective: str,
        instructions: str | None = None,
        context_url: str | None = None,
        context_company: str | None = None,
        context_ticker: str | None = None,
    ) -> ResearchSession:
        """Create a session owned by the current owner (`REQ-INPUT-001..005`).

        The owner comes from the context, never from a caller-supplied argument,
        so there is no call shape that creates research for somebody else.
        """
        session = ResearchSession(
            owner_user_id=self._owner.user_id,
            anonymous_session_id=self._owner.anonymous_session_id,
            objective=objective,
            instructions=instructions,
            context_url=context_url,
            context_company=context_company,
            context_ticker=context_ticker,
            status=ResearchStatus.PENDING,
        )
        self._session.add(session)
        self._session.flush()
        return session

    def get_session(self, session_id: UUID) -> ResearchSession | None:
        """Load one session, or `None` if it does not exist *for this owner*."""
        statement = self._owned(select(ResearchSession)).where(
            ResearchSession.id == session_id,
            ResearchSession.deleted_at.is_(None),
        )
        return self._session.execute(statement).scalar_one_or_none()

    def list_sessions(self, limit: int = 50) -> Sequence[ResearchSession]:
        """History, newest first (`REQ-AUTH-005`).

        Bounded by default: an unbounded list query is a page that gets slower
        every week until someone notices.
        """
        statement = (
            self._owned(select(ResearchSession))
            .where(ResearchSession.deleted_at.is_(None))
            .order_by(ResearchSession.updated_at.desc())
            .limit(limit)
        )
        return self._session.execute(statement).scalars().all()

    # ------------------------------------------------------------------
    # Versions
    # ------------------------------------------------------------------

    def open_version(
        self, session_id: UUID, *, compare_with: ResearchVersion | None = None
    ) -> ResearchVersion | None:
        """Start the next version of a session, or `None` if it is not ours.

        The version number is derived from what already exists rather than kept
        as a counter, and `unique (session_id, version_number)` is what makes
        that safe: two concurrent opens collide instead of both winning.

        `compare_with` names the version the new one is measured against when
        that is not simply the latest — an update after a failed attempt is
        compared with the last version that completed (`DEC-20`), never with
        the failure.
        """
        session = self.get_session(session_id)
        if session is None:
            return None

        latest = self.latest_version(session_id)
        baseline = compare_with if compare_with is not None else latest
        version = ResearchVersion(
            session_id=session.id,
            version_number=1 if latest is None else latest.version_number + 1,
            status=VersionStatus.BUILDING,
            previous_version_id=None if baseline is None else baseline.id,
        )
        self._session.add(version)
        self._session.flush()

        session.current_version_id = version.id
        session.updated_at = utcnow()
        self._session.flush()
        return version

    def get_version(self, session_id: UUID, version_number: int) -> ResearchVersion | None:
        """One version of one owned session (`REQ-WORK-003`)."""
        if self.get_session(session_id) is None:
            return None

        statement = select(ResearchVersion).where(
            ResearchVersion.session_id == session_id,
            ResearchVersion.version_number == version_number,
        )
        return self._session.execute(statement).scalar_one_or_none()

    def latest_version(self, session_id: UUID) -> ResearchVersion | None:
        """The highest-numbered version, or `None` before the first run."""
        statement = (
            select(ResearchVersion)
            .where(ResearchVersion.session_id == session_id)
            .order_by(ResearchVersion.version_number.desc())
            .limit(1)
        )
        return self._session.execute(statement).scalar_one_or_none()

    def latest_completed_version(self, session_id: UUID) -> ResearchVersion | None:
        """The newest version that closed with a report, complete or partial.

        What Update Research measures against. A failed version produced no
        report to compare, and a building one is not finished.
        """
        statement = (
            select(ResearchVersion)
            .where(
                ResearchVersion.session_id == session_id,
                ResearchVersion.closed_at.is_not(None),
                ResearchVersion.status.in_((VersionStatus.COMPLETE, VersionStatus.PARTIAL)),
            )
            .order_by(ResearchVersion.version_number.desc())
            .limit(1)
        )
        return self._session.execute(statement).scalar_one_or_none()

    def list_versions(self, session_id: UUID) -> Sequence[ResearchVersion]:
        """Every version of an owned session, oldest first (`REQ-VER-008`)."""
        if self.get_session(session_id) is None:
            return []

        statement = (
            select(ResearchVersion)
            .where(ResearchVersion.session_id == session_id)
            .order_by(ResearchVersion.version_number)
        )
        return self._session.execute(statement).scalars().all()

    def close_version(
        self, version: ResearchVersion, status: VersionStatus
    ) -> ResearchVersion:
        """Close a version, after which nothing scoped to it may change.

        Immutability (`REQ-VER-002`) is not enforced by a database trigger; it
        is enforced by there being no code path that writes to a closed
        version's children. This method is the boundary that marks where that
        starts, which is what makes such a path reviewable.
        """
        version.status = status
        version.closed_at = utcnow()
        self._session.flush()
        return version
