"""Visualization selection (`REQ-VIZ-001..006`, `DEC-11`).

One constraint carries this whole module and it is worth stating before the
tests: **a chart in this product is a claim about sourced data, not
decoration.** `REQ-VIZ-002 AC-1` requires every point trace to evidence, `AC-2`
forbids a chart built from data lacking source linkage, and `AC-3` forbids ever
rendering a placeholder as if it were real.

Those three are the difference between a research tool and a dashboard that
looks like one, so they are enforced by the types — `Point` cannot be
constructed without an evidence id — and the tests below are mostly about the
cases where the *easy* thing would be to draw something anyway.

The other half is `REQ-VIZ-003 AC-2`: the form follows the data, not the
section it sits under. Masterplan §13 lists revenue history as a line and
competitors as a comparison; the difference is the shape of the numbers, not
the heading above them.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from scrapr_core.db.enums import VizKind
from scrapr_core.evidence.normalize import MetricClass
from scrapr_core.orchestrator.visualize import (
    Chartable,
    Point,
    select_visualization,
)


def value(
    label: str,
    amount: str | None,
    *,
    metric: MetricClass = MetricClass.CURRENCY,
    comparable: bool = True,
    currency: str | None = "USD",
) -> Chartable:
    return Chartable(
        evidence_id=uuid4(),
        label=label,
        value=Decimal(amount) if amount is not None else None,
        metric=metric,
        currency=currency,
        comparable=comparable,
    )


# --------------------------------------------------------------------------
# Nothing is drawn from unsourced data — `REQ-VIZ-002`
# --------------------------------------------------------------------------


def test_a_point_cannot_exist_without_evidence() -> None:
    """`AC-1`, enforced by the type rather than checked later.

    There is no constructor that omits the evidence id, so a chart built from
    an unsourced number is not something this code can express.
    """
    with pytest.raises(TypeError):
        Point(label="FY2025", value=Decimal(1))  # type: ignore[call-arg]


def test_an_unlabelled_point_is_refused() -> None:
    """A value with no label has no position on an axis, and plotting it
    somewhere would be inventing where it goes."""
    with pytest.raises(ValueError, match="labelled"):
        Point(label="  ", value=Decimal(1), evidence_id=uuid4())


def test_non_comparable_values_produce_no_chart() -> None:
    """`AC-2`. Comparability is the same precondition `DEC-10 §4.3` applies to
    conflict, for the same reason: an axis is a comparison."""
    spec = select_visualization(
        "Revenue",
        [value("FY2024", "1", comparable=False), value("FY2025", "2", comparable=False)],
    )

    assert spec is None


def test_no_data_produces_no_chart_rather_than_an_empty_one() -> None:
    """`AC-3`: no placeholder is rendered as if it were real. An empty chart
    frame is a placeholder with axes."""
    assert select_visualization("Revenue", []) is None


def test_a_spec_exposes_every_source_behind_it() -> None:
    """`REQ-VIZ-004 AC-1` and `AC-2`: a chart exposes its sources, and all of
    them when it combines several."""
    points = [value("FY2023", "1"), value("FY2024", "2"), value("FY2025", "3")]

    spec = select_visualization("Revenue", points)

    assert spec is not None
    assert set(spec.evidence_ids) == {item.evidence_id for item in points}


# --------------------------------------------------------------------------
# The form follows the data — `REQ-VIZ-003`
# --------------------------------------------------------------------------


def test_three_periods_of_currency_is_a_line() -> None:
    """Masterplan §13: revenue history as a line chart."""
    spec = select_visualization(
        "Revenue", [value("FY2023", "1"), value("FY2024", "2"), value("FY2025", "3")]
    )

    assert spec is not None
    assert spec.kind is VizKind.LINE


def test_two_values_are_a_comparison_not_a_trend() -> None:
    """A line through two points is a claim about a trend the data cannot
    support. `REQ-VIZ-001 AC-2`: data unsuited to a form is not forced into
    it."""
    spec = select_visualization("Revenue", [value("FY2024", "1"), value("FY2025", "2")])

    assert spec is not None
    assert spec.kind is VizKind.COMPARISON


def test_one_value_is_a_metric() -> None:
    """Rendering a single datum as a chart implies a trend that does not
    exist."""
    spec = select_visualization("Revenue", [value("FY2025", "1")])

    assert spec is not None
    assert spec.kind is VizKind.METRIC


def test_counts_are_bars_not_lines() -> None:
    """A line between headcounts implies interpolation — that there was a
    meaningful value between two reporting dates. Bars say what counts are:
    magnitudes at points in time."""
    spec = select_visualization(
        "Headcount",
        [
            value("2023", "100", metric=MetricClass.COUNT, currency=None),
            value("2024", "150", metric=MetricClass.COUNT, currency=None),
            value("2025", "200", metric=MetricClass.COUNT, currency=None),
        ],
    )

    assert spec is not None
    assert spec.kind is VizKind.BAR


def test_the_form_ignores_the_section_title() -> None:
    """`AC-2`: selection adapts to the data available rather than to a fixed
    section-to-chart mapping. The same three numbers under two different
    headings produce the same chart."""
    points = [value("FY2023", "1"), value("FY2024", "2"), value("FY2025", "3")]

    financial = select_visualization("Financial performance", points)
    other = select_visualization("Something else entirely", points)

    assert financial is not None and other is not None
    assert financial.kind is other.kind


# --------------------------------------------------------------------------
# What the chart refuses to conflate
# --------------------------------------------------------------------------


def test_two_metric_classes_do_not_share_an_axis() -> None:
    """Revenue against headcount on one axis is a category error the reader
    has no way to detect from the picture."""
    spec = select_visualization(
        "Mixed",
        [
            value("FY2023", "1"),
            value("FY2024", "2"),
            value("FY2025", "3"),
            value("Staff", "400", metric=MetricClass.COUNT, currency=None),
        ],
    )

    assert spec is not None
    assert len(spec.series[0].points) == 3


def test_two_values_for_one_period_do_not_both_get_plotted() -> None:
    """Two figures for FY2025 is a disagreement, and `REQ-EVID-012` is where
    that belongs. Silently drawing one of them would resolve a conflict by
    picking, which is the thing the whole trust core refuses to do."""
    first = value("FY2025", "1")
    second = value("FY2025", "9")

    spec = select_visualization("Revenue", [first, second, value("FY2024", "2")])

    assert spec is not None
    labels = [point.label for point in spec.series[0].points]
    assert labels.count("FY2025") == 1


def test_the_unit_travels_with_the_spec() -> None:
    """An axis a reader cannot attach a unit to is a row of numbers."""
    spec = select_visualization(
        "Revenue", [value("FY2023", "1"), value("FY2024", "2"), value("FY2025", "3")]
    )

    assert spec is not None
    assert spec.unit == "USD"


def test_the_persisted_spec_carries_evidence_on_every_point() -> None:
    """`DEC-11 §4`: the spec is the contract, and it has nowhere to put an
    unsourced number. This is what a Phase 7 export renderer will read."""
    spec = select_visualization(
        "Revenue", [value("FY2023", "1"), value("FY2024", "2"), value("FY2025", "3")]
    )

    assert spec is not None
    stored = spec.as_json()
    series = stored["series"]
    assert isinstance(series, list)
    for point in series[0]["points"]:  # type: ignore[index]
        assert point["evidence_id"]

    # Renderer-agnostic: no colour, size or font in the stored form.
    flattened = str(stored).lower()
    for forbidden in ("color", "colour", "#", "px", "font"):
        assert forbidden not in flattened
