"""Driving the research pipeline in tests, without a real model or provider.

Two suites need this: the core integration tests and the API tests. The API's
version of the flow was previously a copy, and a copy of a test harness drifts
exactly as fast as a copy of production code.

**Synthesis is primed after research has run.** It has to cite real evidence
ids, and those do not exist until retrieval and extraction have written rows —
which is also what a real provider would be doing at that point: reading what
was actually gathered.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from scrapr_core.db.enums import ClaimType, SourceCategory
from scrapr_core.db.models import Evidence
from scrapr_core.jobs import JobRunner
from scrapr_core.llm import FakeLLMProvider
from scrapr_core.orchestrator.extract import ExtractedEvidence, Extraction
from scrapr_core.orchestrator.interpret import Interpretation
from scrapr_core.orchestrator.pipeline import build_handlers
from scrapr_core.orchestrator.plan import _PlanDraft
from scrapr_core.orchestrator.synthesize import DraftClaim, DraftSection, SynthesisDraft
from scrapr_core.tools import ToolCategory, ToolRegistry
from scrapr_core.tools.impl import FixtureTool, fixture_item

__all__ = [
    "DISPUTED",
    "REVENUE_EXCERPT",
    "REVENUE_TEXT",
    "cite_everything",
    "default_synthesis",
    "disputed_registry",
    "disputing_provider",
    "estimate_registry",
    "fixture_registry",
    "period_split_registry",
    "run_pipeline",
    "scripted_provider",
    "search_tool",
    "trend_provider",
    "trend_registry",
]

REVENUE_TEXT = "Acme Corp reported revenue of $1.2bn for fiscal 2025, up 18%."
REVENUE_EXCERPT = "revenue of $1.2bn"
HIRING_TEXT = "Acme Corp listed 40 open engineering roles in January."


def search_tool(
    name: str = "fixture_search",
    category: ToolCategory = ToolCategory.WEB_SEARCH,
    texts: Sequence[str] = (REVENUE_TEXT, HIRING_TEXT),
    host: str = "reuters.com",
) -> FixtureTool:
    """A provider returning distinct sources.

    Distinct matters: `MIN_SOURCES_PER_QUESTION` is two, so a fixture returning
    one source would leave every question open and make every test a ceiling
    test by accident.

    The host matters too, now that `DEC-08` tiers on it. A question resolves on
    two distinct sources of which **at least one is above `LOWER`**, so a
    fixture on an unlisted domain leaves every question open — which is correct
    behaviour and a useless default for a test about something else. These
    hosts are on the publisher allowlist, which is what real web search
    returns some of.
    """
    return FixtureTool(
        name=name,
        category=category,
        items=tuple(
            fixture_item(
                source_name=f"{host} result {index}",
                text=body,
                source_url=f"https://{host}/{name}/{index}",
                source_category=SourceCategory.WEB,
            )
            for index, body in enumerate(texts)
        ),
    )


def fixture_registry() -> ToolRegistry:
    """Web search and news, each with two sources."""
    registry = ToolRegistry()
    registry.register(search_tool())
    registry.register(
        search_tool(
            name="fixture_news", category=ToolCategory.NEWS, host="apnews.com"
        )
    )
    registry.freeze()
    return registry


def scripted_provider(
    subject: str,
    questions: Sequence[str],
    areas: Sequence[tuple[str, Sequence[str], Sequence[str]]],
) -> FakeLLMProvider:
    """A provider primed for interpret and plan, with standing extraction.

    Extraction gets a standing answer because the number of extraction calls
    depends on how many rounds the termination gate decides to run — which is
    the behaviour under test, not something a fixture should pin down.
    """
    provider = FakeLLMProvider(
        standing_response=Extraction(
            evidence=[
                ExtractedEvidence(
                    statement="Acme reported $1.2bn revenue for FY2025.",
                    excerpt=REVENUE_EXCERPT,
                    item_index=0,
                ),
                ExtractedEvidence(
                    statement="Acme listed 40 open engineering roles.",
                    excerpt="40 open engineering roles",
                    item_index=1,
                ),
            ]
        )
    )
    provider.enqueue(
        Interpretation(
            subject=subject,
            interpretation_note=None,
            questions=list(questions),
        ),
        _PlanDraft(
            areas=[
                _PlanDraft.Area(
                    name=name,
                    questions=list(area_questions),
                    tool_categories=list(categories),
                )
                for name, area_questions, categories in areas
            ]
        ),
    )
    return provider


def default_synthesis(evidence_ids: Sequence[UUID]) -> SynthesisDraft:
    """A report citing the first piece of evidence that was gathered."""
    cited = [str(evidence_ids[0])] if evidence_ids else []
    return SynthesisDraft(
        summary=[
            DraftClaim(
                text="Acme grew revenue 18% in FY2025 while hiring.",
                claim_type=ClaimType.FACT,
                evidence_ids=cited,
                is_important=True,
            )
        ],
        sections=[
            DraftSection(
                title="Revenue",
                claims=[
                    DraftClaim(
                        text="Acme reported $1.2bn revenue for FY2025.",
                        claim_type=ClaimType.FACT,
                        evidence_ids=cited,
                    )
                ],
            )
        ],
    )


async def run_pipeline(
    session_factory: sessionmaker[Session],
    registry: ToolRegistry,
    provider: FakeLLMProvider,
    synthesis: Callable[[Sequence[UUID]], SynthesisDraft] = default_synthesis,
    worker_id: str = "pipeline-test",
) -> None:
    """Run all four steps, priming synthesis once the evidence exists."""
    runner = JobRunner(
        session_factory, build_handlers(provider, registry), worker_id=worker_id
    )

    await runner.run_one()  # interpret
    await runner.run_one()  # plan
    await runner.run_one()  # research

    with session_factory() as session:
        evidence_ids = list(
            session.execute(select(Evidence.id).order_by(Evidence.extracted_at))
            .scalars()
            .all()
        )
    provider.enqueue(synthesis(evidence_ids))

    await runner.run_one()  # synthesize


# --------------------------------------------------------------------------
# A run whose sources contradict each other
# --------------------------------------------------------------------------

DISPUTED = (
    "Acme Corp reported revenue of USD 1.2bn for fiscal 2025.",
    "Acme Corp reported revenue of USD 1.9bn for fiscal 2025.",
)
"""Two sources, same metric, same period, same currency, 58% apart.

Every other fixture here agrees with itself, which means conflict detection is
reachable in principle and never reached in practice. Proving that code runs
takes a provider that contradicts itself, and it lives in the shared harness
rather than in one suite because two suites now need it — the core integration
tests and the API contract tests.
"""

DISPUTED_QUESTION = "What is Acme's revenue?"


def disputed_registry() -> ToolRegistry:
    """Web search returning two irreconcilable figures."""
    registry = ToolRegistry()
    registry.register(search_tool(texts=DISPUTED, host="reuters.com"))
    registry.freeze()
    return registry


def disputing_provider() -> FakeLLMProvider:
    """A provider that extracts both figures rather than the standing pair.

    `scripted_provider` carries a fixed standing extraction, so whatever a
    fixture publishes, the evidence written is the same two sentences — and the
    grounding check drops anything else. Conflict detection compares the
    numbers *in the evidence*, so exercising it needs extraction that actually
    reads the disputed figures.
    """
    provider = FakeLLMProvider(
        standing_response=Extraction(
            evidence=[
                ExtractedEvidence(
                    statement=DISPUTED[0],
                    excerpt="revenue of USD 1.2bn",
                    item_index=0,
                ),
                ExtractedEvidence(
                    statement=DISPUTED[1],
                    excerpt="revenue of USD 1.9bn",
                    item_index=1,
                ),
            ]
        )
    )
    provider.enqueue(
        Interpretation(
            subject="Acme Corp",
            interpretation_note=None,
            questions=[DISPUTED_QUESTION],
        ),
        _PlanDraft(
            areas=[
                _PlanDraft.Area(
                    name="Financial performance",
                    questions=[DISPUTED_QUESTION],
                    tool_categories=["web_search"],
                )
            ]
        ),
    )
    return provider


def cite_everything(evidence_ids: Sequence[UUID]) -> SynthesisDraft:
    """A report whose single fact cites every piece of evidence gathered.

    Conflict detection compares the values *one claim* cites, so the default
    draft — which cites only the first — can never surface a disagreement no
    matter how badly two sources contradict each other. Being explicit about
    that is the difference between testing the detector and testing the
    fixture.
    """
    return SynthesisDraft(
        summary=[
            DraftClaim(
                text="Acme reported revenue for FY2025.",
                claim_type=ClaimType.FACT,
                evidence_ids=[str(identifier) for identifier in evidence_ids],
                is_important=True,
            )
        ],
        sections=[],
    )


def period_split_registry() -> ToolRegistry:
    """Two figures that differ because they cover different years.

    `DEC-10 §4.1` says this is not a conflict, and until the reporting period
    was actually persisted that exclusion could not fire — the comparison had
    no period to exclude on, so FY2024 and FY2025 revenue looked like two
    sources contradicting each other.
    """
    registry = ToolRegistry()
    registry.register(
        FixtureTool(
            name="fixture_periods",
            category=ToolCategory.WEB_SEARCH,
            items=(
                fixture_item(
                    source_name="reuters.com FY2024",
                    text="Acme Corp reported revenue of USD 1.2bn for fiscal 2024.",
                    source_url="https://reuters.com/acme/fy2024",
                    structured={"period": "2024", "currency": "USD", "basis": "reported"},
                ),
                fixture_item(
                    source_name="reuters.com FY2025",
                    text="Acme Corp reported revenue of USD 1.9bn for fiscal 2025.",
                    source_url="https://reuters.com/acme/fy2025",
                    structured={"period": "2025", "currency": "USD", "basis": "reported"},
                ),
            ),
        )
    )
    registry.freeze()
    return registry


def estimate_registry() -> ToolRegistry:
    """A filed figure and an analyst estimate of the same period.

    `DEC-10 §4.2`: an estimate disagreeing with a reported value is not a
    source being wrong. `REQ-TOOL-004 AC-3` is what makes it detectable, and
    the basis had nowhere to live until migration `0003`.
    """
    registry = ToolRegistry()
    registry.register(
        FixtureTool(
            name="fixture_basis",
            category=ToolCategory.WEB_SEARCH,
            items=(
                fixture_item(
                    source_name="reuters.com filed",
                    text="Acme Corp reported revenue of USD 1.2bn for fiscal 2025.",
                    source_url="https://reuters.com/acme/filed",
                    structured={"period": "2025", "currency": "USD", "basis": "reported"},
                ),
                fixture_item(
                    source_name="reuters.com estimate",
                    text="Acme Corp reported revenue of USD 1.9bn for fiscal 2025.",
                    source_url="https://reuters.com/acme/estimate",
                    structured={"period": "2025", "currency": "USD", "basis": "estimate"},
                ),
            ),
        )
    )
    registry.freeze()
    return registry


def trend_registry() -> ToolRegistry:
    """Three periods of the same metric, each dated and sourced.

    The shape a line chart needs: `MIN_POINTS_FOR_SERIES` is three, and each
    value carries the period that places it on the axis.
    """
    registry = ToolRegistry()
    registry.register(
        FixtureTool(
            name="fixture_trend",
            category=ToolCategory.WEB_SEARCH,
            items=tuple(
                fixture_item(
                    source_name=f"reuters.com FY{year}",
                    text=f"Acme Corp reported revenue of USD {amount} for fiscal {year}.",
                    source_url=f"https://reuters.com/acme/fy{year}",
                    structured={
                        "period": str(year),
                        "currency": "USD",
                        "basis": "reported",
                    },
                )
                for year, amount in (("2023", "0.9bn"), ("2024", "1.2bn"), ("2025", "1.6bn"))
            ),
        )
    )
    registry.freeze()
    return registry


TREND = (
    ("2023", "0.9bn"),
    ("2024", "1.2bn"),
    ("2025", "1.6bn"),
)


def trend_provider() -> FakeLLMProvider:
    """Extraction that reads all three dated figures.

    `disputing_provider`'s standing extraction names two specific excerpts, so
    against a three-period fixture only one grounds and the run produces a
    single point. A chart test needs the provider to actually read the data the
    fixture publishes.
    """
    provider = FakeLLMProvider(
        standing_response=Extraction(
            evidence=[
                ExtractedEvidence(
                    statement=(
                        f"Acme Corp reported revenue of USD {amount} "
                        f"for fiscal {year}."
                    ),
                    excerpt=f"revenue of USD {amount}",
                    item_index=index,
                )
                for index, (year, amount) in enumerate(TREND)
            ]
        )
    )
    provider.enqueue(
        Interpretation(
            subject="Acme Corp",
            interpretation_note=None,
            questions=[DISPUTED_QUESTION],
        ),
        _PlanDraft(
            areas=[
                _PlanDraft.Area(
                    name="Financial performance",
                    questions=[DISPUTED_QUESTION],
                    tool_categories=["web_search"],
                )
            ]
        ),
    )
    return provider
