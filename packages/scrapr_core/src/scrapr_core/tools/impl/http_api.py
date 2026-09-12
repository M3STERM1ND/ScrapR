"""What the four JSON-API tools share (`DEC-07`).

Tavily, FMP, EDGAR and Adzuna differ in what they return and agree on
everything else: an HTTP GET, a status code that has to become a `FailureKind`,
a key that may be absent, and a budget that has to be honoured. Writing that
four times would guarantee four slightly different failure classifications, and
the classification is what `REQ-TOOL-011 AC-2` uses to decide whether evidence
may be cited at all.

**Structured provider output is not more trustworthy than HTML** (`DEC-07 §4`).
A company description field in a financial API response is attacker-influenced
in exactly the way a web page is. Everything that comes back through here is
`Untrusted`, and the helper below is the only way these tools construct an
item, so there is no path that forgets.

This is not the page-fetch path and does not need the SSRF guard: these tools
call fixed, configured hosts rather than a URL chosen by retrieved content.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from typing import Final

import httpx

from scrapr_core.db.enums import Accessibility, SourceCategory
from scrapr_core.domain.json import JsonMapping, JsonValue
from scrapr_core.security.trust import SourceRef, Untrusted
from scrapr_core.tools.contract import (
    FailureKind,
    ToolCategory,
    ToolFailure,
    ToolItem,
)

__all__ = [
    "USER_AGENT",
    "api_item",
    "classify_status",
    "failure",
    "get_json",
]

USER_AGENT: Final = "ScrapR/0.1 (research agent)"

# Shared with page fetch, and for the same reason: the classification drives
# retry policy and citability, so it is a closed mapping rather than a guess
# per provider.
_STATUS_FAILURES: Final[dict[int, FailureKind]] = {
    401: "blocked",
    402: "paywalled",
    403: "blocked",
    404: "not_found",
    410: "not_found",
    429: "rate_limited",
    451: "blocked",
}


def classify_status(status: int) -> FailureKind | None:
    """Map a status code onto a failure kind, or `None` if it is not a failure.

    `401` and `403` are `blocked` rather than `error` on purpose: for a keyed
    API they usually mean the key is wrong or out of quota, and that is an
    operational condition somebody has to fix, not a transient blip worth
    retrying into.
    """
    if status < 400:
        return None
    return _STATUS_FAILURES.get(status, "error")


def failure(
    kind: FailureKind, message: str, tool: str, category: ToolCategory
) -> ToolFailure:
    """A typed failure. `message` is internal (`REQ-SEC-010`)."""
    return ToolFailure(kind=kind, message=message, tool=tool, category=category)


async def get_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: Mapping[str, str | int | float] | None = None,
    headers: Mapping[str, str] | None = None,
) -> tuple[JsonValue, FailureKind | None, str]:
    """GET and decode JSON.

    Returns `(payload, failure_kind, detail)`. Exactly one of the first two is
    meaningful. Failures return rather than raise (`REQ-TOOL-010 AC-1`) all the
    way down, so a caller cannot forget to handle one.
    """
    try:
        response = await client.get(url, params=dict(params or {}), headers=dict(headers or {}))
    except httpx.TimeoutException:
        return None, "timeout", f"{url} did not respond in time"
    except httpx.HTTPError as exc:
        return None, "error", f"{url} failed: {type(exc).__name__}"

    kind = classify_status(response.status_code)
    if kind is not None:
        return None, kind, f"{url} returned {response.status_code}"

    try:
        return response.json(), None, ""
    except ValueError:
        # A 200 that is not JSON is a provider contract break, and treating it
        # as an empty result would report "nothing found" for a broken API.
        return None, "error", f"{url} returned a non-JSON body"


def api_item(
    *,
    tool: str,
    source_name: str,
    text: str,
    source_category: SourceCategory,
    source_url: str | None = None,
    source_identifier: str | None = None,
    published_at: dt.datetime | None = None,
    structured: JsonMapping | None = None,
    accessibility: Accessibility = Accessibility.ACCESSIBLE,
) -> ToolItem:
    """Build one item from a provider payload, wrapping its text as `Untrusted`.

    The single construction path for all four API tools, so that `DEC-07 §4`
    holds by construction: there is no way to build an item here whose content
    is a plain string.

    `structured` carries the provider's own typed data — a financial series, a
    filing header — beside the prose, so numeric extraction does not have to
    re-parse what it was handed in a usable form.
    """
    locator = source_url or source_identifier or source_name
    return ToolItem(
        source_name=source_name,
        source_category=source_category,
        retrieved_at=dt.datetime.now(dt.UTC),
        accessibility=accessibility,
        content=Untrusted(text, SourceRef(kind=tool, locator=locator)),
        source_url=source_url,
        source_identifier=source_identifier,
        published_at=published_at,
        structured=dict(structured) if structured is not None else None,
    )
