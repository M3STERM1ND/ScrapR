"""The five keyed providers (`DEC-07`, closing `OPEN-05..09`).

These are contract tests, not integration tests. What they assert is that each
provider satisfies the uniform tool contract and the specific acceptance
criteria its requirement names — not that the vendor is up. §15 wants a
recorded cassette per tool for the integration half, and a cassette needs a
real call to record; these run against hand-built payloads shaped like the real
ones, which is what can exist today.

The cases chosen are the ones where a provider-specific mistake would be
invisible until it corrupted the report:

* a financial figure with **no reporting period** (`DEC-10 §4.1` would compare
  it against a different year and call it a conflict),
* a salary Adzuna **predicted** rather than the employer publishing it
  (`REQ-TOOL-006 AC-3`),
* an EDGAR hit with no accession number (`REQ-TOOL-005 AC-3` forbids citing a
  search page in place of a filing),
* and, for all five, that a provider payload arrives as `Untrusted`
  (`DEC-07 §4`) — structured JSON from an API is no more trustworthy than HTML.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from scrapr_core.db.enums import Accessibility, SourceCategory
from scrapr_core.security.trust import UntrustedContentError
from scrapr_core.tools.contract import (
    Tool,
    ToolBudget,
    ToolCategory,
    ToolFailure,
    ToolRequest,
    ToolResult,
)
from scrapr_core.tools.impl.adzuna import AdzunaTool
from scrapr_core.tools.impl.edgar import SecEdgarTool
from scrapr_core.tools.impl.fmp import FinancialModelingPrepTool
from scrapr_core.tools.impl.tavily import news_tool, search_tool

Handler = Callable[[httpx.Request], httpx.Response]


def factory(handler: Handler) -> Callable[[ToolRequest], httpx.AsyncClient]:
    return lambda request: httpx.AsyncClient(transport=httpx.MockTransport(handler))


def replying(payload: Any, status: int = 200) -> Callable[[ToolRequest], httpx.AsyncClient]:
    return factory(lambda request: httpx.Response(status, json=payload))


def ask(category: ToolCategory, query: str = "Acme Corp") -> ToolRequest:
    return ToolRequest(
        category=category,
        params={"query": query},
        budget=ToolBudget(timeout_seconds=5.0, max_results=5),
    )


# --------------------------------------------------------------------------
# Tavily — `OPEN-05` and `OPEN-09`
# --------------------------------------------------------------------------

TAVILY_BODY = {
    "results": [
        {
            "title": "Acme reports record year",
            "url": "https://press.example/acme-fy2025",
            "content": "Acme Corp reported revenue of $1.2bn for fiscal 2025.",
            "score": 0.94,
            "published_date": "2026-02-01T09:00:00Z",
        }
    ]
}


async def test_search_returns_located_untrusted_items() -> None:
    """`REQ-TOOL-002 AC-1`, `AC-2`: ranked results with URLs, which become
    `Source` records."""
    tool = search_tool("key", client_factory=replying(TAVILY_BODY))

    outcome = await tool.invoke(ask(ToolCategory.WEB_SEARCH))

    assert isinstance(outcome, ToolResult)
    item = outcome.items[0]
    assert item.source_url == "https://press.example/acme-fy2025"
    assert item.accessibility is Accessibility.ACCESSIBLE
    with pytest.raises(UntrustedContentError):
        str(item.content)


async def test_news_keeps_publication_date_apart_from_retrieval() -> None:
    """`REQ-TOOL-007 AC-1`, and `AC-3` reads it for change detection. Two
    timestamps that collapse into one make "this is new" unanswerable."""
    tool = news_tool("key", client_factory=replying(TAVILY_BODY))

    outcome = await tool.invoke(ask(ToolCategory.NEWS))

    assert isinstance(outcome, ToolResult)
    item = outcome.items[0]
    assert item.published_at is not None
    assert item.published_at.tzinfo is not None
    assert item.published_at != item.retrieved_at
    assert item.source_category is SourceCategory.NEWS


async def test_an_unparseable_date_is_absent_rather_than_invented() -> None:
    """Recency feeds conflict explanation and confidence, so a wrong date is
    worse than a missing one."""
    body = {"results": [{**TAVILY_BODY["results"][0], "published_date": "last Tuesday"}]}
    tool = news_tool("key", client_factory=replying(body))

    outcome = await tool.invoke(ask(ToolCategory.NEWS))

    assert isinstance(outcome, ToolResult)
    assert outcome.items[0].published_at is None


async def test_a_result_without_a_url_is_dropped() -> None:
    """`ToolItem` refuses an unlocatable item, so it is dropped here rather
    than raising and losing the rest of the page of results."""
    body = {"results": [{"title": "no link", "content": "text"}, *TAVILY_BODY["results"]]}
    tool = search_tool("key", client_factory=replying(body))

    outcome = await tool.invoke(ask(ToolCategory.WEB_SEARCH))

    assert isinstance(outcome, ToolResult)
    assert len(outcome.items) == 1


async def test_a_rate_limited_search_is_classified_not_raised() -> None:
    """`REQ-TOOL-010 AC-1`, and `rate_limited` specifically so retry policy can
    tell it from a dead provider."""
    tool = search_tool("key", client_factory=replying({}, status=429))

    outcome = await tool.invoke(ask(ToolCategory.WEB_SEARCH))

    assert isinstance(outcome, ToolFailure)
    assert outcome.kind == "rate_limited"


# --------------------------------------------------------------------------
# Financial Modeling Prep — `OPEN-06`
# --------------------------------------------------------------------------


def fmp_factory(statements: list[dict[str, Any]], symbol: str = "ACME") -> Any:
    """Answers the symbol search, then the statement call."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "/search" in request.url.path:
            return httpx.Response(200, json=[{"symbol": symbol}])
        return httpx.Response(200, json=statements)

    return factory(handler)


STATEMENT = {
    "calendarYear": "2025",
    "reportedCurrency": "USD",
    "date": "2026-01-31",
    "fillingDate": "2026-02-14",
    "revenue": 1_200_000_000,
    "netIncome": 210_000_000,
    "grossProfit": 900_000_000,
    "operatingIncome": 300_000_000,
    "finalLink": "https://sec.example/acme-10k",
}


async def test_financial_values_carry_period_currency_and_basis() -> None:
    """`REQ-TOOL-004 AC-1`, `AC-2`, `AC-3` in one assertion each.

    `basis` is the one `DEC-10 §4.2` depends on: an analyst estimate
    disagreeing with a filed figure is not a conflict, and without this field
    there is no way to tell the two apart.
    """
    tool = FinancialModelingPrepTool(api_key="k", client_factory=fmp_factory([STATEMENT]))

    outcome = await tool.invoke(ask(ToolCategory.FINANCIAL))

    assert isinstance(outcome, ToolResult)
    structured = outcome.items[0].structured
    assert structured is not None
    assert structured["period"] == "2025"
    assert structured["currency"] == "USD"
    assert structured["basis"] == "reported"


async def test_a_figure_with_no_period_is_not_usable_evidence() -> None:
    """`DEC-10 §4.1`: a value that cannot be scoped to a period would be
    compared against a different year and reported as a conflict."""
    undated = {k: v for k, v in STATEMENT.items() if k not in {"calendarYear", "date"}}
    tool = FinancialModelingPrepTool(api_key="k", client_factory=fmp_factory([undated]))

    outcome = await tool.invoke(ask(ToolCategory.FINANCIAL))

    assert isinstance(outcome, ToolResult)
    assert outcome.items == ()


async def test_a_private_company_is_not_found_rather_than_an_error() -> None:
    """The ordinary case for a subject with no listed security. An error here
    would read as a broken tool instead of an absent fact."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    tool = FinancialModelingPrepTool(api_key="k", client_factory=factory(handler))

    outcome = await tool.invoke(ask(ToolCategory.FINANCIAL))

    assert isinstance(outcome, ToolFailure)
    assert outcome.kind == "not_found"


async def test_the_quotable_sentence_states_the_currency() -> None:
    """Extraction quotes verbatim, so a figure whose text omits its unit can be
    cited as a bare number."""
    tool = FinancialModelingPrepTool(api_key="k", client_factory=fmp_factory([STATEMENT]))

    outcome = await tool.invoke(ask(ToolCategory.FINANCIAL))

    assert isinstance(outcome, ToolResult)
    assert "USD" in outcome.items[0].content.text


# --------------------------------------------------------------------------
# SEC EDGAR — `OPEN-07`
# --------------------------------------------------------------------------

EDGAR_BODY = {
    "hits": {
        "hits": [
            {
                "_id": "0000320193-26-000006:acme-10k.htm",
                "_source": {
                    "display_names": ["Acme Corp (ACME)"],
                    "ciks": ["0000320193"],
                    "root_form": "10-K",
                    "file_date": "2026-02-14",
                    "period_ending": "2025-12-31",
                },
            }
        ]
    }
}


async def test_a_filing_citation_resolves_to_the_filing() -> None:
    """`REQ-TOOL-005 AC-3`: the specific document, never a search results
    page."""
    tool = SecEdgarTool(user_agent="ScrapR test (dev@example.com)",
                        client_factory=replying(EDGAR_BODY))

    outcome = await tool.invoke(ask(ToolCategory.FILINGS))

    assert isinstance(outcome, ToolResult)
    url = outcome.items[0].source_url or ""
    assert "acme-10k.htm" in url
    assert "search" not in url


async def test_filings_are_categorised_so_tiering_can_call_them_primary() -> None:
    """`REQ-TOOL-005 AC-1` holds via `DEC-08` rule 1, which tiers on the
    category rather than on a tool asserting its own authority."""
    tool = SecEdgarTool(user_agent="ua", client_factory=replying(EDGAR_BODY))

    outcome = await tool.invoke(ask(ToolCategory.FILINGS))

    assert isinstance(outcome, ToolResult)
    assert outcome.items[0].source_category is SourceCategory.FILING


async def test_filing_date_and_period_are_kept_apart() -> None:
    """`REQ-TOOL-005 AC-2`. `DEC-10 §4.1` compares only within a period, so
    collapsing the two would make every year look like the same year."""
    tool = SecEdgarTool(user_agent="ua", client_factory=replying(EDGAR_BODY))

    outcome = await tool.invoke(ask(ToolCategory.FILINGS))

    assert isinstance(outcome, ToolResult)
    structured = outcome.items[0].structured
    assert structured is not None
    assert structured["period_ending"] == "2025-12-31"
    assert structured["filed"] != structured["period_ending"]


async def test_a_hit_with_no_accession_is_dropped() -> None:
    """Without one there is no filing-specific URL, and `AC-3` forbids citing
    a search page in its place."""
    body = {"hits": {"hits": [{"_id": "", "_source": EDGAR_BODY["hits"]["hits"][0]["_source"]}]}}
    tool = SecEdgarTool(user_agent="ua", client_factory=replying(body))

    outcome = await tool.invoke(ask(ToolCategory.FILINGS))

    assert isinstance(outcome, ToolResult)
    assert outcome.items == ()


async def test_edgar_sends_a_contactable_user_agent() -> None:
    """EDGAR rejects requests without one, which is why that string gates the
    whole category rather than an API key."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("user-agent", ""))
        return httpx.Response(200, json=EDGAR_BODY)

    tool = SecEdgarTool(user_agent="ScrapR (dev@example.com)", client_factory=factory(handler))
    await tool.invoke(ask(ToolCategory.FILINGS))

    assert "dev@example.com" in seen[0]


# --------------------------------------------------------------------------
# Adzuna — `OPEN-08`
# --------------------------------------------------------------------------


def posting(**overrides: Any) -> dict[str, Any]:
    base = {
        "id": "123",
        "title": "Senior Engineer",
        "redirect_url": "https://jobs.example/123",
        "company": {"display_name": "Acme Corp"},
        "location": {"display_name": "Austin, TX"},
        "created": "2026-02-01T09:00:00Z",
        "salary_min": 150000,
        "salary_max": 190000,
        "salary_is_predicted": "0",
    }
    return {**base, **overrides}


async def test_a_published_salary_is_marked_as_stated() -> None:
    """`REQ-TOOL-006 AC-3`: a salary is only surfaced with its reliability
    visible."""
    tool = AdzunaTool(app_id="i", app_key="k",
                      client_factory=replying({"results": [posting()]}))

    outcome = await tool.invoke(ask(ToolCategory.JOBS))

    assert isinstance(outcome, ToolResult)
    item = outcome.items[0]
    assert item.structured is not None
    assert item.structured["salary_is_predicted"] is False
    assert "as stated in the posting" in item.content.text


async def test_a_predicted_salary_says_so_in_the_text() -> None:
    """The trap in this provider. A predicted figure that reads like a
    published one is a number `REQ-EVID-013` could never explain a
    disagreement about."""
    tool = AdzunaTool(
        app_id="i", app_key="k",
        client_factory=replying({"results": [posting(salary_is_predicted="1")]}),
    )

    outcome = await tool.invoke(ask(ToolCategory.JOBS))

    assert isinstance(outcome, ToolResult)
    item = outcome.items[0]
    assert item.structured is not None
    assert item.structured["salary_is_predicted"] is True
    assert "not stated by the employer" in item.content.text


async def test_a_missing_prediction_flag_is_treated_as_predicted() -> None:
    """The safe direction. Treating an unattributed salary as published is the
    failure `AC-3` forbids; the reverse merely understates it."""
    entry = posting()
    del entry["salary_is_predicted"]
    tool = AdzunaTool(app_id="i", app_key="k", client_factory=replying({"results": [entry]}))

    outcome = await tool.invoke(ask(ToolCategory.JOBS))

    assert isinstance(outcome, ToolResult)
    structured = outcome.items[0].structured
    assert structured is not None
    assert structured["salary_is_predicted"] is True


async def test_postings_carry_the_employer_for_tiering() -> None:
    """`REQ-TOOL-006 AC-1` needs the advertising company so `DEC-08` rule 4 can
    tell an official posting from a recruiter's repost."""
    tool = AdzunaTool(app_id="i", app_key="k",
                      client_factory=replying({"results": [posting()]}))

    outcome = await tool.invoke(ask(ToolCategory.JOBS))

    assert isinstance(outcome, ToolResult)
    structured = outcome.items[0].structured
    assert structured is not None
    assert structured["company"] == "Acme Corp"


# --------------------------------------------------------------------------
# What all five share
# --------------------------------------------------------------------------


def every_tool() -> list[tuple[str, Tool, ToolCategory]]:
    return [
        ("tavily_search", search_tool("k", client_factory=replying(TAVILY_BODY)), ToolCategory.WEB_SEARCH),
        ("tavily_news", news_tool("k", client_factory=replying(TAVILY_BODY)), ToolCategory.NEWS),
        ("fmp", FinancialModelingPrepTool(api_key="k", client_factory=fmp_factory([STATEMENT])), ToolCategory.FINANCIAL),
        ("edgar", SecEdgarTool(user_agent="ua", client_factory=replying(EDGAR_BODY)), ToolCategory.FILINGS),
        ("adzuna", AdzunaTool(app_id="i", app_key="k", client_factory=replying({"results": [posting()]})), ToolCategory.JOBS),
    ]


@pytest.mark.parametrize(("label", "tool", "category"), every_tool(), ids=lambda v: v if isinstance(v, str) else "")
async def test_provider_content_is_untrusted(label: str, tool: Tool, category: ToolCategory) -> None:
    """`DEC-07 §4`. A company description field returned by a JSON API is
    attacker-influenced in exactly the way a web page is, and none of these
    tools gets an exemption for returning structured data."""
    outcome = await tool.invoke(ask(category))

    assert isinstance(outcome, ToolResult), label
    for item in outcome.items:
        with pytest.raises(UntrustedContentError):
            str(item.content)


@pytest.mark.parametrize(("label", "tool", "category"), every_tool(), ids=lambda v: v if isinstance(v, str) else "")
async def test_an_empty_query_fails_rather_than_searching_for_nothing(
    label: str, tool: Tool, category: ToolCategory
) -> None:
    outcome = await tool.invoke(ToolRequest(category=category, params={}))

    assert isinstance(outcome, ToolFailure), label
    assert outcome.kind == "not_found"


@pytest.mark.parametrize(("label", "tool", "category"), every_tool(), ids=lambda v: v if isinstance(v, str) else "")
async def test_every_tool_reports_its_own_name_and_category(
    label: str, tool: Tool, category: ToolCategory
) -> None:
    """`REQ-TOOL-001 AC-1`: one common shape, whoever answered."""
    outcome = await tool.invoke(ask(category))

    assert isinstance(outcome, ToolResult), label
    assert outcome.category is category
    assert outcome.tool == tool.name


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        pytest.param(401, "blocked", id="bad-key"),
        pytest.param(403, "blocked", id="forbidden"),
        pytest.param(404, "not_found", id="missing"),
        pytest.param(429, "rate_limited", id="throttled"),
        pytest.param(500, "error", id="server"),
    ],
)
async def test_status_codes_classify_the_same_way_across_providers(
    status: int, kind: str
) -> None:
    """One mapping, not five. The classification drives retry policy and
    whether evidence may be cited, so five slightly different ones would be
    five slightly different products."""
    # Annotated because three unrelated classes have no common base — which is
    # the point of `Tool` being a protocol, and worth stating here rather than
    # letting the list infer as `object`.
    tools: list[Tool] = [
        search_tool("k", client_factory=replying({}, status=status)),
        AdzunaTool(app_id="i", app_key="k", client_factory=replying({}, status=status)),
        SecEdgarTool(user_agent="ua", client_factory=replying({}, status=status)),
    ]

    for tool in tools:
        outcome = await tool.invoke(ask(tool.category))
        assert isinstance(outcome, ToolFailure), tool.name
        assert outcome.kind == kind, tool.name


async def test_a_non_json_body_is_an_error_not_an_empty_result() -> None:
    """A 200 that is not JSON is a provider contract break. Reporting "nothing
    found" for it would file a research gap against a broken API."""
    tool = AdzunaTool(
        app_id="i",
        app_key="k",
        client_factory=factory(lambda r: httpx.Response(200, text="<html>nope</html>")),
    )

    outcome = await tool.invoke(ask(ToolCategory.JOBS))

    assert isinstance(outcome, ToolFailure)
    assert outcome.kind == "error"


def test_the_payloads_here_are_shaped_like_the_real_ones() -> None:
    """A guard on the fixtures themselves.

    These payloads stand in for four vendors. If one drifts into a shape the
    provider would never return, every test above passes while proving nothing
    — so the structural assumptions each parser relies on are asserted once,
    here, where a reader can see what is being assumed.
    """
    assert "results" in TAVILY_BODY
    assert json.dumps(TAVILY_BODY)  # serialisable, as an HTTP body must be
    assert ":" in str(EDGAR_BODY["hits"]["hits"][0]["_id"])
    assert "calendarYear" in STATEMENT and "reportedCurrency" in STATEMENT
    assert "salary_is_predicted" in posting()
    assert "fiscalYear" in STABLE_STATEMENT and "filingDate" in STABLE_STATEMENT


# --------------------------------------------------------------------------
# The first real run — FMP's endpoint, structured queries, diagnosable failures
# --------------------------------------------------------------------------

STABLE_STATEMENT = {
    "date": "2025-01-26",
    "symbol": "NVDA",
    "reportedCurrency": "USD",
    "fiscalYear": "2025",
    "period": "FY",
    "filingDate": "2025-02-26",
    "revenue": 130_497_000_000,
    "grossProfit": 97_858_000_000,
    "operatingIncome": 81_453_000_000,
    "netIncome": 72_880_000_000,
}

STABLE_QUOTE = [
    {
        "symbol": "NVDA",
        "name": "NVIDIA Corporation",
        "price": 181.23,
        "marketCap": 4_419_000_000_000,
        "exchange": "NASDAQ",
        "timestamp": 1_789_300_000,
    }
]

LEGACY_403 = {
    "Error Message": "Legacy Endpoint : Due to Legacy endpoints being no longer "
    "supported - This endpoint is only available for legacy users who have valid "
    "subscriptions prior August 31, 2025."
}


def stable_fmp(
    seen: list[httpx.Request], quote_status: int = 200
) -> Callable[[ToolRequest], httpx.AsyncClient]:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if not path.startswith("/stable/"):
            return httpx.Response(403, json=LEGACY_403)
        if path.endswith("/search-name") or path.endswith("/search-symbol"):
            return httpx.Response(200, json=[{"symbol": "NVDA", "exchange": "NASDAQ"}])
        if path.endswith("/income-statement"):
            return httpx.Response(200, json=[STABLE_STATEMENT])
        if path.endswith("/quote"):
            if quote_status != 200:
                return httpx.Response(quote_status, json={"Error Message": "Premium endpoint"})
            return httpx.Response(200, json=STABLE_QUOTE)
        return httpx.Response(404, json={})

    return factory(handler)


def structured_ask(category: ToolCategory, **params: str) -> ToolRequest:
    return ToolRequest(
        category=category, params=params, budget=ToolBudget(timeout_seconds=5.0, max_results=5)
    )


async def test_fmp_calls_the_stable_api_with_the_known_ticker() -> None:
    """The legacy `/api/v3/` endpoints answer 403 for current keys. A known
    ticker is used as-is: no name search, and never the research question."""
    seen: list[httpx.Request] = []
    tool = FinancialModelingPrepTool(api_key="secret-key", client_factory=stable_fmp(seen))

    outcome = await tool.invoke(structured_ask(ToolCategory.FINANCIAL, query="NVIDIA", symbol="NVDA"))

    assert isinstance(outcome, ToolResult)
    assert all(request.url.path.startswith("/stable/") for request in seen)
    assert not any("search" in request.url.path for request in seen)
    assert {request.url.params.get("symbol") for request in seen} == {"NVDA"}


async def test_fmp_reads_the_stable_statement_and_the_quote() -> None:
    seen: list[httpx.Request] = []
    tool = FinancialModelingPrepTool(api_key="k", client_factory=stable_fmp(seen))

    outcome = await tool.invoke(structured_ask(ToolCategory.FINANCIAL, query="NVIDIA", symbol="NVDA"))

    assert isinstance(outcome, ToolResult)
    statement, quote = outcome.items
    assert statement.structured is not None and statement.structured["period"] == "2025"
    assert statement.published_at is not None  # `filingDate`, not `fillingDate`
    assert "Revenue: 130,497,000,000." in statement.content.text
    assert quote.source_identifier is not None and ":quote:" in quote.source_identifier
    assert "market capitalization was 4,419,000,000,000 USD" in quote.content.text
    assert quote.structured is not None and quote.structured["currency"] == "USD"


async def test_fmp_resolves_a_company_name_when_no_ticker_is_known() -> None:
    seen: list[httpx.Request] = []
    tool = FinancialModelingPrepTool(api_key="k", client_factory=stable_fmp(seen))

    outcome = await tool.invoke(structured_ask(ToolCategory.FINANCIAL, query="NVIDIA"))

    assert isinstance(outcome, ToolResult)
    assert seen[0].url.path == "/stable/search-name"
    assert seen[0].url.params["query"] == "NVIDIA"


async def test_a_quote_the_plan_does_not_include_keeps_the_statements() -> None:
    """Graceful degradation: one read refused is not the whole call refused."""
    tool = FinancialModelingPrepTool(api_key="k", client_factory=stable_fmp([], quote_status=402))

    outcome = await tool.invoke(structured_ask(ToolCategory.FINANCIAL, query="NVIDIA", symbol="NVDA"))

    assert isinstance(outcome, ToolResult)
    assert len(outcome.items) == 1
    assert outcome.items[0].structured is not None
    assert outcome.items[0].structured["period"] == "2025"


async def test_a_403_says_why_and_never_repeats_the_key() -> None:
    """The first run recorded "returned 403" and nothing else. The provider's
    reason is the difference between a config fix and a guess."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={**LEGACY_403, "echo": "apikey=secret-key"})

    tool = FinancialModelingPrepTool(api_key="secret-key", client_factory=factory(handler))

    outcome = await tool.invoke(structured_ask(ToolCategory.FINANCIAL, query="NVIDIA", symbol="NVDA"))

    assert isinstance(outcome, ToolFailure)
    assert outcome.kind == "blocked"
    assert "Legacy Endpoint" in outcome.message
    assert "secret-key" not in outcome.message


async def test_edgar_searches_the_company_and_keeps_only_its_filings() -> None:
    """Full-text search matches any filing that mentions the company. A
    supplier's 10-K naming NVIDIA is not NVIDIA's filing."""
    seen: list[httpx.Request] = []
    other = {
        "_id": "0000999999-26-000001:supplier-10k.htm",
        "_source": {
            "display_names": ["Supplier Inc (SUPP)"],
            "ciks": ["0000999999"],
            "root_form": "10-K",
            "file_date": "2026-02-01",
        },
    }
    own = {
        "_id": "0001045810-25-000023:nvda-20250126.htm",
        "_source": {
            "display_names": ["NVIDIA CORP (NVDA) (CIK 0001045810)"],
            "ciks": ["0001045810"],
            "root_form": "10-K",
            "file_date": "2025-02-26",
            "period_ending": "2025-01-26",
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"hits": {"hits": [other, own]}})

    tool = SecEdgarTool(user_agent="ua (dev@example.com)", client_factory=factory(handler))

    outcome = await tool.invoke(structured_ask(ToolCategory.FILINGS, query="NVIDIA", symbol="NVDA"))

    assert isinstance(outcome, ToolResult)
    assert seen[0].url.params["q"] == '"NVIDIA"'
    assert [item.source_identifier for item in outcome.items] == ["edgar:000104581025000023"]


async def test_adzuna_searches_for_the_employer_it_is_given() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"results": [posting()]})

    tool = AdzunaTool(app_id="i", app_key="k", client_factory=factory(handler))
    await tool.invoke(structured_ask(ToolCategory.JOBS, query="NVIDIA"))

    assert seen[0].url.params["what"] == "NVIDIA"


async def test_tavily_passes_domains_to_look_past() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=TAVILY_BODY)

    tool = search_tool("k", client_factory=factory(handler))
    await tool.invoke(
        ToolRequest(
            category=ToolCategory.WEB_SEARCH,
            params={"query": "NVIDIA revenue", "exclude_domains": ["glassdoor.com", "bad value"]},
        )
    )

    assert bodies[0]["exclude_domains"] == ["glassdoor.com"]


def test_failure_detail_redaction_for_the_operator_table() -> None:
    from scrapr_core.observability.telemetry import redact_detail

    message = (
        "fmp_financial raised ConnectError: https://financialmodelingprep.com/stable/quote"
        "?symbol=NVDA&apikey=secret-key failed; app_key=abc123"
    )

    cleaned = redact_detail(message)

    assert "secret-key" not in cleaned
    assert "abc123" not in cleaned
    assert "financialmodelingprep.com/stable/quote" in cleaned
