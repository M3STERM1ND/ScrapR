"""Stage 3 and the budget: what retrieval must guarantee.

The interesting cases are all about not doing work: not repeating a call that
was already made, not spending past a ceiling, and not letting one dead provider
take the area down with it.
"""

from __future__ import annotations

import datetime as dt

import pytest

from scrapr_core.db.enums import SourceCategory
from scrapr_core.orchestrator.budget import AreaReservation, RunBudget
from scrapr_core.orchestrator.retrieve import RetrievalCache, retrieve_area
from scrapr_core.tools import ToolCategory, ToolFailure, ToolRegistry, ToolResult
from scrapr_core.tools.contract import ToolRequest
from scrapr_core.tools.impl import FailingFixtureTool, FixtureTool, fixture_item

QUERY = "Acme FY2025 revenue"


def search_tool(name: str = "fixture_search", count: int = 2) -> FixtureTool:
    return FixtureTool(
        name=name,
        category=ToolCategory.WEB_SEARCH,
        items=tuple(
            fixture_item(
                source_name=f"Result {index}",
                text=f"Acme revenue detail {index}.",
                source_url=f"https://acme.example/{index}",
                source_category=SourceCategory.WEB,
            )
            for index in range(count)
        ),
    )


@pytest.fixture
def registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(search_tool())
    registry.register(
        FixtureTool(
            name="fixture_filings",
            category=ToolCategory.FILINGS,
            items=(
                fixture_item(
                    source_name="Form 10-K",
                    text="Revenue was $1.2bn.",
                    source_identifier="0000320193-25-000106",
                    source_category=SourceCategory.FILING,
                ),
            ),
        )
    )
    registry.freeze()
    return registry


def reservation(calls: int = 10) -> AreaReservation:
    """A reservation with room to spare, for tests that are not about budget."""
    return RunBudget(tool_calls=calls + 50).reserve(calls)


# --------------------------------------------------------------------------
# Retrieval
# --------------------------------------------------------------------------


async def test_every_category_in_the_area_is_attempted(registry: ToolRegistry) -> None:
    round_result = await retrieve_area(
        "Financials",
        [ToolCategory.WEB_SEARCH, ToolCategory.FILINGS],
        QUERY,
        registry,
        reservation(),
        RetrievalCache(),
    )

    assert len(round_result.results) == 2
    assert round_result.item_count == 3
    assert round_result.had_any_success


async def test_a_failing_category_does_not_stop_the_others() -> None:
    """`REQ-TOOL-010`, `REQ-AGENT-009 AC-1`: one dead provider degrades one
    category, not the area and not the run."""
    registry = ToolRegistry()
    registry.register(search_tool())
    registry.register(
        FailingFixtureTool(
            name="broken_filings", category=ToolCategory.FILINGS, kind="timeout"
        )
    )
    registry.freeze()

    round_result = await retrieve_area(
        "Financials",
        [ToolCategory.FILINGS, ToolCategory.WEB_SEARCH],
        QUERY,
        registry,
        reservation(),
        RetrievalCache(),
    )

    assert [failure.kind for failure in round_result.failures] == ["timeout"]
    assert round_result.item_count == 2
    assert round_result.had_any_success


async def test_an_unserved_category_returns_a_failure_not_an_exception(
    registry: ToolRegistry,
) -> None:
    """Most categories have no provider while `OPEN-05..09` are open."""
    round_result = await retrieve_area(
        "News", [ToolCategory.NEWS], QUERY, registry, reservation(), RetrievalCache()
    )

    assert len(round_result.failures) == 1
    assert round_result.failures[0].kind == "not_found"
    assert not round_result.had_any_success


async def test_an_empty_result_is_a_success_with_nothing_in_it() -> None:
    """A search that found nothing is a gap to state, not a failure to report."""
    registry = ToolRegistry()
    registry.register(
        FixtureTool(name="empty", category=ToolCategory.WEB_SEARCH, items=())
    )
    registry.freeze()

    round_result = await retrieve_area(
        "Anything",
        [ToolCategory.WEB_SEARCH],
        QUERY,
        registry,
        reservation(),
        RetrievalCache(),
    )

    assert round_result.failures == ()
    assert round_result.item_count == 0
    assert not round_result.had_any_success


# --------------------------------------------------------------------------
# The cache
# --------------------------------------------------------------------------


async def test_an_identical_request_is_not_made_twice(registry: ToolRegistry) -> None:
    """`REQ-TOOL-013 AC-1`. Two areas asking about one subject is the common
    case, and the second ask should cost nothing."""
    cache = RetrievalCache()
    reserved = reservation()

    first = await retrieve_area(
        "A", [ToolCategory.WEB_SEARCH], QUERY, registry, reserved, cache
    )
    second = await retrieve_area(
        "B", [ToolCategory.WEB_SEARCH], QUERY, registry, reserved, cache
    )

    assert cache.hits == 1
    assert reserved.spent == 1
    assert first.item_count == second.item_count


async def test_an_expired_entry_is_retrieved_again(registry: ToolRegistry) -> None:
    """`REQ-TOOL-013 AC-4`, `DEC-19`: the in-run lifetime is configurable."""
    now = [0.0]
    cache = RetrievalCache(ttl_seconds=60, clock=lambda: now[0])
    reserved = reservation()

    await retrieve_area("A", [ToolCategory.WEB_SEARCH], QUERY, registry, reserved, cache)
    now[0] = 30.0
    await retrieve_area("B", [ToolCategory.WEB_SEARCH], QUERY, registry, reserved, cache)
    now[0] = 120.0
    await retrieve_area("C", [ToolCategory.WEB_SEARCH], QUERY, registry, reserved, cache)

    assert cache.hits == 1
    assert reserved.spent == 2


def test_every_run_starts_with_an_empty_cache() -> None:
    """`REQ-VER-003 AC-1`: freshness on update is structural, not a flag.

    The cache is an object a run creates, so there is no shared instance for an
    update to forget to bypass.
    """
    assert RetrievalCache().get(
        ToolRequest(category=ToolCategory.NEWS, params={"query": QUERY})
    ) is None


async def test_a_cached_result_keeps_its_original_retrieval_time(
    registry: ToolRegistry,
) -> None:
    """`REQ-TOOL-013 AC-2`. When a page was read is a fact about the page, not
    about the cache, and staleness is judged from it."""
    cache = RetrievalCache()
    reserved = reservation()

    first = await retrieve_area(
        "A", [ToolCategory.WEB_SEARCH], QUERY, registry, reserved, cache
    )
    second = await retrieve_area(
        "B", [ToolCategory.WEB_SEARCH], QUERY, registry, reserved, cache
    )

    assert first.results[0].retrieved_at == second.results[0].retrieved_at


async def test_a_different_query_is_a_different_request(
    registry: ToolRegistry,
) -> None:
    cache = RetrievalCache()
    reserved = reservation()

    await retrieve_area("A", [ToolCategory.WEB_SEARCH], "one", registry, reserved, cache)
    await retrieve_area("B", [ToolCategory.WEB_SEARCH], "two", registry, reserved, cache)

    assert cache.hits == 0
    assert reserved.spent == 2


async def test_failures_are_not_cached() -> None:
    """A timeout is usually a bad moment, and caching it would turn that into a
    run-long outage for the query."""
    registry = ToolRegistry()
    registry.register(
        FailingFixtureTool(name="flaky", category=ToolCategory.WEB_SEARCH)
    )
    registry.freeze()
    cache = RetrievalCache()

    request = ToolRequest(category=ToolCategory.WEB_SEARCH, params={"query": QUERY})
    outcome = await registry.invoke(request)
    cache.put(request, outcome)

    assert isinstance(outcome, ToolFailure)
    assert cache.get(request) is None


# --------------------------------------------------------------------------
# The budget
# --------------------------------------------------------------------------


async def test_an_area_stops_at_its_reservation(registry: ToolRegistry) -> None:
    """A single area cannot starve the rest of the plan (`DEC-04 §5`)."""
    reserved = RunBudget().reserve(1)

    round_result = await retrieve_area(
        "Financials",
        [ToolCategory.WEB_SEARCH, ToolCategory.FILINGS],
        QUERY,
        registry,
        reserved,
        RetrievalCache(),
    )

    assert len(round_result.results) == 1
    assert round_result.skipped == (ToolCategory.FILINGS,)


async def test_a_skipped_category_is_recorded_not_dropped(
    registry: ToolRegistry,
) -> None:
    """An area cut short is a gap the report owes the reader
    (`REQ-AGENT-009 AC-2`)."""
    reserved = RunBudget().reserve(0)

    round_result = await retrieve_area(
        "Financials",
        [ToolCategory.WEB_SEARCH],
        QUERY,
        registry,
        reserved,
        RetrievalCache(),
    )

    assert round_result.skipped == (ToolCategory.WEB_SEARCH,)
    assert round_result.results == ()


def test_the_run_stops_on_whichever_counter_binds_first() -> None:
    """`DEC-04 §5`: three counters, any one of which ends the run."""
    assert RunBudget(cost_micros=0).exhausted() == "cost"
    assert RunBudget(tool_calls=0).exhausted() == "tool_calls"
    assert (
        RunBudget(
            wall_clock_seconds=1,
            started_at=dt.datetime.now(dt.UTC) - dt.timedelta(seconds=5),
        ).exhausted()
        == "wall_clock"
    )
    assert RunBudget().exhausted() is None


def test_unspent_reservation_returns_to_the_pool() -> None:
    """Cheap areas fund expensive ones (`DEC-04 §5`)."""
    budget = RunBudget(tool_calls=10)
    reserved = budget.reserve(6)
    reserved.spend(tool_calls=2)

    assert budget.tool_calls_remaining == 4
    reserved.release()
    assert budget.tool_calls_remaining == 8


def test_releasing_twice_does_not_refund_twice() -> None:
    budget = RunBudget(tool_calls=10)
    reserved = budget.reserve(6)

    reserved.release()
    reserved.release()

    assert budget.tool_calls_remaining == 10


def test_a_reservation_cannot_exceed_the_pool() -> None:
    budget = RunBudget(tool_calls=3)

    reserved = budget.reserve(10)

    assert reserved.granted == 3
    assert budget.tool_calls_remaining == 0


def test_an_area_cannot_spend_once_the_run_is_out_of_time() -> None:
    """The reservation is a cap, not a licence: the run's ceilings still bind."""
    budget = RunBudget(
        wall_clock_seconds=1,
        started_at=dt.datetime.now(dt.UTC) - dt.timedelta(seconds=5),
    )
    reserved = budget.reserve(5)

    assert reserved.remaining == 5
    assert not reserved.can_spend()


def test_spending_past_zero_is_recorded_honestly() -> None:
    """A call that overshoots still happened and still cost what it cost."""
    budget = RunBudget(cost_micros=100)

    budget.spend(cost_micros=250)

    assert budget.cost_micros_remaining == -150
    assert budget.exhausted() == "cost"


async def test_results_carry_provenance_through_retrieval(
    registry: ToolRegistry,
) -> None:
    """`REQ-TOOL-012 AC-1`: identifier or URL, name, category, timestamp."""
    round_result = await retrieve_area(
        "Financials",
        [ToolCategory.FILINGS],
        QUERY,
        registry,
        reservation(),
        RetrievalCache(),
    )

    item = round_result.results[0].items[0]
    assert item.source_identifier == "0000320193-25-000106"
    assert item.source_name == "Form 10-K"
    assert item.source_category is SourceCategory.FILING
    assert item.retrieved_at.tzinfo is not None
    assert isinstance(round_result.results[0], ToolResult)
