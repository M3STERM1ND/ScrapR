"""Stage 11 — visualization selection (`REQ-VIZ-001..006`, `DEC-11`).

The agent decides when structured information benefits from a chart, without
the user configuring anything (`REQ-VIZ-001`). What makes that safe rather than
decorative is a single constraint, and it is enforced by the types here rather
than by asking a model to behave:

**A data point cannot exist without the evidence behind it.** `REQ-VIZ-002
AC-1` requires every point trace to sourced evidence and `AC-2` forbids a chart
built from data lacking source linkage, so `Point` takes an `evidence_id` and
there is no constructor that omits it. `AC-3` — no placeholder chart is ever
rendered as if it were real — follows: a spec with no points is not a chart, so
nothing is emitted.

**The form follows the data, not the section** (`REQ-VIZ-003 AC-2`). A section
called "Financial performance" does not imply a line chart; three years of one
metric does. Selection reads the evidence's shape — how many periods, how many
subjects, whether the values are comparable at all — and picks from there.

**Nothing is invented to make a chart work.** Where the data cannot support the
form the reader asked for, `REQ-VIZ-005 AC-3` requires saying so rather than
fabricating, which is why selection returns `None` rather than a thin chart.

Specs are renderer-agnostic (`DEC-11 §4`): they say what to draw and never how.
Colour, size and font come from the token layer at render time, which is what
lets one spec serve the workspace and a Phase 7 export theme without being
regenerated.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Final, final
from uuid import UUID

from scrapr_core.db.enums import VizKind
from scrapr_core.domain.json import JsonMapping
from scrapr_core.evidence.normalize import MetricClass

__all__ = [
    "MIN_POINTS_FOR_SERIES",
    "Point",
    "Series",
    "VizSpec",
    "select_visualization",
]

MIN_POINTS_FOR_SERIES: Final = 3
"""Below this, a line is a claim about a trend the data does not support.

Two points make a line through anything. `REQ-VIZ-001 AC-2` says data unsuited
to visualization is not forced into a chart, and two revenue figures are a
comparison, not a trend — so they become a comparison instead.
"""

MAX_TABLE_ROWS: Final = 20


@final
@dataclass(frozen=True, slots=True)
class Point:
    """One value on a chart, and the evidence it came from.

    `evidence_id` is required and there is no way around it. `REQ-VIZ-002
    AC-1`: every data point traces to evidence with a source, which is what
    separates this product's charts from decoration.
    """

    label: str
    value: Decimal
    evidence_id: UUID

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise ValueError("a data point must be labelled to be plotted")


@final
@dataclass(frozen=True, slots=True)
class Series:
    """One line, bar group or column of points."""

    name: str
    points: tuple[Point, ...]

    @property
    def evidence_ids(self) -> tuple[UUID, ...]:
        return tuple(point.evidence_id for point in self.points)


@final
@dataclass(frozen=True, slots=True)
class VizSpec:
    """What to draw. Never how (`DEC-11 §4`)."""

    kind: VizKind
    title: str
    series: tuple[Series, ...]
    unit: str | None = None
    """Currency code or unit label, so an axis can be read without guessing."""

    @property
    def evidence_ids(self) -> tuple[UUID, ...]:
        """Every source behind the chart (`REQ-VIZ-004 AC-1`, `AC-2`)."""
        seen: dict[UUID, None] = {}
        for series in self.series:
            for point in series.points:
                seen.setdefault(point.evidence_id, None)
        return tuple(seen)

    @property
    def is_renderable(self) -> bool:
        """`REQ-VIZ-002 AC-3`: an empty chart is not a chart."""
        return any(series.points for series in self.series)

    def as_json(self) -> JsonMapping:
        """The persisted form, for `visualizations.spec`."""
        return {
            "kind": self.kind.value,
            "title": self.title,
            "unit": self.unit,
            "series": [
                {
                    "name": series.name,
                    "points": [
                        {
                            "label": point.label,
                            "value": str(point.value),
                            "evidence_id": str(point.evidence_id),
                        }
                        for point in series.points
                    ],
                }
                for series in self.series
            ],
        }


@final
@dataclass(frozen=True, slots=True)
class Chartable:
    """One evidence row reduced to what visualization needs from it."""

    evidence_id: UUID
    label: str
    """The period, or the subject — whatever this value is indexed by."""

    value: Decimal | None
    metric: MetricClass
    currency: str | None = None
    comparable: bool = False


def select_visualization(
    title: str, chartable: Sequence[Chartable]
) -> VizSpec | None:
    """Choose a form for this data, or decline (`REQ-VIZ-003`).

    Returns `None` rather than a weak chart. `REQ-VIZ-001 AC-2` says data
    unsuited to visualization is not forced into one, and a chart of two
    unrelated numbers is worse than the sentence it replaced — it looks like
    analysis and is arithmetic.
    """
    # `REQ-VIZ-002 AC-2`: a value that could not be normalised has no place on
    # an axis. Comparability is the same precondition `DEC-10 §4.3` applies to
    # conflict, and for the same reason: an axis is a comparison.
    usable = [
        item
        for item in chartable
        if item.comparable and item.value is not None and item.label.strip()
    ]
    if not usable:
        return None

    # One metric class per chart. Plotting revenue against headcount on one
    # axis is a category error the reader has no way to detect.
    metric = usable[0].metric
    usable = [item for item in usable if item.metric is metric]

    by_label: dict[str, Chartable] = {}
    for item in usable:
        # One value per label. Two figures for FY2025 is a conflict, and
        # `REQ-EVID-012` is where that belongs, not silently on a chart.
        by_label.setdefault(item.label.strip(), item)

    points = tuple(
        Point(label=label, value=item.value, evidence_id=item.evidence_id)
        for label, item in sorted(by_label.items())
        if item.value is not None
    )
    if not points:
        return None

    currency = next((item.currency for item in usable if item.currency), None)
    kind = _kind_for(metric, len(points))
    if kind is None:
        return None

    spec = VizSpec(
        kind=kind,
        title=title,
        series=(Series(name=title, points=points),),
        unit=currency or _unit_for(metric),
    )
    return spec if spec.is_renderable else None


def _kind_for(metric: MetricClass, point_count: int) -> VizKind | None:
    """The form this data shape supports (`REQ-VIZ-003 AC-2`).

    Driven by the data, never by the section it sits under — masterplan §13
    lists revenue history as a line and competitors as a comparison table, and
    the difference between them is the shape of the numbers, not the heading
    above them.
    """
    if point_count == 1:
        # A single figure is a metric, and rendering one datum as a chart
        # implies a trend that does not exist.
        return VizKind.METRIC

    if point_count < MIN_POINTS_FOR_SERIES:
        return VizKind.COMPARISON

    if metric in (MetricClass.CURRENCY, MetricClass.RATIO, MetricClass.SHARE_PRICE):
        # Values over successive periods: a line reads as the trend it is.
        return VizKind.LINE

    if metric is MetricClass.COUNT:
        # Counts are magnitudes at points in time, not a continuous quantity;
        # bars say that and a line would imply interpolation between them.
        return VizKind.BAR

    return VizKind.TABLE


def _unit_for(metric: MetricClass) -> str | None:
    if metric is MetricClass.RATIO:
        return "%"
    return None
