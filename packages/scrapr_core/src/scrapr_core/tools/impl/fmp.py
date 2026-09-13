"""Financial data, via Financial Modeling Prep (`DEC-07`, closing `OPEN-06`).

`REQ-TOOL-004` asks for three things this provider can actually answer, which
is why `DEC-07 §3.2` chose it over cheaper alternatives:

* `AC-1` — every value carries its **reporting period**. `DEC-10 §4.1` depends
  on it: FY2024 revenue against FY2025 revenue is two facts, and without the
  period they would be compared and reported as a conflict.
* `AC-2` — **currency and units** are exposed, so `REQ-EVID-008` normalization
  has something to normalize rather than something to guess.
* `AC-3` — **estimates are distinguishable from reported figures**, which
  `DEC-10 §4.2` needs: an analyst estimate disagreeing with a reported figure
  is not a source being wrong, and counting it as a conflict would flood the
  report with disagreements that are not disagreements.

**The `/stable/` API, not `/api/v3/`.** FMP retired the v3 endpoints for keys
issued after August 2025 and answers them with 403, which is exactly how the
first real run failed: every call `blocked`, recorded only as "returned 403".
The stable API takes the symbol as a query parameter and spells two fields
differently (`fiscalYear`, `filingDate`); both spellings are read, so a payload
from either generation is understood.

**The lookup is structured.** The orchestrator sends a company name and, where
it knows one, a ticker (`orchestrator.queries`). A known ticker is used as-is;
otherwise the name is resolved by FMP's name search. The research question is
never sent — a symbol search cannot do anything with a sentence.

**Two reads, degrading independently.** Annual income statements answer
performance questions; the quote answers valuation ones (market capitalisation,
share price). A plan that includes one and not the other yields what it
includes: a 402 on the quote does not throw away the statements. Only when
nothing at all could be read is the call a failure, and then it carries the
provider's own reason (`http_api.failure_detail`), so the next 403 says *why*.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, final

import httpx

from scrapr_core.db.enums import SourceCategory
from scrapr_core.domain.json import JsonMapping, JsonValue
from scrapr_core.tools.contract import (
    FailureKind,
    ToolCategory,
    ToolItem,
    ToolOutcome,
    ToolRequest,
    ToolResult,
)
from scrapr_core.tools.impl.http_api import USER_AGENT, api_item, failure, get_json

__all__ = ["FMP_BASE", "FinancialModelingPrepTool"]

FMP_BASE: Final = "https://financialmodelingprep.com/stable"

MAX_STATEMENTS: Final = 3
"""Three annual periods. Enough for a trend, few enough that one question does
not return a decade of statements nobody asked about."""

_TICKER: Final = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")

US_EXCHANGES: Final = frozenset({"NASDAQ", "NYSE", "AMEX", "NYSE AMERICAN", "NYSEARCA", "CBOE"})
"""Listings whose quote currency is USD by the exchange's own rules. Elsewhere
the quote's currency is not stated, and is not guessed."""


@final
@dataclass(frozen=True, slots=True)
class FinancialModelingPrepTool:
    """Structured financial and stock information."""

    api_key: str
    name: str = "fmp_financial"
    category: ToolCategory = ToolCategory.FINANCIAL
    client_factory: Callable[[ToolRequest], httpx.AsyncClient] | None = None

    async def invoke(self, request: ToolRequest) -> ToolOutcome:
        query = str(request.params.get("query", "")).strip()
        given = str(request.params.get("symbol") or "").strip().upper()
        if not query and not given:
            return failure("not_found", "no query supplied", self.name, self.category)

        client = self._client(request)
        async with client:
            if _TICKER.match(given):
                symbol, exchange = given, None
            else:
                resolved, kind, detail = await self._resolve_symbol(client, query)
                if resolved is None:
                    return failure(kind or "not_found", detail, self.name, self.category)
                symbol, exchange = resolved

            statements, statement_kind, statement_detail = await get_json(
                client,
                f"{FMP_BASE}/income-statement",
                params={
                    "symbol": symbol,
                    "apikey": self.api_key,
                    "limit": MAX_STATEMENTS,
                    "period": "annual",
                },
            )
            quote, quote_kind, _ = await get_json(
                client,
                f"{FMP_BASE}/quote",
                params={"symbol": symbol, "apikey": self.api_key},
            )

        if statement_kind is not None and quote_kind is not None:
            # Nothing could be read. The statement call's reason leads: it is
            # the read every financial question depends on.
            return failure(statement_kind, statement_detail, self.name, self.category)

        items: list[ToolItem] = []
        if statement_kind is None:
            if not isinstance(statements, list):
                if quote_kind is not None:
                    return failure(
                        "error", "fmp returned no statement array", self.name, self.category
                    )
            else:
                items.extend(self._statement_items(statements, symbol, request))
        if quote_kind is None:
            quoted = self._quote_item(quote, symbol, exchange)
            if quoted is not None:
                items.append(quoted)

        return ToolResult(
            items=tuple(items[: request.budget.max_results]),
            retrieved_at=dt.datetime.now(dt.UTC),
            tool=self.name,
            category=self.category,
        )

    # ------------------------------------------------------------------

    async def _resolve_symbol(
        self, client: httpx.AsyncClient, query: str
    ) -> tuple[tuple[str, str | None] | None, FailureKind | None, str]:
        """Turn a company name (or a bare ticker) into a listed symbol.

        A subject with no listed security is the ordinary case for a private
        company, not an error — it returns `not_found`, the run reports the
        area as a gap, and nothing else is affected.
        """
        # Name first: an all-capitals company name ("NVIDIA") looks like a
        # ticker and is not one. A bare ticker is tried as a symbol only when
        # the name search found nothing.
        endpoints = ["search-name"]
        if _TICKER.match(query):
            endpoints.append("search-symbol")

        for endpoint in endpoints:
            payload, kind, detail = await get_json(
                client,
                f"{FMP_BASE}/{endpoint}",
                params={"query": query, "limit": 5, "apikey": self.api_key},
            )
            if kind is not None:
                return None, kind, detail
            if not isinstance(payload, list):
                continue
            for entry in payload:
                if not isinstance(entry, dict):
                    continue
                symbol = str(entry.get("symbol") or "").strip()
                if symbol:
                    exchange = entry.get("exchange") or entry.get("exchangeShortName")
                    return (symbol, str(exchange) if exchange else None), None, ""

        return None, "not_found", f"no listed security matches {query!r}"

    def _client(self, request: ToolRequest) -> httpx.AsyncClient:
        if self.client_factory is not None:
            built = self.client_factory(request)
            if isinstance(built, httpx.AsyncClient):
                return built
        return httpx.AsyncClient(
            timeout=request.budget.timeout_seconds, headers={"User-Agent": USER_AGENT}
        )

    def _statement_items(
        self, payload: list[JsonValue], symbol: str, request: ToolRequest
    ) -> list[ToolItem]:
        items: list[ToolItem] = []
        for row in payload[: request.budget.max_results]:
            if not isinstance(row, dict):
                continue

            period = str(
                row.get("fiscalYear") or row.get("calendarYear") or row.get("date") or ""
            ).strip()
            currency = str(row.get("reportedCurrency") or "").strip()
            if not period:
                # `AC-1` and `DEC-10 §4.1`: a figure with no period cannot be
                # compared against anything, so it is not usable evidence.
                continue

            items.append(
                api_item(
                    tool=self.name,
                    source_name=f"{symbol} income statement {period}",
                    text=self._describe(row, symbol, period, currency),
                    source_category=SourceCategory.FINANCIAL,
                    source_url=str(row.get("finalLink") or row.get("link") or "") or None,
                    source_identifier=f"fmp:{symbol}:income:{period}",
                    published_at=_as_date(
                        row.get("filingDate") or row.get("fillingDate") or row.get("date")
                    ),
                    structured={
                        "symbol": symbol,
                        "period": period,
                        "currency": currency,
                        # `AC-3`, and `DEC-10 §4.2` reads it. These are filed
                        # figures, not projections, and saying so is what keeps
                        # an estimate from being compared against them as if it
                        # were a competing measurement.
                        "basis": "reported",
                        "revenue": row.get("revenue"),
                        "net_income": row.get("netIncome"),
                        "gross_profit": row.get("grossProfit"),
                        "operating_income": row.get("operatingIncome"),
                    },
                )
            )
        return items

    def _quote_item(
        self, payload: JsonValue, symbol: str, exchange: str | None
    ) -> ToolItem | None:
        """Market capitalisation and share price, as of the quote's own time.

        Point-in-time figures: no reporting period, and a timestamp that is the
        publication date, so `DEC-10 §5` staleness treats a day-old price as
        stale. Returns `None` for a payload that is not a quote rather than
        failing the statements that may have come back beside it.
        """
        row = payload[0] if isinstance(payload, list) and payload else payload
        if not isinstance(row, dict):
            return None
        price = row.get("price")
        market_cap = row.get("marketCap")
        if not isinstance(price, (int, float)) and not isinstance(market_cap, (int, float)):
            return None

        listed_on = str(row.get("exchange") or exchange or "").strip().upper()
        currency = "USD" if listed_on in US_EXCHANGES else ""
        as_of = _as_timestamp(row.get("timestamp"))
        stamp = as_of.date().isoformat() if as_of else "the time of retrieval"
        unit = f" {currency}" if currency else ""

        sentences = []
        if isinstance(market_cap, (int, float)):
            sentences.append(
                f"{symbol} market capitalization was {market_cap:,.0f}{unit} as of {stamp}."
            )
        if isinstance(price, (int, float)):
            sentences.append(f"{symbol} share price was {price:,.2f}{unit} as of {stamp}.")

        return api_item(
            tool=self.name,
            source_name=f"{symbol} market quote",
            text=" ".join(sentences),
            source_category=SourceCategory.FINANCIAL,
            source_identifier=f"fmp:{symbol}:quote:{stamp}",
            published_at=as_of,
            structured={
                "symbol": symbol,
                "currency": currency or None,
                "basis": "reported",
                "price": price,
                "market_cap": market_cap,
            },
        )

    @staticmethod
    def _describe(row: JsonMapping, symbol: str, period: str, currency: str) -> str:
        """A sentence extraction can quote verbatim.

        Written by us from typed fields, never from provider prose, so the
        excerpt a reader sees is one the numbers actually support. The
        structured payload carries the same values for anything that needs to
        compute rather than quote.
        """
        unit = currency or "reporting currency"
        parts = [f"{symbol} reported the following for fiscal {period}, in {unit}."]
        for label, key in (
            ("Revenue", "revenue"),
            ("Gross profit", "grossProfit"),
            ("Operating income", "operatingIncome"),
            ("Net income", "netIncome"),
        ):
            value = row.get(key)
            if isinstance(value, (int, float)):
                parts.append(f"{label}: {value:,.0f}.")
        return " ".join(parts)


def _as_date(raw: object) -> dt.datetime | None:
    """Parse a filing date. Timezone-aware or absent — `ToolItem` rejects naive."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)


def _as_timestamp(raw: object) -> dt.datetime | None:
    """A quote's epoch-seconds timestamp, or `None`."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw <= 0:
        return None
    try:
        return dt.datetime.fromtimestamp(raw, tz=dt.UTC)
    except (OverflowError, OSError, ValueError):
        return None
