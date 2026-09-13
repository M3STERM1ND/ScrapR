"""What's Changed, as a pure function (`REQ-VER-004`, `REQ-VER-006`, `REQ-VER-007`, `DEC-20`).

One test per kind of change `DEC-20` recognises, and — just as important — one
per thing it must *not* report: rewording, a figure within tolerance, sources
churning, and a claim the report simply chose not to write again.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from uuid import UUID, uuid4

from scrapr_core.db.enums import ClaimType, NormalizationStatus, SourceCategory
from scrapr_core.evidence.normalize import MetricClass, NormalizedValue
from scrapr_core.versioning import (
    ChangeCategory,
    ChangeKind,
    ChangeSummary,
    ClaimSnapshot,
    Figure,
    compare_claims,
)
from scrapr_core.versioning.compare import similarity


def figure(
    reported: str,
    value: str,
    *,
    metric: MetricClass = MetricClass.CURRENCY,
    period: str | None = "2025-12-31",
    currency: str | None = "USD",
    category: SourceCategory = SourceCategory.NEWS,
) -> Figure:
    return Figure(
        evidence_id=uuid4(),
        value=NormalizedValue(
            reported=reported,
            status=NormalizationStatus.NORMALIZED,
            metric_class=metric,
            value=Decimal(value),
            currency=currency,
        ),
        period=period,
        basis=None,
        source_name="Reuters",
        source_category=category,
    )


def claim(
    text: str,
    claim_type: ClaimType = ClaimType.FACT,
    *,
    confidence: str | None = "moderate",
    figures: tuple[Figure, ...] = (),
    sources: frozenset[str] = frozenset({"url:reuters.com/a"}),
    categories: frozenset[SourceCategory] = frozenset({SourceCategory.NEWS}),
    assumptions: tuple[str, ...] = (),
) -> ClaimSnapshot:
    return ClaimSnapshot(
        id=uuid4(),
        text=text,
        claim_type=claim_type,
        confidence=confidence,
        assumptions=assumptions,
        figures=figures,
        evidence_ids=tuple(item.evidence_id for item in figures) or (uuid4(),),
        source_keys=sources,
        source_categories=categories,
    )


def only(changes: list, kind: ChangeKind):  # type: ignore[no-untyped-def, type-arg]
    matching = [change for change in changes if change.kind is kind]
    assert len(matching) == 1, [change.kind for change in changes]
    return matching[0]


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------


def test_figures_are_ignored_when_matching_wording() -> None:
    assert similarity(
        "Acme reported revenue of $1.2bn for fiscal 2025.",
        "Acme reported revenue of $1.4bn for fiscal 2025.",
    ) == 1.0


def test_unrelated_claims_do_not_match() -> None:
    assert similarity(
        "Acme reported revenue of $1.2bn.", "Acme is hiring forty engineers in Berlin."
    ) < 0.6


# --------------------------------------------------------------------------
# Changes that are meaningful
# --------------------------------------------------------------------------


def test_a_figure_beyond_tolerance_is_a_change_with_its_evidence() -> None:
    """`REQ-VER-004 AC-1`, `REQ-VER-007 AC-1`: the new evidence is linked."""
    old = claim("Acme reported revenue of $1.2bn for fiscal 2025.", figures=(figure("$1.2bn", "1200000000"),))
    new_figure = figure("$1.4bn", "1400000000")
    new = claim("Acme reported revenue of $1.4bn for fiscal 2025.", figures=(new_figure,))

    changes, unchanged = compare_claims([old], [new])

    change = only(changes, ChangeKind.FIGURE_CHANGED)
    assert change.category is ChangeCategory.FINANCIAL_FIGURES
    assert change.before is not None and change.before.value == "$1.2bn"
    assert change.after is not None and change.after.value == "$1.4bn"
    assert change.evidence_ids == (new_figure.evidence_id,)
    assert "$1.2bn" in change.summary and "$1.4bn" in change.summary
    assert unchanged == 0


def test_a_later_period_supersedes_rather_than_conflicts() -> None:
    old = claim(
        "Acme reported annual revenue of $1.2bn.",
        figures=(figure("$1.2bn", "1200000000", period="2024-12-31"),),
    )
    new = claim(
        "Acme reported annual revenue of $1.5bn.",
        figures=(figure("$1.5bn", "1500000000", period="2025-12-31"),),
    )

    change = only(compare_claims([old], [new])[0], ChangeKind.NEWER_PERIOD)

    assert "2025-12-31" in change.summary


def test_a_share_price_move_is_stock_information() -> None:
    old = claim(
        "Acme's share price closed at $42.",
        figures=(figure("$42", "42", metric=MetricClass.SHARE_PRICE),),
    )
    new = claim(
        "Acme's share price closed at $51.",
        figures=(figure("$51", "51", metric=MetricClass.SHARE_PRICE),),
    )

    change = only(compare_claims([old], [new])[0], ChangeKind.FIGURE_CHANGED)

    assert change.category is ChangeCategory.STOCK_INFORMATION


def test_a_changed_confidence_on_a_conclusion_is_stated() -> None:
    """`REQ-VER-007 AC-3`: confidence changes are stated with the conclusion."""
    old = claim("Acme's growth outlook is positive given enterprise demand.", ClaimType.ANALYSIS, confidence="low")
    new = claim("Acme's growth outlook is positive given enterprise demand.", ClaimType.ANALYSIS, confidence="high")

    change = only(compare_claims([old], [new])[0], ChangeKind.CONCLUSION_CHANGED)

    assert change.confidence_from == "low" and change.confidence_to == "high"
    assert change.as_json()["confidence_change"] == {"from": "low", "to": "high"}


def test_a_reversed_assessment_is_a_changed_conclusion() -> None:
    old = claim("Acme's competitive position in enterprise software is strong.", ClaimType.ANALYSIS)
    new = claim("Acme's competitive position in enterprise software is weak.", ClaimType.ANALYSIS)

    change = only(compare_claims([old], [new])[0], ChangeKind.CONCLUSION_CHANGED)

    assert "assessment" in change.summary


def test_different_forecast_assumptions_are_reported() -> None:
    old = claim(
        "Acme revenue is likely to keep growing next year.",
        ClaimType.FORECAST,
        assumptions=("enterprise renewals continue at current rates",),
    )
    new = claim(
        "Acme revenue is likely to keep growing next year.",
        ClaimType.FORECAST,
        assumptions=("a new government contract closes in the spring",),
    )

    change = only(compare_claims([old], [new])[0], ChangeKind.ASSUMPTIONS_CHANGED)

    assert change.category is ChangeCategory.FORECAST_ASSUMPTIONS


def test_a_claim_on_a_new_source_is_a_new_finding() -> None:
    product = claim(
        "Acme launched a new analytics product for hospitals in March.",
        sources=frozenset({"url:techcrunch.com/acme-launch"}),
    )

    change = only(
        compare_claims([], [product], before_sources={"url:reuters.com/a"})[0],
        ChangeKind.NEW_FINDING,
    )

    assert change.category is ChangeCategory.NEW_PRODUCTS
    assert change.before is None


def test_new_job_postings_and_competitors_are_labelled_as_such() -> None:
    jobs = claim(
        "Acme listed forty open engineering roles in Berlin.",
        sources=frozenset({"url:adzuna.com/1"}),
        categories=frozenset({SourceCategory.JOBS}),
    )
    rival = claim(
        "Globex entered the market as a direct rival to Acme.",
        sources=frozenset({"url:ft.com/globex"}),
    )

    changes, _ = compare_claims([], [jobs, rival], before_sources=set())

    assert {change.category for change in changes} == {
        ChangeCategory.JOB_POSTINGS,
        ChangeCategory.NEW_COMPETITORS,
    }


def test_a_claim_whose_sources_did_not_come_back_is_no_longer_found() -> None:
    old = claim(
        "Acme opened an office in Lisbon.", sources=frozenset({"url:local-news.pt/acme"})
    )

    change = only(
        compare_claims([old], [], after_sources={"url:reuters.com/a"})[0],
        ChangeKind.NO_LONGER_FOUND,
    )

    # Never "no longer true": absence of evidence this time is all that is known.
    assert "Not found again" in change.summary
    assert change.after is None


def test_a_gap_that_is_now_evidenced_is_closed() -> None:
    old = claim("Acme's operating margin for fiscal 2025 could not be established.", ClaimType.UNCERTAINTY)
    new = claim("Acme's operating margin for fiscal 2025 was established at 18%.", ClaimType.FACT)

    assert only(compare_claims([old], [new])[0], ChangeKind.GAP_CLOSED)


def test_a_fact_that_could_not_be_confirmed_again_opens_a_gap() -> None:
    old = claim("Acme's operating margin for fiscal 2025 was established at 18%.", ClaimType.FACT)
    new = claim("Acme's operating margin for fiscal 2025 could not be established.", ClaimType.UNCERTAINTY)

    assert only(compare_claims([old], [new])[0], ChangeKind.GAP_OPENED)


# --------------------------------------------------------------------------
# Differences that are not meaningful
# --------------------------------------------------------------------------


def test_rewording_alone_is_not_a_change() -> None:
    old = claim("Acme reported revenue growth driven by enterprise customers this year.", ClaimType.ANALYSIS)
    new = claim("Acme reported revenue growth this year, driven by enterprise customers.", ClaimType.ANALYSIS)

    changes, unchanged = compare_claims([old], [new])

    assert changes == []
    assert unchanged == 1


def test_a_figure_within_tolerance_is_not_a_change() -> None:
    """`$1.2bn` against `$1,198m` is the same figure (`DEC-10`)."""
    old = claim("Acme reported revenue of $1.2bn.", figures=(figure("$1.2bn", "1200000000"),))
    new = claim("Acme reported revenue of $1,198m.", figures=(figure("$1,198m", "1198000000"),))

    assert compare_claims([old], [new])[0] == []


def test_figures_in_different_currencies_are_not_compared() -> None:
    """`REQ-VER-004 AC-2`: a currency difference is not reported as change."""
    old = claim("Acme reported revenue of $1.2bn.", figures=(figure("$1.2bn", "1200000000"),))
    new = claim(
        "Acme reported revenue of EUR 1.1bn.",
        figures=(figure("EUR 1.1bn", "1100000000", currency="EUR"),),
    )

    assert compare_claims([old], [new])[0] == []


def test_a_fact_whose_confidence_moved_on_the_same_figure_is_not_announced() -> None:
    same = figure("$1.2bn", "1200000000")
    old = claim("Acme reported revenue of $1.2bn.", confidence="moderate", figures=(same,))
    new = claim("Acme reported revenue of $1.2bn.", confidence="high", figures=(same,))

    assert compare_claims([old], [new])[0] == []


def test_a_claim_not_rewritten_while_its_sources_remain_is_not_reported() -> None:
    """The report chose differently from the same evidence."""
    kept = frozenset({"url:reuters.com/a"})
    old = claim("Acme opened an office in Lisbon.", sources=kept)
    other = claim("Acme's revenue grew in fiscal 2025.", sources=kept)

    assert compare_claims([old], [other], after_sources=set(kept))[0] == []


def test_a_new_claim_from_sources_already_known_is_not_a_finding() -> None:
    known = frozenset({"url:reuters.com/a"})
    new = claim("Acme's revenue grew in fiscal 2025.", sources=known)

    assert compare_claims([], [new], before_sources=set(known))[0] == []


# --------------------------------------------------------------------------
# The summary
# --------------------------------------------------------------------------


def test_no_change_says_so_explicitly() -> None:
    """`REQ-VER-006 AC-3`."""
    summary = ChangeSummary(
        compared_version_id=UUID(int=1),
        compared_version_number=1,
        compared_created_at=dt.datetime(2026, 9, 3, tzinfo=dt.UTC),
    )

    assert summary.headline == "No meaningful changes since version 1, from Sep 3, 2026."
    body = summary.as_json()
    assert body["has_changes"] is False and body["changes"] == []


def test_changes_are_ordered_with_conclusions_first() -> None:
    gone = claim("Acme opened an office in Lisbon.", sources=frozenset({"url:old.example/a"}))
    before_conclusion = claim("Acme's market position is strong.", ClaimType.ANALYSIS)
    after_conclusion = claim("Acme's market position is weak.", ClaimType.ANALYSIS)

    changes, _ = compare_claims(
        [gone, before_conclusion], [after_conclusion], after_sources={"url:new.example/b"}
    )

    assert [change.kind for change in changes] == [
        ChangeKind.CONCLUSION_CHANGED,
        ChangeKind.NO_LONGER_FOUND,
    ]
    summary = ChangeSummary(
        compared_version_id=UUID(int=1),
        compared_version_number=2,
        compared_created_at=dt.datetime(2026, 9, 3, tzinfo=dt.UTC),
        changes=tuple(changes),
    )
    assert summary.headline == "2 meaningful changes since version 2."
