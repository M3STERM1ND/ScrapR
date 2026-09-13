"""Normalization, conflict detection and explanation (`REQ-EVID-008..014`).

The cases below are the specification `DEC-10 §9.3` names, plus the three
exclusions that keep the report readable. They matter in a specific way: every
false positive here becomes a "sources disagree" banner on a claim where
nothing is wrong, and every false negative is a real disagreement the product
promised to surface and did not.

`DEC-10 §11` is candid that every tolerance is a guess that looks like a
measurement. These tests pin the *behaviour* of the table, not its correctness
— when the numbers change against real data, these are what say what changed.
"""

from __future__ import annotations

import datetime as dt

import pytest

from scrapr_core.db.enums import NormalizationStatus
from scrapr_core.evidence.conflict import ConflictReason, compare, explain, is_stale
from scrapr_core.evidence.normalize import (
    MetricClass,
    classify_metric,
    normalize_value,
)

# --------------------------------------------------------------------------
# Normalization — `REQ-EVID-008`
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("Revenue of $1.2bn", 1_200_000_000, id="billions"),
        pytest.param("Revenue of $1,200m", 1_200_000_000, id="millions-comma"),
        pytest.param("Revenue of USD 1200 million", 1_200_000_000, id="code-and-word"),
        pytest.param("Revenue of $1.2 trillion", 1_200_000_000_000, id="trillions"),
    ],
)
def test_scale_is_applied(text: str, expected: int) -> None:
    """`AC-1`. `$1.2bn` and `$1,200m` are one number wearing different
    clothes, and conflict detection must not see two."""
    assert normalize_value(text).value == expected


def test_the_reported_form_is_never_discarded() -> None:
    """`AC-2`: normalization is non-destructive. A user inspecting a citation
    must see what the source said, not what this module made of it."""
    normalized = normalize_value("Revenue of $1.2bn")

    assert "1.2bn" in normalized.reported
    assert normalized.value == 1_200_000_000


def test_a_currency_figure_with_no_currency_is_non_comparable() -> None:
    """`AC-3`, and the case `DEC-10 §4.3` depends on.

    A bare number is not "probably dollars". Guessing is how two correct
    sources reporting in different currencies become a conflict.
    """
    normalized = normalize_value("Revenue reached 1.2bn")

    assert normalized.status is NormalizationStatus.NON_COMPARABLE
    assert not normalized.comparable


def test_a_statement_with_no_number_is_non_comparable_not_zero() -> None:
    normalized = normalize_value("The company grew strongly.")

    assert normalized.status is NormalizationStatus.NON_COMPARABLE
    assert normalized.value is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("Revenue of $1.2bn", MetricClass.CURRENCY, id="revenue"),
        pytest.param("Gross margin of 75%", MetricClass.RATIO, id="margin"),
        pytest.param("Revenue growth of 18%", MetricClass.RATIO, id="growth-wins"),
        pytest.param("4,000 employees", MetricClass.COUNT, id="headcount"),
        pytest.param("Share price of $45", MetricClass.SHARE_PRICE, id="price"),
        pytest.param("The weather was fine", MetricClass.UNKNOWN, id="unknown"),
    ],
)
def test_metric_class_inference(text: str, expected: MetricClass) -> None:
    """`DEC-10 §10` left this open; the tolerance table is keyed by it.

    "Revenue growth of 18%" is the case worth pinning: it mentions revenue and
    is a ratio, and reading it as currency would apply a relative tolerance to
    a percentage.
    """
    assert classify_metric(text) == expected


# --------------------------------------------------------------------------
# The specification cases — `DEC-10 §9.3`
# --------------------------------------------------------------------------


def test_rounding_is_not_a_conflict() -> None:
    """`$1.2bn` against `$1,198m` is 0.17% — the case `OPEN-16` opened with."""
    result = compare(normalize_value("Revenue of $1.2bn"), normalize_value("Revenue of $1,198m"))

    assert result.verdict == "agrees"


def test_a_real_currency_difference_is_a_conflict() -> None:
    result = compare(
        normalize_value("Revenue of $1.2bn"), normalize_value("Revenue of $1.35bn")
    )

    assert result.is_conflict


def test_a_margin_gap_over_the_absolute_tolerance_is_a_conflict() -> None:
    """2.0% against 2.8% is 0.8 points, over the 0.5-point tolerance."""
    result = compare(
        normalize_value("Operating margin of 2.0%"),
        normalize_value("Operating margin of 2.8%"),
    )

    assert result.is_conflict


def test_a_small_margin_gap_is_rounding_not_disagreement() -> None:
    """The reason ratios are measured in points rather than proportionally.

    0.3 points is within tolerance. Under the 1% *relative* rule that currency
    uses, the tolerance on a 2% base would be 0.02 points — so two sources
    reporting 2.00% and 2.03% would be published as disagreeing, which is
    rounding presented as a finding.

    `DEC-10 §9.3` originally gave this case backwards; the correction is
    recorded there.
    """
    result = compare(
        normalize_value("Operating margin of 2.0%"),
        normalize_value("Operating margin of 2.3%"),
    )

    assert result.verdict == "agrees"


def test_headcount_moves_without_disagreeing() -> None:
    """4,000 against 4,150 is under 5% — two true reporting dates, not two
    sources contradicting each other."""
    result = compare(
        normalize_value("4,000 employees"), normalize_value("4,150 employees")
    )

    assert result.verdict == "agrees"


# --------------------------------------------------------------------------
# The three exclusions — `DEC-10 §4`
# --------------------------------------------------------------------------


def test_different_periods_are_not_compared() -> None:
    """`§4.1`, `REQ-EVID-009 AC-3`. FY2024 revenue and FY2025 revenue are two
    facts, and the period difference is itself the explanation."""
    result = compare(
        normalize_value("Revenue of $1.2bn"),
        normalize_value("Revenue of $1.5bn"),
        left_period="2024",
        right_period="2025",
    )

    assert result.verdict == "not_compared"
    assert result.reason is ConflictReason.PERIOD


def test_an_estimate_against_a_reported_figure_is_not_a_conflict() -> None:
    """`§4.2`, and `REQ-TOOL-004 AC-3` is what makes it detectable.

    An analyst estimate of $1.3bn against a filed $1.2bn is not a source being
    wrong. It is an estimate, and counting it as disagreement would flood the
    report.
    """
    result = compare(
        normalize_value("Revenue of $1.2bn"),
        normalize_value("Revenue of $1.35bn"),
        left_basis="reported",
        right_basis="estimate",
    )

    assert result.verdict == "not_compared"
    assert result.reason is ConflictReason.ESTIMATE


def test_two_reported_figures_that_disagree_are_in_scope() -> None:
    """The other half of `§4.2`: the exclusion is about *differing* bases."""
    result = compare(
        normalize_value("Revenue of $1.2bn"),
        normalize_value("Revenue of $1.35bn"),
        left_basis="reported",
        right_basis="reported",
    )

    assert result.is_conflict


def test_a_non_comparable_value_is_neither_agreement_nor_conflict() -> None:
    """`§4.3`. Treating it as agreement hides a gap; treating it as conflict
    invents one."""
    result = compare(
        normalize_value("Revenue reached 1.2bn"), normalize_value("Revenue of $1.2bn")
    )

    assert result.verdict == "non_comparable"


def test_different_currencies_are_not_silently_converted() -> None:
    """An exchange rate is itself a dated figure. Picking one would make this
    answer depend on a source nobody cited."""
    result = compare(
        normalize_value("Revenue of $1.2bn"), normalize_value("Revenue of £1.2bn")
    )

    assert result.verdict == "non_comparable"
    assert result.reason is ConflictReason.CURRENCY


# --------------------------------------------------------------------------
# Explanation — `REQ-EVID-013`
# --------------------------------------------------------------------------


def test_a_currency_difference_is_an_explained_conflict() -> None:
    """`AC-1` names currency as an attributable cause."""
    result = explain(
        normalize_value("Revenue of $1.2bn"), normalize_value("Revenue of €1.2bn")
    )

    assert result.reason is ConflictReason.CURRENCY


def test_staleness_explains_when_only_one_side_is_old() -> None:
    now = dt.datetime(2026, 6, 1, tzinfo=dt.UTC)
    result = explain(
        normalize_value("Share price of $45"),
        normalize_value("Share price of $61"),
        left_published=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        right_published=dt.datetime(2026, 5, 31, tzinfo=dt.UTC),
        now=now,
    )

    assert result.reason is ConflictReason.STALE


def test_an_unexplainable_conflict_says_so() -> None:
    """`AC-3`: an explanation is offered only when evidence supports it, and
    explanations are never invented.

    `UNEXPLAINED` is an honest answer and a better one than a plausible story
    about methodology nobody checked. `REQ-EVID-014` renders it as unresolved.
    """
    result = explain(
        normalize_value("Revenue of $1.2bn"), normalize_value("Revenue of $1.9bn")
    )

    assert result.reason is ConflictReason.UNEXPLAINED


# --------------------------------------------------------------------------
# Staleness — read by `DEC-09 §4.2`
# --------------------------------------------------------------------------


def test_a_share_price_goes_stale_in_a_day_and_revenue_does_not() -> None:
    """Per class, for the same reason tolerance is per class."""
    now = dt.datetime(2026, 6, 1, tzinfo=dt.UTC)
    a_week_ago = dt.datetime(2026, 5, 25, tzinfo=dt.UTC)

    assert is_stale(a_week_ago, MetricClass.SHARE_PRICE, now=now)
    assert not is_stale(a_week_ago, MetricClass.CURRENCY, now=now)


def test_an_unknown_publication_date_is_not_stale() -> None:
    """Absence of a date is absence of evidence about age. Demoting for it
    would punish sources that simply do not publish one."""
    assert not is_stale(None, MetricClass.SHARE_PRICE)


# --------------------------------------------------------------------------
# The reported form is a figure, not a sentence
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param(
            "Acme Corp reported revenue of USD 1.2bn for fiscal 2025.",
            "1.2bn",
            id="figure-not-sentence",
        ),
        pytest.param("Revenue of $1,198m", "$1,198m", id="symbol-and-commas"),
        pytest.param("4,000 employees", "4,000", id="noun-is-not-a-scale"),
        pytest.param("Operating margin of 2.4%", "2.4%", id="percent-survives"),
        pytest.param("Loss of -$3.4m", "-$3.4m", id="sign-survives"),
    ],
)
def test_the_reported_form_is_the_figure_alone(text: str, expected: str) -> None:
    """`REQ-EVID-008 AC-2` retains the reported *value*, and
    `evidence.value_raw` documents the shape: `"$1.2bn"`, `"12,345"`.

    Found by looking at the rendered conflict panel, which read "Primary value:
    Acme Corp reported revenue of USD 1.2bn for fiscal 2025., from ..." — the
    sentence serving as both the value and the claim, with the punctuation of
    both. No unit test caught it because none of them asserted the shape of
    `reported`; they asserted the number parsed out of it.

    The two traps are in here: the scale group matches any short run of
    letters, so "employees" would ride along as a unit, and a trailing percent
    sits outside the match entirely, so "2.4%" would lose the character that
    says what it is.
    """
    assert normalize_value(text).reported == expected


def test_a_statement_with_no_figure_keeps_the_statement() -> None:
    """With no span to point at, the sentence is the most faithful thing to
    keep — and it is still marked non-comparable."""
    normalized = normalize_value("The company grew strongly.")

    assert normalized.reported == "The company grew strongly."
    assert not normalized.comparable
