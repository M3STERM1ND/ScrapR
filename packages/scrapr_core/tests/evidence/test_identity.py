"""What a figure measures, and when two figures may be compared at all.

The regression these pin is the first real run's headline defect: NVIDIA's
revenue and a Glassdoor compensation rating were cited by one claim, both read
as ratios, and $1.2bn against 4.5 was published as "sources disagree". Every
case below is a pair that must not be compared, plus the controls proving
comparison still happens where it should.
"""

from __future__ import annotations

import datetime as dt

import pytest

from scrapr_core.db.enums import NormalizationStatus
from scrapr_core.evidence.conflict import ConflictReason, compare
from scrapr_core.evidence.identity import (
    IMPLICIT_SUBJECT,
    identify,
    mismatch,
    subject_aliases,
)
from scrapr_core.evidence.normalize import MetricClass, classify_statement, normalize_value

ALIASES = subject_aliases("NVIDIA Corporation (NVDA)")

REVENUE = "NVIDIA reported $1.2bn revenue for FY2025, representing an 18% year-over-year increase."
RATING = (
    "NVIDIA employees rate their compensation and benefits as 4.5 out of 5 according "
    "to anonymously submitted Glassdoor reviews."
)


def ident(text: str, **kwargs: object):  # type: ignore[no-untyped-def]
    return identify(text, aliases=ALIASES, **kwargs)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# The NVIDIA pair
# --------------------------------------------------------------------------


def test_revenue_and_an_employee_rating_are_different_measurements() -> None:
    assert ident(REVENUE).metric == "revenue"
    assert ident(RATING).metric == "rating:compensation"
    assert mismatch(ident(REVENUE), ident(RATING)) is not None


def test_the_revenue_figure_is_currency_even_beside_a_percentage() -> None:
    """The sentence carries "18%", which used to make the whole statement a
    ratio while the stored value was the $1.2bn."""
    assert classify_statement(REVENUE) is MetricClass.CURRENCY
    normalized = normalize_value(REVENUE)
    assert normalized.value == 1_200_000_000
    assert normalized.currency == "USD"
    assert not normalized.is_percentage


def test_rate_as_a_verb_is_not_a_ratio() -> None:
    assert classify_statement(RATING) is not MetricClass.RATIO
    assert classify_statement("The interest rate was 5%") is MetricClass.RATIO


def test_the_numeric_comparison_itself_refuses_money_against_a_bare_number() -> None:
    """Defence in depth: even a pair that slipped past identity is not a
    conflict when one side is a currency amount and the other is not."""
    result = compare(normalize_value(REVENUE), normalize_value(RATING))

    assert not result.is_conflict
    assert result.verdict == "non_comparable"
    assert result.reason is ConflictReason.CURRENCY


def test_a_percentage_is_not_compared_with_a_plain_figure() -> None:
    result = compare(
        normalize_value("Headcount grew 5%"), normalize_value("Headcount of 5,000 employees")
    )

    assert not result.is_conflict


# --------------------------------------------------------------------------
# Subject
# --------------------------------------------------------------------------


def test_the_subject_is_recognised_by_name_ticker_or_implication() -> None:
    assert ident("NVDA revenue was $130.5bn in fiscal 2025.").subject == {IMPLICIT_SUBJECT}
    assert ident("Nvidia Corp revenue was $130.5bn in fiscal 2025.").subject == {IMPLICIT_SUBJECT}
    assert ident("Revenue was $130.5bn in fiscal 2025.").subject == {IMPLICIT_SUBJECT}


def test_another_companys_figure_is_not_the_subjects() -> None:
    nvidia = ident("NVIDIA revenue was $130.5bn for fiscal 2025.")
    amd = ident("AMD revenue was $25.8bn for fiscal 2025.")

    assert nvidia.metric == amd.metric == "revenue"
    assert nvidia.period == amd.period == "FY2025"
    assert mismatch(nvidia, amd) == "different subjects"


def test_a_publisher_attribution_does_not_change_the_subject() -> None:
    first = ident("Revenue was $130.5bn for fiscal 2025, according to Reuters.")
    second = ident("NVIDIA revenue was $130.5bn for fiscal 2025.")

    assert mismatch(first, second) is None


# --------------------------------------------------------------------------
# Metric
# --------------------------------------------------------------------------


def test_segment_revenue_is_not_total_revenue() -> None:
    total = ident("NVIDIA revenue was $130.5bn for fiscal 2025.")
    segment = ident("NVIDIA data center revenue was $115.2bn for fiscal 2025.")

    assert segment.metric == "revenue@data_center"
    assert mismatch(total, segment) is not None


def test_guidance_is_not_a_reported_figure() -> None:
    reported = ident("NVIDIA revenue was $39.3bn in Q4 FY2025.")
    guided = ident("NVIDIA expects revenue of $43bn in Q1 FY2026.")

    assert guided.metric is not None and guided.metric.startswith("forecast:")
    assert mismatch(reported, guided) is not None


def test_growth_is_qualified_by_what_grew() -> None:
    revenue_growth = ident("NVIDIA revenue grew 114% in fiscal 2025.")
    headcount_growth = ident("NVIDIA headcount grew 21% in fiscal 2025.")

    assert revenue_growth.metric != headcount_growth.metric


def test_a_figure_no_term_describes_is_compared_with_nothing() -> None:
    assert ident("The figure was 42 in fiscal 2025.").metric is None


# --------------------------------------------------------------------------
# Period
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "period"),
    [
        pytest.param("Revenue was $39.3bn in Q4 FY2025.", "Q4-2025", id="quarter"),
        pytest.param("Revenue was $35.1bn in the third quarter of fiscal 2025.", "Q3-2025", id="quarter-words"),
        pytest.param("Revenue was $130.5bn for FY25.", "FY2025", id="short-fy"),
        pytest.param("Revenue was $130.5bn for fiscal year 2025.", "FY2025", id="fiscal-year"),
        pytest.param("Revenue was $130.5bn in 2025.", "FY2025", id="bare-year"),
        pytest.param("Revenue rose to $130.5bn in 2025 from $60.9bn in 2024.", None, id="two-years"),
        pytest.param("Revenue was $130.5bn.", None, id="none"),
    ],
)
def test_the_period_is_read_from_the_statement(text: str, period: str | None) -> None:
    assert ident(text).period == period


def test_a_structured_period_wins_over_the_text() -> None:
    found = ident("Revenue was $130.5bn.", period_end=dt.date(2025, 1, 26))

    assert found.period == "FY2025"


def test_revenue_with_a_period_on_one_side_only_is_not_compared() -> None:
    dated = ident("NVIDIA revenue was $130.5bn for fiscal 2025.")
    undated = ident("NVIDIA revenue was $60.9bn.")

    assert mismatch(dated, undated) is not None


def test_undated_revenue_on_both_sides_is_not_compared() -> None:
    """A flow measure with no period on either side could be any two years."""
    assert mismatch(ident("Revenue was $130.5bn."), ident("Revenue was $60.9bn.")) is not None


def test_undated_point_in_time_readings_are_compared() -> None:
    """A share price is a reading, not a total; staleness speaks to its age."""
    first = ident("NVIDIA share price was $181.20.")
    second = ident("NVIDIA share price was $140.10.")

    assert mismatch(first, second) is None


# --------------------------------------------------------------------------
# The control: same subject, metric and period still compares
# --------------------------------------------------------------------------


def test_the_same_measurement_from_two_sources_is_compared_and_can_conflict() -> None:
    left_text = "Acme Corp reported revenue of USD 1.2bn for fiscal 2025."
    right_text = "Acme Corp reported revenue of USD 1.9bn for fiscal 2025."
    aliases = subject_aliases("Acme Corp")
    left = identify(left_text, aliases=aliases)
    right = identify(right_text, aliases=aliases)

    assert mismatch(left, right) is None
    result = compare(
        normalize_value(left_text),
        normalize_value(right_text),
        left_period=left.period,
        right_period=right.period,
    )
    assert result.is_conflict


def test_subject_aliases_cover_name_core_and_ticker() -> None:
    assert {"nvidia corporation", "nvidia", "nvda"} <= ALIASES
    assert subject_aliases(None, "  ") == frozenset()


def test_an_unnormalisable_value_stays_non_comparable() -> None:
    assert normalize_value("Revenue reached 1.2bn").status is NormalizationStatus.NON_COMPARABLE
