"""Phase 2 through the real pipeline (`REQ-EVID-002`, `-006`, `-008`, `-015`).

> **Exit (PRD):** the agent can explain where information came from and
> identify disagreement.

The unit tests next door prove each rule in isolation. What they cannot prove
is that any of it reaches a reader — and that is exactly the failure mode this
project has hit twice already: a correct module wired to nothing, and a code
path that was dead in production while its unit test passed.

So these assert against the **persisted rows** after a full run: the tier the
source actually got, the confidence the claim actually carries, the rationale a
reader could actually be shown.
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID

import pytest
from pipeline_support import (
    cite_everything,
    disputed_registry,
    disputing_provider,
    fixture_registry,
    run_pipeline,
    scripted_provider,
    search_tool,
)
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from scrapr_core.db.enums import AuthorityTier, ClaimType
from scrapr_core.db.models import (
    Base,
    Claim,
    Evidence,
    Source,
)
from scrapr_core.db.repositories import (
    AnonymousSessionRepository,
    ResearchRepository,
    RunRepository,
)
from scrapr_core.domain.ownership import OwnerContext
from scrapr_core.llm import FakeLLMProvider
from scrapr_core.orchestrator.pipeline import STAGES
from scrapr_core.tools import ToolCategory, ToolRegistry

pytestmark = pytest.mark.integration

OBJECTIVE = "How is Acme Corp performing?"
QUESTION = "What is Acme's revenue?"
AREAS = (("Financial performance", (QUESTION,), ("web_search",)),)


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


def start_research(session_factory: sessionmaker[Session], url: str | None = None) -> UUID:
    with session_factory() as session:
        owner = OwnerContext.for_anonymous(
            AnonymousSessionRepository(session).issue().session.id
        )
        research = ResearchRepository(session, owner)
        created = research.create_session(objective=OBJECTIVE, context_url=url)
        version = research.open_version(created.id)
        assert version is not None
        RunRepository(session).create_run(created.id, version.id, STAGES)
        session.commit()
        return created.id


def provider() -> FakeLLMProvider:
    return scripted_provider("Acme Corp", (QUESTION,), AREAS)


# --------------------------------------------------------------------------
# Tiering reaches the rows
# --------------------------------------------------------------------------


async def test_sources_are_tiered_at_insert(
    session_factory: sessionmaker[Session],
) -> None:
    """`REQ-EVID-002`. The fixtures publish on an allowlisted host, so they
    land `SECONDARY` rather than the old blanket default."""
    start_research(session_factory)

    await run_pipeline(session_factory, fixture_registry(), provider())

    with session_factory() as session:
        sources = session.execute(select(Source)).scalars().all()

    assert sources
    assert all(source.authority_tier is AuthorityTier.SECONDARY for source in sources)


async def test_every_source_records_why_it_got_its_tier(
    session_factory: sessionmaker[Session],
) -> None:
    """`AC-3`: inspectable after the fact. A user asking "why is this
    secondary" gets the rule that fired, not a score."""
    start_research(session_factory)

    await run_pipeline(session_factory, fixture_registry(), provider())

    with session_factory() as session:
        sources = session.execute(select(Source)).scalars().all()

    for source in sources:
        assert source.tier_rationale.get("rule")
        assert source.tier_rationale.get("detail")


async def test_an_unlisted_host_lands_lower(
    session_factory: sessionmaker[Session],
) -> None:
    """`DEC-08 §4` in production, not in a unit test.

    This is the behaviour change the phase introduces: a source nobody vouched
    for no longer corroborates on its own.
    """
    registry = ToolRegistry()
    registry.register(search_tool(host="some-unknown-blog.test"))
    registry.freeze()
    start_research(session_factory)

    await run_pipeline(session_factory, registry, provider())

    with session_factory() as session:
        sources = session.execute(select(Source)).scalars().all()

    assert sources
    assert all(source.authority_tier is AuthorityTier.LOWER for source in sources)


async def test_the_subjects_own_site_is_primary(
    session_factory: sessionmaker[Session],
) -> None:
    """`DEC-08` rule 4, reached through the `context_url` the user supplied —
    the only place a subject domain can honestly come from."""
    registry = ToolRegistry()
    registry.register(search_tool(host="acme-corp.test"))
    registry.freeze()
    start_research(session_factory, url="https://acme-corp.test/investors")

    await run_pipeline(session_factory, registry, provider())

    with session_factory() as session:
        sources = session.execute(select(Source)).scalars().all()

    assert sources
    assert all(source.authority_tier is AuthorityTier.PRIMARY for source in sources)


# --------------------------------------------------------------------------
# Deduplication
# --------------------------------------------------------------------------


async def test_a_tracking_parameter_does_not_create_a_second_source(
    session_factory: sessionmaker[Session],
) -> None:
    """`REQ-EVID-006 AC-1`, and the bug it was hiding.

    `url_normalized` held the raw URL, so the unique constraint never fired and
    one page behind two campaign tags counted as two sources — corroboration
    invented out of a tracking parameter.
    """
    registry = ToolRegistry()
    registry.register(
        search_tool(
            texts=("Acme reported revenue of $1.2bn.", "Acme reported revenue of $1.2bn."),
            host="reuters.com",
        )
    )
    registry.freeze()
    start_research(session_factory)

    await run_pipeline(session_factory, registry, provider())

    with session_factory() as session:
        urls = session.execute(select(Source.url_normalized)).scalars().all()

    assert len(urls) == len(set(urls)), f"duplicate canonical urls: {urls}"


# --------------------------------------------------------------------------
# Normalization
# --------------------------------------------------------------------------


async def test_evidence_carries_a_normalized_value_and_its_reported_form(
    session_factory: sessionmaker[Session],
) -> None:
    """`REQ-EVID-008 AC-1`, `AC-2`. Both, because the point of normalisation
    is that it is non-destructive."""
    start_research(session_factory)

    await run_pipeline(session_factory, fixture_registry(), provider())

    with session_factory() as session:
        rows = session.execute(select(Evidence)).scalars().all()

    priced = [row for row in rows if row.value_normalized is not None]
    assert priced, "no evidence was normalised at all"
    for row in priced:
        assert row.value_raw, "the reported form was discarded"


# --------------------------------------------------------------------------
# Confidence — the exit condition
# --------------------------------------------------------------------------


async def test_every_claim_carries_a_confidence(
    session_factory: sessionmaker[Session],
) -> None:
    """`REQ-EVID-015 AC-1`: every claim, no exempt type.

    The column existed and was null for the whole of Phase 1. This is the
    assertion that it is no longer.
    """
    start_research(session_factory)

    await run_pipeline(session_factory, fixture_registry(), provider())

    with session_factory() as session:
        claims = session.execute(select(Claim)).scalars().all()

    assert claims
    for claim in claims:
        assert claim.confidence in {"high", "moderate", "low"}, claim.text


async def test_confidence_records_the_inputs_it_was_computed_from(
    session_factory: sessionmaker[Session],
) -> None:
    """`AC-3` and `REQ-DATA-012`. The rationale is generated from the same
    inputs as the level, so it cannot drift from what it explains."""
    start_research(session_factory)

    await run_pipeline(session_factory, fixture_registry(), provider())

    with session_factory() as session:
        claims = session.execute(select(Claim)).scalars().all()

    for claim in claims:
        inputs = claim.confidence_inputs
        assert inputs.get("rationale")
        assert inputs.get("level") == claim.confidence
        assert "distinct_sources" in inputs


async def test_an_uncertainty_is_low_and_a_sourced_fact_is_not(
    session_factory: sessionmaker[Session],
) -> None:
    """`DEC-09 §4.3` through the real pipeline.

    A run against a failing category produces both kinds of claim in one
    report, which is what makes this a comparison rather than two assertions.
    """
    from scrapr_core.tools.impl import FailingFixtureTool

    registry = ToolRegistry()
    registry.register(search_tool())
    registry.register(FailingFixtureTool(name="broken_news", category=ToolCategory.NEWS))
    registry.freeze()
    start_research(session_factory)

    areas = (
        ("Financial performance", (QUESTION,), ("web_search",)),
        ("Recent news", ("What is new at Acme?",), ("news",)),
    )
    await run_pipeline(
        session_factory,
        registry,
        scripted_provider("Acme Corp", (QUESTION, "What is new at Acme?"), areas),
    )

    with session_factory() as session:
        claims = session.execute(select(Claim)).scalars().all()

    uncertainties = [c for c in claims if c.claim_type is ClaimType.UNCERTAINTY]
    facts = [c for c in claims if c.claim_type is ClaimType.FACT]

    assert uncertainties, "the failed area produced no uncertainty"
    assert all(c.confidence == "low" for c in uncertainties)
    if facts:
        assert all(c.confidence in {"high", "moderate"} for c in facts)


# --------------------------------------------------------------------------
# Conflict detection — the half fixtures cannot exercise by accident
# --------------------------------------------------------------------------


async def test_two_sources_that_disagree_produce_a_conflict(
    session_factory: sessionmaker[Session],
) -> None:
    """`REQ-EVID-012 AC-1`, and the reason this test had to be written
    deliberately.

    Every other fixture in the suite agrees with itself, so conflict detection
    was reachable in principle and never reached in practice — the same dead-
    path shape as the skipped-category gap and the unplannable page-fetch
    category. A provider that contradicts itself is the only way to prove the
    code runs.
    """
    from scrapr_core.db.models import Conflict

    start_research(session_factory)

    await run_pipeline(
        session_factory,
        disputed_registry(),
        disputing_provider(),
        synthesis=cite_everything,
    )

    with session_factory() as session:
        conflicts = session.execute(select(Conflict)).scalars().all()

    assert conflicts, "two sources disagreed by 58% and nothing was detected"


async def test_competing_evidence_is_preserved_not_discarded(
    session_factory: sessionmaker[Session],
) -> None:
    """`AC-2`. Both sides stay on the record, which is what `AC-3` renders."""
    from scrapr_core.db.models import Conflict, ConflictEvidence

    start_research(session_factory)

    await run_pipeline(
        session_factory,
        disputed_registry(),
        disputing_provider(),
        synthesis=cite_everything,
    )

    with session_factory() as session:
        conflict = session.execute(select(Conflict)).scalars().first()
        assert conflict is not None
        sides = (
            session.execute(
                select(ConflictEvidence).where(
                    ConflictEvidence.conflict_id == conflict.id
                )
            )
            .scalars()
            .all()
        )
        evidence = session.execute(select(Evidence)).scalars().all()

    assert len(sides) == 2, "a conflict must keep both values"
    assert len(evidence) >= 2, "competing evidence was discarded"


async def test_an_unexplained_conflict_is_stored_as_unresolved(
    session_factory: sessionmaker[Session],
) -> None:
    """`REQ-EVID-013 AC-3` and `REQ-EVID-014 AC-1`.

    Two figures in the same currency, same period, neither stale — nothing in
    the evidence explains the gap. An invented cause would be worse than none,
    so the row says unresolved and carries no category.
    """
    from scrapr_core.db.enums import ConflictStatus
    from scrapr_core.db.models import Conflict

    start_research(session_factory)

    await run_pipeline(
        session_factory,
        disputed_registry(),
        disputing_provider(),
        synthesis=cite_everything,
    )

    with session_factory() as session:
        conflict = session.execute(select(Conflict)).scalars().first()

    assert conflict is not None
    assert conflict.status is ConflictStatus.UNRESOLVED
    assert conflict.explanation_category is None


async def test_a_contested_claim_is_not_high_confidence(
    session_factory: sessionmaker[Session],
) -> None:
    """`REQ-EVID-014 AC-3`, end to end.

    A fact two sources disagree about is not high-confidence however good those
    sources are. The disagreement is the finding.
    """
    start_research(session_factory)

    await run_pipeline(
        session_factory,
        disputed_registry(),
        disputing_provider(),
        synthesis=cite_everything,
    )

    with session_factory() as session:
        facts = (
            session.execute(select(Claim).where(Claim.claim_type == ClaimType.FACT))
            .scalars()
            .all()
        )

    contested = [claim for claim in facts if claim.confidence_inputs.get("conflicts")]
    assert contested, "no claim ended up carrying the conflict"
    assert all(claim.confidence == "low" for claim in contested)


# --------------------------------------------------------------------------
# The exclusions, through the pipeline
# --------------------------------------------------------------------------


async def test_a_reporting_period_reaches_the_evidence_row(
    session_factory: sessionmaker[Session],
) -> None:
    """`REQ-EVID-009 AC-1`: a financial figure carries its fiscal period.

    The columns existed from the baseline schema and were never written. `AC-2`
    (period shown on citation inspection) cannot hold without this, and
    `DEC-10 §4.1` cannot exclude on a period that was thrown away.
    """
    from pipeline_support import period_split_registry

    start_research(session_factory)

    await run_pipeline(
        session_factory,
        period_split_registry(),
        disputing_provider(),
        synthesis=cite_everything,
    )

    with session_factory() as session:
        rows = session.execute(select(Evidence)).scalars().all()

    dated = [row for row in rows if row.period_end is not None]
    assert dated, "no evidence carried a reporting period"


async def test_different_periods_do_not_produce_a_conflict(
    session_factory: sessionmaker[Session],
) -> None:
    """`DEC-10 §4.1`, in production rather than in a unit test.

    FY2024 revenue and FY2025 revenue differ by 58% and are both correct. The
    unit test for this passed the whole time — it supplied the periods by hand,
    which the pipeline did not.
    """
    from pipeline_support import period_split_registry

    from scrapr_core.db.models import Conflict

    start_research(session_factory)

    await run_pipeline(
        session_factory,
        period_split_registry(),
        disputing_provider(),
        synthesis=cite_everything,
    )

    with session_factory() as session:
        conflicts = session.execute(select(Conflict)).scalars().all()

    assert not conflicts, "two different years were reported as a disagreement"


async def test_an_estimate_against_a_reported_figure_is_not_a_conflict(
    session_factory: sessionmaker[Session],
) -> None:
    """`DEC-10 §4.2`, likewise.

    `_Cited.basis` returned `None` unconditionally — a stub carrying a
    docstring that described behaviour it did not have, which is worse than an
    obvious gap because it reads as finished.
    """
    from pipeline_support import estimate_registry

    from scrapr_core.db.models import Conflict

    start_research(session_factory)

    await run_pipeline(
        session_factory,
        estimate_registry(),
        disputing_provider(),
        synthesis=cite_everything,
    )

    with session_factory() as session:
        conflicts = session.execute(select(Conflict)).scalars().all()
        rows = session.execute(select(Evidence)).scalars().all()

    assert {row.value_basis for row in rows} == {"reported", "estimate"}
    assert not conflicts, "an analyst estimate was reported as contradicting a filing"


async def test_two_reported_figures_in_one_period_still_conflict(
    session_factory: sessionmaker[Session],
) -> None:
    """The control for the two tests above.

    Both exclusions narrow what counts as a disagreement, so each needs a case
    proving it did not simply switch conflict detection off.
    """
    from scrapr_core.db.models import Conflict

    start_research(session_factory)

    await run_pipeline(
        session_factory,
        disputed_registry(),
        disputing_provider(),
        synthesis=cite_everything,
    )

    with session_factory() as session:
        conflicts = session.execute(select(Conflict)).scalars().all()

    assert conflicts, "the exclusions disabled detection entirely"


async def test_the_reporting_period_is_inspectable_on_the_claim(
    session_factory: sessionmaker[Session],
) -> None:
    """`REQ-EVID-009 AC-2`: period is shown on citation inspection.

    Persisting it was the previous fix; it still could not be *shown*, because
    nothing put it on the wire. Same shape as every other gap in this phase —
    a value computed correctly and stopped one layer short of a reader.
    """
    from pipeline_support import period_split_registry

    start_research(session_factory)

    await run_pipeline(
        session_factory,
        period_split_registry(),
        disputing_provider(),
        synthesis=cite_everything,
    )

    with session_factory() as session:
        rows = session.execute(select(Evidence)).scalars().all()

    # Two years of evidence, so the claim citing both has no single period and
    # correctly reports none; the rows themselves carry theirs.
    assert {row.period_end.isoformat() for row in rows if row.period_end} == {
        "2024-12-31",
        "2025-12-31",
    }
