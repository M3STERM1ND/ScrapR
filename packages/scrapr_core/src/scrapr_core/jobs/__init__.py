"""The durable job state machine (implementation plan §5.6).

Postgres is the source of truth for job state, which is what lets a
single-process runner satisfy every Phase 1 requirement while `OPEN-03` — where
workers execute in production, and what dispatches them — stays open.
"""

from __future__ import annotations

from scrapr_core.jobs.contract import StepContext, StepHandler, StepPermanentError
from scrapr_core.jobs.runner import JobRunner

__all__ = ["JobRunner", "StepContext", "StepHandler", "StepPermanentError"]
