"""The durable job state machine, and the internal observability record.

Implementation plan §5.6. **Postgres is the source of truth for job state**, and
that single decision is what removes `OPEN-03` from the critical path: a worker
claims the next runnable step with `SELECT ... FOR UPDATE SKIP LOCKED`, executes
it, checkpoints, and releases. A single-process local runner and a hosted queue
are then the same program with different dispatch, so where workers run in
production stays a deployment choice rather than a redesign.

Two columns carry the weight:

* `checkpoint` is written **before** a step marks itself complete, so a step
  reclaimed after a crash resumes from its last checkpoint rather than from the
  start (`NFR-REL-001`).
* `lease_owner` / `lease_expires_at` are how a dead worker's step is reclaimed.
  Every step must be idempotent, because a step whose lease expires while it is
  still running will be picked up by someone else.

`tool_invocations` is the diagnostic mirror of `activity_events`: tool names,
error kinds and latencies live here and are never rendered (`REQ-OBS-007`,
`REQ-ACT-003`).
"""

from __future__ import annotations

import datetime as dt
from uuid import UUID

from sqlalchemy import BigInteger, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from scrapr_core.db.base import Base, CreatedAt, Json, UuidPk, utcnow
from scrapr_core.db.enums import (
    RunKind,
    RunStatus,
    StepStatus,
    TerminationReason,
    pg_enum,
)

__all__ = ["ResearchRun", "RunStep", "ToolInvocation"]

STEP_LEASE_SECONDS = 300
"""How long a claimed step is held before another worker may reclaim it.

Longer than the slowest single step, short enough that a dead worker's step is
picked up promptly. Re-read against real tool timeouts once `TBD-02` is set.
"""

STEP_MAX_ATTEMPTS = 3
"""Two retries covers transient provider and network failure. A third failure is
a defect, not bad luck: the step is marked `dead` and the run terminates with
`termination_reason = 'failure'` rather than cycling forever."""


class ResearchRun(Base):
    """One execution producing one version (implementation plan §7.1)."""

    __tablename__ = "research_runs"
    __table_args__ = (
        Index("ix_research_runs_session_id", "session_id"),
        Index("ix_research_runs_version_id", "version_id"),
    )

    id: Mapped[UuidPk]

    session_id: Mapped[UUID] = mapped_column(ForeignKey("research_sessions.id"))
    version_id: Mapped[UUID] = mapped_column(ForeignKey("research_versions.id"))

    kind: Mapped[RunKind] = mapped_column(pg_enum(RunKind, "run_kind"))
    status: Mapped[RunStatus] = mapped_column(pg_enum(RunStatus, "run_status"))

    termination_reason: Mapped[TerminationReason | None] = mapped_column(
        pg_enum(TerminationReason, "termination_reason")
    )
    """Why the run stopped (`REQ-AGENT-005 AC-4`). Null while it is still
    running; set exactly once when it is not."""

    effort_used: Mapped[Json] = mapped_column(default=dict)
    """Budget consumed against the effort ceiling (`DEC-04`)."""

    tokens: Mapped[Json] = mapped_column(default=dict)
    cost_micros: Mapped[int | None] = mapped_column(BigInteger)
    """Millionths of a currency unit. Integer, because floating-point money is a
    reconciliation bug waiting to happen."""

    started_at: Mapped[dt.datetime | None]
    finished_at: Mapped[dt.datetime | None]


class RunStep(Base):
    """One stage attempt: the unit a worker claims, executes and checkpoints."""

    __tablename__ = "run_steps"
    __table_args__ = (
        UniqueConstraint("run_id", "ordinal"),
        # The claim query: runnable steps, oldest first.
        Index("ix_run_steps_status_lease_expires_at", "status", "lease_expires_at"),
    )

    id: Mapped[UuidPk]

    run_id: Mapped[UUID] = mapped_column(ForeignKey("research_runs.id"))

    stage: Mapped[str]
    """Stage name from implementation plan §5.1. Text rather than an enum: the
    stage list is still being built out through Phase 1, and a migration per
    stage added would be friction with no safety benefit — nothing branches on
    this value the way the validation gate branches on `claim_type`."""

    ordinal: Mapped[int]

    status: Mapped[StepStatus] = mapped_column(pg_enum(StepStatus, "step_status"))

    attempt: Mapped[int] = mapped_column(default=0)

    checkpoint: Mapped[Json] = mapped_column(default=dict)
    """Resume point, written before the step completes."""

    lease_owner: Mapped[str | None]
    """Identifies the worker holding the step. Diagnostic, not a lock: the lock
    is `FOR UPDATE SKIP LOCKED` plus `lease_expires_at`."""

    lease_expires_at: Mapped[dt.datetime | None]

    error: Mapped[str | None]

    created_at: Mapped[CreatedAt]
    updated_at: Mapped[CreatedAt] = mapped_column(onupdate=utcnow)


class ToolInvocation(Base):
    """Internal record of one tool call. Never rendered (`REQ-OBS-007`)."""

    __tablename__ = "tool_invocations"
    __table_args__ = (Index("ix_tool_invocations_run_id", "run_id"),)

    id: Mapped[UuidPk]

    run_id: Mapped[UUID] = mapped_column(ForeignKey("research_runs.id"))

    tool_name: Mapped[str]
    tool_category: Mapped[str]

    status: Mapped[str]
    error_kind: Mapped[str | None]
    """Classified failure (`REQ-TOOL-010`): timeout, rate limit, unavailable,
    malformed. Drives retry policy; the user sees a gap, not this."""

    latency_ms: Mapped[int | None]

    request_digest: Mapped[Json | None]
    """A digest of the request, not the request. Storing raw provider payloads
    would put retrieved content — and possibly credentials — in a table whose
    whole purpose is to be read casually during debugging."""

    cost_micros: Mapped[int | None] = mapped_column(BigInteger)

    created_at: Mapped[CreatedAt]
