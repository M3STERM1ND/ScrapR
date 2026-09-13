"""The sweep that turns deleted research into no research (`DEC-17`, `DEC-18`).

Runs from the worker loop every few minutes. Each pass does five things, each in
its own transaction so one stuck step cannot hold the others back:

1. **Expire** anonymous research whose session has lapsed, by marking it
   deleted — the same tombstone a user's own deletion writes, so it reaches the
   same purge.
2. **Purge** research deleted longer ago than `PURGE_GRACE`: stored objects
   first, then every row beneath the session. Research whose objects could not
   all be removed keeps its rows for the next pass, because the rows are the
   only record of which keys still need deleting.
3. **Drop** anonymous sessions that have lapsed and own nothing.
4. **Drop** account sessions that expired or were revoked a day ago or more.
5. **Drop** rate-limit windows that can no longer be read.

**Why a grace period at all.** A worker may be executing a step of the run under
its lease when the owner deletes the research. The run's wall-clock ceiling is
fifteen minutes (`RUN_CEILING_WALL_CLOCK_SECONDS`), so twenty minutes is past
any step that could still be writing.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, final
from uuid import UUID

from sqlalchemy import Select, delete, func, or_, select, update
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql.dml import Delete, Update

from scrapr_core.db.base import utcnow
from scrapr_core.db.models import (
    ActivityEvent,
    AnonymousSession,
    Claim,
    Conflict,
    ConversationMessage,
    Evidence,
    Export,
    ReportSection,
    ResearchQuestion,
    ResearchRun,
    ResearchSession,
    ResearchVersion,
    RunStep,
    Source,
    ToolInvocation,
    Upload,
    UserSession,
    Visualization,
)
from scrapr_core.db.repositories.anonymous_sessions import ANONYMOUS_LIFETIME
from scrapr_core.db.repositories.rate_limits import RateLimiter
from scrapr_core.lifecycle.deletion import (
    ObjectDeleter,
    cancel_runs,
    delete_objects,
    storage_keys_for_sessions,
)

__all__ = ["PURGE_BATCH", "PURGE_GRACE", "PurgeReport", "purge", "purge_rows"]

logger = logging.getLogger(__name__)

PURGE_GRACE: Final = dt.timedelta(minutes=20)
"""How long a tombstone stands before its rows go. See the module docstring."""

PURGE_BATCH: Final = 50
"""Sessions purged per pass. Bounded so one pass after a long outage is a few
short transactions rather than one long lock on half the schema."""

HOUSEKEEPING_AGE: Final = dt.timedelta(days=1)
"""How long a dead account session or a spent rate-limit window is kept. A day
is long enough to diagnose a sign-in complaint from yesterday."""


@final
@dataclass(frozen=True, slots=True)
class PurgeReport:
    """What one pass did. Logged by the worker; asserted on by the tests."""

    expired_sessions: int = 0
    purged_sessions: int = 0
    deferred_sessions: int = 0
    anonymous_sessions_removed: int = 0
    account_sessions_removed: int = 0
    rate_windows_removed: int = 0


def purge(
    session_factory: sessionmaker[Session],
    store: ObjectDeleter | None,
    *,
    now: dt.datetime | None = None,
    grace: dt.timedelta = PURGE_GRACE,
) -> PurgeReport:
    """Run one full pass. Safe to run concurrently with itself and with requests."""
    moment = now or utcnow()

    with session_factory() as session:
        expired = _expire_anonymous_research(session, moment)
        session.commit()

    purged = deferred = 0
    with session_factory() as session:
        candidates = _purgeable(session, moment - grace)
        for research_id in candidates:
            keys = storage_keys_for_sessions(session, [research_id])
            failed = 0
            if store is not None and keys:
                _, failed = delete_objects(store, keys)
            if failed:
                deferred += 1
                continue
            purge_rows(session, [research_id])
            session.commit()
            purged += 1

    with session_factory() as session:
        anonymous_removed = _remove_lapsed_anonymous_sessions(session, moment - grace)
        account_removed = _remove_dead_account_sessions(session, moment - HOUSEKEEPING_AGE)
        windows_removed = RateLimiter(session).purge_before(moment - HOUSEKEEPING_AGE)
        session.commit()

    report = PurgeReport(
        expired_sessions=expired,
        purged_sessions=purged,
        deferred_sessions=deferred,
        anonymous_sessions_removed=anonymous_removed,
        account_sessions_removed=account_removed,
        rate_windows_removed=windows_removed,
    )
    if report != PurgeReport():
        logger.info("purge: %s", report)
    return report


def _lapsed_anonymous(moment: dt.datetime) -> Select[tuple[UUID]]:
    """Anonymous sessions past their expiry, claimed or not."""
    expiry = func.coalesce(
        AnonymousSession.expires_at, AnonymousSession.last_seen_at + ANONYMOUS_LIFETIME
    )
    return select(AnonymousSession.id).where(expiry <= moment)


def _expire_anonymous_research(session: Session, moment: dt.datetime) -> int:
    """Tombstone research whose anonymous owner has lapsed (`DEC-17`).

    Unreachable already — `is_valid` refuses the session — so this changes
    nothing a visitor can see. It is what lets the purge find the rows.
    """
    ids = list(
        session.execute(
            select(ResearchSession.id).where(
                ResearchSession.deleted_at.is_(None),
                ResearchSession.anonymous_session_id.in_(_lapsed_anonymous(moment)),
            )
        ).scalars()
    )
    if not ids:
        return 0

    session.execute(
        update(ResearchSession)
        .where(ResearchSession.id.in_(ids))
        .values(deleted_at=moment)
        .execution_options(synchronize_session=False)
    )
    cancel_runs(session, ids, now=moment, kind="expired")
    return len(ids)


def _purgeable(session: Session, cutoff: dt.datetime) -> list[UUID]:
    return list(
        session.execute(
            select(ResearchSession.id)
            .where(ResearchSession.deleted_at.is_not(None), ResearchSession.deleted_at < cutoff)
            .order_by(ResearchSession.deleted_at)
            .limit(PURGE_BATCH)
        ).scalars()
    )


def purge_rows(session: Session, session_ids: Sequence[UUID]) -> None:
    """Hard-delete every row beneath these research sessions, and the sessions.

    Ordered by foreign key, children first. Join tables whose foreign keys
    cascade (`claim_evidence`, `conflict_evidence`, `visualization_evidence`,
    `message_evidence`, `question_evidence`, `upload_chunks`) go with their
    parents. The caller has already removed stored objects and commits.
    """
    if not session_ids:
        return

    ids = list(session_ids)
    versions = select(ResearchVersion.id).where(ResearchVersion.session_id.in_(ids))
    runs = select(ResearchRun.id).where(ResearchRun.session_id.in_(ids))

    def run(statement: Delete | Update) -> None:
        session.execute(statement.execution_options(synchronize_session=False))

    # The session points at a version; unhook it before versions go.
    run(
        update(ResearchSession)
        .where(ResearchSession.id.in_(ids))
        .values(current_version_id=None)
    )
    run(delete(Conflict).where(Conflict.version_id.in_(versions)))
    run(delete(Visualization).where(Visualization.version_id.in_(versions)))
    run(delete(Claim).where(Claim.version_id.in_(versions)))
    run(delete(ReportSection).where(ReportSection.version_id.in_(versions)))
    run(delete(ConversationMessage).where(ConversationMessage.session_id.in_(ids)))
    run(delete(ResearchQuestion).where(ResearchQuestion.version_id.in_(versions)))
    run(delete(Evidence).where(Evidence.version_id.in_(versions)))
    run(delete(Source).where(Source.version_id.in_(versions)))
    run(delete(Export).where(Export.version_id.in_(versions)))
    run(delete(ActivityEvent).where(ActivityEvent.session_id.in_(ids)))
    run(delete(ToolInvocation).where(ToolInvocation.run_id.in_(runs)))
    run(delete(RunStep).where(RunStep.run_id.in_(runs)))
    run(delete(ResearchRun).where(ResearchRun.session_id.in_(ids)))
    # One statement for every version of a session: `previous_version_id`
    # references a sibling, and Postgres checks that constraint at the end of
    # the statement, so deleting them together never orphans one mid-way.
    run(delete(ResearchVersion).where(ResearchVersion.session_id.in_(ids)))
    run(delete(Upload).where(Upload.session_id.in_(ids)))
    run(delete(ResearchSession).where(ResearchSession.id.in_(ids)))
    session.flush()


def _remove_lapsed_anonymous_sessions(session: Session, cutoff: dt.datetime) -> int:
    """Anonymous sessions lapsed before `cutoff` that no research references."""
    owned = select(ResearchSession.anonymous_session_id).where(
        ResearchSession.anonymous_session_id.is_not(None)
    )
    result = session.execute(
        delete(AnonymousSession)
        .where(
            AnonymousSession.id.in_(_lapsed_anonymous(cutoff)),
            AnonymousSession.id.not_in(owned),
        )
        .execution_options(synchronize_session=False)
    )
    return int(getattr(result, "rowcount", 0) or 0)


def _remove_dead_account_sessions(session: Session, cutoff: dt.datetime) -> int:
    result = session.execute(
        delete(UserSession)
        .where(or_(UserSession.expires_at < cutoff, UserSession.revoked_at < cutoff))
        .execution_options(synchronize_session=False)
    )
    return int(getattr(result, "rowcount", 0) or 0)
