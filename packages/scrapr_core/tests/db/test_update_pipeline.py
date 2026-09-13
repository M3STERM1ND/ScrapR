"""Update Research end to end, through the step runner (`REQ-VER-001..007`, `DEC-19`, `DEC-20`).

A first run, then an update whose sources now report a different figure. The
properties under test are the ones Phase 6 exists for: the old version is
untouched down to its retrieval timestamps, the new version fetched afresh, the
update re-asked the same questions in priority order, and What's Changed names
the figure that moved and links the evidence that moved it.
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID

import pytest
from pipeline_support import (
    HIRING_TEXT,
    extraction_provider,
    fixture_registry,
    run_pipeline,
    run_update,
    scripted_provider,
    search_tool,
)
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from scrapr_core.db.enums import ClaimType, RunKind, VersionStatus
from scrapr_core.db.models import (
    ActivityEvent,
    Base,
    Evidence,
    ResearchQuestion,
    ResearchRun,
    ResearchSession,
    ResearchVersion,
    RunStep,
    Source,
)
from scrapr_core.db.repositories import (
    AnonymousSessionRepository,
    ResearchRepository,
    RunRepository,
)
from scrapr_core.domain.ownership import OwnerContext
from scrapr_core.orchestrator import pipeline
from scrapr_core.orchestrator.pipeline import STAGES, UPDATE_STAGES
from scrapr_core.orchestrator.synthesize import DraftClaim, SynthesisDraft
from scrapr_core.tools import ToolCategory, ToolRegistry

pytestmark = pytest.mark.integration

QUESTION = "What is Acme's revenue?"
AREAS = (("Financial performance", (QUESTION,), ("web_search",)),)
NEW_REVENUE = "Acme Corp reported revenue of $1.5bn for fiscal 2025, up 25%."


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


def synthesis_saying(revenue: str):  # type: ignore[no-untyped-def]
    def draft(evidence_ids):  # type: ignore[no-untyped-def]
        cited = [str(evidence_ids[0])] if evidence_ids else []
        return SynthesisDraft(
            summary=[
                DraftClaim(
                    text=f"Acme reported {revenue} revenue for FY2025.",
                    claim_type=ClaimType.FACT,
                    evidence_ids=cited,
                    is_important=True,
                )
            ],
            sections=[],
        )

    return draft


def updated_registry(revenue_text: str) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(search_tool(texts=(revenue_text, HIRING_TEXT)))
    registry.register(search_tool(name="fixture_news", category=ToolCategory.NEWS, host="apnews.com"))
    registry.freeze()
    return registry


async def first_version(session_factory: sessionmaker[Session]) -> tuple[UUID, UUID]:
    """Research once. Returns (anonymous owner id, research session id)."""
    with session_factory() as session:
        owner = OwnerContext.for_anonymous(AnonymousSessionRepository(session).issue().session.id)
        research = ResearchRepository(session, owner)
        created = research.create_session(objective="How is Acme Corp performing?")
        version = research.open_version(created.id)
        assert version is not None
        RunRepository(session).create_run(created.id, version.id, STAGES)
        session.commit()
        anonymous_id, research_id = owner.anonymous_session_id, created.id
    assert anonymous_id is not None

    await run_pipeline(
        session_factory,
        fixture_registry(),
        scripted_provider("Acme Corp", (QUESTION,), AREAS),
        synthesis=synthesis_saying("$1.2bn"),
    )
    return anonymous_id, research_id


def queue_update(session_factory: sessionmaker[Session], anonymous_id: UUID, research_id: UUID) -> UUID:
    """Everything `POST /v1/research/{id}/update` does, minus the HTTP."""
    with session_factory() as session:
        research = ResearchRepository(session, OwnerContext.for_anonymous(anonymous_id))
        baseline = research.latest_completed_version(research_id)
        assert baseline is not None
        version = research.open_version(research_id, compare_with=baseline)
        assert version is not None
        RunRepository(session).create_run(research_id, version.id, UPDATE_STAGES, kind=RunKind.UPDATE)
        session.commit()
        return version.id


def evidence_record(session: Session, version_id: UUID) -> list[tuple[UUID, str, object]]:
    return [
        (evidence.id, evidence.content, source.retrieved_at)
        for evidence, source in session.execute(
            select(Evidence, Source)
            .join(Source, Source.id == Evidence.source_id)
            .where(Evidence.version_id == version_id)
            .order_by(Evidence.id)
        ).all()
    ]


async def test_an_update_reports_the_figure_that_moved_and_leaves_the_original_alone(
    session_factory: sessionmaker[Session],
) -> None:
    anonymous_id, research_id = await first_version(session_factory)
    with session_factory() as session:
        original = session.execute(
            select(ResearchVersion).where(ResearchVersion.session_id == research_id)
        ).scalar_one()
        original_evidence = evidence_record(session, original.id)
        original_questions = [
            question.text
            for question in session.execute(
                select(ResearchQuestion).where(ResearchQuestion.version_id == original.id)
            ).scalars()
        ]
    assert original.status is VersionStatus.COMPLETE and original_evidence

    update_id = queue_update(session_factory, anonymous_id, research_id)
    await run_update(
        session_factory,
        updated_registry(NEW_REVENUE),
        extraction_provider("Acme reported $1.5bn revenue for FY2025.", "revenue of $1.5bn"),
        synthesis=synthesis_saying("$1.5bn"),
    )

    with session_factory() as session:
        updated = session.get(ResearchVersion, update_id)
        again = session.get(ResearchVersion, original.id)
        research = session.get(ResearchSession, research_id)
        assert updated is not None and again is not None and research is not None

        # `REQ-VER-005`: a new version, current, pointing back at its baseline.
        assert updated.version_number == 2
        assert updated.status is VersionStatus.COMPLETE and updated.closed_at is not None
        assert updated.previous_version_id == original.id
        assert research.current_version_id == update_id

        # `REQ-VER-002 AC-2`: the original is exactly as it was.
        assert evidence_record(session, original.id) == original_evidence
        assert again.change_summary is None and again.status is VersionStatus.COMPLETE

        # `REQ-VER-003 AC-2`: fresh rows with fresh retrieval timestamps.
        new_evidence = evidence_record(session, update_id)
        assert new_evidence
        assert not {row[0] for row in new_evidence} & {row[0] for row in original_evidence}
        assert min(row[2] for row in new_evidence) > max(row[2] for row in original_evidence)  # type: ignore[operator]

        # `DEC-19`: the same questions, re-asked, with no interpret or plan step.
        questions = [
            question.text
            for question in session.execute(
                select(ResearchQuestion).where(ResearchQuestion.version_id == update_id)
            ).scalars()
        ]
        assert questions == original_questions
        run = session.execute(select(ResearchRun).where(ResearchRun.version_id == update_id)).scalar_one()
        assert run.kind is RunKind.UPDATE
        stages = [step.stage for step in session.execute(select(RunStep).where(RunStep.run_id == run.id)).scalars()]
        assert "interpret" not in stages and "plan" not in stages
        prioritized = next(
            step for step in session.execute(select(RunStep).where(RunStep.run_id == run.id)).scalars()
            if step.stage == "prioritize"
        )
        assert prioritized.checkpoint["areas"][0]["priority"] in {"stale", "volatile", "stable"}

        labels = set(session.execute(select(ActivityEvent.label)).scalars())
        assert {"Checking what may have changed", "Comparing with the previous version"} <= labels

        # `REQ-VER-006`, `REQ-VER-007`: the figure that moved, with its evidence.
        summary = updated.change_summary
        assert summary is not None and summary["available"] is True
        assert summary["has_changes"] is True
        assert summary["compared_with"]["version_number"] == 1
        moved = [change for change in summary["changes"] if change["kind"] == "figure_changed"]
        assert moved, summary
        assert "1.2" in moved[0]["before"]["value"] and "1.5" in moved[0]["after"]["value"]
        assert moved[0]["category"] == "financial_figures"
        assert set(moved[0]["evidence_ids"]) <= {str(row[0]) for row in new_evidence}


async def test_an_update_that_finds_the_same_evidence_says_nothing_changed(
    session_factory: sessionmaker[Session],
) -> None:
    anonymous_id, research_id = await first_version(session_factory)
    update_id = queue_update(session_factory, anonymous_id, research_id)

    await run_update(
        session_factory,
        fixture_registry(),
        extraction_provider("Acme reported $1.2bn revenue for FY2025.", "revenue of $1.2bn"),
        synthesis=synthesis_saying("$1.2bn"),
    )

    with session_factory() as session:
        updated = session.get(ResearchVersion, update_id)
        assert updated is not None and updated.change_summary is not None
        assert updated.change_summary["has_changes"] is False
        assert updated.change_summary["headline"].startswith("No meaningful changes since version 1")


async def test_a_comparison_that_fails_does_not_fail_the_version(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    anonymous_id, research_id = await first_version(session_factory)
    update_id = queue_update(session_factory, anonymous_id, research_id)

    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("comparison defect")

    monkeypatch.setattr(pipeline, "compare_versions", broken)
    await run_update(
        session_factory,
        fixture_registry(),
        extraction_provider("Acme reported $1.2bn revenue for FY2025.", "revenue of $1.2bn"),
        synthesis=synthesis_saying("$1.2bn"),
    )

    with session_factory() as session:
        updated = session.get(ResearchVersion, update_id)
        assert updated is not None
        assert updated.status is VersionStatus.COMPLETE
        assert updated.change_summary is not None and updated.change_summary["available"] is False


async def test_nothing_reruns_research_on_its_own(session_factory: sessionmaker[Session]) -> None:
    """`REQ-VER-001 AC-1`, `REQ-VER-009 AC-2`: an idle worker creates no runs."""
    from scrapr_core.jobs import JobRunner

    await first_version(session_factory)
    runner = JobRunner(session_factory, pipeline.build_handlers(extraction_provider("x", "x"), fixture_registry()), worker_id="idle")

    assert await runner.run_until_idle() == 0
    with session_factory() as session:
        assert len(session.execute(select(ResearchRun)).scalars().all()) == 1
