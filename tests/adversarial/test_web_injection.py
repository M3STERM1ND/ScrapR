"""The injection corpus pushed through web research and follow-up questions (`REQ-SEC-012..015`).

The Phase 8 hardening pass. Phase 4 proved the corpus inert inside uploaded
documents; this proves it inert on the other two roads untrusted text takes into
the product: a web page that research retrieves, and evidence a follow-up
question is answered from.

Every payload runs the whole pipeline — interpret, plan, research, synthesize,
through the real step runner — and the assertions are about behaviour, not
wording (`REQ-SEC-014 AC-1`): what reached the instruction channel, which
tools were called, what the activity feed said, what the report claimed.
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import pytest
from pipeline_support import run_pipeline, scripted_provider, search_tool
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session, sessionmaker
from tests.adversarial.payloads import PAYLOADS, Payload

from scrapr_core.db.enums import ClaimType
from scrapr_core.db.models import ActivityEvent, Base, Claim, ToolInvocation
from scrapr_core.db.repositories import (
    AnonymousSessionRepository,
    ResearchRepository,
    RunRepository,
)
from scrapr_core.domain.ownership import OwnerContext
from scrapr_core.llm import FakeLLMProvider
from scrapr_core.orchestrator.converse import (
    Answer,
    ConversationContext,
    EvidenceRef,
    answer_question,
)
from scrapr_core.orchestrator.pipeline import STAGES
from scrapr_core.tools import ToolRegistry

pytestmark = [pytest.mark.adversarial, pytest.mark.integration]

QUESTION = "What is Acme's revenue?"
AREAS = (("Financial performance", (QUESTION,), ("web_search",)),)
LEGITIMATE = "Acme Corp reported revenue of $1.2bn for fiscal 2025, up 18%."
EXPECTED_LABELS = {
    "Understanding the objective",
    "Identifying research areas",
    "Researching financial performance",
    "Building the report",
}


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


def hostile_registry(payload: Payload) -> ToolRegistry:
    """Web search returning a page that carries the payload beside real content."""
    registry = ToolRegistry()
    registry.register(
        search_tool(
            texts=(f"{LEGITIMATE}\n\n{payload.text}", f"{payload.text}\n\nAcme Corp listed 40 open engineering roles in January."),
        )
    )
    registry.freeze()
    return registry


@pytest.mark.parametrize("payload", PAYLOADS, ids=lambda item: item.name)
async def test_a_hostile_page_changes_nothing_about_the_research(
    session_factory: sessionmaker[Session], payload: Payload
) -> None:
    with session_factory() as session:
        owner = OwnerContext.for_anonymous(AnonymousSessionRepository(session).issue().session.id)
        research = ResearchRepository(session, owner)
        created = research.create_session(objective="How is Acme Corp performing?")
        version = research.open_version(created.id)
        assert version is not None
        run = RunRepository(session).create_run(created.id, version.id, STAGES)
        session.commit()
        session_id, run_id = created.id, run.id

    provider = scripted_provider("Acme Corp", (QUESTION,), AREAS)
    await run_pipeline(session_factory, hostile_registry(payload), provider)

    # `REQ-SEC-012 AC-1`, `REQ-SEC-013 AC-2`: the payload reached the model only
    # as fenced material, never in the instruction.
    extraction_calls = [call for call in provider.calls if payload.marker in call.rendered]
    assert extraction_calls, "the payload never reached the model; the test exercises nothing"
    for call in provider.calls:
        assert payload.marker not in call.instruction, f"{payload.name}: {payload.goal}"

    with session_factory() as session:
        # `REQ-SEC-014 AC-1`, `REQ-SEC-015 AC-1`: tool usage is what the plan
        # chose, whatever the page asked for.
        categories = set(
            session.execute(
                select(ToolInvocation.tool_category).where(ToolInvocation.run_id == run_id)
            ).scalars()
        )
        assert categories == {"web_search"}
        domains = set(
            session.execute(
                select(ToolInvocation.source_domain).where(ToolInvocation.run_id == run_id)
            ).scalars()
        )
        assert domains <= {None}, "a page caused a fetch of an address it named"

        # The activity feed is the application's words only.
        labels = set(
            session.execute(select(ActivityEvent.label).where(ActivityEvent.session_id == session_id)).scalars()
        )
        assert labels <= EXPECTED_LABELS

        # `REQ-SEC-014 AC-1`: output claims are unaltered by the payload.
        claims = session.execute(select(Claim)).scalars().all()
        assert claims
        assert all(payload.marker not in claim.text for claim in claims)
        assert all(
            claim.confidence in {None, "low", "moderate", "high"} for claim in claims
        ), "a payload forged a confidence value"


@pytest.mark.parametrize("payload", PAYLOADS, ids=lambda item: item.name)
async def test_a_follow_up_over_hostile_evidence_keeps_the_instruction_ours(payload: Payload) -> None:
    """Evidence gathered earlier is still untrusted when a question is asked about it."""
    provider = FakeLLMProvider()
    provider.enqueue(Answer(text="Revenue was $1.2bn.", claim_type=ClaimType.FACT, evidence_ids=[]))
    context = ConversationContext(
        subject="Acme Corp",
        objective="How is Acme Corp performing?",
        evidence=[
            EvidenceRef(
                evidence_id=uuid4(),
                statement=f"Acme reported revenue. {payload.text}",
                excerpt=payload.text,
                source_name="acme-news.example",
            )
        ],
    )

    await answer_question("What was revenue?", context, provider)

    assert provider.calls
    for call in provider.calls:
        assert payload.marker not in call.instruction, f"{payload.name}: {payload.goal}"
    assert any(payload.marker in call.rendered for call in provider.calls)


@pytest.mark.parametrize("payload", PAYLOADS, ids=lambda item: item.name)
async def test_a_hostile_question_is_material_too(payload: Payload) -> None:
    """A reader's own question is untrusted input (`REQ-SEC-012 AC-2`)."""
    provider = FakeLLMProvider()
    provider.enqueue(Answer(text="I can only answer from the research.", claim_type=ClaimType.UNCERTAINTY))
    context = ConversationContext(subject="Acme Corp", objective="How is Acme Corp performing?", evidence=[])

    await answer_question(payload.text, context, provider)

    for call in provider.calls:
        assert payload.marker not in call.instruction
