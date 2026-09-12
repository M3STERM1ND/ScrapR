"""The worker's wiring, and the guard that keeps the stand-in out of production.

This file is thin because the process is thin. What it checks is the part that
would be expensive to get wrong: that tool availability is closed before any run
starts, and that a worker with no real model refuses to serve users rather than
serving them research nobody did.
"""

from __future__ import annotations

import pytest

import scrapr_worker.main as worker
from scrapr_core.config import Settings
from scrapr_core.orchestrator.pipeline import STAGES
from scrapr_core.tools import RegistryFrozenError, ToolCategory
from scrapr_core.tools.impl import FixtureTool
from scrapr_worker.main import build_registry, build_runner


def test_the_registry_is_frozen_before_any_run() -> None:
    """`REQ-SEC-015 AC-1`: retrieved content cannot add, name or reach a tool,
    because by the time anything has been retrieved the registry is closed."""
    registry = build_registry()

    assert registry.is_frozen
    with pytest.raises(RegistryFrozenError):
        registry.register(
            FixtureTool(name="late", category=ToolCategory.WEB_SEARCH, items=())
        )


def test_the_fixtures_can_actually_resolve_a_question() -> None:
    """`MIN_SOURCES_PER_QUESTION` is two, so a single-source fixture set would
    make every local run look like a research failure rather than a missing
    provider."""
    registry = build_registry()

    for category in registry.categories():
        tools = registry.for_category(category)
        assert tools
        for tool in tools:
            assert len(tool.items) >= 2  # type: ignore[attr-defined]


def test_every_stage_has_a_handler() -> None:
    """A stage with no handler is poisoned on its first attempt, so a wiring
    gap here would fail every run at exactly the step it reached."""
    runner = build_runner("test-worker")

    assert set(runner._handlers) == set(STAGES)


def test_the_stand_in_provider_refuses_to_run_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deterministic echo is a development convenience. Shipping it would mean
    serving users research nobody did."""
    production = Settings().model_copy(update={"scrapr_env": "production"})
    monkeypatch.setattr(worker, "get_settings", lambda: production)

    with pytest.raises(RuntimeError, match="must never run in production"):
        worker.build_runner("test-worker")


async def test_the_loop_stops_when_asked() -> None:
    """A worker finishes the step in flight and then exits.

    Killing one mid-execution would be safe — the lease expires and another
    worker retries it — but "safe to interrupt" is not a reason to interrupt.
    """
    import asyncio

    executed = 0

    class OneStepRunner:
        async def run_one(self) -> bool:
            nonlocal executed
            executed += 1
            return executed < 3

    stopping = asyncio.Event()
    finished = asyncio.Event()

    class StoppingRunner(OneStepRunner):
        async def run_one(self) -> bool:
            more = await super().run_one()
            if not more:
                # The queue is empty: ask the loop to finish, the way a signal
                # handler would.
                stopping.set()
                finished.set()
            return more

    await worker.run_forever(StoppingRunner(), stopping)  # type: ignore[arg-type]

    assert executed == 3
    assert finished.is_set()


async def test_an_idle_loop_waits_rather_than_spinning() -> None:
    """With nothing to claim, the loop sleeps on the stop event instead of
    hammering the database in a tight cycle."""
    import asyncio

    calls = 0

    class IdleRunner:
        async def run_one(self) -> bool:
            nonlocal calls
            calls += 1
            return False

    stopping = asyncio.Event()
    task = asyncio.create_task(
        worker.run_forever(IdleRunner(), stopping)  # type: ignore[arg-type]
    )
    await asyncio.sleep(0.05)
    stopping.set()
    await task

    # One poll, then a wait: not one per scheduler tick.
    assert calls == 1
