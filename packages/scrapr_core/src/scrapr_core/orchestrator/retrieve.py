"""Stage 3 — Retrieve for one area (`REQ-TOOL-001..013`).

In: an area and its budget. Out: what each of its tool categories returned,
successes and typed failures alike.

**Failures are outcomes, not exceptions** (`REQ-TOOL-010 AC-1`). One dead
provider degrades one category of one area; everything else in the run
continues, which is what `REQ-AGENT-009 AC-1` asks for. The registry already
converts a raising tool into a `ToolFailure`, so this stage never has to guess
what an exception meant.

**Repeated identical retrieval inside a run does not repeat the call**
(`REQ-TOOL-013 AC-1`). Two areas asking web search the same thing is common —
they share questions about one subject — and the second ask should cost nothing.
The cached result keeps its **original** `retrieved_at` (`AC-2`), because the
time a page was read is a fact about the page, not about the cache.

**A targeted category is given its target.** `ToolCategory.DOCUMENTS` searches
one research session's uploads, and the registry is frozen per process rather
than built per run — so the session id travels in the request instead of being
baked into the tool. It is added only for that category: putting it in every
request would change the cache key of every web search for no reason, and hand
a session id to providers that have no business holding one.

**Nothing is stored here.** This stage returns what tools said; stage 4 decides
what becomes a source and a piece of evidence. Keeping the split means a
provenance-less result is rejected at extraction (`REQ-TOOL-012 AC-2`) rather
than half-written first.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import final
from uuid import UUID

from scrapr_core.domain.json import JsonValue
from scrapr_core.orchestrator.budget import AreaReservation
from scrapr_core.tools.contract import (
    ToolBudget,
    ToolCategory,
    ToolFailure,
    ToolOutcome,
    ToolRequest,
    ToolResult,
)
from scrapr_core.tools.registry import ToolRegistry

__all__ = ["PERMANENT_WITHIN_RUN", "RetrievalCache", "RoundResult", "retrieve_area"]

PERMANENT_WITHIN_RUN: frozenset[str] = frozenset({"blocked", "paywalled", "not_found"})
"""Failure kinds that asking again minutes later will not change."""


@final
class RetrievalCache:
    """Within one run, one identical request is made once (`REQ-TOOL-013`).

    Keyed by category, pinned tool and parameters. Scoped to a run object rather
    than a process: `REQ-VER-003` requires Update Research to re-fetch for
    freshness, and the way to guarantee that is for the new run to start with an
    empty cache rather than to remember to bypass a shared one.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """`ttl_seconds` bounds reuse within the run (`REQ-TOOL-013 AC-4`).

        `None` keeps an entry for the life of the run. The lifetime never
        exceeds one run whatever it is set to (`DEC-19`), because the cache is
        an object a run creates rather than something runs share.
        """
        self._entries: dict[tuple[str, str, str], tuple[float, ToolOutcome]] = {}
        self._ttl = ttl_seconds
        self._clock = clock
        self.hits = 0

    @staticmethod
    def _key(request: ToolRequest) -> tuple[str, str, str]:
        params = ";".join(
            f"{name}={request.params[name]!r}" for name in sorted(request.params)
        )
        return (request.category.value, request.tool, params)

    def get(self, request: ToolRequest) -> ToolOutcome | None:
        key = self._key(request)
        entry = self._entries.get(key)
        if entry is None:
            return None
        stored_at, outcome = entry
        if self._ttl is not None and self._clock() - stored_at > self._ttl:
            # Expired: dropped, so the next ask retrieves afresh.
            del self._entries[key]
            return None
        self.hits += 1
        return outcome

    def put(self, request: ToolRequest, outcome: ToolOutcome) -> None:
        """Cache a success, or a failure that cannot change within the run.

        A timeout, a rate limit or a server error is usually transient, and
        caching it would turn one bad moment into a run-long outage for that
        query. A rejected key, a paywall or a missing listing is not: the first
        real run re-sent the same three 403s to FMP in its second round, which
        spent calls and learned nothing.
        """
        if isinstance(outcome, ToolResult) or outcome.kind in PERMANENT_WITHIN_RUN:
            self._entries[self._key(request)] = (self._clock(), outcome)


@final
@dataclass(frozen=True, slots=True)
class RoundResult:
    """What one retrieval round produced for one area."""

    area_name: str
    results: Sequence[ToolResult] = field(default_factory=tuple)
    failures: Sequence[ToolFailure] = field(default_factory=tuple)
    skipped: Sequence[ToolCategory] = field(default_factory=tuple)
    """Categories not attempted because the budget ran out mid-round. Recorded
    rather than dropped: an area that was cut short is a gap the report owes the
    reader (`REQ-AGENT-009 AC-2`)."""

    @property
    def item_count(self) -> int:
        return sum(len(result.items) for result in self.results)

    @property
    def had_any_success(self) -> bool:
        return any(not result.is_empty for result in self.results)

    def merged(self, other: RoundResult) -> RoundResult:
        """This round plus another for the same area — a fallback, say."""
        return RoundResult(
            area_name=self.area_name,
            results=(*self.results, *other.results),
            failures=(*self.failures, *other.failures),
            skipped=(*self.skipped, *other.skipped),
        )


async def retrieve_area(
    area_name: str,
    categories: Sequence[ToolCategory],
    query: str,
    registry: ToolRegistry,
    reservation: AreaReservation,
    cache: RetrievalCache,
    budget: ToolBudget | None = None,
    session_id: UUID | None = None,
    params_for: Callable[[ToolCategory], Mapping[str, JsonValue]] | None = None,
) -> RoundResult:
    """Run one retrieval round across an area's categories.

    Every category is attempted once, in plan order, while budget remains. The
    budget is checked *before* each call, so an area stops before exceeding its
    reservation rather than after.

    `params_for` shapes the request per category (`orchestrator.queries`): a
    financial API wants a company and a ticker, not the research question.
    Without it every category is sent `query`.
    """
    results: list[ToolResult] = []
    failures: list[ToolFailure] = []
    skipped: list[ToolCategory] = []

    for category in categories:
        params: dict[str, JsonValue] = (
            dict(params_for(category)) if params_for is not None else {"query": query}
        )
        if category is ToolCategory.DOCUMENTS and session_id is not None:
            params["session_id"] = str(session_id)

        request = ToolRequest(
            category=category,
            params=params,
            budget=budget or ToolBudget(),
        )

        cached = cache.get(request)
        if cached is not None:
            # A cache hit costs no call and no budget, and keeps the original
            # retrieval time (`REQ-TOOL-013 AC-2`).
            _record(cached, results, failures)
            continue

        if not reservation.can_spend():
            skipped.append(category)
            continue

        reservation.spend(tool_calls=1)
        outcome = await registry.invoke(request)
        cache.put(request, outcome)
        _record(outcome, results, failures)

    return RoundResult(
        area_name=area_name,
        results=tuple(results),
        failures=tuple(failures),
        skipped=tuple(skipped),
    )


def _record(
    outcome: ToolOutcome,
    results: list[ToolResult],
    failures: list[ToolFailure],
) -> None:
    if isinstance(outcome, ToolResult):
        results.append(outcome)
    else:
        failures.append(outcome)
