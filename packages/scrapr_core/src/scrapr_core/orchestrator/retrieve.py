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

from collections.abc import Sequence
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

__all__ = ["RetrievalCache", "RoundResult", "retrieve_area"]


@final
class RetrievalCache:
    """Within one run, one identical request is made once (`REQ-TOOL-013`).

    Keyed by category, pinned tool and parameters. Scoped to a run object rather
    than a process: `REQ-VER-003` requires Update Research to re-fetch for
    freshness, and the way to guarantee that is for the new run to start with an
    empty cache rather than to remember to bypass a shared one.
    """

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str, str], ToolOutcome] = {}
        self.hits = 0

    @staticmethod
    def _key(request: ToolRequest) -> tuple[str, str, str]:
        params = ";".join(
            f"{name}={request.params[name]!r}" for name in sorted(request.params)
        )
        return (request.category.value, request.tool, params)

    def get(self, request: ToolRequest) -> ToolOutcome | None:
        outcome = self._entries.get(self._key(request))
        if outcome is not None:
            self.hits += 1
        return outcome

    def put(self, request: ToolRequest, outcome: ToolOutcome) -> None:
        """Cache a *success* only.

        A failure is usually transient — a timeout, a rate limit — and caching
        it would turn one bad moment into a run-long outage for that query.
        """
        if isinstance(outcome, ToolResult):
            self._entries[self._key(request)] = outcome


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


async def retrieve_area(
    area_name: str,
    categories: Sequence[ToolCategory],
    query: str,
    registry: ToolRegistry,
    reservation: AreaReservation,
    cache: RetrievalCache,
    budget: ToolBudget | None = None,
    session_id: UUID | None = None,
) -> RoundResult:
    """Run one retrieval round across an area's categories.

    Every category is attempted once, in plan order, while budget remains. The
    budget is checked *before* each call, so an area stops before exceeding its
    reservation rather than after.
    """
    results: list[ToolResult] = []
    failures: list[ToolFailure] = []
    skipped: list[ToolCategory] = []

    for category in categories:
        params: dict[str, JsonValue] = {"query": query}
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
