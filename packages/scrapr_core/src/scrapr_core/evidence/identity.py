"""What a figure measures: subject, metric and period (`DEC-10 §4`, `REQ-EVID-012`).

Conflict detection used to compare **any two numbers one claim cited**, gated
only by a coarse metric class. The first real run showed what that produces: an
analysis claim cited NVIDIA's revenue and a Glassdoor compensation rating, both
were classed as ratios, and $1.2bn against 4.5 was published as "sources
disagree".

Two values disagree only if they are measurements **of the same thing**. This
module states what a statement measures, as three keys, and a pair is compared
only when all three are established and equal:

* **subject** — whose figure it is. A statement naming the research subject is
  about the subject; one naming only another company is about that company; one
  naming nobody is read as the subject, because it was retrieved for it.
* **metric** — which measure, as a canonical key taken from the metric term
  nearest the figure: `revenue`, `net_income`, `rating:compensation`,
  `revenue@data_center`. A statement whose figure no known term describes has no
  metric key and is compared with nothing.
* **period** — which reporting period. For flow measures (revenue, income, cash
  flow) the period must be known on both sides and match: FY2024 and FY2025
  revenue, or "revenue" with no year beside "revenue for FY2025", are not a
  disagreement anyone can establish. Point-in-time measures (share price, a
  rating, headcount) may both be undated, and staleness handles their age.

**Every rule here fails towards not comparing.** A missed comparison leaves two
cited values side by side for the reader, each with its source; a false one
tells the reader two sources contradict each other when they do not. The second
is the worse failure for a product whose claim to trust is that it says only
what the evidence supports.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final, final

from scrapr_core.evidence.normalize import figure_span

__all__ = [
    "IMPLICIT_SUBJECT",
    "MeasurementIdentity",
    "identify",
    "mismatch",
    "subject_aliases",
]

IMPLICIT_SUBJECT: Final = "@subject"

# (pattern, canonical key). Matched case-insensitively; the key of the match
# nearest the figure wins.
_METRIC_TERMS: Final[tuple[tuple[re.Pattern[str], str], ...]] = tuple(
    (re.compile(pattern, re.I), key)
    for pattern, key in (
        (r"\bnet (?:income|profit|earnings|loss)\b", "net_income"),
        (r"\bgross margins?\b", "gross_margin"),
        (r"\boperating margins?\b", "operating_margin"),
        (r"\b(?:net|profit) margins?\b", "net_margin"),
        (r"\bgross profit\b", "gross_profit"),
        (r"\boperating (?:income|profit|loss)\b", "operating_income"),
        (r"\bebitda\b", "ebitda"),
        (r"\b(?:earnings per share|eps)\b", "eps"),
        (r"\bfree cash flow\b", "free_cash_flow"),
        (r"\b(?:operating cash flow|cash flow from operations)\b", "operating_cash_flow"),
        (r"\b(?:capital expenditures?|capex)\b", "capex"),
        (r"\b(?:research and development|r&d)\b", "rd_spend"),
        (r"\bmarket (?:cap|capitali[sz]ation|value)\b", "market_cap"),
        (r"\b(?:share|stock) price\b|\bprice per share\b", "share_price"),
        (r"\bprice targets?\b", "price_target"),
        (r"(?:\bforward\s+)?\bp/e\b|\bprice[- ]to[- ]earnings\b|\bpe ratio\b", "pe_ratio"),
        (r"\bp/s\b|\bprice[- ]to[- ]sales\b", "ps_ratio"),
        (r"\bdividend yield\b", "dividend_yield"),
        (r"\bmarket share\b", "market_share"),
        (r"\bout of (?:5|five|10|ten)\b|\bratings?\b|\brated\b|\bstars?\b", "rating"),
        (r"\b(?:salary|salaries|compensation|pay|wages?)\b", "compensation"),
        (r"\b(?:headcount|employees|employee count|workforce|staff)\b", "headcount"),
        (
            r"\b(?:open|job) (?:roles|positions|openings|postings|listings)\b"
            r"|\b(?:openings|postings|vacancies)\b",
            "job_openings",
        ),
        (r"\b(?:revenues?|sales|turnover)\b", "revenue"),
        (r"\b(?:total )?debt\b", "debt"),
        (r"\bcash(?: and (?:cash )?equivalents)?\b", "cash"),
        (r"\b(?:backlog|bookings)\b", "bookings"),
        (r"\b(?:users|subscribers|customers)\b", "customers"),
        (r"\b(?:growth|grew|grow|increased?|rose|declined?|fell|up|down)\b", "change"),
    )
)

_QUALIFIED_BY_NEIGHBOUR: Final = frozenset({"rating", "change"})
"""Keys that mean nothing alone. A rating of *what*, a change in *what*: the
nearest other metric term completes them, so "compensation rated 4.5" and
"overall rating 4.1" — or revenue growth and headcount growth — stay apart."""

_POINT_IN_TIME: Final = frozenset(
    {
        "share_price",
        "market_cap",
        "pe_ratio",
        "ps_ratio",
        "price_target",
        "dividend_yield",
        "market_share",
        "rating",
        "headcount",
        "job_openings",
        "customers",
        "compensation",
    }
)
"""Measures that are a reading at a moment rather than a total over a period.
Two undated readings may be compared; staleness speaks to their age."""

_SEGMENTS: Final = re.compile(
    r"\b(data cent(?:er|re)|gaming|automotive|professional visuali[sz]ation|cloud|"
    r"networking|hardware|software|services|advertising|subscriptions?|"
    r"international|domestic|china|americas|europe|asia|emea|segment)\b",
    re.I,
)
"""A segment named in the statement scopes its figure: data center revenue is
not total revenue, and comparing the two invents a disagreement."""

_FORECAST: Final = re.compile(
    r"\b(guidance|outlook|forecasts?|projected|projections?|expects?|expected|"
    r"estimates?|consensus|targets?|anticipates?)\b",
    re.I,
)
"""A forecast of a measure is not the measure. Kept apart from reported
figures even where no provider supplied a `basis`."""

_QUARTER: Final = re.compile(
    r"\bQ([1-4])\s*(?:of\s+)?(?:FY|fiscal(?:\s+year)?)?\s*'?(\d{4}|\d{2})\b", re.I
)
_QUARTER_WORDS: Final = re.compile(
    r"\b(first|second|third|fourth) quarter(?: of)?(?: fiscal(?: year)?)?\s+'?(\d{4}|\d{2})\b",
    re.I,
)
_FISCAL_YEAR: Final = re.compile(
    r"\bFY\s*'?(\d{4}|\d{2})\b|\bfiscal(?:\s+year)?\s+(\d{4})\b", re.I
)
_BARE_YEAR: Final = re.compile(r"(?<![\d.,$£€])\b((?:19|20)\d{2})\b(?![\d.,]*\s*(?:%|bn|m\b|million|billion))")
_ORDINAL: Final = {"first": 1, "second": 2, "third": 3, "fourth": 4}

_CORPORATE_SUFFIX: Final = re.compile(
    r"\b(corporation|corp|incorporated|inc|limited|ltd|plc|llc|co|company|"
    r"holdings|group|n\.?v|s\.?a|ag|se)\b\.?",
    re.I,
)

_ATTRIBUTION: Final = re.compile(
    r"\b(?:according to|per|reported by|citing|via|from|on|in)\s+"
    r"(?:the\s+)?[A-Z][\w&.'-]*(?:\s+[A-Z][\w&.'-]*)*",
)
""""according to Glassdoor", "per Reuters": the publisher is not the subject."""

_CAPITALISED: Final = re.compile(r"\b[A-Z][A-Za-z0-9&'-]*[A-Za-z0-9]|\b[A-Z]\b")

_NOT_ENTITIES: Final = frozenset(
    word.casefold()
    for word in (
        # Sentence starters and function words.
        "The A An This That These Those Its It Their Our His Her We They He She "
        "There Here In On At For By With Over During After Before Since While "
        "Despite However Meanwhile Also And But Or As If When Where Which Who "
        "According Per Based Total Annual Quarterly Fiscal Full Year Years "
        # Measures and finance vocabulary a sentence may start with.
        "Revenue Revenues Sales Net Gross Operating Income Profit Margin Margins "
        "Earnings Cash Debt Shares Share Stock Stocks Market Price Prices Growth "
        "Employees Employee Staff Headcount Compensation Salary Salaries Benefits "
        "Rating Ratings Reviews Review Jobs Job Roles Openings Postings Hiring "
        "Analysts Analyst Investors Guidance Outlook Forecast Data Center Centre "
        "Gaming Automotive Cloud Segment Segments Company Companies Business "
        "Chief Executive Officer CEO CFO COO CTO Board Management "
        # Periods and units.
        "FY Q1 Q2 Q3 Q4 H1 H2 January February March April May June July August "
        "September October November December Monday Tuesday Wednesday Thursday "
        "Friday Saturday Sunday USD EUR GBP JPY CNY INR CAD AUD CHF US U.S USA UK "
        "EU GAAP Non-GAAP YoY QoQ TTM AI GPU GPUs CPU CPUs API ETF IPO "
        # Publishers and data sources, which attribute rather than describe.
        "Glassdoor Indeed LinkedIn Reuters Bloomberg CNBC Yahoo Finance "
        "MarketWatch Morningstar Zacks Barron's Forbes Nasdaq NYSE SEC EDGAR "
        "FactSet Wall Street Journal WSJ Financial Times FT Motley Fool Seeking "
        "Alpha Adzuna Tavily FMP Fixture FIXTURE PLACEHOLDER"
    ).split()
)


@final
@dataclass(frozen=True, slots=True)
class MeasurementIdentity:
    """What one figure measures. `None` in a field means "not established"."""

    subject: frozenset[str]
    """`{IMPLICIT_SUBJECT}`, or the other entities the statement names."""
    metric: str | None
    period: str | None
    """`FY2025`, `Q3-2026`, or `None` when the statement and provider say nothing."""

    @property
    def is_point_in_time(self) -> bool:
        base = (self.metric or "").split("@")[0].split(":")[0].removeprefix("forecast:")
        return base in _POINT_IN_TIME


def subject_aliases(*names: str | None) -> frozenset[str]:
    """How statements may name the research subject.

    From the session's subject, company and ticker: "NVIDIA Corporation (NVDA)"
    yields `nvidia corporation`, `nvidia` and `nvda`.
    """
    aliases: set[str] = set()
    for name in names:
        if not name or not name.strip():
            continue
        for ticker in re.findall(r"\(([A-Za-z.]{1,8})\)", name):
            aliases.add(ticker.casefold())
        base = " ".join(re.sub(r"\([^)]*\)", " ", name).split())
        if not base:
            continue
        aliases.add(base.casefold())
        core = " ".join(_CORPORATE_SUFFIX.sub(" ", base).replace(",", " ").split())
        if core:
            aliases.add(core.casefold())
    return frozenset(alias for alias in aliases if len(alias) >= 2)


def identify(
    statement: str,
    *,
    aliases: frozenset[str],
    period_end: dt.date | None = None,
    period_start: dt.date | None = None,
) -> MeasurementIdentity:
    """State what the figure in `statement` measures.

    `period_end`/`period_start` are the provider's structured period, when it
    gave one, and win over anything read from the text.
    """
    return MeasurementIdentity(
        subject=_subject(statement, aliases),
        metric=_metric(statement),
        period=_structured_period(period_end, period_start) or _text_period(statement),
    )


def mismatch(left: MeasurementIdentity, right: MeasurementIdentity) -> str | None:
    """Why two figures are not measurements of the same thing, or `None`.

    `None` is the only answer that permits a numeric comparison.
    """
    if left.metric is None or right.metric is None:
        return "no recognised metric for at least one value"
    if left.metric != right.metric:
        return f"different metrics: {left.metric} and {right.metric}"
    if not _same_subject(left.subject, right.subject):
        return "different subjects"
    if left.period != right.period:
        if left.period is None or right.period is None:
            return "the reporting period is known for only one value"
        return f"different reporting periods: {left.period} and {right.period}"
    if left.period is None and not left.is_point_in_time:
        return f"no reporting period for either {left.metric} figure"
    return None


# --------------------------------------------------------------------------


def _same_subject(left: frozenset[str], right: frozenset[str]) -> bool:
    if IMPLICIT_SUBJECT in left or IMPLICIT_SUBJECT in right:
        return left == right
    return bool(left & right)


def _subject(statement: str, aliases: frozenset[str]) -> frozenset[str]:
    lowered = statement.casefold()
    if any(re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", lowered) for alias in aliases):
        return frozenset({IMPLICIT_SUBJECT})

    entities = set(_entities(_ATTRIBUTION.sub(" ", statement)))
    if not entities:
        # Retrieved for the subject and naming nobody else: read as the subject.
        return frozenset({IMPLICIT_SUBJECT})
    return frozenset(entities)


def _entities(text: str) -> Iterable[str]:
    for match in _CAPITALISED.finditer(text):
        word = match.group(0).removesuffix("'s").removesuffix("'")
        folded = word.casefold()
        if folded in _NOT_ENTITIES or _CORPORATE_SUFFIX.fullmatch(word):
            continue
        if any(character.isdigit() for character in word):
            continue
        yield folded


def _metric(statement: str) -> str | None:
    span = figure_span(statement)
    if span is None:
        return None
    start, end = span

    found: list[tuple[int, int, str]] = []
    for pattern, key in _METRIC_TERMS:
        for match in pattern.finditer(statement):
            if match.start() >= end:
                distance = match.start() - end
            elif match.end() <= start:
                distance = start - match.end()
            else:
                continue
            found.append((distance, -(match.end() - match.start()), key))
    if not found:
        return None

    found.sort()
    key = found[0][2]
    if key in _QUALIFIED_BY_NEIGHBOUR:
        neighbour = next(
            (other for _, _, other in found[1:] if other not in _QUALIFIED_BY_NEIGHBOUR),
            None,
        )
        if neighbour is None:
            # "rose 18%" with no measure named is not a measurement of anything
            # this module can put a name to.
            return None
        key = f"{key}:{neighbour}"

    segment = _SEGMENTS.search(statement)
    if segment is not None:
        key = f"{key}@{'_'.join(segment.group(1).casefold().split())}"
    if _FORECAST.search(statement):
        key = f"forecast:{key}"
    return key


def _structured_period(end: dt.date | None, start: dt.date | None) -> str | None:
    anchor = end or start
    return f"FY{anchor.year}" if anchor is not None else None


def _year(raw: str) -> int:
    year = int(raw)
    return 2000 + year if year < 100 else year


def _text_period(statement: str) -> str | None:
    quarter = _QUARTER.search(statement)
    if quarter:
        return f"Q{quarter.group(1)}-{_year(quarter.group(2))}"
    words = _QUARTER_WORDS.search(statement)
    if words:
        return f"Q{_ORDINAL[words.group(1).casefold()]}-{_year(words.group(2))}"
    fiscal = _FISCAL_YEAR.search(statement)
    if fiscal:
        return f"FY{_year(fiscal.group(1) or fiscal.group(2))}"
    years = {int(match.group(1)) for match in _BARE_YEAR.finditer(statement)}
    if len(years) == 1:
        return f"FY{years.pop()}"
    # No year, or several: "rose in 2025 from 2024" does not say which one the
    # figure belongs to.
    return None
