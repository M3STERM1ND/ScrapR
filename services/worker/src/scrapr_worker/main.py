"""The worker entrypoint: claim a step, execute it, checkpoint, release.

Thin by design. The loop itself is `scrapr_core.jobs.JobRunner`; what lives here
is the wiring — which tools exist, which model provider answers, and how long to
wait when there is nothing to do.

**Tool availability is fixed here, at startup, and then frozen**
(`REQ-SEC-015 AC-1`). Retrieved content cannot add, name or reach a tool,
because by the time any content has been retrieved the registry no longer
accepts registrations.

Phase 0 wires fixtures and the fake provider, because `OPEN-04..09` name no
providers yet. Each real one lands as a registration next to the fixture it
replaces — no other file changes.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from types import FrameType

from scrapr_core.config import get_settings
from scrapr_core.db.engine import build_engine, build_session_factory
from scrapr_core.jobs import JobRunner
from scrapr_core.llm import FakeLLMProvider
from scrapr_core.orchestrator.skeleton import (
    RETRIEVE_STAGE,
    SYNTHESIZE_STAGE,
    RetrieveHandler,
    SkeletonClaim,
    SynthesizeHandler,
)
from scrapr_core.tools import ToolCategory, ToolRegistry
from scrapr_core.tools.impl import FixtureTool, fixture_item

__all__ = ["build_registry", "build_runner", "main"]

logger = logging.getLogger("scrapr.worker")

IDLE_SLEEP_SECONDS = 1.0
"""How long to wait when no step is runnable.

Polling, not notification, on purpose: `LISTEN`/`NOTIFY` would tie the worker to
a long-lived connection, and that is exactly the constraint `OPEN-03` has not
resolved. A second of latency on a job that takes minutes is not the bottleneck.
"""


def build_registry() -> ToolRegistry:
    """Register the tools this process may use, then close the registry."""
    registry = ToolRegistry()
    registry.register(
        FixtureTool(
            name="fixture_search",
            category=ToolCategory.WEB_SEARCH,
            items=(
                fixture_item(
                    source_name="Example company results",
                    text=(
                        "The company reported $1.2bn revenue for FY2025, up 18% "
                        "year over year."
                    ),
                    source_url="https://example.com/ir/fy2025",
                ),
            ),
        )
    )
    registry.freeze()
    return registry


def build_runner(worker_id: str) -> JobRunner:
    """Wire the runner to its handlers.

    The fake provider is given a *standing* answer rather than a queue: a worker
    process serves an unbounded number of runs, and a queue of one would starve
    the second one. Tests keep the strict queue, which is where running out of
    responses is information rather than an outage.
    """
    provider = FakeLLMProvider(
        standing_response=SkeletonClaim(
            section_title="Revenue",
            claim_text="The company reported $1.2bn revenue for FY2025.",
        )
    )

    return JobRunner(
        build_session_factory(build_engine()),
        {
            RETRIEVE_STAGE: RetrieveHandler(registry=build_registry()),
            SYNTHESIZE_STAGE: SynthesizeHandler(provider=provider),
        },
        worker_id=worker_id,
    )


async def run_forever(runner: JobRunner, stopping: asyncio.Event) -> None:
    """Poll for work until asked to stop.

    A step in flight is always finished before the loop exits. Killing one
    mid-execution would be safe — its lease expires and another worker retries
    it — but "safe to interrupt" is not a reason to interrupt.
    """
    while not stopping.is_set():
        if not await runner.run_one():
            try:
                async with asyncio.timeout(IDLE_SLEEP_SECONDS):
                    await stopping.wait()
            except TimeoutError:
                continue


def main() -> int:
    """Entry point. Returns a process exit code."""
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    worker_id = f"worker-{settings.scrapr_env}"
    runner = build_runner(worker_id)
    stopping = asyncio.Event()

    def _stop(signum: int, frame: FrameType | None) -> None:
        logger.info("received signal %s, finishing the current step", signum)
        stopping.set()

    for received in (signal.SIGINT, signal.SIGTERM):
        signal.signal(received, _stop)

    logger.info("worker %s polling for runnable steps", worker_id)
    asyncio.run(run_forever(runner, stopping))
    logger.info("worker %s stopped", worker_id)
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    sys.exit(main())
