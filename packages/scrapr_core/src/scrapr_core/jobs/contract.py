"""What a step is, and what a handler for one must do.

Implementation plan §5.6. A run is a durable state machine in Postgres: one row
per stage attempt, claimed with `FOR UPDATE SKIP LOCKED`, checkpointed before
completion, reclaimed after a lease expires.

**Every handler must be idempotent.** A step whose lease expires while it is
still running will be picked up by another worker, and idempotency is the only
thing that makes that safe. It is a requirement of the interface, not advice:
a handler that cannot be run twice is a handler that corrupts a run the first
time a worker is slow.

A handler returns its checkpoint. The runner persists it *before* marking the
step complete, so a crash between the two leaves a step that resumes from where
it got to rather than from the beginning (`NFR-REL-001`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, final

from sqlalchemy.orm import Session

from scrapr_core.db.models import ResearchRun, RunStep
from scrapr_core.domain.json import JsonMapping
from scrapr_core.domain.ownership import OwnerContext

__all__ = ["StepContext", "StepHandler", "StepPermanentError"]


class StepPermanentError(Exception):
    """Raised by a handler for a failure that retrying cannot fix.

    An ordinary exception means "try again": transient provider trouble, a
    network blip, a lock. This one skips the remaining attempts and kills the
    run, because burning two more attempts on a malformed input only delays the
    same answer.
    """


@final
@dataclass(frozen=True, slots=True)
class StepContext:
    """Everything a handler is given.

    It carries the run's `OwnerContext`, so a handler builds repositories the
    same way a request does and cannot reach research the run does not own
    (`REQ-SEC-011`).
    """

    session: Session
    run: ResearchRun
    step: RunStep
    owner: OwnerContext
    checkpoint: JsonMapping
    """Whatever the previous attempt of *this* step wrote. Empty on attempt one."""


class StepHandler(Protocol):
    """Executes one stage of a run.

    Handlers are `async` because retrieval and model calls are; the database
    work inside them is synchronous, which is the same split the tool contract
    documents.
    """

    async def execute(self, context: StepContext) -> JsonMapping:
        """Do the stage's work and return the checkpoint to persist.

        Must be safe to call twice with the same context.
        """
