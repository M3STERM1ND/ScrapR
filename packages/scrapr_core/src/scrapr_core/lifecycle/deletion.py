"""User-initiated deletion of a research session (`REQ-SEC-008`, `DEC-18`).

The request-time half. Three things happen before the response, in an order
chosen so a failure part-way leaves the user's instruction honoured:

1. **Hide.** `deleted_at` is set, and every read already filters on it.
2. **Stop.** Outstanding steps of every run become `dead` and the runs
   `failed`, so no worker claims the work again.
3. **Remove files.** Upload objects and export artifacts are deleted from
   storage. A storage failure is reported, not raised: steps 1 and 2 stand, and
   the purge retries the objects before it removes the rows that name them.

The rows themselves wait for `purge`, because a worker may be holding a step of
the run under its lease right now, and deleting rows out from under it would
turn a user's deletion into a worker crash.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol, final
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from scrapr_core.db.base import utcnow
from scrapr_core.db.enums import RunStatus, StepStatus, TerminationReason
from scrapr_core.db.models import (
    Export,
    ResearchRun,
    ResearchSession,
    ResearchVersion,
    RunStep,
    Upload,
)

__all__ = [
    "DeletionOutcome",
    "ObjectDeleter",
    "cancel_runs",
    "delete_objects",
    "delete_research",
    "storage_keys_for_sessions",
]

logger = logging.getLogger(__name__)


class ObjectDeleter(Protocol):
    """The one storage capability deletion needs.

    A protocol rather than `ObjectStore` itself, so the lifecycle can be tested
    against a recorder and does not need MinIO running to prove it asked for
    the right keys.
    """

    def delete(self, storage_key: str) -> None: ...


@final
@dataclass(frozen=True, slots=True)
class DeletionOutcome:
    """What a deletion did, for the caller to report or retry."""

    runs_cancelled: int
    objects_deleted: int
    objects_failed: int


def delete_research(
    session: Session,
    research: ResearchSession,
    store: ObjectDeleter | None,
    *,
    now: dt.datetime | None = None,
) -> DeletionOutcome:
    """Delete one research session the caller has already proven they own.

    Takes the loaded row, not an id: ownership is the repository's job
    (implementation plan §4.3), and a function that accepted a bare id would be
    a second place that job could be skipped.

    Idempotent. A session already marked deleted keeps its original
    `deleted_at`, so a retried request does not push its purge further away.
    """
    moment = now or utcnow()
    if research.deleted_at is None:
        research.deleted_at = moment
        research.updated_at = moment

    cancelled = cancel_runs(session, [research.id], now=moment)
    session.flush()

    deleted, failed = (0, 0)
    if store is not None:
        deleted, failed = delete_objects(
            store, storage_keys_for_sessions(session, [research.id])
        )

    return DeletionOutcome(
        runs_cancelled=cancelled,
        objects_deleted=deleted,
        objects_failed=failed,
    )


def cancel_runs(
    session: Session,
    session_ids: Sequence[UUID],
    *,
    now: dt.datetime | None = None,
    kind: str = "deleted_by_owner",
) -> int:
    """Stop every unfinished run of these sessions. Returns runs stopped.

    `dead`, the runner's terminal state, rather than a new one: the claim query
    already refuses it, so nothing about the runner has to learn that deletion
    exists.
    """
    if not session_ids:
        return 0
    moment = now or utcnow()

    unfinished = (
        session.execute(
            select(ResearchRun).where(
                ResearchRun.session_id.in_(session_ids),
                ResearchRun.status.in_((RunStatus.PENDING, RunStatus.RUNNING)),
            )
        )
        .scalars()
        .all()
    )
    if not unfinished:
        return 0

    run_ids = [run.id for run in unfinished]
    session.execute(
        update(RunStep)
        .where(
            RunStep.run_id.in_(run_ids),
            RunStep.status.in_((StepStatus.PENDING, StepStatus.FAILED, StepStatus.RUNNING)),
        )
        .values(
            status=StepStatus.DEAD,
            error="research deleted by its owner",
            lease_owner=None,
            lease_expires_at=None,
            updated_at=moment,
        )
        .execution_options(synchronize_session=False)
    )

    for run in unfinished:
        run.status = RunStatus.FAILED
        run.termination_reason = TerminationReason.FAILURE
        run.finished_at = moment
        # Not a defect, and the failure report must be able to say so
        # (`REQ-OBS-001 AC-2`).
        run.failure_kind = kind
    return len(unfinished)


def storage_keys_for_sessions(
    session: Session, session_ids: Sequence[UUID]
) -> list[str]:
    """Every stored object belonging to these sessions.

    Soft-deleted uploads are included: their objects should already be gone,
    and asking storage to delete a key twice is free, where missing one is a
    file that outlives the research it belonged to.
    """
    if not session_ids:
        return []

    upload_keys = session.execute(
        select(Upload.storage_key).where(Upload.session_id.in_(session_ids))
    ).scalars().all()
    export_keys = session.execute(
        select(Export.storage_key)
        .join(ResearchVersion, ResearchVersion.id == Export.version_id)
        .where(ResearchVersion.session_id.in_(session_ids))
    ).scalars().all()

    return sorted({key for key in (*upload_keys, *export_keys) if key})


def delete_objects(store: ObjectDeleter, keys: Iterable[str]) -> tuple[int, int]:
    """Delete each key, counting successes and failures rather than stopping.

    One unreachable object must not leave the rest behind. The exception is
    logged by type only: storage error text can include the endpoint, and the
    key is enough to find the object again.
    """
    deleted = failed = 0
    for key in keys:
        try:
            store.delete(key)
        except Exception as exc:
            failed += 1
            logger.warning("could not delete stored object %s: %s", key, type(exc).__name__)
        else:
            deleted += 1
    return deleted, failed
