"""A period marker is not the figure (`REQ-EVID-008`, `DEC-10 §4.1`).

Found by a real run, not by a unit test. "Anthropic's total headcount at the end
of Q3 2026 was 3,250 employees" normalised to 32026 — the `3` from `Q3` and the
year run together — and then conflicted with "3,250" extracted from the same
document. The report told the reader two sources disagreed about headcount when
both said 3,250, and `value_raw` showed them "3 2026", a figure nobody
published.

Three things went wrong and each is pinned below: digits joined across ordinary
sentence spacing, a digit taken out of the middle of a word, and a bare year
taken as the value.
"""

from __future__ import annotations

import pytest

from scrapr_core.db.enums import NormalizationStatus
from scrapr_core.evidence.normalize import normalize_value


@pytest.mark.parametrize(
    ("statement", "expected"),
    [
        ("Anthropic's total headcount at the end of Q3 2026 was 3,250 employees.", 3250),
        ("Anthropic had 3,250 total employees at the end of Q3 2026.", 3250),
        ("Headcount in 2026 reached 4,100.", 4100),
        ("In FY2025 the company listed 40 open roles.", 40),
        ("Q4 2025 headcount was 900 staff.", 900),
    ],
)
def test_a_year_or_quarter_label_is_not_the_figure(
    statement: str, expected: int
) -> None:
    assert normalize_value(statement).value == expected


def test_two_statements_of_the_same_headcount_produce_the_same_number() -> None:
    """The defect, stated as the harm it caused.

    These two sentences say the identical thing. Before the fix one parsed as
    32026 and the other as 3250, and conflict detection dutifully reported a
    disagreement — a fabricated conflict is worse than a missed one, because a
    reader cannot tell it apart from a real disagreement they need to resolve.
    """
    first = normalize_value(
        "Anthropic's total headcount at the end of Q3 2026 was 3,250 employees."
    )
    second = normalize_value("Anthropic had 3,250 total employees at the end of Q3 2026.")

    assert first.value == second.value
    assert first.reported == second.reported == "3,250"


def test_space_grouped_thousands_still_parse() -> None:
    """The permissive digit class existed for a reason, and it is kept.

    European grouping is real. What is dropped is joining across *any* run of
    spaces; groups of exactly three still work.
    """
    assert normalize_value("Revenue in 2026 was 1 200 000 dollars.").value == 1_200_000


def test_a_statement_whose_only_number_is_a_year_still_reports_it() -> None:
    """Skipping years unconditionally would turn a legitimate statement into
    "no number here", which `AC-3` treats as non-comparable and drops from
    every comparison."""
    value = normalize_value("The company was founded in 1998.")

    assert value.value == 1998
    assert value.status is NormalizationStatus.NORMALIZED


def test_a_currency_symbol_beats_the_year_rule() -> None:
    """Nobody writes "$2026" to mean a year, so the symbol settles it."""
    assert normalize_value("A one-off charge of $2026 was booked in Q1.").value == 2026


def test_a_scale_word_beats_the_year_rule() -> None:
    assert normalize_value("Revenue was 2025 million dollars.").value == 2_025_000_000


def test_a_trailing_separator_is_not_part_of_the_figure() -> None:
    """"3,250, of which 1,180" reported the value as "3,250," — a comma the
    source wrote as punctuation, shown to the reader as part of the number."""
    value = normalize_value(
        "Anthropic's total headcount was 3,250, of which 1,180 were in research."
    )

    assert value.reported == "3,250"
    assert value.value == 3250


def test_a_single_digit_still_parses() -> None:
    """The guard against a trailing separator must not require two digits."""
    assert normalize_value("A single digit, 7, appeared.").value == 7


def test_non_breaking_space_grouping_parses() -> None:
    """What a European-formatted figure copied out of a web page contains.

    The digit stripper removed the ASCII space and left the non-breaking one,
    so `Decimal` refused it and a perfectly good number was reported as
    non-comparable and dropped from every comparison.
    """
    value = normalize_value(f"Revenue was 1{chr(160)}200{chr(160)}000 USD.")

    assert value.value == 1_200_000
    assert value.status is NormalizationStatus.NORMALIZED
