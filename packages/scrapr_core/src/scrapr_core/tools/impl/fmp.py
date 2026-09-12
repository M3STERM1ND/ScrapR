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

A lookup takes two calls — symbol search, then statements — because the
orchestrator asks in prose and this API answers by ticker. That is one tool
call against the budget, which under-counts the real cost slightly; `DEC-07
§6.3` flags per-call pricing as Phase 8 work.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, final

import httpx

from scrapr_core.db.enums import SourceCategory
from scrapr_core.domain.json import JsonMapping, JsonValue
from scrapr_core.tools.contract import (
    FailureKind,
    ToolCategory,
    ToolOutcome,
    ToolRequest,
    ToolResult,
)
from scrapr_core.tools.impl.http_api import USER_AGENT, api_item, failure, get_json

__all__ = ["FMP_BASE", "FinancialModelingPrepTool"]

FMP_BASE: Final = "https://financialmodelingprep.com/api/v3"

MAX_STATEMENTS: Final = 3
"""Three annual periods. Enough for a trend, few enough that one question does
not return a decade of statements nobody asked about."""


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
        if not query:
            return failure("not_found", "no query supplied", self.name, self.category)

        client = self._client(request)
        async with client:
            symbol, kind, detail = await self._resolve_symbol(client, query)
            if symbol is None:
                return failure(kind or "not_found", detail, self.name, self.category)

            payload, kind, detail = await get_json(
                client,
                f"{FMP_BASE}/income-statement/{symbol}",
                params={
                    "apikey": self.api_key,
                    "limit": MAX_STATEMENTS,
                    "period": "annual",
                },
            )
            if kind is not None:
                return failure(kind, detail, self.name, self.category)

        return self._to_result(payload, symbol, request)

    # ------------------------------------------------------------------

    async def _resolve_symbol(
        self, client: httpx.AsyncClient, query: str
    ) -> tuple[str | None, FailureKind | None, str]:
        """Turn prose into a ticker, or say why not.

        A subject with no listed security is the ordinary case for a private
        company, not an error — it returns `not_found`, the run reports the
        area as a gap, and nothing else is affected.
        """
        payload, kind, detail = await get_json(
            client,
            f"{FMP_BASE}/search",
            params={"query": query, "limit": 1, "apikey": self.api_key},
        )
        if kind is not None:
            return None, kind, detail

        if not isinstance(payload, list) or not payload:
            return None, "not_found", f"no listed security matches {query!r}"

        first = payload[0]
        symbol = first.get("symbol") if isinstance(first, dict) else None
        if not symbol:
            return None, "not_found", f"no symbol in the match for {query!r}"
        return str(symbol), None, ""

    def _client(self, request: ToolRequest) -> httpx.AsyncClient:
        if self.client_factory is not None:
            built = self.client_factory(request)
            if isinstance(built, httpx.AsyncClient):
                return built
        return httpx.AsyncClient(
            timeout=request.budget.timeout_seconds, headers={"User-Agent": USER_AGENT}
        )

    def _to_result(
        self, payload: JsonValue, symbol: str, request: ToolRequest
    ) -> ToolOutcome:
        if not isinstance(payload, list):
            return failure(
                "error", "fmp returned no statement array", self.name, self.category
            )

        items = []
        for row in payload[: request.budget.max_results]:
            if not isinstance(row, dict):
                continue

            period = str(row.get("calendarYear") or row.get("date") or "").strip()
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
                    published_at=_as_date(row.get("fillingDate") or row.get("date")),
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

        return ToolResult(
            items=tuple(items),
            retrieved_at=dt.datetime.now(dt.UTC),
            tool=self.name,
            category=self.category,
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
