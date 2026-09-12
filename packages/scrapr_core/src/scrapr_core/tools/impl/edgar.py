"""Regulatory filings, via SEC EDGAR (`DEC-07`, closing `OPEN-07`).

The easiest row in `DEC-07` and the only one with no key. It is the
authoritative source, which makes `REQ-TOOL-005 AC-1` — filing-derived sources
are primary tier — true by construction rather than by assertion: `DEC-08`
rule 1 tiers on `SourceCategory.FILING`, and that is what this tool sets.

`AC-3` asks that a citation resolve to **the specific filing, not a search
results page**. EDGAR addresses filings by accession number, so the URL this
builds points at one document and keeps pointing at it.

**Its limit is jurisdiction, and the limit is the design.** US registrants
only. A non-US subject produces no filings, the category returns nothing, and
the run names the gap — which is the ordinary unserved-category path, not a new
failure mode. `DEC-07 §3.3` records this rather than hiding it.

EDGAR has no API key and instead **requires a descriptive User-Agent with
contact details**, refusing requests without one. That string is therefore what
gates this category: absent, the tool does not register.
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
    ToolCategory,
    ToolOutcome,
    ToolRequest,
    ToolResult,
)
from scrapr_core.tools.impl.http_api import api_item, failure, get_json

__all__ = ["EDGAR_SEARCH_URL", "SecEdgarTool"]

EDGAR_SEARCH_URL: Final = "https://efts.sec.gov/LATEST/search-index"
EDGAR_ARCHIVE: Final = "https://www.sec.gov/Archives/edgar/data"

FORMS: Final = "10-K,10-Q,8-K,20-F,S-1,DEF 14A"
"""Annual, quarterly, current, foreign-private-issuer, registration and proxy.
The set a research question about a company is plausibly answered by."""


@final
@dataclass(frozen=True, slots=True)
class SecEdgarTool:
    """Full-text search over EDGAR filings."""

    user_agent: str
    name: str = "sec_edgar"
    category: ToolCategory = ToolCategory.FILINGS
    client_factory: Callable[[ToolRequest], httpx.AsyncClient] | None = None

    async def invoke(self, request: ToolRequest) -> ToolOutcome:
        query = str(request.params.get("query", "")).strip()
        if not query:
            return failure("not_found", "no query supplied", self.name, self.category)

        client = self._client(request)
        async with client:
            payload, kind, detail = await get_json(
                client,
                EDGAR_SEARCH_URL,
                params={"q": f'"{query}"', "forms": FORMS},
                # Required. EDGAR rejects a request without a contactable
                # User-Agent, which is why this string gates the category.
                headers={"User-Agent": self.user_agent},
            )
            if kind is not None:
                return failure(kind, detail, self.name, self.category)

        return self._to_result(payload, request)

    # ------------------------------------------------------------------

    def _client(self, request: ToolRequest) -> httpx.AsyncClient:
        if self.client_factory is not None:
            built = self.client_factory(request)
            if isinstance(built, httpx.AsyncClient):
                return built
        return httpx.AsyncClient(timeout=request.budget.timeout_seconds)

    def _to_result(self, payload: JsonValue, request: ToolRequest) -> ToolOutcome:
        hits = _hits(payload)
        if hits is None:
            return failure(
                "error", "edgar returned no hits structure", self.name, self.category
            )

        items = []
        for hit in hits[: request.budget.max_results]:
            if not isinstance(hit, dict):
                continue
            source = hit.get("_source")
            if not isinstance(source, dict):
                continue

            accession, document = _locate(hit)
            cik = _first_cik(source)
            if not accession or not cik:
                # Without both there is no filing-specific URL, and `AC-3`
                # forbids citing a search page in its place.
                continue

            form = str(source.get("root_form") or source.get("file_type") or "filing")
            company = _company(source)
            filed = _as_date(source.get("file_date"))
            period = str(source.get("period_ending") or "").strip()

            items.append(
                api_item(
                    tool=self.name,
                    source_name=f"{company} {form}"
                    + (f" for period {period}" if period else ""),
                    text=_describe(company, form, filed, period),
                    # `DEC-08` rule 1 tiers on this, which is how `AC-1` holds
                    # without this tool asserting a tier of its own.
                    source_category=SourceCategory.FILING,
                    source_url=f"{EDGAR_ARCHIVE}/{cik}/{accession}/{document}"
                    if document
                    else f"{EDGAR_ARCHIVE}/{cik}/{accession}",
                    source_identifier=f"edgar:{accession}",
                    published_at=filed,
                    structured={
                        "form": form,
                        "cik": cik,
                        "accession": accession,
                        # `AC-2`: filing date and reporting period, kept apart.
                        # `DEC-10 §4.1` compares only within a period.
                        "period_ending": period or None,
                        "filed": filed.isoformat() if filed else None,
                    },
                )
            )

        return ToolResult(
            items=tuple(items),
            retrieved_at=dt.datetime.now(dt.UTC),
            tool=self.name,
            category=self.category,
        )


def _hits(payload: JsonValue) -> list[JsonValue] | None:
    """EDGAR nests hits one level deeper than most search APIs."""
    if not isinstance(payload, dict):
        return None
    outer = payload.get("hits")
    if not isinstance(outer, dict):
        return None
    inner = outer.get("hits")
    return inner if isinstance(inner, list) else None


def _locate(hit: JsonMapping) -> tuple[str, str]:
    """Split EDGAR's `<accession>:<document>` id into its two halves."""
    raw = str(hit.get("_id") or "")
    accession, _, document = raw.partition(":")
    return accession.replace("-", ""), document


def _first_cik(source: JsonMapping) -> str:
    ciks = source.get("ciks")
    if isinstance(ciks, list) and ciks:
        return str(ciks[0]).lstrip("0") or str(ciks[0])
    return ""


def _company(source: JsonMapping) -> str:
    names = source.get("display_names")
    if isinstance(names, list) and names:
        return str(names[0])
    return "Unknown registrant"


def _describe(
    company: str, form: str, filed: dt.datetime | None, period: str
) -> str:
    """A sentence stating what the filing is.

    Written from typed fields rather than from filing prose. The filing body is
    not fetched here: page fetch retrieves a specific document when a question
    needs its text, and `REQ-TOOL-003` already does that job properly.
    """
    parts = [f"{company} filed a {form} with the SEC"]
    if filed:
        parts.append(f"on {filed.date().isoformat()}")
    if period:
        parts.append(f"covering the period ending {period}")
    return " ".join(parts) + "."


def _as_date(raw: object) -> dt.datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)
