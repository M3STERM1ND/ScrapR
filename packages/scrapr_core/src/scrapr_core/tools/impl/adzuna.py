"""Job postings, via Adzuna (`DEC-07`, closing `OPEN-08`).

`REQ-TOOL-006` asks for three things, and one of them is a trap worth naming.

* `AC-1` — official company postings are **primary tier**. Adzuna carries the
  advertising company, which is what lets `DEC-08` rule 4 recognise a posting
  on the subject's own domain as primary rather than treating a recruiter's
  repost as equally close to the source.
* `AC-2` — postings carry a **retrieval timestamp** so Update Research can
  detect new ones (`REQ-VER-004`). Every `ToolItem` does, structurally.
* `AC-3` — **salary is only surfaced with its source and reliability visible.**

`AC-3` is the trap. Adzuna returns a salary for most postings, but a large
share of those are *predicted* by Adzuna rather than stated in the ad, and the
API flags which via `salary_is_predicted`. A tool that dropped that flag would
hand synthesis a number indistinguishable from one an employer actually
published — and `REQ-EVID-013` would then have no way to explain a disagreement
between a predicted figure and a real one. The flag travels in `structured` and
is stated in the text.
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
from scrapr_core.tools.impl.http_api import USER_AGENT, api_item, failure, get_json

__all__ = ["ADZUNA_BASE", "AdzunaTool"]

ADZUNA_BASE: Final = "https://api.adzuna.com/v1/api/jobs"

DEFAULT_COUNTRY: Final = "us"
"""Adzuna partitions by country and has no global endpoint. US by default
because it matches EDGAR's coverage, so the two tools describe the same
population of companies rather than quietly different ones."""


@final
@dataclass(frozen=True, slots=True)
class AdzunaTool:
    """Job postings and hiring information."""

    app_id: str
    app_key: str
    name: str = "adzuna_jobs"
    category: ToolCategory = ToolCategory.JOBS
    country: str = DEFAULT_COUNTRY
    client_factory: Callable[[ToolRequest], httpx.AsyncClient] | None = None

    async def invoke(self, request: ToolRequest) -> ToolOutcome:
        query = str(request.params.get("query", "")).strip()
        if not query:
            return failure("not_found", "no query supplied", self.name, self.category)

        client = self._client(request)
        async with client:
            payload, kind, detail = await get_json(
                client,
                f"{ADZUNA_BASE}/{self.country}/search/1",
                params={
                    "app_id": self.app_id,
                    "app_key": self.app_key,
                    "what": query,
                    "results_per_page": request.budget.max_results,
                    "content-type": "application/json",
                },
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
        return httpx.AsyncClient(
            timeout=request.budget.timeout_seconds, headers={"User-Agent": USER_AGENT}
        )

    def _to_result(self, payload: JsonValue, request: ToolRequest) -> ToolOutcome:
        raw = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(raw, list):
            return failure(
                "error", "adzuna returned no results array", self.name, self.category
            )

        items = []
        for entry in raw[: request.budget.max_results]:
            if not isinstance(entry, dict):
                continue
            url = entry.get("redirect_url")
            title = str(entry.get("title") or "").strip()
            if not url or not title:
                continue

            company = _company(entry)
            predicted = _is_predicted(entry)
            salary_min = entry.get("salary_min")
            salary_max = entry.get("salary_max")

            items.append(
                api_item(
                    tool=self.name,
                    source_name=f"{company}: {title}",
                    text=_describe(
                        company, title, entry, salary_min, salary_max, predicted
                    ),
                    source_category=SourceCategory.JOBS,
                    source_url=str(url),
                    source_identifier=f"adzuna:{entry.get('id')}"
                    if entry.get("id")
                    else None,
                    published_at=_as_date(entry.get("created")),
                    structured={
                        "company": company,
                        "title": title,
                        "salary_min": salary_min,
                        "salary_max": salary_max,
                        # `AC-3`. Without this a predicted figure is
                        # indistinguishable from a published one.
                        "salary_is_predicted": predicted,
                        "location": _location(entry),
                    },
                )
            )

        return ToolResult(
            items=tuple(items),
            retrieved_at=dt.datetime.now(dt.UTC),
            tool=self.name,
            category=self.category,
        )


def _company(entry: JsonMapping) -> str:
    company = entry.get("company")
    if isinstance(company, dict):
        return str(company.get("display_name") or "Unknown employer")
    return "Unknown employer"


def _location(entry: JsonMapping) -> str | None:
    location = entry.get("location")
    if isinstance(location, dict):
        name = location.get("display_name")
        return str(name) if name else None
    return None


def _is_predicted(entry: JsonMapping) -> bool:
    """Adzuna returns this as `"0"` / `"1"` strings, not booleans.

    Defaulting to `True` when the flag is missing is deliberate: an
    unattributed salary treated as published is the failure `AC-3` forbids,
    and treating a published one as predicted merely understates it.
    """
    raw = entry.get("salary_is_predicted")
    if raw is None:
        return True
    return str(raw).strip() not in {"0", "false", "False", ""}


def _describe(
    company: str,
    title: str,
    entry: JsonMapping,
    salary_min: JsonValue,
    salary_max: JsonValue,
    predicted: bool,
) -> str:
    """A quotable sentence that never states a salary without its basis."""
    parts = [f"{company} is advertising a {title} role"]
    location = _location(entry)
    if location:
        parts.append(f"in {location}")

    sentence = " ".join(parts) + "."

    if isinstance(salary_min, (int, float)) and isinstance(salary_max, (int, float)):
        basis = (
            "estimated by the job board, not stated by the employer"
            if predicted
            else "as stated in the posting"
        )
        sentence += f" Advertised salary {salary_min:,.0f} to {salary_max:,.0f}, {basis}."

    return sentence


def _as_date(raw: object) -> dt.datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)
