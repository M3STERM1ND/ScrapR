"""Telemetry, cost ceilings, heartbeats and the operator report (`REQ-OBS-001..007`, `DEC-24`, `DEC-25`).

Against the real pipeline and a real database. The fake model is given a price
per call, so cost accounting is exercised with numbers rather than zeros.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from uuid import UUID

import pytest
from pipeline_support import fixture_registry, run_pipeline, scripted_provider
from pydantic import BaseModel
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from scrapr_core.config import get_settings
from scrapr_core.db.base import utcnow
from scrapr_core.db.enums import (
    Accessibility,
    ActivityStatus,
    AuthorityTier,
    RunKind,
    RunStatus,
    SourceCategory,
    VersionStatus,
)
from scrapr_core.db.models import (
    ActivityEvent,
    Base,
    ResearchRun,
    ResearchVersion,
    Source,
    ToolInvocation,
)
from scrapr_core.db.repositories import (
    ActivityRepository,
    AnonymousSessionRepository,
    ResearchRepository,
    RunRepository,
)
from scrapr_core.domain.ownership import OwnerContext
from scrapr_core.jobs import JobRunner
from scrapr_core.llm import FakeLLMProvider
from scrapr_core.llm.contract import ModelTier, StructuredResult, TokenUsage, UntrustedDocument
from scrapr_core.observability import MeteredProvider, step_telemetry
from scrapr_core.observability.report import build_report, render_text
from scrapr_core.orchestrator.pipeline import STAGES, build_handlers
from scrapr_core.security.trust import Trusted

pytestmark = pytest.mark.integration

QUESTION = "What is Acme's revenue?"
AREAS = (("Financial performance", (QUESTION, "How is Acme hiring?", "Who competes with Acme?"), ("web_search",)),)


class PricedProvider(FakeLLMProvider):
    """A fake that charges for every call, so the ledger has something to add."""

    PRICE = 1_000

    async def complete_structured[T: BaseModel](
        self,
        instruction: Trusted,
        untrusted: Sequence[UntrustedDocument],
        schema: type[T],
        model_tier: ModelTier = ModelTier.STANDARD,
    ) -> StructuredResult[T]:
        result = await super().complete_structured(instruction, untrusted, schema, model_tier)
        return StructuredResult(
            value=result.value,
            model=result.model,
            tier=result.tier,
            usage=TokenUsage(input_tokens=120, output_tokens=30, cost_micros=self.PRICE),
        )


def priced(questions: Sequence[str] = AREAS[0][1]) -> PricedProvider:
    base = scripted_provider("Acme Corp", tuple(questions), ((AREAS[0][0], tuple(questions), ("web_search",)),))
    provider = PricedProvider(standing_response=base._standing)  # type: ignore[attr-defined]
    provider.enqueue(*base._responses)  # type: ignore[attr-defined]
    return provider


@pytest.fixture
def session_factory(migrated_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=migrated_engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
def _empty_afterwards(session_factory: sessionmaker[Session]) -> Iterator[None]:
    yield
    tables = ", ".join(sorted(Base.metadata.tables))
    with session_factory() as session:
        session.execute(text(f"TRUNCATE {tables} CASCADE"))
        session.commit()


@pytest.fixture
def settings_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    """Change settings for one test, and put them back."""
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


def start(session_factory: sessionmaker[Session], kind: RunKind = RunKind.INITIAL) -> tuple[UUID, UUID]:
    with session_factory() as session:
        owner = OwnerContext.for_anonymous(AnonymousSessionRepository(session).issue().session.id)
        research = ResearchRepository(session, owner)
        created = research.create_session(objective="How is Acme Corp doing?")
        version = research.open_version(created.id)
        assert version is not None
        run = RunRepository(session).create_run(created.id, version.id, STAGES, kind=kind)
        session.commit()
        return created.id, run.id


# --------------------------------------------------------------------------
# Recording — `REQ-OBS-002..005`, `REQ-OBS-007`
# --------------------------------------------------------------------------


async def test_every_tool_call_and_model_call_is_recorded_against_its_run(
    session_factory: sessionmaker[Session],
) -> None:
    _, run_id = start(session_factory)
    provider = priced()

    await run_pipeline(session_factory, fixture_registry(), provider)

    with session_factory() as session:
        run = session.get(ResearchRun, run_id)
        assert run is not None
        invocations = session.execute(select(ToolInvocation).where(ToolInvocation.run_id == run_id)).scalars().all()

        # `REQ-OBS-002 AC-1`: attributable to a tool; `REQ-OBS-003 AC-2`: timed.
        assert invocations
        assert {row.tool_name for row in invocations} == {"fixture_search"}
        assert all(row.status == "success" and row.latency_ms is not None for row in invocations)
        # `REQ-OBS-007 AC-1`: the raw query is available to operators.
        assert all("Acme Corp:" in str(row.request_digest["query"]) for row in invocations)
        assert all(row.request_digest["stage"] == "research" for row in invocations)

        # `REQ-OBS-004 AC-1`: usage attributable to a session and a phase.
        by_stage = run.tokens["by_stage"]
        assert set(by_stage) == set(STAGES)
        assert by_stage["interpret"]["model_calls"] == 1
        assert by_stage["research"]["tool_calls"] == len(invocations)
        model_calls = sum(stage["model_calls"] for stage in by_stage.values())

        # `REQ-OBS-005 AC-1`: cost per run computable from recorded usage.
        tool_cost = sum(row.cost_micros or 0 for row in invocations)
        assert run.cost_micros == model_calls * PricedProvider.PRICE + tool_cost
        assert tool_cost == 8_000 * len(invocations)

        # `REQ-OBS-003 AC-1`: end-to-end and per-stage timing.
        assert set(run.effort_used["stage_ms"]) == set(STAGES)
        assert run.started_at is not None and run.finished_at is not None


async def test_a_failed_run_records_its_stage_and_cause(session_factory: sessionmaker[Session]) -> None:
    """`REQ-OBS-001 AC-1`, `AC-2`: failures carry stage and a groupable cause."""
    _, run_id = start(session_factory)
    runner = JobRunner(session_factory, {}, worker_id="no-handlers")

    await runner.run_one()

    with session_factory() as session:
        run = session.get(ResearchRun, run_id)
        assert run is not None and run.status is RunStatus.FAILED
        assert run.failure_stage == "interpret"
        assert run.failure_kind == "no_handler"


async def test_a_step_that_keeps_raising_is_recorded_by_exception_type(
    session_factory: sessionmaker[Session],
) -> None:
    _, run_id = start(session_factory)
    provider = FakeLLMProvider()  # no canned responses: interpret raises

    runner = JobRunner(session_factory, build_handlers(provider, fixture_registry()), worker_id="raiser")
    for _ in range(3):
        await runner.run_one()

    with session_factory() as session:
        run = session.get(ResearchRun, run_id)
        assert run is not None and run.status is RunStatus.FAILED
        assert run.failure_kind == "exception:NoCannedResponseError"


async def test_the_metering_wrapper_changes_nothing_outside_a_step(
    session_factory: sessionmaker[Session],
) -> None:
    """A stage called directly — a unit test, a script — records nothing and
    returns exactly what the provider returned; inside a step, it is counted."""
    from scrapr_core.orchestrator.interpret import Interpretation

    inner = PricedProvider()
    answer = Interpretation(subject="Acme", interpretation_note=None, questions=["q"])
    inner.enqueue(answer, answer)
    metered = MeteredProvider(inner)

    outside = await metered.complete_structured(Trusted("interpret"), [], Interpretation)
    assert outside.value == answer and outside.usage.cost_micros == PricedProvider.PRICE

    with session_factory() as session, step_telemetry(session, UUID(int=1), "interpret") as telemetry:
        await metered.complete_structured(Trusted("interpret"), [], Interpretation)
    assert telemetry.ledger.model_calls == 1
    assert telemetry.ledger.cost_micros == PricedProvider.PRICE
    assert telemetry.ledger.by_tier["standard"]["input"] == 120


# --------------------------------------------------------------------------
# The cost ceiling — `DEC-25`
# --------------------------------------------------------------------------


async def test_a_run_stops_retrieving_when_its_cost_ceiling_is_reached(
    session_factory: sessionmaker[Session], settings_env: pytest.MonkeyPatch
) -> None:
    """Before a call it cannot afford, never after; the report is partial, not absent."""
    settings_env.setenv("RUN_COST_CEILING_MICROS", "12000")
    get_settings.cache_clear()
    _, run_id = start(session_factory)

    await run_pipeline(session_factory, fixture_registry(), priced())

    with session_factory() as session:
        run = session.get(ResearchRun, run_id)
        assert run is not None
        calls = session.execute(
            select(func.count(ToolInvocation.id)).where(ToolInvocation.run_id == run_id)
        ).scalar_one()
        # Two model calls (interpret, plan) spent 2,000; one search at 8,000
        # brings it to 10,000 plus extraction — the budget refuses the rest.
        assert calls <= 2
        version = session.get(ResearchVersion, run.version_id)
        assert version is not None
        assert version.status is VersionStatus.PARTIAL
        assert run.status is RunStatus.PARTIAL


# --------------------------------------------------------------------------
# Activity during research — `NFR-PERF-003`, `TBD-05`
# --------------------------------------------------------------------------


async def test_a_long_area_keeps_the_timeline_moving(
    session_factory: sessionmaker[Session], settings_env: pytest.MonkeyPatch
) -> None:
    settings_env.setenv("ACTIVITY_HEARTBEAT_SECONDS", "0.001")
    get_settings.cache_clear()
    session_id, _ = start(session_factory)

    await run_pipeline(session_factory, fixture_registry(), priced())

    with session_factory() as session:
        area_events = session.execute(
            select(func.count(ActivityEvent.id)).where(
                ActivityEvent.session_id == session_id,
                ActivityEvent.label == "Researching financial performance",
                ActivityEvent.status == ActivityStatus.IN_PROGRESS,
            )
        ).scalar_one()
    # The opening line plus one per question, all under the area's own label.
    assert area_events >= 1 + len(AREAS[0][1])


def test_an_event_is_visible_before_the_step_that_wrote_it_commits(
    session_factory: sessionmaker[Session],
) -> None:
    """`REQ-ACT-001 AC-3`: the reader sees progress during a long step."""
    session_id, _ = start(session_factory)

    with session_factory() as step_session:
        ActivityRepository(step_session).append(session_id, "Researching hiring", ActivityStatus.IN_PROGRESS)
        # The step's own transaction is still open and uncommitted.
        with session_factory() as reader:
            labels = reader.execute(
                select(ActivityEvent.label).where(ActivityEvent.session_id == session_id)
            ).scalars().all()
        step_session.rollback()

    assert labels == ["Researching hiring"]


# --------------------------------------------------------------------------
# The report — `REQ-OBS-001..006`
# --------------------------------------------------------------------------


async def test_the_report_groups_failures_tools_costs_and_domains(
    session_factory: sessionmaker[Session],
) -> None:
    _, run_id = start(session_factory)
    await run_pipeline(session_factory, fixture_registry(), priced())

    with session_factory() as session:
        run = session.get(ResearchRun, run_id)
        assert run is not None
        # A second, expensive, failed run and some failures to count.
        failed_session, failed_run_id = start(session_factory)
        failed = session.get(ResearchRun, failed_run_id)
        assert failed is not None
        failed.status = RunStatus.FAILED
        failed.failure_stage = "research"
        failed.failure_kind = "exception:TimeoutError"
        failed.cost_micros = 1_900_000
        failed.finished_at = utcnow()
        session.add(
            ToolInvocation(
                run_id=failed_run_id,
                tool_name="page_fetch",
                tool_category="page_fetch",
                status="failure",
                error_kind="paywalled",
                latency_ms=900,
                source_domain="ft.com",
            )
        )
        session.add(
            Source(
                version_id=failed.version_id,
                url="https://www.wsj.com/articles/acme",
                url_normalized="https://www.wsj.com/articles/acme",
                name="WSJ",
                category=SourceCategory.NEWS,
                authority_tier=AuthorityTier.SECONDARY,
                tier_rationale={},
                retrieved_at=utcnow(),
                accessibility=Accessibility.PAYWALLED,
            )
        )
        session.commit()
        assert failed_session

        report = build_report(session, days=1)

    assert report.runs == 2 and report.failed == 1
    assert ("research", "exception:TimeoutError", 1) in report.failures_by_cause
    assert any(entry[0] == str(failed_run_id) for entry in report.expensive_runs)
    tools = {tool.tool: tool for tool in report.tools}
    assert tools["page_fetch"].failure_rate == 1.0
    assert tools["page_fetch"].error_kinds == {"paywalled": 1}
    assert tools["fixture_search"].failures == 0
    assert ("ft.com", "paywalled", 1) in report.source_failures
    assert ("www.wsj.com", "paywalled", 1) in report.source_failures

    text_report = render_text(report)
    assert "exception:TimeoutError" in text_report and "ft.com" in text_report


def test_no_api_route_exposes_operator_data() -> None:
    """`REQ-OBS-007 AC-2`: raw queries and step state are never served."""
    from scrapr_api.main import create_app

    schema = str(create_app().openapi())
    for field in ("request_digest", "checkpoint", "failure_kind", "tool_invocations", "cost_micros"):
        assert field not in schema, field


def test_step_telemetry_is_scoped_to_its_block(session_factory: sessionmaker[Session]) -> None:
    from scrapr_core.observability import current_telemetry

    with session_factory() as session, step_telemetry(session, UUID(int=1), "research") as telemetry:
        assert current_telemetry() is telemetry
    assert current_telemetry() is None
