"""Creating runs and the steps that make them resumable.

A run is enqueued by writing rows, not by calling a queue (implementation plan
§5.6). That is the whole trick: `POST /v1/research` commits a transaction and is
done, and whatever picks the work up — this repo's local runner now, a hosted
queue once `OPEN-03` closes — reads the same table.

Steps are written up front, all `pending`, in the order the stages run. Planning
the shape of a run before executing any of it is what makes progress visible and
resumption unambiguous: a worker that comes back after a crash reads the state
machine rather than reconstructing intent.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from scrapr_core.db.base import utcnow
from scrapr_core.db.enums import RunKind, RunStatus, StepStatus
from scrapr_core.db.models import ResearchRun, RunStep

__all__ = ["RunRepository"]


class RunRepository:
    """Creates runs and reads their steps."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create_run(
        self,
        session_id: UUID,
        version_id: UUID,
        stages: Sequence[str],
        kind: RunKind = RunKind.INITIAL,
    ) -> ResearchRun:
        """Enqueue a run and its steps, ready for a worker to claim."""
        if not stages:
            raise ValueError("a run needs at least one stage")

        run = ResearchRun(
            session_id=session_id,
            version_id=version_id,
            kind=kind,
            status=RunStatus.PENDING,
            started_at=utcnow(),
        )
        self._session.add(run)
        self._session.flush()

        self._session.add_all(
            RunStep(
                run_id=run.id,
                stage=stage,
                ordinal=ordinal,
                status=StepStatus.PENDING,
            )
            for ordinal, stage in enumerate(stages)
        )
        self._session.flush()
        return run

    def has_run_for_version(self, version_id: UUID) -> bool:
        """Whether a run has already been enqueued for this version.

        What makes starting a deferred run idempotent. A client retrying after a
        dropped connection must not get a second run against the same version:
        both would write claims into one report, and the reader would see every
        finding twice with no way to tell which pass produced it.
        """
        return (
            self._session.execute(
                select(ResearchRun.id)
                .where(ResearchRun.version_id == version_id)
                .limit(1)
            ).first()
            is not None
        )

    def steps(self, run_id: UUID) -> Sequence[RunStep]:
        """A run's steps in execution order."""
        statement = (
            select(RunStep).where(RunStep.run_id == run_id).order_by(RunStep.ordinal)
        )
        return self._session.execute(statement).scalars().all()

    def get_run(self, run_id: UUID) -> ResearchRun | None:
        return self._session.get(ResearchRun, run_id)
