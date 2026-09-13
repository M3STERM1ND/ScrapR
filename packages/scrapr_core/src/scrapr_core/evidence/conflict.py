"""Conflict detection and explanation (`DEC-10`, `REQ-EVID-012..014`).

Two values conflict when they are **comparable** and differ by more than the
tolerance for their metric class. Comparability is a precondition, not a
tolerance: values that cannot be brought to the same unit, currency and period
are `NON_COMPARABLE`, which is a third outcome distinct from both agreement and
disagreement.

`OPEN-16` stated the problem exactly — *"a 0.4% difference in a revenue figure
is probably rounding; a 12% difference is probably a real conflict"* — and named
the failure on both sides. Tuned too tight, the product reports rounding as
disagreement; too loose, it misses real disagreement. Both destroy the trust
core, which is the whole point of this phase.

**Three things are explicitly not conflicts** (`DEC-10 §4`), and each would
otherwise flood the report with disagreements that are not disagreements:
different reporting periods, an estimate against a reported figure, and
non-comparable values.

**Detection never discards** (`AC-2`). Competing evidence is preserved and both
values are shown with their source, tier and retrieval time (`AC-3`). An
unresolved conflict renders as unresolved (`REQ-EVID-014 AC-1`) and no value
from one is presented as settled (`AC-2`).

**Every number in the tolerance table is a guess that looks like a
measurement** (`DEC-10 §11`). None is derived from data about how often real
sources disagree, because that data does not exist yet. They are configuration
for exactly that reason.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum, unique
from typing import Final, Literal, final

from scrapr_core.evidence.normalize import MetricClass, NormalizedValue

__all__ = [
    "STALENESS_DAYS",
    "TOLERANCES",
    "ComparisonResult",
    "ConflictReason",
    "Tolerance",
    "compare",
    "explain",
    "is_stale",
]


@final
@dataclass(frozen=True, slots=True)
class Tolerance:
    """How close two values must be to count as the same.

    `relative` compares a proportion of the larger magnitude; `absolute`
    compares points. Which applies is a property of the metric class, not a
    preference: a relative tolerance on a 2% margin is 0.02 points, and no real
    disagreement is that small.
    """

    relative: Decimal | None = None
    absolute: Decimal | None = None


TOLERANCES: Final[dict[MetricClass, Tolerance]] = {
    # Rounding and unit presentation. `$1.2bn` vs `$1,198m` is 0.17%.
    MetricClass.CURRENCY: Tolerance(relative=Decimal("0.01")),
    # Points, not proportion. 2.0% vs 2.4% is a real 0.4-point disagreement
    # that a 1% relative rule would wave through.
    MetricClass.RATIO: Tolerance(absolute=Decimal("0.5")),
    # Genuinely fluctuates between two true reporting dates.
    MetricClass.COUNT: Tolerance(relative=Decimal("0.05")),
    # Intraday movement between two correct retrievals.
    MetricClass.SHARE_PRICE: Tolerance(relative=Decimal("0.02")),
    # The currency default, as the most common shape.
    MetricClass.UNKNOWN: Tolerance(relative=Decimal("0.01")),
}

STALENESS_DAYS: Final[dict[MetricClass, int]] = {
    MetricClass.SHARE_PRICE: 1,
    MetricClass.COUNT: 90,
    MetricClass.CURRENCY: 400,
    MetricClass.RATIO: 400,
    MetricClass.UNKNOWN: 180,
}
"""When evidence goes stale, per class (`DEC-10 §5`).

Read by `DEC-09 §4.2`, which demotes confidence one level. Staleness never
creates a conflict on its own: an old figure and a new one that differ are
usually both correct, and saying so is what explanation is for.

400 days for currency and ratio is "one reporting period past its period end"
made concrete — an annual figure stays current until the next annual figure
plausibly exists.
"""


@unique
class ConflictReason(StrEnum):
    """Why two values disagree (`REQ-EVID-013 AC-1`).

    The closed set that requirement names. An explanation outside it would be
    invented, which `AC-3` forbids.
    """

    PERIOD = "different_periods"
    DEFINITION = "different_definitions"
    CURRENCY = "different_currencies"
    ESTIMATE = "estimated_versus_reported"
    METHODOLOGY = "different_methodologies"
    STALE = "stale_information"
    UNEXPLAINED = "unexplained"
    """No supported explanation. `AC-3` means this is an honest outcome, not a
    gap to fill — and `REQ-EVID-014` renders it as unresolved."""


type Verdict = Literal["agrees", "conflicts", "non_comparable", "not_compared"]


@final
@dataclass(frozen=True, slots=True)
class ComparisonResult:
    """What comparing two values produced."""

    verdict: Verdict
    reason: ConflictReason | None = None
    detail: str = ""

    @property
    def is_conflict(self) -> bool:
        return self.verdict == "conflicts"


def is_stale(
    published_at: dt.datetime | None,
    metric: MetricClass,
    *,
    now: dt.datetime | None = None,
) -> bool:
    """Whether evidence is too old to be current for its class.

    Unknown publication date is **not** stale. Absence of a date is absence of
    evidence about age, and demoting confidence for it would punish sources
    that simply do not publish one.
    """
    if published_at is None:
        return False
    limit = STALENESS_DAYS.get(metric, STALENESS_DAYS[MetricClass.UNKNOWN])
    age = (now or dt.datetime.now(dt.UTC)) - published_at
    return age.days > limit


def _within(left: Decimal, right: Decimal, tolerance: Tolerance) -> bool:
    """Whether two magnitudes are the same value under `tolerance`."""
    difference = abs(left - right)

    if tolerance.absolute is not None:
        return difference <= tolerance.absolute

    if tolerance.relative is not None:
        scale = max(abs(left), abs(right))
        if scale == 0:
            return difference == 0
        return difference / scale <= tolerance.relative

    return difference == 0


def compare(
    left: NormalizedValue,
    right: NormalizedValue,
    *,
    left_period: str | None = None,
    right_period: str | None = None,
    left_basis: str | None = None,
    right_basis: str | None = None,
) -> ComparisonResult:
    """Decide whether two values agree, conflict, or cannot be compared.

    The exclusions run before the arithmetic, because each is a case where the
    numbers genuinely differ and the difference is not a disagreement.
    """
    # `DEC-10 §4.1`. FY2024 revenue and FY2025 revenue are two facts.
    # `REQ-EVID-009 AC-3` makes the period difference itself the explanation.
    if left_period and right_period and left_period != right_period:
        return ComparisonResult(
            "not_compared",
            ConflictReason.PERIOD,
            f"different reporting periods: {left_period} and {right_period}",
        )

    # `DEC-10 §4.2`, and `REQ-TOOL-004 AC-3` is what makes it detectable. An
    # analyst estimate against a filed figure is not a source being wrong.
    if left_basis and right_basis and left_basis != right_basis:
        return ComparisonResult(
            "not_compared",
            ConflictReason.ESTIMATE,
            f"{left_basis} compared against {right_basis}",
        )

    # `DEC-10 §4.3`. Comparability is a precondition, not a tolerance.
    if not left.comparable or not right.comparable:
        return ComparisonResult(
            "non_comparable", None, "one or both values could not be normalized"
        )

    if left.currency and right.currency and left.currency != right.currency:
        # Not converted. An exchange rate is itself a dated figure, and picking
        # one would make this module's answer depend on a source nobody cited.
        return ComparisonResult(
            "non_comparable",
            ConflictReason.CURRENCY,
            f"{left.currency} against {right.currency}, not converted",
        )

    if bool(left.currency) != bool(right.currency):
        # A money amount and a bare number are not the same kind of value,
        # however their metric classes were inferred. This is the comparison
        # that let $1.2bn revenue "disagree" with a 4.5-out-of-5 rating.
        return ComparisonResult(
            "non_comparable",
            ConflictReason.CURRENCY,
            "one value is a currency amount and the other is not",
        )

    if left.is_percentage != right.is_percentage:
        return ComparisonResult(
            "non_comparable",
            ConflictReason.DEFINITION,
            "one value is a percentage and the other is not",
        )

    if left.metric_class is not right.metric_class:
        return ComparisonResult(
            "non_comparable",
            ConflictReason.DEFINITION,
            f"{left.metric_class.value} against {right.metric_class.value}",
        )

    assert left.value is not None and right.value is not None  # noqa: S101
    tolerance = TOLERANCES.get(left.metric_class, TOLERANCES[MetricClass.UNKNOWN])

    if _within(left.value, right.value, tolerance):
        return ComparisonResult("agrees")

    return ComparisonResult(
        "conflicts",
        None,
        f"{left.reported} against {right.reported}",
    )


def explain(
    left: NormalizedValue,
    right: NormalizedValue,
    *,
    left_published: dt.datetime | None = None,
    right_published: dt.datetime | None = None,
    now: dt.datetime | None = None,
) -> ComparisonResult:
    """Attribute a detected conflict, or decline to (`REQ-EVID-013`).

    `AC-3` is the constraint that shapes this: an explanation is offered **only
    when evidence supports it**, and explanations are never invented. So the
    only attributions made here are ones with a fact behind them — a currency
    difference, a definition difference, or one source being materially older
    than the other.

    Everything else is `UNEXPLAINED`, which `REQ-EVID-014` renders as an
    unresolved conflict. That is an honest answer, and a better one than a
    plausible story about methodology nobody checked.
    """
    if left.currency and right.currency and left.currency != right.currency:
        return ComparisonResult(
            "conflicts",
            ConflictReason.CURRENCY,
            f"reported in {left.currency} and {right.currency}",
        )

    if left.metric_class is not right.metric_class:
        return ComparisonResult(
            "conflicts",
            ConflictReason.DEFINITION,
            f"measuring {left.metric_class.value} against {right.metric_class.value}",
        )

    left_stale = is_stale(left_published, left.metric_class, now=now)
    right_stale = is_stale(right_published, right.metric_class, now=now)
    if left_stale != right_stale:
        stale_side = "the first" if left_stale else "the second"
        return ComparisonResult(
            "conflicts",
            ConflictReason.STALE,
            f"{stale_side} figure is outside the currency window for "
            f"{left.metric_class.value}",
        )

    # `REQ-EVID-013 AC-3`: no supported explanation, so none is offered.
    # `REQ-EVID-014` takes it from here and states it as unresolved.
    return ComparisonResult(
        "conflicts",
        ConflictReason.UNEXPLAINED,
        "the sources disagree and the evidence does not say why",
    )
