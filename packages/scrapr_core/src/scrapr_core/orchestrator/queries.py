"""What each provider is asked, and how a follow-up round asks differently.

Two defects from the first real run, fixed in one place because both are about
the words a tool receives:

**Structured providers were sent the research question.** FMP's symbol search,
EDGAR's full-text search and Adzuna's keyword search all received
`"NVIDIA Corporation (NVDA): What has been NVIDIA's revenue, net income, and
gross margin trend over the past several quarters/fiscal years?"`. None of them
can do anything with a sentence: a symbol search wants a company or a ticker,
EDGAR matched it as an exact phrase, and Adzuna ANDs every word. Now each
category gets the parameters it can use — a company name and a ticker for
financial data and filings, the employer for jobs — and only search and news,
which read prose, get the question.

**Every round asked the identical question.** The query was
`f"{subject}: {question}"` in round one and in every round after, so round two
was answered from the retrieval cache, added no source, and the no-progress
rule ended the area — a loop that could not do anything but stop. A follow-up
round is now built from the question's **gap**: the question's content words,
a modifier chosen by what is missing (no sources, only low-authority sources, or
one short of corroboration) and rotated per round, and the domains the question
already cites excluded, so the next search looks for the source it does not
have. Different parameters are also a different cache key, so the call is
really made.

Deterministic, and no model call: the gap is counted from rows, and a query
rewrite by a model would be one more place for retrieved text to steer what the
agent does next (§9).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final, final

from scrapr_core.domain.json import JsonValue
from scrapr_core.tools.contract import ToolCategory

__all__ = [
    "PROSE_CATEGORIES",
    "SPECIALIZED_CATEGORIES",
    "QuestionGap",
    "SubjectTarget",
    "request_params",
    "subject_target",
]

SPECIALIZED_CATEGORIES: Final = frozenset(
    {ToolCategory.FINANCIAL, ToolCategory.FILINGS, ToolCategory.JOBS}
)
"""Providers that answer a structured lookup about a named company, and whose
failure or silence web search can stand in for."""

PROSE_CATEGORIES: Final = frozenset({ToolCategory.WEB_SEARCH, ToolCategory.NEWS})
"""Providers that read a natural-language query, and so can be re-asked."""

_TICKER_IN_NAME: Final = re.compile(r"\(\s*([A-Z][A-Z0-9.\-]{0,9})\s*\)")
_TICKER: Final = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
_CORPORATE_SUFFIX: Final = re.compile(
    r"[,\s]+(?:corporation|corp|incorporated|inc|limited|ltd|plc|llc|co|company|"
    r"holdings|group)\.?$",
    re.I,
)

_STOPWORDS: Final = frozenset(
    """a about above after again against all also am an and any are as at be been
    before being below between both but by can could did do does doing down during
    each e.g eg etc few for from further had has have having how i if in into is it
    its itself just me more most my no nor not now of off on once only or other our
    out over own same should so some such than that the their them then there these
    they this those through to too under until up very was we were what when where
    which while who whom why will with would you your including compared compare
    current currently recent recently past several over next versus vs""".split()
)

_MAX_KEYWORDS: Final = 12

_NO_SOURCES: Final = ("", "analysis", "report")
_ONLY_LOWER_TIER: Final = (
    "annual report investor relations",
    "SEC filing press release",
    "Reuters Bloomberg coverage",
)
_ONE_SHORT: Final = ("independent analysis", "news coverage", "industry report")
"""Modifiers per gap, rotated by round so no two follow-up rounds send the same
query. Chosen for what each gap lacks: somewhere to start, a source with
authority, or a second, independent source."""


@final
@dataclass(frozen=True, slots=True)
class SubjectTarget:
    """The research subject in the forms providers accept."""

    label: str
    """The subject as recorded: "NVIDIA Corporation (NVDA)". Prefixes prose
    queries, as it always has."""
    name: str
    """The company name a lookup wants: "NVIDIA"."""
    ticker: str | None = None


@final
@dataclass(frozen=True, slots=True)
class QuestionGap:
    """What one open question lacks, counted from its evidence rows."""

    distinct_sources: int = 0
    above_lower_sources: int = 0
    cited_hosts: Sequence[str] = field(default_factory=tuple)


def subject_target(
    subject: str,
    context_company: str | None = None,
    context_ticker: str | None = None,
) -> SubjectTarget:
    """Resolve what to call the subject when asking a structured provider.

    What the user typed wins over what interpretation wrote: a ticker supplied
    in the research form is a fact, a ticker read out of a parenthesis is a
    reading of one.
    """
    label = " ".join(subject.split()) or (context_company or "").strip()

    ticker = (context_ticker or "").strip().upper() or None
    if ticker is None:
        found = _TICKER_IN_NAME.search(label)
        ticker = found.group(1) if found else None
    if ticker is not None and not _TICKER.match(ticker):
        ticker = None

    base = (context_company or "").strip() or _TICKER_IN_NAME.sub(" ", label)
    base = " ".join(base.split())
    name = _CORPORATE_SUFFIX.sub("", base).strip() or base
    return SubjectTarget(label=label, name=name, ticker=ticker)


def request_params(
    category: ToolCategory,
    target: SubjectTarget,
    question: str,
    *,
    round_index: int = 0,
    gap: QuestionGap | None = None,
) -> dict[str, JsonValue]:
    """The parameters one category is sent for one question in one round."""
    if category in (ToolCategory.FINANCIAL, ToolCategory.FILINGS):
        params: dict[str, JsonValue] = {"query": target.name}
        if target.ticker:
            params["symbol"] = target.ticker
        return params

    if category is ToolCategory.JOBS:
        return {"query": target.name}

    if category in PROSE_CATEGORIES and round_index > 0:
        return _follow_up(target, question, round_index, gap or QuestionGap())

    return {"query": f"{target.label}: {question}"}


def _follow_up(
    target: SubjectTarget, question: str, round_index: int, gap: QuestionGap
) -> dict[str, JsonValue]:
    """A query aimed at what the question is missing."""
    if gap.distinct_sources == 0:
        modifiers = _NO_SOURCES
    elif gap.above_lower_sources == 0:
        modifiers = _ONLY_LOWER_TIER
    else:
        modifiers = _ONE_SHORT
    modifier = modifiers[(round_index - 1) % len(modifiers)]

    words = [target.name, *_keywords(question, target), modifier]
    params: dict[str, JsonValue] = {"query": " ".join(word for word in words if word)}
    if gap.cited_hosts:
        params["exclude_domains"] = sorted(set(gap.cited_hosts))
    return params


def _keywords(question: str, target: SubjectTarget) -> list[str]:
    """The question's content words, without the subject's own name."""
    subject_words = {word.casefold() for word in re.findall(r"[\w&/.-]+", target.label)}
    keywords: list[str] = []
    for raw in re.findall(r"[A-Za-z0-9][\w&/.\-']*", question):
        word = raw.strip(".'").removesuffix("'s")
        folded = word.casefold()
        if not word or folded in _STOPWORDS or folded in subject_words:
            continue
        if folded not in (existing.casefold() for existing in keywords):
            keywords.append(word)
    return keywords[:_MAX_KEYWORDS]
