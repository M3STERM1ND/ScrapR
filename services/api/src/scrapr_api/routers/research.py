"""Research: create a session, read its header, read one version.

Thin by construction. Each handler resolves an owner, calls a repository, and
maps rows to wire types — there is no domain logic here to drift from the
worker's copy, because there is no copy.

Two rules run through every handler:

* **A session owned by somebody else is absent, not forbidden.** 404, never 403,
  so an identifier cannot be probed for existence (`REQ-SEC-009`).
* **Nothing mutates on a GET.** A version read is always safe to retry.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query, status
from sqlalchemy import select

from scrapr_api.deps import CurrentOwner, DbSession, OwnerOrNew, Research
from scrapr_api.errors import ApiError
from scrapr_api.schemas import (
    ActivityEventOut,
    ActivityPage,
    ClaimOut,
    ConflictOut,
    ConflictSideOut,
    CreateResearchRequest,
    CreateResearchResponse,
    ResearchSessionOut,
    SectionOut,
    SourceOut,
    VersionOut,
    VersionSummary,
)
from scrapr_core.db.models import (
    Claim,
    ClaimEvidence,
    Conflict,
    ConflictEvidence,
    Evidence,
    ReportSection,
    Source,
)
from scrapr_core.db.repositories import ActivityRepository, ResearchRepository, RunRepository
from scrapr_core.orchestrator.pipeline import STAGES

router = APIRouter(prefix="/v1/research", tags=["research"])

"""The stages a research run is made of (`orchestrator.pipeline.STAGES`).

The route does not choose them and does not change when they change: enqueuing
a run is writing rows, and which rows is the pipeline's business.
"""


def _not_found() -> ApiError:
    return ApiError(
        status.HTTP_404_NOT_FOUND,
        "not_found",
        "That research does not exist.",
    )


@router.post("", status_code=status.HTTP_202_ACCEPTED)
def create_research(
    body: CreateResearchRequest,
    owner: OwnerOrNew,
    session: DbSession,
) -> CreateResearchResponse:
    """Start research. **202**, because the answer does not exist yet.

    The response is committed rows and nothing else: the run is picked up by
    whatever is polling `run_steps`, which is what keeps `OPEN-03` a hosting
    question (implementation plan §5.6).
    """
    research = ResearchRepository(session, owner)
    created = research.create_session(
        objective=body.objective,
        instructions=body.instructions,
        context_url=body.context_url,
        context_company=body.context_company,
        context_ticker=body.context_ticker,
    )

    version = research.open_version(created.id)
    if version is None:  # pragma: no cover - we just created it as this owner
        raise _not_found()

    RunRepository(session).create_run(created.id, version.id, STAGES)

    return CreateResearchResponse(
        session_id=created.id,
        version_id=version.id,
        version_number=version.version_number,
    )


@router.get("/{session_id}")
def get_research(session_id: UUID, research: Research) -> ResearchSessionOut:
    """The session header the workspace renders around (`REQ-WORK-002`)."""
    found = research.get_session(session_id)
    if found is None:
        raise _not_found()

    return ResearchSessionOut(
        id=found.id,
        objective=found.objective,
        subject=found.subject,
        subject_interpretation_note=found.subject_interpretation_note,
        status=found.status,
        current_version_id=found.current_version_id,
        created_at=found.created_at,
        updated_at=found.updated_at,
        versions=[
            VersionSummary(
                id=version.id,
                version_number=version.version_number,
                status=version.status,
                created_at=version.created_at,
                closed_at=version.closed_at,
            )
            for version in research.list_versions(session_id)
        ],
    )


@router.get("/{session_id}/versions/{version_number}")
def get_version(
    session_id: UUID,
    version_number: int,
    research: Research,
    session: DbSession,
) -> VersionOut:
    """One version, denormalised into a single response (`NFR-PERF-004`)."""
    version = research.get_version(session_id, version_number)
    if version is None:
        raise _not_found()

    sections = (
        session.execute(
            select(ReportSection)
            .where(ReportSection.version_id == version.id)
            .order_by(ReportSection.ordering)
        )
        .scalars()
        .all()
    )
    claims = (
        session.execute(
            select(Claim).where(Claim.version_id == version.id).order_by(Claim.created_at)
        )
        .scalars()
        .all()
    )
    sources = (
        session.execute(select(Source).where(Source.version_id == version.id))
        .scalars()
        .all()
    )

    # The citation map: claim id -> the sources behind its supporting evidence.
    # Built in one query rather than per claim, because a report with forty
    # claims would otherwise be forty round trips.
    citations: dict[UUID, list[UUID]] = {claim.id: [] for claim in claims}
    rows = session.execute(
        select(ClaimEvidence.claim_id, Evidence.source_id)
        .join(Evidence, Evidence.id == ClaimEvidence.evidence_id)
        .where(ClaimEvidence.claim_id.in_(citations.keys()))
    ).all()
    for claim_id, source_id in rows:
        if source_id not in citations[claim_id]:
            citations[claim_id].append(source_id)

    # Conflicts, with both sides and the source behind each (`REQ-WORK-009
    # AC-1`). Loaded in one pass for the same reason the citation map is: a
    # report with several contested claims would otherwise be a query apiece.
    conflict_rows = (
        session.execute(
            select(Conflict)
            .where(Conflict.version_id == version.id)
            .order_by(Conflict.id)
        )
        .scalars()
        .all()
    )
    sides: dict[UUID, list[ConflictSideOut]] = {row.id: [] for row in conflict_rows}
    if sides:
        for conflict_id, evidence, label in session.execute(
            select(ConflictEvidence.conflict_id, Evidence, ConflictEvidence.label)
            .join(Evidence, Evidence.id == ConflictEvidence.evidence_id)
            .where(ConflictEvidence.conflict_id.in_(sides.keys()))
        ).all():
            sides[conflict_id].append(
                ConflictSideOut(
                    evidence_id=evidence.id,
                    source_id=evidence.source_id,
                    label=label,
                    # The reported form, never the normalised one
                    # (`REQ-EVID-008 AC-2`): a reader comparing two values must
                    # see what each source actually published.
                    value=evidence.value_raw or evidence.content,
                )
            )

    return VersionOut(
        id=version.id,
        session_id=version.session_id,
        version_number=version.version_number,
        status=version.status,
        created_at=version.created_at,
        closed_at=version.closed_at,
        sections=[
            SectionOut(
                id=section.id,
                title=section.title,
                ordering=section.ordering,
                is_executive_summary=section.is_executive_summary,
                claim_ids=[
                    claim.id for claim in claims if claim.section_id == section.id
                ],
            )
            for section in sections
        ],
        claims=[
            ClaimOut(
                id=claim.id,
                text=claim.text,
                claim_type=claim.claim_type,
                confidence=claim.confidence,
                confidence_rationale=_rationale(claim),
                is_important=claim.is_important,
                source_ids=citations[claim.id],
            )
            for claim in claims
        ],
        sources=[
            SourceOut(
                id=source.id,
                name=source.name,
                url=source.url,
                publisher=source.publisher,
                authority_tier=source.authority_tier,
                retrieved_at=source.retrieved_at,
                published_at=source.published_at,
            )
            for source in sources
        ],
        conflicts=[
            ConflictOut(
                id=row.id,
                claim_id=row.claim_id,
                status=row.status,
                explanation=row.explanation,
                explanation_category=row.explanation_category,
                sides=sides[row.id],
            )
            for row in conflict_rows
        ],
    )


def _rationale(claim: Claim) -> str | None:
    """The sentence explaining a claim's confidence (`REQ-DATA-012`).

    Read from what the pipeline stored rather than recomputed here: the API
    must show the reasoning that actually produced the level, not a second
    opinion formed from the same inputs at a different time.
    """
    value = claim.confidence_inputs.get("rationale")
    return value if isinstance(value, str) else None


@router.get("/{session_id}/activity")
def get_activity(
    session_id: UUID,
    research: Research,
    session: DbSession,
    owner: CurrentOwner,
    after: int = Query(default=0, ge=0),
) -> ActivityPage:
    """Events newer than `after` (`REQ-ACT-001`, implementation plan §6.3).

    Phase 1 ships polling. Phase 3 gives this same route a streaming content
    type; because `seq` is already the resume key, that changes no schema, no
    route and no client state model.
    """
    if research.get_session(session_id) is None:
        raise _not_found()

    events = ActivityRepository(session).since(session_id, after=after)
    return ActivityPage(
        events=[
            ActivityEventOut(
                seq=event.seq,
                label=event.label,
                status=event.status,
                tool_category=event.tool_category,
                created_at=event.created_at,
            )
            for event in events
        ],
        next_after=events[-1].seq if events else after,
    )
