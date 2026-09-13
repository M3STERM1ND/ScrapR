"""The first real research run, replayed through the real pipeline.

"Analyze NVIDIA as a company, investment opportunity, and potential employer."
The run used Anthropic and Tavily, and produced a report holding one revenue
figure and one Glassdoor rating, reported as disagreeing. Five defects made
that report, and each has a test here against the persisted rows:

1. **Fixture data reached a real run** — a canned "$1.2bn revenue" item on
   `reuters.com` stood in for filings and was cited as a Reuters report.
   (The registry half is `services/worker/tests/test_worker_wiring.py`.)
2. **Tavily evidence was lost after retrieval** — extraction blocks were
   numbered from the question, so a model citing the number it saw pointed
   every excerpt two blocks away, and grounding dropped them.
3. **A blocked specialized provider left its questions unanswered** — FMP
   answered 403 and nothing else was asked.
4. **Revenue and an employee rating were compared** and published as a
   conflict.
5. **Later rounds repeated the first round's query** and were answered from the
   cache, so no round after the first could find anything.

Nothing here reaches the network. Tavily is the real tool over a mock
transport, and the model is a stand-in that reads the rendered envelope and
cites blocks by the number printed in their headers — which is what a real
model does, and exactly the behaviour the numbering bug punished.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any, final
from uuid import UUID

import httpx
import pytest
from pipeline_support import cite_everything, run_pipeline
from pydantic import BaseModel
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from scrapr_core.db.models import (
    Base,
    Conflict,
    Evidence,
    QuestionEvidence,
    QuestionState,
    ResearchQuestion,
    ResearchRun,
    RunStep,
    Source,
    ToolInvocation,
)
from scrapr_core.db.repositories import (
    AnonymousSessionRepository,
    ResearchRepository,
    RunRepository,
)
from scrapr_core.domain.ownership import OwnerContext
from scrapr_core.llm import FakeLLMProvider, ModelTier
from scrapr_core.llm.contract import StructuredResult, TokenUsage, UntrustedDocument
from scrapr_core.llm.envelope import render_untrusted
from scrapr_core.orchestrator.extract import ExtractedEvidence, Extraction
from scrapr_core.orchestrator.interpret import Interpretation
from scrapr_core.orchestrator.pipeline import STAGES
from scrapr_core.orchestrator.plan import _PlanDraft
from scrapr_core.security.trust import Trusted
from scrapr_core.tools import ToolCategory, ToolRegistry
from scrapr_core.tools.contract import ToolOutcome, ToolRequest
from scrapr_core.tools.impl import FailingFixtureTool, FixtureTool
from scrapr_core.tools.impl.tavily import search_tool

pytestmark = pytest.mark.integration

OBJECTIVE = (
    "Analyze NVIDIA as a company, investment opportunity, and potential employer. "
    "Evaluate its recent financial performance, growth prospects, major risks, "
    "competitive position, and current job opportunities."
)
SUBJECT = "NVIDIA Corporation (NVDA)"

FINANCIAL_QUESTIONS = (
    "What has been NVIDIA's revenue, net income, and gross margin trend over the past several fiscal years?",
    "How has NVIDIA's data center segment revenue grown compared to other segments?",
    "What is NVIDIA's compensation, benefits, and workplace culture reputation according to employee reviews?",
)

GLASSDOOR = {
    "title": 'NVIDIA "work life balance" Reviews | Glassdoor',
    "url": "https://www.glassdoor.com/Reviews/NVIDIA-Reviews-E7633.htm",
    "content": (
        "NVIDIA employees rate their compensation and benefits as 4.5 out of 5 "
        "according to anonymously submitted Glassdoor reviews."
    ),
}
CNBC = {
    "title": "Nvidia fiscal 2025 results",
    "url": "https://www.cnbc.com/2025/02/26/nvidia-earnings.html",
    "content": "NVIDIA reported revenue of $130.5 billion for fiscal 2025, up 114% from a year ago.",
}
REUTERS = {
    "title": "Nvidia data center sales",
    "url": "https://www.reuters.com/technology/nvidia-data-center-2025-02-26/",
    "content": "NVIDIA said data center revenue reached $115.2 billion in fiscal 2025.",
}

FMP_403 = (
    "https://financialmodelingprep.com/api/v3/search?query=NVIDIA&apikey=secret-fmp-key "
    "returned 403: Legacy Endpoint : Due to Legacy endpoints being no longer supported"
)


# --------------------------------------------------------------------------
# The stand-ins
# --------------------------------------------------------------------------


@final
@dataclass
class Recording:
    """A tool wrapper that keeps the parameters each call was sent."""

    inner: Any
    params: list[dict[str, Any]] = field(default_factory=list)

    @property
    def name(self) -> str:
        return str(self.inner.name)

    @property
    def category(self) -> ToolCategory:
        return self.inner.category  # type: ignore[no-any-return]

    async def invoke(self, request: ToolRequest) -> ToolOutcome:
        self.params.append(dict(request.params))
        return await self.inner.invoke(request)  # type: ignore[no-any-return]


def tavily_transport() -> httpx.MockTransport:
    """Tavily, answering the first round thinly and a follow-up round well.

    A first-round query (the `subject: question` form) finds only the Glassdoor
    page, which tiers `LOWER` and cannot resolve a question on its own. A
    follow-up query that looks past Glassdoor finds the two publisher pages.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "glassdoor.com" in body.get("exclude_domains", []):
            return httpx.Response(200, json={"results": [CNBC, REUTERS]})
        return httpx.Response(200, json={"results": [GLASSDOOR]})

    return httpx.MockTransport(handler)


_BLOCK = re.compile(
    r"<<(UNTRUSTED-[0-9a-f]+) #(\S+) label=.*?>>\n(.*?)\n<<END \1 #\2>>", re.S
)


class EnvelopeReadingModel(FakeLLMProvider):
    """Interpret, plan and synthesis are canned; extraction reads the prompt.

    For each block it quotes the block's first sentence verbatim and cites the
    block by **the number printed in its envelope header** — the only number a
    real model can see. Under the old positional numbering that number was two
    higher than the item's index.
    """

    async def complete_structured[T: BaseModel](
        self,
        instruction: Trusted,
        untrusted: Sequence[UntrustedDocument],
        schema: type[T],
        model_tier: ModelTier = ModelTier.STANDARD,
    ) -> StructuredResult[T]:
        if schema is not Extraction:
            return await super().complete_structured(instruction, untrusted, schema, model_tier)

        found: list[ExtractedEvidence] = []
        for _, number, body in _BLOCK.findall(render_untrusted(untrusted)):
            if not number.isdigit():
                continue  # the question block
            sentence = re.split(r"(?<=[.!?])\s", body.strip(), maxsplit=1)[0]
            found.append(
                ExtractedEvidence(statement=sentence, excerpt=sentence, item_index=int(number))
            )
        return StructuredResult(
            value=Extraction(evidence=found),  # type: ignore[arg-type]
            model="envelope-reader",
            tier=model_tier,
            usage=TokenUsage(),
        )


def nvidia_model() -> EnvelopeReadingModel:
    model = EnvelopeReadingModel()
    model.enqueue(
        Interpretation(
            subject=SUBJECT, interpretation_note=None, questions=list(FINANCIAL_QUESTIONS)
        ),
        _PlanDraft(
            areas=[
                # The NVIDIA plan's shape for this area: financial data and
                # filings, no web search.
                _PlanDraft.Area(
                    name="Financial Performance",
                    questions=list(FINANCIAL_QUESTIONS),
                    tool_categories=["financial", "filings"],
                )
            ]
        ),
    )
    return model


@final
@dataclass
class NvidiaRegistry:
    registry: ToolRegistry
    financial: Recording
    filings: Recording
    web: Recording


def nvidia_registry() -> NvidiaRegistry:
    """FMP blocked, EDGAR returning nothing usable, Tavily working."""
    financial = Recording(
        FailingFixtureTool(
            name="fmp_financial",
            category=ToolCategory.FINANCIAL,
            kind="blocked",
            message=FMP_403,
        )
    )
    filings = Recording(FixtureTool(name="sec_edgar", category=ToolCategory.FILINGS, items=()))
    web = Recording(
        search_tool(
            "tv-test",
            client_factory=lambda _: httpx.AsyncClient(transport=tavily_transport()),
        )
    )
    registry = ToolRegistry()
    for tool in (financial, filings, web):
        registry.register(tool)
    registry.freeze()
    return NvidiaRegistry(registry=registry, financial=financial, filings=filings, web=web)


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


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


def start_research(session_factory: sessionmaker[Session]) -> UUID:
    with session_factory() as session:
        owner = OwnerContext.for_anonymous(
            AnonymousSessionRepository(session).issue().session.id
        )
        research = ResearchRepository(session, owner)
        created = research.create_session(objective=OBJECTIVE)
        version = research.open_version(created.id)
        assert version is not None
        RunRepository(session).create_run(created.id, version.id, STAGES)
        session.commit()
        return created.id


@pytest.fixture
async def nvidia_run(session_factory: sessionmaker[Session]) -> NvidiaRegistry:
    start_research(session_factory)
    tools = nvidia_registry()
    await run_pipeline(
        session_factory, tools.registry, nvidia_model(), synthesis=cite_everything
    )
    return tools


def question_hosts(session: Session, question_text: str) -> set[str]:
    rows = session.execute(
        select(Source.url)
        .join(Evidence, Evidence.source_id == Source.id)
        .join(QuestionEvidence, QuestionEvidence.evidence_id == Evidence.id)
        .join(ResearchQuestion, ResearchQuestion.id == QuestionEvidence.question_id)
        .where(ResearchQuestion.text == question_text)
    ).scalars()
    return {httpx.URL(url).host for url in rows if url}


# --------------------------------------------------------------------------
# 2. Tavily evidence survives extraction and grounding
# --------------------------------------------------------------------------


async def test_tavily_evidence_is_grounded_and_stored(
    session_factory: sessionmaker[Session], nvidia_run: NvidiaRegistry
) -> None:
    with session_factory() as session:
        stored = {
            (source.url, evidence.content)
            for evidence, source in session.execute(
                select(Evidence, Source).join(Source, Source.id == Evidence.source_id)
            ).all()
        }

    assert (GLASSDOOR["url"], GLASSDOOR["content"]) in stored
    assert (CNBC["url"], CNBC["content"]) in stored
    assert (REUTERS["url"], REUTERS["content"]) in stored


async def test_nothing_was_dropped_for_pointing_at_the_wrong_block(
    session_factory: sessionmaker[Session], nvidia_run: NvidiaRegistry
) -> None:
    with session_factory() as session:
        run = session.execute(select(ResearchRun)).scalar_one()
        step = session.execute(
            select(RunStep).where(RunStep.run_id == run.id, RunStep.stage == "research")
        ).scalar_one()

    retrieval = run.effort_used["retrieval"]
    assert retrieval["items_returned"] > 0
    assert retrieval["evidence_grounded"] == retrieval["evidence_candidates"] > 0
    assert retrieval.get("dropped_out_of_range", 0) == 0
    assert retrieval.get("dropped_excerpt_not_in_source", 0) == 0

    area = step.checkpoint["areas"][0]
    assert area["retrieval"]["grounded"] == retrieval["evidence_grounded"]
    assert area["retrieval"]["dropped"] == {}


# --------------------------------------------------------------------------
# 3. A failed specialized provider triggers web-search fallback
# --------------------------------------------------------------------------


async def test_a_blocked_financial_provider_falls_back_to_web_search(
    session_factory: sessionmaker[Session], nvidia_run: NvidiaRegistry
) -> None:
    """The plan chose financial data and filings only. FMP is blocked and
    EDGAR has nothing; web search is asked for every question anyway."""
    assert nvidia_run.web.params, "web search was never asked"

    with session_factory() as session:
        questions = session.execute(select(ResearchQuestion)).scalars().all()
        step = session.execute(select(RunStep).where(RunStep.stage == "research")).scalar_one()

    assert all(q.tool_categories == ["financial", "filings"] for q in questions)
    assert not any(q.resolution_state is QuestionState.UNANSWERABLE for q in questions)
    for question in FINANCIAL_QUESTIONS:
        with session_factory() as session:
            assert question_hosts(session, question), f"no evidence for {question!r}"
    assert step.checkpoint["areas"][0]["retrieval"]["fallbacks"] >= len(FINANCIAL_QUESTIONS)


async def test_the_blocked_call_is_diagnosable_and_not_repeated(
    session_factory: sessionmaker[Session], nvidia_run: NvidiaRegistry
) -> None:
    """One 403 for the whole run. The original run sent six: three questions,
    each asked again in round two. Every question now sends FMP the same
    structured request, and a `blocked` answer is cached for the run, so the
    other five come from the cache. The reason is on record; the key is not."""
    with session_factory() as session:
        invocations = session.execute(select(ToolInvocation)).scalars().all()

    blocked = [row for row in invocations if row.tool_category == "financial"]
    assert len(blocked) == 1
    assert len(nvidia_run.financial.params) == 1
    assert all(row.error_kind == "blocked" for row in blocked)
    assert all(row.error_detail and "Legacy Endpoint" in row.error_detail for row in blocked)
    assert not any("secret-fmp-key" in (row.error_detail or "") for row in blocked)
    assert all(row.result_items is None for row in blocked)

    searches = [row for row in invocations if row.tool_category == "web_search"]
    assert searches and all(row.result_items for row in searches)


async def test_structured_providers_received_the_company_not_the_question(
    nvidia_run: NvidiaRegistry,
) -> None:
    for recorded in (nvidia_run.financial, nvidia_run.filings):
        assert recorded.params
        for params in recorded.params:
            assert params == {"query": "NVIDIA", "symbol": "NVDA"}
    # EDGAR answered successfully with nothing, which is cached like any
    # success: one call serves every question.
    assert len(nvidia_run.filings.params) == 1


# --------------------------------------------------------------------------
# 4. Revenue against a Glassdoor rating is not a conflict
# --------------------------------------------------------------------------


async def test_revenue_and_a_glassdoor_rating_are_not_a_conflict(
    session_factory: sessionmaker[Session], nvidia_run: NvidiaRegistry
) -> None:
    """`cite_everything` puts the revenue, the segment revenue and the rating
    on one claim — the exact shape of the claim that carried the conflict."""
    with session_factory() as session:
        contents = set(session.execute(select(Evidence.content)).scalars())
        conflicts = session.execute(select(Conflict)).scalars().all()

    assert CNBC["content"] in contents and GLASSDOOR["content"] in contents
    assert conflicts == [], "unrelated figures were reported as disagreeing"


# --------------------------------------------------------------------------
# 5. Later rounds target the gap with different queries
# --------------------------------------------------------------------------


async def test_later_rounds_send_different_gap_targeted_queries(
    session_factory: sessionmaker[Session], nvidia_run: NvidiaRegistry
) -> None:
    by_question: dict[str, list[dict[str, Any]]] = {}
    for params in nvidia_run.web.params:
        query = str(params["query"])
        key = next(
            (q for q in FINANCIAL_QUESTIONS if q in query),
            None,
        )
        if key is not None:
            by_question.setdefault(key, []).append(params)

    follow_ups = [
        params for params in nvidia_run.web.params if not any(q in str(params["query"]) for q in FINANCIAL_QUESTIONS)
    ]

    # Round one asked each question in full.
    assert set(by_question) == set(FINANCIAL_QUESTIONS)
    # Round two asked something else: no follow-up repeats a first-round query,
    # each one looks past the source the question already had, and each asks
    # for authority because that source was low-tier.
    assert len(follow_ups) == len(FINANCIAL_QUESTIONS)
    first_round = {json.dumps(p, sort_keys=True) for group in by_question.values() for p in group}
    for params in follow_ups:
        assert json.dumps(params, sort_keys=True) not in first_round
        assert params["exclude_domains"] == ["glassdoor.com"]
        assert str(params["query"]).startswith("NVIDIA ")
        assert "investor relations" in str(params["query"])
    assert len({str(params["query"]) for params in follow_ups}) == len(FINANCIAL_QUESTIONS)

    # And the follow-up found what the first round could not.
    with session_factory() as session:
        for question in FINANCIAL_QUESTIONS:
            assert {"www.glassdoor.com", "www.cnbc.com", "www.reuters.com"} <= question_hosts(
                session, question
            )
        step = session.execute(select(RunStep).where(RunStep.stage == "research")).scalar_one()
    assert step.checkpoint["areas"][0]["rounds"] == 2
