"""The single-process job runner over `run_steps`.

Implementation plan §5.6, and the reason `OPEN-03` blocks nothing. Because
Postgres holds job state, "where workers run and what dispatches them" is a
concurrency and hosting question rather than a design question: this loop, a
Redis-backed queue and a hosted queue all drive the same state machine, and only
this one has to exist for Phase 0 and Phase 1.

The claim is `SELECT ... FOR UPDATE SKIP LOCKED` over runnable steps. That makes
the loop correct with two workers as well as one, so the local runner is not a
toy that gets thrown away when the real answer arrives — it is the real
mechanism with a trivial dispatcher.

Three rules the loop enforces:

* **Steps of one run execute in order.** A step is runnable only when every
  lower ordinal in its run has completed.
* **A crashed step is reclaimed, not lost.** `running` past its lease is
  runnable again; the handler's idempotency is what makes that safe.
* **A poison step stops the run visibly.** Past `STEP_MAX_ATTEMPTS` the step is
  `dead` and the run terminates with `termination_reason = 'failure'`. Cycling
  forever, or presenting the result as complete, are both worse than failing
  (`REQ-AGENT-009 AC-3`).
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from collections.abc import Mapping
from uuid import UUID

from sqlalchemy import Select, and_, exists, or_, select
from sqlalchemy.orm import Session, sessionmaker

from scrapr_core.db.base import utcnow
from scrapr_core.db.enums import (
    ResearchStatus,
    RunStatus,
    StepStatus,
    TerminationReason,
    VersionStatus,
)
from scrapr_core.db.models import (
    ResearchRun,
    ResearchSession,
    ResearchVersion,
    RunStep,
)
from scrapr_core.db.models.runs import STEP_LEASE_SECONDS, STEP_MAX_ATTEMPTS
from scrapr_core.domain.ownership import OwnerContext
from scrapr_core.jobs.contract import StepContext, StepHandler, StepPermanentError
from scrapr_core.observability.telemetry import RunLedger, step_telemetry

__all__ = ["JobRunner"]

logger = logging.getLogger(__name__)

RUNNABLE_STATUSES = (StepStatus.PENDING, StepStatus.FAILED)
"""`FAILED` is runnable: it means "this attempt failed", not "give up". Giving
up is `DEAD`, which is terminal by design."""

_SESSION_STATUS = {
    RunStatus.COMPLETE: ResearchStatus.COMPLETE,
    RunStatus.PARTIAL: ResearchStatus.PARTIAL,
    RunStatus.FAILED: ResearchStatus.FAILED,
    RunStatus.RUNNING: ResearchStatus.RUNNING,
    RunStatus.PENDING: ResearchStatus.PENDING,
}
"""How a run's outcome reads to the person who asked the question."""


class JobRunner:
    """Claims runnable steps and executes them, one at a time."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        handlers: Mapping[str, StepHandler],
        *,
        worker_id: str,
        lease_seconds: int = STEP_LEASE_SECONDS,
        max_attempts: int = STEP_MAX_ATTEMPTS,
    ) -> None:
        self._session_factory = session_factory
        self._handlers = dict(handlers)
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts

    # ------------------------------------------------------------------
    # The loop
    # ------------------------------------------------------------------

    async def run_until_idle(self, max_steps: int = 1000) -> int:
        """Execute steps until none are runnable. Returns how many ran.

        Bounded so a handler that keeps re-queueing itself fails a test in
        seconds rather than hanging one.
        """
        executed = 0
        while executed < max_steps:
            if not await self.run_one():
                break
            executed += 1
        return executed

    async def run_one(self) -> bool:
        """Claim and execute a single step. Returns whether one was found."""
        step_id = self._claim()
        if step_id is None:
            return False

        with self._session_factory() as session:
            step = session.get(RunStep, step_id)
            if step is None:  # pragma: no cover - claimed rows exist
                return False
            run = session.get(ResearchRun, step.run_id)
            if run is None:  # pragma: no cover - enforced by the foreign key
                return False

            await self._execute(session, run, step)
            session.commit()

        return True

    # ------------------------------------------------------------------
    # Claiming
    # ------------------------------------------------------------------

    def _claim(self) -> UUID | None:
        """Take the next runnable step, marking it running under our lease.

        `SKIP LOCKED` is what makes this safe to run in parallel: two workers
        racing on the same row do not block each other, the loser simply takes
        the next one.
        """
        now = utcnow()
        with self._session_factory() as session:
            step = (
                session.execute(
                    self._runnable_query(now).with_for_update(skip_locked=True)
                )
                .scalars()
                .first()
            )

            if step is None:
                return None

            step.status = StepStatus.RUNNING
            step.attempt += 1
            step.lease_owner = self._worker_id
            step.lease_expires_at = now + dt.timedelta(seconds=self._lease_seconds)
            step.updated_at = now
            session.commit()
            return step.id

    def _runnable_query(self, now: dt.datetime) -> Select[tuple[RunStep]]:
        """Steps that may be claimed, oldest first.

        A step is runnable when it is pending or retryable, or when its lease
        has expired, **and** no earlier step in its run is still outstanding.
        """
        earlier = RunStep.__table__.alias("earlier")
        blocked = exists(
            select(earlier.c.id).where(
                and_(
                    earlier.c.run_id == RunStep.run_id,
                    earlier.c.ordinal < RunStep.ordinal,
                    earlier.c.status != StepStatus.COMPLETE,
                )
            )
        )

        return (
            select(RunStep)
            .where(
                or_(
                    RunStep.status.in_(RUNNABLE_STATUSES),
                    and_(
                        RunStep.status == StepStatus.RUNNING,
                        RunStep.lease_expires_at < now,
                    ),
                ),
                ~blocked,
            )
            .order_by(RunStep.created_at, RunStep.ordinal)
            .limit(1)
        )

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def _execute(
        self, session: Session, run: ResearchRun, step: RunStep
    ) -> None:
        handler = self._handlers.get(step.stage)
        if handler is None:
            # A stage with no handler cannot succeed on a later attempt: it is a
            # wiring error, so it poisons the step immediately rather than after
            # three identical failures.
            self._poison(
                session, run, step, f"no handler registered for {step.stage!r}", kind="no_handler"
            )
            return

        self._mark_started(session, run)

        context = StepContext(
            session=session,
            run=run,
            step=step,
            owner=self._owner_of(session, run),
            checkpoint=dict(step.checkpoint),
        )

        started = time.perf_counter()
        # Everything the step spends — model tokens, tool calls, cost — is
        # recorded against it as it happens (`REQ-OBS-004`, `DEC-25`).
        with step_telemetry(session, run.id, step.stage) as telemetry:
            try:
                checkpoint = await handler.execute(context)
            except StepPermanentError as exc:
                self._record_usage(run, step, telemetry.ledger, started)
                self._poison(session, run, step, str(exc), kind="permanent")
            except Exception as exc:
                logger.warning("step %s failed on attempt %s", step.id, step.attempt)
                self._record_usage(run, step, telemetry.ledger, started)
                self._fail(
                    session,
                    run,
                    step,
                    f"{type(exc).__name__}: {exc}",
                    kind=f"exception:{type(exc).__name__}",
                )
            else:
                self._record_usage(run, step, telemetry.ledger, started)
                # Checkpoint first, then completion. A crash between the two
                # costs one idempotent re-run; the reverse order would lose the
                # work.
                step.checkpoint = dict(checkpoint)
                session.flush()
                self._complete(session, run, step)

    @staticmethod
    def _record_usage(
        run: ResearchRun, step: RunStep, ledger: RunLedger, started: float
    ) -> None:
        """Add one step attempt's spend to its run (`REQ-OBS-003..005`).

        Accumulated rather than overwritten: a retried step spent its first
        attempt too, and a cost report that forgot failed attempts would
        understate exactly the runs worth investigating.
        """
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        tokens = dict(run.tokens or {})
        stages = dict(tokens.get("by_stage") or {})
        previous = dict(stages.get(step.stage) or {})
        stages[step.stage] = {
            "input_tokens": int(previous.get("input_tokens", 0)) + ledger.input_tokens,
            "output_tokens": int(previous.get("output_tokens", 0)) + ledger.output_tokens,
            "model_calls": int(previous.get("model_calls", 0)) + ledger.model_calls,
            "tool_calls": int(previous.get("tool_calls", 0)) + ledger.tool_calls,
            "cost_micros": int(previous.get("cost_micros", 0)) + ledger.cost_micros,
        }
        tokens["by_stage"] = stages
        tokens["input_tokens"] = int(tokens.get("input_tokens", 0)) + ledger.input_tokens
        tokens["output_tokens"] = int(tokens.get("output_tokens", 0)) + ledger.output_tokens
        tokens["model_calls"] = int(tokens.get("model_calls", 0)) + ledger.model_calls
        run.tokens = tokens
        run.cost_micros = (run.cost_micros or 0) + ledger.cost_micros

        effort = dict(run.effort_used or {})
        timings = dict(effort.get("stage_ms") or {})
        timings[step.stage] = int(timings.get(step.stage, 0)) + elapsed_ms
        effort["stage_ms"] = timings
        effort["tool_calls"] = int(effort.get("tool_calls", 0)) + ledger.tool_calls
        if ledger.retrieval:
            # Items returned, evidence grounded and dropped by reason, summed
            # across attempts like everything else here.
            retrieval = dict(effort.get("retrieval") or {})
            for name, value in ledger.retrieval.items():
                retrieval[name] = int(retrieval.get(name, 0)) + value
            effort["retrieval"] = retrieval
        run.effort_used = effort

    def _mark_started(self, session: Session, run: ResearchRun) -> None:
        """Move the run, and the research it belongs to, out of `pending`.

        The session's status is what the workspace header reads
        (`REQ-WORK-002`), so leaving it at `pending` while steps execute would
        tell the user their research is queued while they watch it run.
        """
        if run.status is not RunStatus.PENDING:
            return

        run.status = RunStatus.RUNNING
        if run.started_at is None:
            run.started_at = utcnow()

        research = session.get(ResearchSession, run.session_id)
        if research is not None:
            research.status = ResearchStatus.RUNNING
            research.updated_at = utcnow()
        session.flush()

    def _complete(self, session: Session, run: ResearchRun, step: RunStep) -> None:
        step.status = StepStatus.COMPLETE
        step.error = None
        step.lease_owner = None
        step.lease_expires_at = None
        step.updated_at = utcnow()
        session.flush()

        if not self._has_outstanding_steps(session, run):
            self._finish_run(session, run, RunStatus.COMPLETE)

    def _fail(
        self, session: Session, run: ResearchRun, step: RunStep, error: str, *, kind: str
    ) -> None:
        if step.attempt >= self._max_attempts:
            self._poison(session, run, step, error, kind=kind)
            return

        step.status = StepStatus.FAILED
        step.error = error
        step.lease_owner = None
        step.lease_expires_at = None
        step.updated_at = utcnow()
        session.flush()

    def _poison(
        self, session: Session, run: ResearchRun, step: RunStep, error: str, *, kind: str
    ) -> None:
        """Stop trying, and stop the run with it.

        The run records where and why (`REQ-OBS-001`): the stage, and a
        category a query can group failures by. The full error stays on the
        step, where only operators read it.
        """
        step.status = StepStatus.DEAD
        step.error = error
        step.lease_owner = None
        step.lease_expires_at = None
        step.updated_at = utcnow()
        run.failure_stage = step.stage
        run.failure_kind = kind
        session.flush()

        self._finish_run(session, run, RunStatus.FAILED, TerminationReason.FAILURE)


    # ------------------------------------------------------------------
    # Run bookkeeping
    # ------------------------------------------------------------------

    def _has_outstanding_steps(self, session: Session, run: ResearchRun) -> bool:
        statement = select(RunStep.id).where(
            RunStep.run_id == run.id,
            RunStep.status != StepStatus.COMPLETE,
        )
        return session.execute(statement).first() is not None

    def _finish_run(
        self,
        session: Session,
        run: ResearchRun,
        status: RunStatus,
        reason: TerminationReason | None = None,
    ) -> None:
        """Close the run and the version it was building.

        A handler that stopped for a reason only it knows — the effort ceiling,
        say (`REQ-AGENT-005 AC-4`) — records that itself, and the runner does
        not overwrite it. Absent one, a run that executed every planned step
        stopped because it ran out of work to do, which is what `sufficiency`
        means here.
        """
        if reason is not None:
            run.termination_reason = reason
        elif run.termination_reason is None:
            run.termination_reason = TerminationReason.SUFFICIENCY
        run.finished_at = utcnow()

        version = session.get(ResearchVersion, run.version_id)
        version_status = self._close_version(version, status)

        # `partial` outranks the runner's view. A handler that judged the
        # research incomplete (`REQ-AGENT-009`) knows something the step machine
        # does not: every step ran, and the result is still short. Letting the
        # runner overwrite that with `complete` would be the system telling the
        # user it covered ground it never reached.
        run.status = (
            RunStatus.PARTIAL
            if version_status is VersionStatus.PARTIAL and status is RunStatus.COMPLETE
            else status
        )

        # The session status is what the workspace header shows, so it has to
        # end where the version ended. A failed run under a session still
        # reading "researching" is the exact shape of `REQ-AGENT-009 AC-3`.
        research = session.get(ResearchSession, run.session_id)
        if research is not None:
            research.status = _SESSION_STATUS[run.status]
            research.updated_at = utcnow()

        session.flush()

    @staticmethod
    def _close_version(
        version: ResearchVersion | None, status: RunStatus
    ) -> VersionStatus | None:
        """Close the version, keeping any outcome a handler already recorded.

        A handler that wrote `partial` did so knowing what the research actually
        covered. The runner only decides the status of a version still marked
        `building`, which is the case where nothing else has an opinion.
        """
        if version is None or version.closed_at is not None:
            return version.status if version is not None else None

        if version.status is VersionStatus.BUILDING:
            version.status = (
                VersionStatus.COMPLETE
                if status is RunStatus.COMPLETE
                else VersionStatus.FAILED
            )
        version.closed_at = utcnow()
        return version.status

    def _owner_of(self, session: Session, run: ResearchRun) -> OwnerContext:
        """Rebuild the owning context so handlers use scoped repositories too.

        `REQ-SEC-011`: a worker must not be able to touch research it does not
        own, and the way to guarantee that is for it to go through the same
        enforcement point a request does.
        """
        research = session.get(ResearchSession, run.session_id)
        if research is None:  # pragma: no cover - enforced by the foreign key
            raise LookupError(f"run {run.id} references a missing research session")

        if research.owner_user_id is not None:
            return OwnerContext.for_user(research.owner_user_id)
        if research.anonymous_session_id is None:  # pragma: no cover - check constraint
            raise LookupError(f"research session {research.id} has no owner")
        return OwnerContext.for_anonymous(research.anonymous_session_id)
