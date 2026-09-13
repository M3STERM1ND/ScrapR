"""The worker entrypoint: claim a step, execute it, checkpoint, release.

Thin by design. The loop itself is `scrapr_core.jobs.JobRunner`; what lives here
is the wiring — which tools exist, which model provider answers, and how long to
wait when there is nothing to do.

**Tool availability is fixed here, at startup, and then frozen**
(`REQ-SEC-015 AC-1`). Retrieved content cannot add, name or reach a tool,
because by the time any content has been retrieved the registry no longer
accepts registrations. The registry itself is built by
`scrapr_core.tools.builder` from configuration, so which providers exist is an
environment question rather than a code one.

**Which model answers is `DEC-06`; which providers answer is `DEC-07`.** Both
are configuration. What is not configurable is the choice between a real model
and the stand-in: a worker with no key refuses to start rather than quietly
serving deterministic echo as research (§`build_runner`).
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from dataclasses import replace
from types import FrameType

import anthropic

from scrapr_core.config import Settings, get_settings
from scrapr_core.db.engine import build_engine, build_session_factory
from scrapr_core.jobs import JobRunner
from scrapr_core.llm.anthropic_provider import DEFAULT_PROFILES, AnthropicProvider
from scrapr_core.llm.contract import LLMProvider, ModelTier
from scrapr_core.llm.scripted import ScriptedProvider
from scrapr_core.orchestrator.pipeline import build_handlers
from scrapr_core.tools.builder import build_registry

__all__ = ["build_provider", "build_registry", "build_runner", "main"]

logger = logging.getLogger("scrapr.worker")

IDLE_SLEEP_SECONDS = 1.0
"""How long to wait when no step is runnable.

Polling, not notification, on purpose: `LISTEN`/`NOTIFY` would tie the worker to
a long-lived connection, and that is exactly the constraint `OPEN-03` has not
resolved. A second of latency on a job that takes minutes is not the bottleneck.
"""


def build_provider(settings: Settings) -> LLMProvider:
    """The model behind every stage.

    Three outcomes, and the middle one is the point:

    * a key, anywhere — the real provider, with the `DEC-06` tier table
      overridden by whatever configuration says;
    * no key, in production — **refuse to start**;
    * no key, locally — the deterministic stand-in, which rearranges retrieved
      text rather than writing anything.

    The refusal is what stops the convenient path from becoming the shipped
    path. A stand-in that invented plausible findings would make a broken
    pipeline look like a working product, and the only reliable guard against
    shipping it is that it cannot run where users are.
    """
    if settings.has_ai_provider:
        # Only the model name is configurable. `max_tokens`, thinking and
        # effort stay as `DEC-06` set them, because those are facts about what
        # each model accepts rather than preferences — Haiku rejects `effort`
        # whoever names it in an environment variable.
        return AnthropicProvider(
            # The key comes from settings, not from the ambient environment.
            # `pydantic-settings` reads `.env` into the Settings object without
            # exporting it to `os.environ`, so a zero-argument client finds
            # nothing and fails on the first call rather than at startup —
            # which is the worst possible place for a credential problem to
            # surface.
            client=anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key),
            profiles={
                tier: replace(DEFAULT_PROFILES[tier], model=model)
                for tier, model in (
                    (ModelTier.CHEAP, settings.model_cheap),
                    (ModelTier.STANDARD, settings.model_standard),
                    (ModelTier.DEEP, settings.model_deep),
                )
            }
        )

    if settings.scrapr_env == "production":
        raise RuntimeError(
            "no AI provider is configured (ANTHROPIC_API_KEY is unset), and the "
            "scripted stand-in must never run in production"
        )

    logger.warning(
        "no ANTHROPIC_API_KEY: running the deterministic stand-in, which "
        "rearranges retrieved text and writes no research of its own"
    )
    return ScriptedProvider()


def build_runner(worker_id: str) -> JobRunner:
    """Wire the runner to the pipeline's stage handlers."""
    settings = get_settings()
    provider = build_provider(settings)
    registry, report = build_registry(settings)

    # Said once, at start, rather than discovered per run. An operator needs to
    # know a category is unserved before the reports start naming it as a gap.
    logger.info("provider: %s; %s", provider.name, report.summary)

    return JobRunner(
        build_session_factory(build_engine()),
        build_handlers(provider, registry),
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
