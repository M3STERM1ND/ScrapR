"""What each provider is asked, and how a follow-up round asks differently.

From the first real run: FMP, EDGAR and Adzuna were each sent
`"NVIDIA Corporation (NVDA): What has been NVIDIA's revenue…?"`, and every
round re-sent the identical query, so round two came from the cache and ended
the area on the no-progress rule.
"""

from __future__ import annotations

import pytest

from scrapr_core.orchestrator.queries import (
    QuestionGap,
    SubjectTarget,
    request_params,
    subject_target,
)
from scrapr_core.tools.contract import ToolCategory

NVIDIA = subject_target("NVIDIA Corporation (NVDA)")
QUESTION = (
    "What has been NVIDIA's revenue, net income, and gross margin trend over the "
    "past several quarters/fiscal years?"
)


# --------------------------------------------------------------------------
# The subject, in provider terms
# --------------------------------------------------------------------------


def test_the_nvidia_subject_resolves_to_a_name_and_a_ticker() -> None:
    assert NVIDIA == SubjectTarget(label="NVIDIA Corporation (NVDA)", name="NVIDIA", ticker="NVDA")


def test_what_the_user_supplied_wins_over_the_interpretation() -> None:
    target = subject_target("Acme (ACM)", context_company="Acme Holdings Inc", context_ticker="acme")

    assert target.name == "Acme Holdings"
    assert target.ticker == "ACME"


def test_a_subject_with_no_ticker_has_none() -> None:
    target = subject_target("OpenAI")

    assert target.ticker is None
    assert target.name == "OpenAI"


# --------------------------------------------------------------------------
# Structured providers never receive the research question
# --------------------------------------------------------------------------


@pytest.mark.parametrize("category", [ToolCategory.FINANCIAL, ToolCategory.FILINGS])
def test_financial_and_filings_get_the_company_and_ticker(category: ToolCategory) -> None:
    params = request_params(category, NVIDIA, QUESTION)

    assert params == {"query": "NVIDIA", "symbol": "NVDA"}


def test_jobs_get_the_employer() -> None:
    assert request_params(ToolCategory.JOBS, NVIDIA, QUESTION) == {"query": "NVIDIA"}


@pytest.mark.parametrize(
    "category", [ToolCategory.FINANCIAL, ToolCategory.FILINGS, ToolCategory.JOBS]
)
def test_no_structured_provider_is_sent_the_question(category: ToolCategory) -> None:
    for round_index in range(3):
        params = request_params(category, NVIDIA, QUESTION, round_index=round_index)
        assert "?" not in str(params["query"])
        assert "revenue" not in str(params["query"]).casefold()


def test_the_first_web_query_keeps_the_question() -> None:
    params = request_params(ToolCategory.WEB_SEARCH, NVIDIA, QUESTION)

    assert params == {"query": f"NVIDIA Corporation (NVDA): {QUESTION}"}


# --------------------------------------------------------------------------
# Follow-up rounds target the gap
# --------------------------------------------------------------------------


def test_every_round_asks_something_different() -> None:
    gap = QuestionGap(distinct_sources=1, above_lower_sources=0, cited_hosts=("glassdoor.com",))
    queries = [
        request_params(ToolCategory.WEB_SEARCH, NVIDIA, QUESTION, round_index=index, gap=gap)
        for index in range(4)
    ]

    assert len({str(params) for params in queries}) == 4


def test_a_follow_up_looks_past_the_sources_already_cited() -> None:
    gap = QuestionGap(distinct_sources=1, above_lower_sources=0, cited_hosts=("glassdoor.com",))

    params = request_params(ToolCategory.WEB_SEARCH, NVIDIA, QUESTION, round_index=1, gap=gap)

    assert params["exclude_domains"] == ["glassdoor.com"]


def test_only_low_authority_sources_asks_for_authoritative_ones() -> None:
    gap = QuestionGap(distinct_sources=2, above_lower_sources=0)

    query = str(request_params(ToolCategory.WEB_SEARCH, NVIDIA, QUESTION, round_index=1, gap=gap)["query"])

    assert "investor relations" in query or "filing" in query


def test_one_short_of_corroboration_asks_for_an_independent_source() -> None:
    gap = QuestionGap(distinct_sources=1, above_lower_sources=1)

    query = str(request_params(ToolCategory.NEWS, NVIDIA, QUESTION, round_index=1, gap=gap)["query"])

    assert "independent" in query


def test_a_follow_up_is_built_from_the_questions_content_words() -> None:
    query = str(
        request_params(ToolCategory.WEB_SEARCH, NVIDIA, QUESTION, round_index=1, gap=QuestionGap())[
            "query"
        ]
    )

    assert query.startswith("NVIDIA ")
    for word in ("revenue", "income", "margin", "trend"):
        assert word in query
    for stopword in (" What ", " has ", " the "):
        assert stopword not in f" {query} "
    # The subject is named once, not repeated from the question.
    assert query.casefold().count("nvidia") == 1
