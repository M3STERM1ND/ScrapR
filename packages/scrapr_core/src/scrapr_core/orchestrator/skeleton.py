"""The Phase 0 walking skeleton: two stages that cross every seam once.

**This is scaffolding, not the research loop.** The real orchestrator is Phase 1
and arrives as seven stages, each a separate unit of work with its own declared
input and output types. What this module exists to prove is that the *seams*
work end to end: a tool behind the uniform contract, a model behind the provider
abstraction, evidence and claims persisted under a version, activity emitted,
and the validation gate run over the result.

An earlier draft of the plan asked Phase 0 for "a fake-backed research run end to
end", which silently required most of Phase 1. The exit condition was narrowed
for exactly that reason, and this file is deliberately no larger than the
narrowed condition needs:

* `retrieve` — invoke one tool by category, persist a source and one evidence
  row per item, emit activity.
* `synthesize` — ask the model for one claim, persist it with its evidence link
  and a section, run the gate, emit activity.

Phase 1 replaces both, one stage at a time, against these same fixtures.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from typing import final

from pydantic import BaseModel, Field
from sqlalchemy import select

from scrapr_core.db.enums import (
    ActivityStatus,
    AuthorityTier,
    ClaimType,
    EvidenceRole,
    NormalizationStatus,
)
from scrapr_core.db.models import Claim, ClaimEvidence, Evidence, ReportSection, Source
from scrapr_core.db.repositories.activity import ActivityRepository
from scrapr_core.domain.json import JsonMapping
from scrapr_core.jobs.contract import StepContext, StepPermanentError
from scrapr_core.llm.contract import LLMProvider, ModelTier, UntrustedDocument
from scrapr_core.security.trust import SourceRef, Trusted, Untrusted
from scrapr_core.synthesis.validation import validate_version
from scrapr_core.tools.contract import ToolCategory, ToolItem, ToolRequest, ToolResult
from scrapr_core.tools.registry import ToolRegistry

__all__ = [
    "RETRIEVE_STAGE",
    "SYNTHESIZE_STAGE",
    "RetrieveHandler",
    "SkeletonClaim",
    "SynthesizeHandler",
]

RETRIEVE_STAGE = "retrieve"
SYNTHESIZE_STAGE = "synthesize"

SYNTHESIS_INSTRUCTION = Trusted(
    "State one factual claim that the supplied material supports. Assert nothing "
    "the material does not say, and give the claim a short section title."
)

DEFAULT_TIER = AuthorityTier.SECONDARY
"""`OPEN-15` decides how authority tier is assigned. Until it does the skeleton
records the middle tier and says so in `tier_rationale`, rather than inventing a
rule that would read like a decision somebody made."""


class SkeletonClaim(BaseModel):
    """What the model returns at this stage.

    Structured, not prose: even the skeleton returns a validated schema, because
    the moment a stage returns free text somebody has to parse it, and that
    parser is where "the model said something odd" becomes an incident.
    """

    section_title: str = Field(min_length=1)
    claim_text: str = Field(min_length=1)


@final
@dataclass(frozen=True, slots=True)
class RetrieveHandler:
    """Stage one: retrieve through the tool contract and persist evidence.

    Idempotent by way of `unique (version_id, url_normalized)`: a re-run after a
    crash finds the source already present and reuses it instead of duplicating
    it, which is the property `REQ-EVID-006` asks of deduplication anyway.
    """

    registry: ToolRegistry
    category: ToolCategory = ToolCategory.WEB_SEARCH

    async def execute(self, context: StepContext) -> JsonMapping:
        activity = ActivityRepository(context.session)
        label = "Searching for information"
        activity.append(
            context.run.session_id,
            label,
            ActivityStatus.IN_PROGRESS,
            version_id=context.run.version_id,
            tool_category=self.category.value,
        )

        outcome = await self.registry.invoke(ToolRequest(category=self.category))
        if not isinstance(outcome, ToolResult):
            # A tool failure is data rather than an exception — but with one
            # category planned and nothing else to fall back on, there is no
            # partial result to salvage, so the run stops instead of producing
            # an empty report and calling it complete (`REQ-AGENT-009 AC-3`).
            activity.append(
                context.run.session_id,
                label,
                ActivityStatus.FAILED,
                version_id=context.run.version_id,
                tool_category=self.category.value,
            )
            raise StepPermanentError(f"retrieval failed: {outcome.kind}")

        evidence_ids = [str(self._persist(context, item).id) for item in outcome.items]

        activity.append(
            context.run.session_id,
            label,
            ActivityStatus.COMPLETE,
            version_id=context.run.version_id,
            tool_category=self.category.value,
        )
        return {"evidence_ids": evidence_ids, "tool": outcome.tool}

    def _persist(self, context: StepContext, item: ToolItem) -> Evidence:
        source = self._source_for(context, item)
        evidence = Evidence(
            version_id=context.run.version_id,
            source_id=source.id,
            # `.text` is the deliberate, greppable escape hatch: extraction is
            # one of the two places untrusted content enters ordinary code.
            content=item.content.text,
            excerpt=item.content.text,
            normalization=NormalizationStatus.NOT_APPLICABLE,
            extracted_at=dt.datetime.now(dt.UTC),
        )
        context.session.add(evidence)
        context.session.flush()
        return evidence

    def _source_for(self, context: StepContext, item: ToolItem) -> Source:
        existing = context.session.execute(
            select(Source).where(
                Source.version_id == context.run.version_id,
                Source.url_normalized == item.source_url,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        source = Source(
            version_id=context.run.version_id,
            url=item.source_url,
            url_normalized=item.source_url,
            identifier=item.source_identifier,
            name=item.source_name,
            category=item.source_category,
            authority_tier=DEFAULT_TIER,
            tier_rationale={"basis": "OPEN-15 unresolved; Phase 0 assigns a default"},
            retrieved_at=item.retrieved_at,
            published_at=item.published_at,
            accessibility=item.accessibility,
        )
        context.session.add(source)
        context.session.flush()
        return source


@final
@dataclass(frozen=True, slots=True)
class SynthesizeHandler:
    """Stage two: one claim, one section, then the gate.

    The gate runs here rather than in the runner because it judges a *version*,
    and only synthesis knows when a version is finished enough to be judged.
    """

    provider: LLMProvider
    tier: ModelTier = ModelTier.CHEAP

    async def execute(self, context: StepContext) -> JsonMapping:
        activity = ActivityRepository(context.session)
        label = "Building the report"
        activity.append(
            context.run.session_id,
            label,
            ActivityStatus.IN_PROGRESS,
            version_id=context.run.version_id,
        )

        evidence = self._evidence(context)
        if not evidence:
            raise StepPermanentError(
                "synthesis has no evidence to work from; a claim without "
                "evidence is exactly what REQ-EVID-017 exists to prevent"
            )

        answer = await self.provider.complete_structured(
            SYNTHESIS_INSTRUCTION,
            [
                UntrustedDocument(content=_as_material(row), label=f"evidence {index}")
                for index, row in enumerate(evidence, 1)
            ],
            SkeletonClaim,
            self.tier,
        )

        section = ReportSection(
            version_id=context.run.version_id,
            title=answer.value.section_title,
            body={"blocks": [{"kind": "claim", "ordinal": 0}]},
            ordering=0,
            is_executive_summary=True,
        )
        context.session.add(section)
        context.session.flush()

        claim = Claim(
            version_id=context.run.version_id,
            section_id=section.id,
            text=answer.value.claim_text,
            claim_type=ClaimType.FACT,
            is_important=True,
        )
        context.session.add(claim)
        context.session.flush()

        context.session.add(
            ClaimEvidence(
                claim_id=claim.id,
                evidence_id=evidence[0].id,
                role=EvidenceRole.SUPPORTING,
            )
        )
        context.session.flush()

        report = validate_version(context.session, context.run.version_id)
        if not report.passed:
            # A gate failure is a generation defect, and it never ships
            # silently (`REQ-SYNTH-006 AC-3`).
            raise StepPermanentError(report.summary())

        activity.append(
            context.run.session_id,
            label,
            ActivityStatus.COMPLETE,
            version_id=context.run.version_id,
        )
        return {"claim_id": str(claim.id), "section_id": str(section.id)}

    def _evidence(self, context: StepContext) -> Sequence[Evidence]:
        statement = (
            select(Evidence)
            .where(Evidence.version_id == context.run.version_id)
            .order_by(Evidence.extracted_at, Evidence.id)
        )
        return context.session.execute(statement).scalars().all()


def _as_material(row: Evidence) -> Untrusted:
    """Re-wrap persisted evidence as untrusted before it goes near the model.

    Storing content in a `text` column does not launder it: the row came from a
    web page, and it is a directive to nobody.
    """
    return Untrusted(row.content, SourceRef(kind="evidence", locator=str(row.source_id)))
