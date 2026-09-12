"""Task 1.9 — the web page retrieval tool (`REQ-TOOL-003`).

The one tool in the six that needed no provider decision. `OPEN-05..09` each
name a vendor nobody has chosen; fetching a URL names nobody, so this one could
be built while the rest wait.

What the requirement asks, and where each part lives:

* `AC-1` — retrieved content is `Untrusted` (`REQ-SEC-013`). Structural: a
  `ToolItem` has nowhere to put a trusted string.
* `AC-2` — failure, paywall and block are **distinguishable** (`REQ-TOOL-011`).
  Mapped from the status code, never guessed from the body.
* `AC-3` — the retrieval timestamp is recorded. `ToolItem` requires one.

**The status code is the classifier, and nothing else is.** A paywalled page
that answers `200` with three paragraphs and a subscribe wall is not detectable
without a heuristic, and a heuristic that is wrong marks a readable page
unreadable — which `REQ-TOOL-011 AC-2` then uses to strip its evidence out of
the report. `402` is a paywall because the code says so. The rest is Phase 2's
problem, where tiering and normalisation give it somewhere to live.

**Redirects are followed here rather than by the client**, so that every hop is
re-validated against the SSRF guard (`REQ-SEC-015 AC-2`). See `net.py` for why
a hostname check would not be one.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Final, final

import httpx

from scrapr_core.db.enums import Accessibility, SourceCategory
from scrapr_core.security.trust import SourceRef, Untrusted
from scrapr_core.tools.contract import (
    FailureKind,
    ToolCategory,
    ToolFailure,
    ToolItem,
    ToolOutcome,
    ToolRequest,
    ToolResult,
)
from scrapr_core.tools.impl.net import (
    MAX_REDIRECTS,
    UrlRejected,
    resolve_and_validate,
)

__all__ = ["MAX_CONTENT_BYTES", "PageFetchTool", "extract_text"]

MAX_CONTENT_BYTES = 2_000_000
"""Two megabytes of markup. A cap on what one page may cost, because a tool
budget counts calls and a response body is the one dimension a single call can
spend without limit."""

USER_AGENT: Final = "ScrapR/0.1 (research agent)"
"""Honest about what it is. A crawler that lies about its identity is asking to
be blocked, and being blocked is a worse outcome than being refused politely."""

# `REQ-TOOL-003 AC-2`, from the code alone. A body-sniffing heuristic would
# make a readable page unreadable on a false positive, and `REQ-TOOL-011 AC-2`
# would then delete its evidence from the report.
_STATUS_FAILURES: Final[dict[int, tuple[FailureKind, Accessibility]]] = {
    401: ("blocked", Accessibility.BLOCKED),
    402: ("paywalled", Accessibility.PAYWALLED),
    403: ("blocked", Accessibility.BLOCKED),
    404: ("not_found", Accessibility.FAILED),
    410: ("not_found", Accessibility.FAILED),
    429: ("rate_limited", Accessibility.FAILED),
    451: ("blocked", Accessibility.BLOCKED),
}

_REJECTION_FAILURES: Final[dict[str, FailureKind]] = {
    "scheme": "blocked",
    "credentials": "blocked",
    "no_host": "not_found",
    "unresolvable": "not_found",
    "private_address": "blocked",
}

_SKIP_TAGS: Final = frozenset({"script", "style", "noscript", "template"})
_BREAK_TAGS: Final = frozenset(
    {"p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section"}
)


class _TextExtractor(HTMLParser):
    """Markup to readable text, on the standard library alone.

    Deliberately not a readability algorithm. Extraction into discrete evidence
    is stage 4's job and it works from the text plus the question; what this
    owes is text that still contains the sentences the page showed, with the
    script and style bodies that would otherwise be quoted as evidence removed.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skipping = 0

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag in _SKIP_TAGS:
            self._skipping += 1
        elif tag in _BREAK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skipping:
            self._skipping -= 1
        elif tag in _BREAK_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skipping:
            self._chunks.append(data)

    @property
    def text(self) -> str:
        joined = "".join(self._chunks)
        lines = [line.strip() for line in joined.splitlines()]
        return "\n".join(line for line in lines if line)


def extract_text(markup: str) -> str:
    """Readable text from an HTML document.

    Malformed markup yields whatever was parsed before the break rather than
    raising: a page that is half valid is half evidence, and the alternative is
    discarding a real source over a stray tag.
    """
    parser = _TextExtractor()
    try:
        parser.feed(markup)
        parser.close()
    except AssertionError:  # pragma: no cover - html.parser's own guard
        pass
    return parser.text


def _title(markup: str) -> str:
    """The page's own name for itself, for the source record."""
    lowered = markup.casefold()
    start = lowered.find("<title")
    if start == -1:
        return ""
    opened = lowered.find(">", start)
    end = lowered.find("</title", opened)
    if opened == -1 or end == -1:
        return ""
    return " ".join(markup[opened + 1 : end].split())[:200]


@final
@dataclass(frozen=True, slots=True)
class PageFetchTool:
    """Retrieve one page, by URL, within the request's budget.

    The URL arrives in `params["url"]`. Unlike a search tool, this one is not
    given a query to interpret: the caller already knows the page it wants, and
    the orchestrator reaches it the same way it reaches any category.
    """

    name: str = "page_fetch"
    category: ToolCategory = ToolCategory.PAGE_FETCH
    client_factory: Callable[[ToolRequest], httpx.AsyncClient] | None = field(
        default=None, repr=False
    )
    """Injection point for tests. `None` builds a real `httpx.AsyncClient`;
    a factory lets the SSRF and classification suites run without a network."""

    async def invoke(self, request: ToolRequest) -> ToolOutcome:
        """Fetch, classify, and wrap. Never raises for anything a page can do."""
        raw_url = str(request.params.get("url", "")).strip()
        if not raw_url:
            return self._failure("not_found", "no url was supplied to page fetch")

        try:
            return await self._fetch(raw_url, request)
        except httpx.TimeoutException:
            return self._failure("timeout", f"{raw_url} did not respond in time")
        except httpx.HTTPError as exc:
            # `REQ-TOOL-010 AC-1`: a transport failure is a return value. The
            # registry would convert a raise anyway; classifying it here is
            # what makes the difference between `timeout` and `error` real.
            return self._failure("error", f"{raw_url} failed: {type(exc).__name__}")

    # ------------------------------------------------------------------

    async def _fetch(self, raw_url: str, request: ToolRequest) -> ToolOutcome:
        client = self._client(request)
        async with client:
            url = raw_url
            for _ in range(MAX_REDIRECTS + 1):
                verdict = await resolve_and_validate(url)
                if isinstance(verdict, UrlRejected):
                    return self._failure(
                        _REJECTION_FAILURES[verdict.reason], verdict.detail
                    )

                response = await client.get(url, follow_redirects=False)

                if response.is_redirect:
                    location = response.headers.get("location", "")
                    if not location:
                        return self._failure(
                            "error", f"{url} redirected without a location"
                        )
                    # Resolved against the current URL, so a relative Location
                    # cannot be read as a different host, and re-validated on
                    # the next turn of the loop (`REQ-SEC-015 AC-2`).
                    url = str(response.url.join(location))
                    continue

                return self._classify(response, url)

            return self._failure("error", f"{raw_url} exceeded {MAX_REDIRECTS} redirects")

    def _client(self, request: ToolRequest) -> httpx.AsyncClient:
        if self.client_factory is not None:
            return self.client_factory(request)
        return httpx.AsyncClient(
            timeout=request.budget.timeout_seconds,
            headers={"User-Agent": USER_AGENT},
            # Off, because this tool follows hops itself so it can re-validate
            # each one against the SSRF guard.
            follow_redirects=False,
        )

    def _classify(self, response: httpx.Response, url: str) -> ToolOutcome:
        """Turn a response into an item or a typed failure (`AC-2`)."""
        known = _STATUS_FAILURES.get(response.status_code)
        if known is not None:
            kind, _accessibility = known
            return self._failure(kind, f"{url} returned {response.status_code}")

        if response.status_code >= 400:
            return self._failure("error", f"{url} returned {response.status_code}")

        body = response.content[:MAX_CONTENT_BYTES]
        markup = body.decode(response.encoding or "utf-8", errors="replace")
        text = extract_text(markup)

        if not text.strip():
            # A page that parsed to nothing was reached but cannot be read, and
            # `REQ-TOOL-011` would rather say so than file an empty source.
            return self._failure("error", f"{url} contained no readable text")

        item = ToolItem(
            source_name=_title(markup) or url,
            source_category=SourceCategory.WEB,
            retrieved_at=dt.datetime.now(dt.UTC),  # `AC-3`
            accessibility=Accessibility.ACCESSIBLE,
            # `AC-1`: the page becomes `Untrusted` at the boundary and there is
            # no later point at which it could have failed to.
            content=Untrusted(text, SourceRef(kind="page_fetch", locator=url)),
            source_url=url,
        )
        return ToolResult(
            items=(item,),
            retrieved_at=item.retrieved_at,
            tool=self.name,
            category=self.category,
        )

    def _failure(self, kind: FailureKind, message: str) -> ToolFailure:
        return ToolFailure(
            kind=kind, message=message, tool=self.name, category=self.category
        )
