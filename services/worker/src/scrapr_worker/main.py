"""The worker entrypoint: claim a step, execute it, checkpoint, release.

Thin by design. The loop itself is `scrapr_core.jobs.JobRunner`; what lives here
is the wiring — which tools exist, which model provider answers, and how long to
wait when there is nothing to do.

**Tool availability is fixed here, at startup, and then frozen**
(`REQ-SEC-015 AC-1`). Retrieved content cannot add, name or reach a tool,
because by the time any content has been retrieved the registry no longer
accepts registrations.

Fixtures and a fake provider, because `OPEN-04..09` name no providers yet. Each
real one lands as a registration next to the fixture it replaces, and no other
file changes.

Until `OPEN-04` closes, the model is a deterministic stand-in that rearranges
what retrieval found rather than writing anything of its own, and refuses to run
in production at all. That is what implementation plan §15 means by exercising
the pipeline "over fixtures plus the fake LLM".
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
from scrapr_core.llm.scripted import ScriptedProvider
from scrapr_core.orchestrator.pipeline import build_handlers
from scrapr_core.tools import ToolCategory, ToolRegistry
from scrapr_core.tools.impl import FixtureTool, PageFetchTool, fixture_item

__all__ = ["build_registry", "build_runner", "main"]

logger = logging.getLogger("scrapr.worker")

IDLE_SLEEP_SECONDS = 1.0
"""How long to wait when no step is runnable.

Polling, not notification, on purpose: `LISTEN`/`NOTIFY` would tie the worker to
a long-lived connection, and that is exactly the constraint `OPEN-03` has not
resolved. A second of latency on a job that takes minutes is not the bottleneck.
"""


def build_registry() -> ToolRegistry:
    """Register the tools this process may use, then close the registry.

    Two fixture categories with two sources each, because
    `MIN_SOURCES_PER_QUESTION` is two: a one-source fixture set would leave
    every question open and make every local run look like a research failure
    rather than a missing provider.

    **Page fetch is real** (`REQ-TOOL-003`). It is the only one of Task 1.9's
    six tools that needed no provider decision — `OPEN-05..09` each name a
    vendor nobody has chosen, and a URL names nobody — so it registers here
    beside the fixtures rather than waiting with them. That it can do so
    without the orchestrator learning anything is `REQ-TOOL-009 AC-2` holding
    up: the loop still asks for a category.
    """
    registry = ToolRegistry()
    registry.register(PageFetchTool())

    for name, category, host, bodies in (
        (
            "fixture_search",
            ToolCategory.WEB_SEARCH,
            "example.com",
            (
                "The company reported $1.2bn revenue for FY2025, up 18% year "
                "over year.",
                "Gross margin held at 75% through the year.",
            ),
        ),
        (
            "fixture_news",
            ToolCategory.NEWS,
            "press.example",
            (
                "The company reported $1.2bn in annual revenue, according to "
                "its latest filing.",
                "Hiring continued through the fourth quarter, with 40 open "
                "engineering roles listed.",
            ),
        ),
    ):
        registry.register(
            FixtureTool(
                name=name,
                category=category,
                items=tuple(
                    fixture_item(
                        source_name=f"{host} result {index + 1}",
                        text=body,
                        source_url=f"https://{host}/{name}/{index + 1}",
                    )
                    for index, body in enumerate(bodies)
                ),
            )
        )

    registry.freeze()
    return registry


def build_runner(worker_id: str) -> JobRunner:
    """Wire the runner to the pipeline's stage handlers.

    One line changes when `OPEN-04` closes: the provider. Everything else here
    is already what production runs, because the stages read their inputs from
    the database rather than from whatever assembled them.
    """
    settings = get_settings()
    if settings.scrapr_env == "production":
        # A deterministic echo is a development convenience. Serving it to users
        # would mean presenting research nobody did.
        raise RuntimeError(
            "no AI provider is configured (OPEN-04), and the scripted stand-in "
            "must never run in production"
        )

    return JobRunner(
        build_session_factory(build_engine()),
        build_handlers(ScriptedProvider(), build_registry()),
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
