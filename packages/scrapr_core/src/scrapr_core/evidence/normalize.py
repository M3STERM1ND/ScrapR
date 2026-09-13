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

import datetime as dt
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
    "parse_period",
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
    # Not preceded by a letter or a digit. Without this, "Q3 2026" matched the
    # `3` inside `Q3`, and "FY2025" the `2025` inside the label — a period
    # marker read as the figure.
    r"(?<![A-Za-z0-9])"
    r"(?P<sign>-|\(|minus\s)?\s*"
    r"(?P<symbol>[$£€¥₹])?\s*"
    # Space-grouped thousands ("1 200 000") are allowed only in true groups of
    # three. Allowing any run of spaces let ordinary sentence spacing join two
    # separate numbers: "Q3 2026 was 3,250" produced the digits "3 2026" and
    # the value 32026.
    # A trailing separator is not part of the number. Without the final `\d`,
    # "3,250, of which" reported the figure as "3,250," — a comma the source
    # wrote as punctuation, shown to the reader as part of the value.
    r"(?P<digits>\d{1,3}(?:[ \xa0]\d{3})+(?:\.\d+)?|\d(?:[\d,]*\d)?(?:\.\d+)?)"
    r"\s*(?P<scale>[a-zA-Z]{1,10})?",
)

_BARE_YEAR = re.compile(r"^(?:19|20)\d{2}$")
"""A four-digit year, which is a period and not a figure.

`DEC-10 §4.1` already treats the reporting period as its own axis, handled by
`parse_period`. A bare year appearing in a metric statement is naming that
period — "revenue in 2026 was $9.4bn" — so taking it as the value produces a
figure no source published and, worse, a conflict between two statements that
agree. Skipped only when the sentence offers another candidate: a statement
whose only number is a year still reports that number rather than nothing.
"""


@final
@dataclass(frozen=True, slots=True)
class NormalizedValue:
    """One value, comparable or explicitly not.

    `reported` is never discarded (`AC-2`) — it is what citation inspection
    shows, and what a user checks the normalisation against.
    """

    reported: str
    """The figure exactly as the source wrote it: `"$1.2bn"`, `"12,345"`,
    `"(3.4)%"` — the span the number occupies, not the sentence around it.

    `REQ-EVID-008 AC-2` calls this the reported *value*, and `evidence.value_raw`
    documents the same shape. Storing the whole statement here made the conflict
    panel read "Primary value: Acme Corp reported revenue of USD 1.2bn for
    fiscal 2025., from ..." — the sentence twice over, once as the value and
    once as the claim it supports."""

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


def _first_figure(text: str) -> re.Match[str] | None:
    """The first number in the statement that is plausibly the figure.

    A bare year is passed over while another candidate remains, because a year
    in a metric statement is naming the reporting period. Everything else is
    taken in order, so the rule stays "the first number" for every sentence
    that does not mention a date.

    Found by a real run: "total headcount at the end of Q3 2026 was 3,250
    employees" reported 32026, and then conflicted with "3,250" from the same
    document — a disagreement shown to the reader between two statements that
    say the same thing.
    """
    candidates = list(_NUMBER.finditer(text))
    if not candidates:
        return None

    for candidate in candidates:
        # A currency symbol or a *real* scale word settles it: nobody writes
        # "$2026" or "2026 billion" to mean a year. Checked against
        # `SCALE_WORDS` rather than against the group, because the `scale`
        # group matches any short run of letters — "2026 was" would otherwise
        # look scaled and the year would win.
        scale = (candidate.group("scale") or "").lower().rstrip(".")
        if candidate.group("symbol") or scale in SCALE_WORDS:
            return candidate
        if not _BARE_YEAR.match(candidate.group("digits").strip()):
            return candidate

    # Every number in the sentence is a year. Report the first rather than
    # nothing: the statement may genuinely be about one.
    return candidates[0]


def normalize_value(text: str, *, currency_hint: str | None = None) -> NormalizedValue:
    """Pull a comparable number out of a statement, or say why not.

    `currency_hint` comes from the provider's structured payload — FMP reports
    `reportedCurrency` beside the figure — and is used only when the text
    itself does not say. A hint never overrides what the source wrote.
    """
    metric = classify_metric(text)
    match = _first_figure(text)

    if match is None:
        # `AC-3`: no number is not a zero, and it is not a comparison failure
        # either. There is simply nothing here to compare. With no span to
        # point at, the statement itself is the most faithful thing to keep.
        return NormalizedValue(
            reported=text.strip(),
            status=NormalizationStatus.NON_COMPARABLE,
            metric_class=metric,
        )

    # The non-breaking space too: it is what a European-formatted figure copied
    # out of a web page or a spreadsheet actually contains, and stripping only
    # the ASCII one left "1\xa0200\xa0000" to fail `Decimal` and be reported as
    # non-comparable — a legitimate number silently dropped from every
    # comparison.
    digits = match.group("digits").replace(",", "").replace(" ", "").replace("\xa0", "")
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
    scaled = scale_word in SCALE_WORDS
    if scaled:
        magnitude *= Decimal(10) ** SCALE_WORDS[scale_word]

    # Rebuilt from the parts that were actually understood, rather than taken
    # from the raw match. The `scale` group is any short run of letters, so
    # "4,000 employees" would otherwise report the noun as part of the figure —
    # and a trailing `%` sits outside the match entirely, so "2.4%" would lose
    # the one character that says what it is.
    span = _reported_span(text, match, scaled)

    is_percentage = "%" in text or metric is MetricClass.RATIO
    currency = _currency_of(text, match.group("symbol")) or (
        currency_hint if currency_hint else None
    )

    # `AC-3` again, and the case `DEC-10 §4.3` depends on: a currency magnitude
    # whose currency nobody stated cannot be compared against one that is in
    # dollars. Guessing here is how two correct sources become a conflict.
    if metric is MetricClass.CURRENCY and not currency:
        return NormalizedValue(
            reported=span,
            status=NormalizationStatus.NON_COMPARABLE,
            metric_class=metric,
            value=magnitude,
        )

    return NormalizedValue(
        reported=span,
        status=NormalizationStatus.NORMALIZED,
        metric_class=metric,
        value=magnitude,
        currency=currency,
        is_percentage=is_percentage,
    )


def _reported_span(text: str, match: re.Match[str], scaled: bool) -> str:
    """The figure exactly as written, and nothing around it.

    Assembled from the parts the parser recognised — sign, currency symbol,
    digits as punctuated, a scale word only when it really is one — plus a
    trailing percent sign if the source wrote one. What it deliberately does
    not include is the rest of the sentence, which is `evidence.content`'s job.
    """
    pieces: list[str] = []

    sign = match.group("sign")
    if sign and sign.strip() == "-":
        pieces.append("-")

    symbol = match.group("symbol")
    if symbol:
        pieces.append(symbol)

    pieces.append(match.group("digits").strip())

    if scaled:
        pieces.append(match.group("scale"))

    span = "".join(pieces)

    tail = text[match.end() :]
    if tail.startswith("%"):
        span += "%"

    return span


def parse_period(raw: object) -> tuple[dt.date | None, dt.date | None]:
    """A provider's period string as a date range (`REQ-EVID-009 AC-1`).

    Two shapes, because two providers say it differently: FMP reports a fiscal
    year (`"2025"`), EDGAR a period end (`"2025-12-31"`). A bare year becomes
    the whole calendar year, which is the honest reading — the filing covers
    it, and pretending to know the fiscal year-end when the provider did not
    say would be inventing precision.

    Anything unparseable returns `(None, None)` rather than raising. A figure
    with no period is compared against nothing (`DEC-10 §4.1`), which is a
    weaker outcome than a wrong period and a much safer one.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None, None

    text = raw.strip()

    if len(text) == 4 and text.isdigit():
        year = int(text)
        return dt.date(year, 1, 1), dt.date(year, 12, 31)

    try:
        parsed = dt.date.fromisoformat(text[:10])
    except ValueError:
        return None, None
    return None, parsed
