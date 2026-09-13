"""Web search and news, via Tavily (`DEC-07`, closing `OPEN-05` and `OPEN-09`).

One client, two registrations. The registry keys on category, so search and
news are separate tools that happen to share a vendor — either can be replaced
without touching the other, which is the property that makes serving two
categories from one provider acceptable rather than merely convenient.

**News mode is the weakest row in `DEC-07`**, and the docstring says so where
someone changing it will read it. A general search API in news mode has no
outlet metadata and no wire-story deduplication, so three outlets carrying one
wire story arrive as three independent sources. `DEC-08` has to supply the
"established publisher" judgement from its own allowlist instead, and
`DEC-07 §7` records the symptom that should prompt replacing this: conflict
detection reporting the same story as corroboration.

The one thing news mode genuinely does supply is a **publication date distinct
from the retrieval timestamp**, which `REQ-TOOL-007 AC-1` requires and Update
Research change detection reads (`AC-3`).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, final

import httpx

from scrapr_core.db.enums import SourceCategory
from scrapr_core.domain.json import JsonValue
from scrapr_core.tools.contract import (
    ToolCategory,
    ToolOutcome,
    ToolRequest,
    ToolResult,
)
from scrapr_core.tools.impl.http_api import (
    USER_AGENT,
    api_item,
    classify_status,
    failure,
    failure_detail,
)

__all__ = ["TAVILY_SEARCH_URL", "TavilyTool", "news_tool", "search_tool"]

TAVILY_SEARCH_URL: Final = "https://api.tavily.com/search"


def _published(raw: object) -> dt.datetime | None:
    """Parse a publication date, or `None`.

    Never raises and never guesses. `ToolItem` rejects a naive datetime, and an
    unparseable date is better absent than invented: recency feeds both
    conflict explanation and confidence, and a wrong date there is worse than
    a missing one.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)


MAX_EXCLUDED_DOMAINS: Final = 20


def _domains(raw: object) -> list[str]:
    """A clean list of hostnames from a request parameter, or nothing."""
    if not isinstance(raw, (list, tuple)):
        return []
    hosts = [
        str(value).strip().lower()
        for value in raw
        if isinstance(value, str) and value.strip() and " " not in value.strip()
    ]
    return list(dict.fromkeys(hosts))[:MAX_EXCLUDED_DOMAINS]


@final
@dataclass(frozen=True, slots=True)
class TavilyTool:
    """Tavily search, in either general or news mode."""

    api_key: str
    name: str = "tavily_search"
    category: ToolCategory = ToolCategory.WEB_SEARCH
    topic: str = "general"
    source_category: SourceCategory = SourceCategory.WEB
    client_factory: Callable[[ToolRequest], httpx.AsyncClient] | None = None

    async def invoke(self, request: ToolRequest) -> ToolOutcome:
        query = str(request.params.get("query", "")).strip()
        if not query:
            return failure("not_found", "no query supplied", self.name, self.category)

        payload: dict[str, object] = {
            "api_key": self.api_key,
            "query": query,
            "topic": self.topic,
            "max_results": request.budget.max_results,
            "search_depth": "basic",
            "include_raw_content": False,
        }
        excluded = _domains(request.params.get("exclude_domains"))
        if excluded:
            # A follow-up round asking for sources the question does not have
            # yet: the domains it already cites are what it must look past.
            payload["exclude_domains"] = excluded

        client = self._client(request)
        async with client:
            # Tavily takes the query as a POST body; `get_json` is the GET
            # path, so this one call is made directly and classified the same
            # way rather than bending the shared helper out of shape.
            try:
                response = await client.post(TAVILY_SEARCH_URL, json=payload)
            except httpx.TimeoutException:
                return failure(
                    "timeout", "tavily did not respond in time", self.name, self.category
                )
            except httpx.HTTPError as exc:
                return failure(
                    "error", f"tavily failed: {type(exc).__name__}", self.name, self.category
                )

            kind = classify_status(response.status_code)
            if kind is not None:
                return failure(
                    kind,
                    failure_detail(
                        TAVILY_SEARCH_URL, response, {"api_key": self.api_key}
                    ),
                    self.name,
                    self.category,
                )

            try:
                body = response.json()
            except ValueError:
                return failure(
                    "error", "tavily returned a non-JSON body", self.name, self.category
                )

        return self._to_result(body, request)

    # ------------------------------------------------------------------

    def _client(self, request: ToolRequest) -> httpx.AsyncClient:
        if self.client_factory is not None:
            built = self.client_factory(request)
            if isinstance(built, httpx.AsyncClient):
                return built
        return httpx.AsyncClient(
            timeout=request.budget.timeout_seconds, headers={"User-Agent": USER_AGENT}
        )

    def _to_result(self, body: JsonValue, request: ToolRequest) -> ToolOutcome:
        raw = body.get("results") if isinstance(body, dict) else None
        if not isinstance(raw, list):
            return failure(
                "error", "tavily returned no results array", self.name, self.category
            )

        items = []
        for entry in raw[: request.budget.max_results]:
            if not isinstance(entry, dict):
                continue
            url = entry.get("url")
            text = str(entry.get("content") or "").strip()
            if not url or not text:
                # No locator or no content: `ToolItem` would reject the first
                # and the second is not evidence of anything.
                continue

            items.append(
                api_item(
                    tool=self.name,
                    source_name=str(entry.get("title") or url),
                    text=text,
                    source_category=self.source_category,
                    source_url=str(url),
                    published_at=_published(entry.get("published_date")),
                    structured={"score": entry.get("score")}
                    if entry.get("score") is not None
                    else None,
                )
            )

        return ToolResult(
            items=tuple(items),
            retrieved_at=dt.datetime.now(dt.UTC),
            tool=self.name,
            category=self.category,
        )


def search_tool(api_key: str, client_factory: Callable[[ToolRequest], httpx.AsyncClient] | None = None) -> TavilyTool:
    """General web search (`REQ-TOOL-002`, `OPEN-05`)."""
    return TavilyTool(
        api_key=api_key,
        name="tavily_search",
        category=ToolCategory.WEB_SEARCH,
        topic="general",
        source_category=SourceCategory.WEB,
        client_factory=client_factory,
    )


def news_tool(api_key: str, client_factory: Callable[[ToolRequest], httpx.AsyncClient] | None = None) -> TavilyTool:
    """Recent news (`REQ-TOOL-007`, `OPEN-09`). See the module docstring for
    what this does not give us."""
    return TavilyTool(
        api_key=api_key,
        name="tavily_news",
        category=ToolCategory.NEWS,
        topic="news",
        source_category=SourceCategory.NEWS,
        client_factory=client_factory,
    )
