"""The run budget: three counters, any one of which ends the run.

`DEC-04 §5`. Cost, wall clock and tool calls are bounded **simultaneously**, and
whichever binds first, binds. One counter would leave the other two failure
modes open: a cheap run that never finishes, or a fast one that spends the
month's budget.

**This object is deliberately mutable**, against the project's usual preference.
It is a ledger, and the whole point of `DEC-04 §5` is that areas draw from *one
shared pool* — unspent reservation returning to it so cheap areas fund expensive
ones. A value type copied on every spend would give each area its own private
pool and quietly delete that behaviour.

The V1 numbers are placeholders. `TBD-04` and `TBD-10` are Phase 8 measurement
work (`DEC-04 §11`), so these are generous on purpose: a ceiling that binds in
Phase 1 would be measuring the placeholder rather than the pipeline.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Literal, final

from scrapr_core.db.base import utcnow

__all__ = [
    "RUN_CEILING_COST_MICROS",
    "RUN_CEILING_TOOL_CALLS",
    "RUN_CEILING_WALL_CLOCK_SECONDS",
    "AreaReservation",
    "BudgetBound",
    "RunBudget",
]

RUN_CEILING_COST_MICROS = 2_000_000
"""$2.00 per run. Placeholder until `TBD-10`."""

RUN_CEILING_WALL_CLOCK_SECONDS = 900.0
"""Fifteen minutes. Placeholder until `TBD-04`."""

RUN_CEILING_TOOL_CALLS = 120
"""Operational backstop. Not a quality target: a run that wants 120 tool calls
has usually gone circular, and the no-progress rule should have caught it first."""

type BudgetBound = Literal["cost", "wall_clock", "tool_calls"]
"""Which counter ran out. Recorded, because "the run hit its ceiling" is a
different diagnosis depending on which ceiling."""


@final
class RunBudget:
    """What a run may spend, across every area."""

    def __init__(
        self,
        *,
        cost_micros: int = RUN_CEILING_COST_MICROS,
        wall_clock_seconds: float = RUN_CEILING_WALL_CLOCK_SECONDS,
        tool_calls: int = RUN_CEILING_TOOL_CALLS,
        started_at: dt.datetime | None = None,
    ) -> None:
        self._cost_micros = cost_micros
        self._wall_clock_seconds = wall_clock_seconds
        self._tool_calls = tool_calls
        self._started_at = started_at or utcnow()

    @property
    def cost_micros_remaining(self) -> int:
        return self._cost_micros

    @property
    def tool_calls_remaining(self) -> int:
        return self._tool_calls

    @property
    def started_at(self) -> dt.datetime:
        return self._started_at

    def elapsed_seconds(self, now: dt.datetime | None = None) -> float:
        return ((now or utcnow()) - self._started_at).total_seconds()

    def exhausted(self, now: dt.datetime | None = None) -> BudgetBound | None:
        """Which counter is spent, if any.

        Checked before every call rather than after, so a run stops *before*
        exceeding its ceiling rather than on the way past it.
        """
        if self._cost_micros <= 0:
            return "cost"
        if self._tool_calls <= 0:
            return "tool_calls"
        if self.elapsed_seconds(now) >= self._wall_clock_seconds:
            return "wall_clock"
        return None

    def spend(self, *, cost_micros: int = 0, tool_calls: int = 0) -> None:
        """Record spend. Counters may go negative and that is not an error.

        A call that overshoots the last of the budget still happened and still
        cost what it cost; pretending otherwise would under-report spend, and
        `exhausted()` already refuses the next one.
        """
        self._cost_micros -= cost_micros
        self._tool_calls -= tool_calls

    def reserve(self, tool_calls: int) -> AreaReservation:
        """Set aside an area's share, drawn from the shared pool.

        The reservation is a cap on what this area may spend, not a transfer:
        what it does not use is returned on `release()` so the next area can
        have it (`DEC-04 §5`).
        """
        granted = max(0, min(tool_calls, self._tool_calls))
        self._tool_calls -= granted
        return AreaReservation(budget=self, granted=granted)


@final
@dataclass(slots=True)
class AreaReservation:
    """One area's claim on the run's budget."""

    budget: RunBudget
    granted: int
    spent: int = 0
    released: bool = False

    @property
    def remaining(self) -> int:
        return self.granted - self.spent

    def can_spend(self) -> bool:
        """Whether this area may make another call.

        Both the reservation and the run have to allow it: an area that has
        budget left cannot proceed once the run is out of time.
        """
        return self.remaining > 0 and self.budget.exhausted() is None

    def spend(self, *, cost_micros: int = 0, tool_calls: int = 1) -> None:
        self.spent += tool_calls
        self.budget.spend(cost_micros=cost_micros)

    def release(self) -> None:
        """Return what this area did not use. Idempotent."""
        if self.released:
            return
        self.released = True
        self.budget.spend(tool_calls=-self.remaining)
