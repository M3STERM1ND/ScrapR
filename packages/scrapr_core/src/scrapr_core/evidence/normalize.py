"""Evidence normalization (`REQ-EVID-008`, `REQ-EVID-009`).

Conflict detection compares numbers. Before anything can be compared it has to
be brought to the same unit, the same currency and the same reporting period —
and `DEC-10 §3` is explicit that tolerance is the *last* step, never the first.

Three criteria, and the third is the one that matters most:

* `AC-1` — units, currency and **scale** are captured and normalised. `$1.2bn`
  and `$1,200m` are the same number wearing different clothes.
* `AC-2` — **non-destructive**. The reported value is always retained. A user
  inspecting a citation must see what the source actually said, not what this
  module made of it.
* `AC-3` — **failure marks evidence non-comparable rather than guessing.** A
  figure with no currency is not "probably dollars". `NON_COMPARABLE` is a fact
  about the data, and `DEC-10 §4.3` relies on it: treating it as agreement
  hides a gap, treating it as conflict invents one.

**Metric class is assigned here**, because `DEC-10`'s tolerance table is keyed
by it and `DEC-10 §10` left the inference open. The classes are the ones that
table names, and nothing more — an unrecognised metric gets `UNKNOWN` and the
default tolerance rather than a guess that looks like a measurement.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum, unique
from typing import Final, final

from scrapr_core.db.enums import NormalizationStatus

__all__ = [
    "SCALE_WORDS",
    "MetricClass",
    "NormalizedValue",
    "classify_metric",
    "normalize_value",
]


@unique
class MetricClass(StrEnum):
    """What kind of number this is (`DEC-10 §3`).

    The unit of the tolerance table. Currency magnitudes tolerate a relative
    difference; ratios need an absolute one, because 1% of a 2% margin is 0.02
    points and no real disagreement is that small.
    """

    CURRENCY = "currency"
    """Revenue, cost, cash, market capitalisation."""

    RATIO = "ratio"
    """Margin, growth rate, yield. Already a percentage."""

    COUNT = "count"
    """Headcount, postings, locations. Genuinely moves between true dates."""

    SHARE_PRICE = "share_price"
    """Moves intraday, so two correct sources hours apart disagree."""

    UNKNOWN = "unknown"
    """Unrecognised. Gets the default tolerance, never an invented one."""


SCALE_WORDS: Final[dict[str, int]] = {
    "k": 3,
    "thousand": 3,
    "m": 6,
    "mm": 6,
    "million": 6,
    "millions": 6,
    "bn": 9,
    "b": 9,
    "billion": 9,
    "billions": 9,
    "tn": 12,
    "t": 12,
    "trillion": 12,
    "trillions": 12,
}
"""Scale suffixes and their powers of ten (`AC-1`).

`mm` is included because financial filings use it for millions, and reading it
as a typo for `m` would be right by accident. `b` and `t` are ambiguous in
prose but unambiguous beside a currency figure, which is the only place this
runs.
"""

_CURRENCY_SYMBOLS: Final[dict[str, str]] = {
    "$": "USD",
    "£": "GBP",
    "€": "EUR",
    "¥": "JPY",
    "₹": "INR",
}

_KNOWN_CODES: Final[frozenset[str]] = frozenset(
    {"USD", "EUR", "GBP", "JPY", "CNY", "INR", "CAD", "AUD", "CHF", "SEK", "KRW", "BRL"}
)

_RATIO_TERMS = re.compile(
    r"\b(margin|growth|yield|rate|share of|percent|percentage|cagr|churn)\b", re.I
)
_COUNT_TERMS = re.compile(
    r"\b(headcount|employees|staff|roles|openings|postings|locations|stores|users|"
    r"subscribers|customers)\b",
    re.I,
)
_PRICE_TERMS = re.compile(r"\b(share price|stock price|price per share|quote)\b", re.I)
_CURRENCY_TERMS = re.compile(
    r"\b(revenue|sales|income|profit|loss|cost|expense|cash|debt|assets|"
    r"market cap|capitalisation|capitalization|ebitda|arr|mrr|bookings)\b",
    re.I,
)

_NUMBER = re.compile(
    r"(?P<sign>-|\(|minus\s)?\s*"
    r"(?P<symbol>[$£€¥₹])?\s*"
    r"(?P<digits>\d[\d,\s]*(?:\.\d+)?)\s*"
    r"(?P<scale>[a-zA-Z]{1,10})?",
)


@final
@dataclass(frozen=True, slots=True)
class NormalizedValue:
    """One value, comparable or explicitly not.

    `reported` is never discarded (`AC-2`) — it is what citation inspection
    shows, and what a user checks the normalisation against.
    """

    reported: str
    status: NormalizationStatus
    metric_class: MetricClass = MetricClass.UNKNOWN
    value: Decimal | None = None
    """The normalised magnitude, scale applied. `None` when not comparable."""

    currency: str | None = None
    is_percentage: bool = False

    @property
    def comparable(self) -> bool:
        """Whether `DEC-10` may compare this against another value at all."""
        return self.status is NormalizationStatus.NORMALIZED and self.value is not None


def classify_metric(text: str) -> MetricClass:
    """Infer the metric class from the surrounding statement.

    Order matters: a sentence can mention both "revenue" and "growth", and
    "revenue growth of 18%" is a ratio. The more specific terms are checked
    first so the percentage wins.
    """
    if _PRICE_TERMS.search(text):
        return MetricClass.SHARE_PRICE
    if _RATIO_TERMS.search(text) or "%" in text:
        return MetricClass.RATIO
    if _COUNT_TERMS.search(text):
        return MetricClass.COUNT
    if _CURRENCY_TERMS.search(text):
        return MetricClass.CURRENCY
    return MetricClass.UNKNOWN


def _currency_of(text: str, symbol: str | None) -> str | None:
    """Currency from an explicit code, else from a symbol."""
    for code in _KNOWN_CODES:
        if re.search(rf"\b{code}\b", text):
            return code
    if symbol:
        return _CURRENCY_SYMBOLS.get(symbol)
    return None


def normalize_value(text: str, *, currency_hint: str | None = None) -> NormalizedValue:
    """Pull a comparable number out of a statement, or say why not.

    `currency_hint` comes from the provider's structured payload — FMP reports
    `reportedCurrency` beside the figure — and is used only when the text
    itself does not say. A hint never overrides what the source wrote.
    """
    metric = classify_metric(text)
    match = _NUMBER.search(text)

    if match is None:
        # `AC-3`: no number is not a zero, and it is not a comparison failure
        # either. There is simply nothing here to compare.
        return NormalizedValue(
            reported=text.strip(),
            status=NormalizationStatus.NON_COMPARABLE,
            metric_class=metric,
        )

    digits = match.group("digits").replace(",", "").replace(" ", "")
    try:
        magnitude = Decimal(digits)
    except InvalidOperation:
        return NormalizedValue(
            reported=text.strip(),
            status=NormalizationStatus.NON_COMPARABLE,
            metric_class=metric,
        )

    if match.group("sign"):
        magnitude = -magnitude

    scale_word = (match.group("scale") or "").lower().rstrip(".")
    if scale_word in SCALE_WORDS:
        magnitude *= Decimal(10) ** SCALE_WORDS[scale_word]

    is_percentage = "%" in text or metric is MetricClass.RATIO
    currency = _currency_of(text, match.group("symbol")) or (
        currency_hint if currency_hint else None
    )

    # `AC-3` again, and the case `DEC-10 §4.3` depends on: a currency magnitude
    # whose currency nobody stated cannot be compared against one that is in
    # dollars. Guessing here is how two correct sources become a conflict.
    if metric is MetricClass.CURRENCY and not currency:
        return NormalizedValue(
            reported=text.strip(),
            status=NormalizationStatus.NON_COMPARABLE,
            metric_class=metric,
            value=magnitude,
        )

    return NormalizedValue(
        reported=text.strip(),
        status=NormalizationStatus.NORMALIZED,
        metric_class=metric,
        value=magnitude,
        currency=currency,
        is_percentage=is_percentage,
    )
