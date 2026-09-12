"""Dispatch by category, and the guarantees that go with it.

Two requirements are on trial here:

* `REQ-TOOL-009` — a tool can be added without the caller naming it, and a
  second tool in an existing category needs no orchestrator change.
* `REQ-SEC-015 AC-1` — tool availability is fixed at run start, so retrieved
  content cannot add, name or reach a tool.
"""

from __future__ import annotations

import asyncio
import datetime as dt

import pytest

from scrapr_core.db.enums import SourceCategory
from scrapr_core.tools import (
    RegistryFrozenError,
    ToolBudget,
    ToolCategory,
    ToolFailure,
    ToolRegistry,
    ToolRequest,
    ToolResult,
    UnknownToolError,
)
from scrapr_core.tools.impl import FailingFixtureTool, FixtureTool, fixture_item


def search_tool(name: str = "fixture_search", count: int = 3) -> FixtureTool:
    return FixtureTool(
        name=name,
        category=ToolCategory.WEB_SEARCH,
        items=tuple(
            fixture_item(
                source_name=f"Result {index}",
                text=f"Acme grew {index}% last quarter.",
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
    return registry


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------


def test_a_tool_is_found_by_its_category(registry: ToolRegistry) -> None:
    assert [tool.name for tool in registry.for_category(ToolCategory.WEB_SEARCH)] == [
        "fixture_search"
    ]


def test_categories_lists_only_what_is_registered(registry: ToolRegistry) -> None:
    """Planning consults this: an area is only planned against retrieval that
    actually exists."""
    assert registry.categories() == (ToolCategory.WEB_SEARCH,)


def test_a_second_tool_in_a_category_needs_no_caller_change(
    registry: ToolRegistry,
) -> None:
    """`REQ-TOOL-009 AC-3`, the extensibility proof: the request below is
    identical before and after the second registration."""
    registry.register(search_tool(name="fixture_search_alt"))

    assert len(registry.for_category(ToolCategory.WEB_SEARCH)) == 2


def test_a_duplicate_name_is_refused(registry: ToolRegistry) -> None:
    with pytest.raises(ValueError, match="already registered"):
        registry.register(search_tool())


def test_registration_after_freeze_is_refused(registry: ToolRegistry) -> None:
    """`REQ-SEC-015 AC-1`. Refused loudly: a silently dropped registration is a
    tool that mysteriously never runs."""
    registry.freeze()

    assert registry.is_frozen
    with pytest.raises(RegistryFrozenError, match="fixed at run start"):
        registry.register(search_tool(name="late_arrival"))


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------


async def test_a_category_request_reaches_a_tool(registry: ToolRegistry) -> None:
    outcome = await registry.invoke(ToolRequest(category=ToolCategory.WEB_SEARCH))

    assert isinstance(outcome, ToolResult)
    assert outcome.tool == "fixture_search"
    assert len(outcome.items) == 3


async def test_the_result_budget_is_honoured(registry: ToolRegistry) -> None:
    """Even by a fake: a pipeline that only ever sees fixtures small enough to
    fit never exercises its truncation path."""
    request = ToolRequest(
        category=ToolCategory.WEB_SEARCH, budget=ToolBudget(max_results=2)
    )

    outcome = await registry.invoke(request)

    assert isinstance(outcome, ToolResult)
    assert len(outcome.items) == 2


async def test_an_unserved_category_returns_a_failure(registry: ToolRegistry) -> None:
    """No provider configured yet is the normal state while `OPEN-05..09` are
    open, so it is an outcome rather than an exception."""
    outcome = await registry.invoke(ToolRequest(category=ToolCategory.FILINGS))

    assert isinstance(outcome, ToolFailure)
    assert outcome.kind == "not_found"


async def test_a_pinned_tool_is_used(registry: ToolRegistry) -> None:
    registry.register(search_tool(name="fixture_search_alt", count=1))

    outcome = await registry.invoke(
        ToolRequest(category=ToolCategory.WEB_SEARCH, tool="fixture_search_alt")
    )

    assert isinstance(outcome, ToolResult)
    assert outcome.tool == "fixture_search_alt"


async def test_pinning_an_unregistered_tool_is_a_wiring_bug(
    registry: ToolRegistry,
) -> None:
    """Raises rather than returning a failure: this cannot happen to a correct
    caller, so swallowing it would only hide a typo."""
    with pytest.raises(UnknownToolError, match="no tool named"):
        await registry.invoke(
            ToolRequest(category=ToolCategory.WEB_SEARCH, tool="nope")
        )


async def test_pinning_a_tool_from_another_category_is_refused(
    registry: ToolRegistry,
) -> None:
    registry.register(
        FixtureTool(name="fixture_news", category=ToolCategory.NEWS, items=())
    )

    with pytest.raises(UnknownToolError, match="serves"):
        await registry.invoke(
            ToolRequest(category=ToolCategory.WEB_SEARCH, tool="fixture_news")
        )


async def test_a_failing_tool_returns_rather_than_raises() -> None:
    """`REQ-TOOL-010 AC-1`: one dead provider degrades one area instead of
    aborting the run."""
    registry = ToolRegistry()
    registry.register(
        FailingFixtureTool(
            name="fixture_paywalled",
            category=ToolCategory.NEWS,
            kind="paywalled",
            message="subscription required",
        )
    )

    outcome = await registry.invoke(ToolRequest(category=ToolCategory.NEWS))

    assert isinstance(outcome, ToolFailure)
    assert outcome.kind == "paywalled"
    assert not outcome.retryable


async def test_a_raising_tool_cannot_abort_the_run() -> None:
    """A third-party client that violates the contract must not take the run
    with it (`REQ-AGENT-009 AC-1`)."""

    class Exploding:
        name = "exploding"
        category = ToolCategory.WEB_SEARCH

        async def invoke(self, request: ToolRequest) -> ToolResult:
            raise RuntimeError("provider client blew up")

    registry = ToolRegistry()
    registry.register(Exploding())

    outcome = await registry.invoke(ToolRequest(category=ToolCategory.WEB_SEARCH))

    assert isinstance(outcome, ToolFailure)
    assert outcome.kind == "error"
    assert "RuntimeError" in outcome.message


async def test_a_slow_tool_is_cut_off_at_its_budget() -> None:
    """`REQ-TOOL-013`: a tool that never returns must not hold a step open until
    its lease expires."""

    class Slow:
        name = "slow"
        category = ToolCategory.WEB_SEARCH

        async def invoke(self, request: ToolRequest) -> ToolResult:
            await asyncio.sleep(5)
            return ToolResult(
                items=(),
                retrieved_at=dt.datetime.now(dt.UTC),
                tool=self.name,
                category=self.category,
            )

    registry = ToolRegistry()
    registry.register(Slow())

    outcome = await registry.invoke(
        ToolRequest(
            category=ToolCategory.WEB_SEARCH, budget=ToolBudget(timeout_seconds=0.01)
        )
    )

    assert isinstance(outcome, ToolFailure)
    assert outcome.kind == "timeout"


# --------------------------------------------------------------------------
# What may be planned against
# --------------------------------------------------------------------------


def test_page_fetch_is_registered_but_never_planned_against() -> None:
    """A bug that shipped, kept as a test.

    Registering page fetch in the worker made it selectable by the planner. It
    answers a URL, not a question, so every retrieval returned `not_found` and
    the run reported an area that "could not be researched" — when the truth
    was that the area had been asked the wrong kind of question. The tool was
    behaving exactly as specified the whole time.
    """
    registry = ToolRegistry()
    registry.register(FixtureTool(name="pf", category=ToolCategory.PAGE_FETCH, items=()))
    registry.register(FixtureTool(name="ws", category=ToolCategory.WEB_SEARCH, items=()))
    registry.freeze()

    assert ToolCategory.PAGE_FETCH in registry.categories()
    assert ToolCategory.PAGE_FETCH not in registry.plannable_categories()
    assert ToolCategory.WEB_SEARCH in registry.plannable_categories()
    # Still reachable by a caller that has a URL, which is how `REQ-TOOL-003`
    # is meant to be invoked.
    assert registry.for_category(ToolCategory.PAGE_FETCH)


def test_documents_are_targeted_too() -> None:
    """Phase 4's upload tool reads one document. Same shape, same exclusion,
    recorded now so it is not rediscovered then."""
    registry = ToolRegistry()
    registry.register(FixtureTool(name="doc", category=ToolCategory.DOCUMENTS, items=()))
    registry.freeze()

    assert registry.plannable_categories() == ()
