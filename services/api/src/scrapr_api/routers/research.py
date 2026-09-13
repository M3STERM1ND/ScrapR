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

import logging
from collections.abc import Sequence
from uuid import UUID

from fastapi import APIRouter, Query, status
from pydantic import ValidationError
from sqlalchemy import select

from scrapr_api.deps import CurrentOwner, DbSession, OwnerOrNew, Research
from scrapr_api.errors import ApiError
from scrapr_api.routers.uploads import get_object_store
from scrapr_api.schemas import (
    ActivityEventOut,
    ActivityPage,
    AskIn,
    AskOut,
    ChangeSummaryOut,
    ClaimOut,
    ConflictOut,
    ConflictSideOut,
    CreateResearchRequest,
    CreateResearchResponse,
    EvidenceOut,
    MessageOut,
    ResearchSessionOut,
    SectionOut,
    SourceOut,
    VersionOut,
    VersionSummary,
    VisualizationOut,
)
from scrapr_core.config import get_settings
from scrapr_core.db.enums import (
    ClaimType,
    MessageRole,
    ResearchStatus,
    RunKind,
    SourceCategory,
)
from scrapr_core.db.models import (
    Claim,
    ClaimEvidence,
    Conflict,
    ConflictEvidence,
    ConversationMessage,
    Evidence,
    ReportSection,
    ResearchSession,
    Source,
    Upload,
    Visualization,
    VisualizationEvidence,
)
from scrapr_core.db.repositories import (
    ActivityRepository,
    ConversationRepository,
    ResearchRepository,
    RunRepository,
)
from scrapr_core.lifecycle import delete_research
from scrapr_core.orchestrator.converse import (
    ConversationContext,
    EvidenceRef,
    GroundedAnswer,
    Intent,
    answer_question,
    classify_intent,
)
from scrapr_core.orchestrator.pipeline import CONVERSATION_STAGES, STAGES, UPDATE_STAGES
from scrapr_worker.main import build_provider

logger = logging.getLogger(__name__)

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

    if not body.defer_start:
        RunRepository(session).create_run(created.id, version.id, STAGES)

    return CreateResearchResponse(
        session_id=created.id,
        version_id=version.id,
        version_number=version.version_number,
    )


@router.post("/{session_id}/start", status_code=status.HTTP_202_ACCEPTED)
def start_research(
    session_id: UUID,
    research: Research,
    session: DbSession,
) -> CreateResearchResponse:
    """Enqueue the run for a session created with `defer_start`.

    **Idempotent.** A session that already has a run is returned unchanged
    rather than given a second one: this is the call a client retries after a
    dropped connection, and two runs against one version would produce two sets
    of claims in the same report.
    """
    found = research.get_session(session_id)
    if found is None:
        raise _not_found()

    # `latest_version`, never `open_version`: the version was created alongside
    # the session and opening another here would give the session two, each
    # with its own run, and the reader a report assembled from both.
    version = research.latest_version(session_id)
    if version is None:  # pragma: no cover - creation always opens version 1
        raise _not_found()

    runs = RunRepository(session)
    if not runs.has_run_for_version(version.id):
        runs.create_run(session_id, version.id, STAGES)

    return CreateResearchResponse(
        session_id=session_id,
        version_id=version.id,
        version_number=version.version_number,
    )


@router.get("/{session_id}")
def get_research(
    session_id: UUID, research: Research, session: DbSession
) -> ResearchSessionOut:
    """The session header the workspace renders around (`REQ-WORK-002`)."""
    found = research.get_session(session_id)
    if found is None:
        raise _not_found()

    versions = research.list_versions(session_id)
    origins = RunRepository(session).kinds_by_version([version.id for version in versions])

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
                origin=origins.get(version.id),
            )
            for version in versions
        ],
    )


@router.post("/{session_id}/update", status_code=status.HTTP_202_ACCEPTED)
def update_research(
    session_id: UUID,
    research: Research,
    session: DbSession,
) -> CreateResearchResponse:
    """Update Research: fresh retrieval into a new version (`REQ-VER-001`, `REQ-VER-005`).

    **Only ever from this request.** Nothing schedules it and nothing retries it
    on a timer (`REQ-VER-009`); a reader pressing the control is the only thing
    that reaches this line.

    **Refused while research is already running**, because two runs would open
    two versions against one baseline and race to be current. Refused, too,
    when there is no completed version to update: an update is measured against
    a report, and a failed first run produced none.

    The previous version is untouched by any of this (`REQ-VER-002`): the new
    version gets its own sources, evidence and retrieval timestamps, and the
    session's current pointer moves while every earlier version stays readable.
    """
    found = research.get_session(session_id)
    if found is None:
        raise _not_found()

    runs = RunRepository(session)
    if runs.has_active_run(session_id):
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "research_in_progress",
            "This research is still running. Update it once it finishes.",
        )

    baseline = research.latest_completed_version(session_id)
    if baseline is None:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "nothing_to_update",
            "There is no finished report to update yet. Start the research again instead.",
        )

    version = research.open_version(session_id, compare_with=baseline)
    if version is None:  # pragma: no cover - ownership was checked above
        raise _not_found()

    runs.create_run(session_id, version.id, UPDATE_STAGES, kind=RunKind.UPDATE)
    found.status = ResearchStatus.PENDING

    return CreateResearchResponse(
        session_id=session_id,
        version_id=version.id,
        version_number=version.version_number,
    )


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_research_session(
    session_id: UUID,
    research: Research,
    session: DbSession,
) -> None:
    """Delete research and everything produced for it (`REQ-SEC-008`, `DEC-18`).

    Hidden and stopped in this request, its files removed from storage, and its
    rows purged by the worker's sweep once no step can still be writing them.

    **Idempotent, and silent about what it could not find.** Research that is
    already gone and research that was never this caller's both answer 204:
    a 404 for one and not the other would let a guessed id be tested for
    existence (`REQ-SEC-009`).
    """
    found = research.get_session(session_id)
    if found is None:
        return

    delete_research(session, found, get_object_store())


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
    # Which cited documents have been deleted since (`REQ-AUTH-008 AC-3`).
    upload_ids = {source.upload_id for source in sources if source.upload_id}
    removed_uploads: set[UUID] = (
        set(
            session.execute(
                select(Upload.id).where(
                    Upload.id.in_(upload_ids), Upload.deleted_at.is_not(None)
                )
            ).scalars()
        )
        if upload_ids
        else set()
    )

    # The citation map: claim id -> the sources behind its supporting evidence.
    # Built in one query rather than per claim, because a report with forty
    # claims would otherwise be forty round trips.
    citations: dict[UUID, list[UUID]] = {claim.id: [] for claim in claims}
    # `REQ-EVID-009 AC-2`: the reporting period travels with the citation, so a
    # reader inspecting a figure can see which year it covers.
    periods: dict[UUID, set[str]] = {claim.id: set() for claim in claims}
    # The evidence itself, not just which sources it came from
    # (`REQ-EVID-019 AC-1`). One query for the whole version: a report with
    # forty claims would otherwise be forty round trips, and `NFR-PERF-004`
    # asks for one response.
    supporting: dict[UUID, list[EvidenceOut]] = {claim.id: [] for claim in claims}
    rows = session.execute(
        select(ClaimEvidence.claim_id, Evidence)
        .join(Evidence, Evidence.id == ClaimEvidence.evidence_id)
        .where(ClaimEvidence.claim_id.in_(citations.keys()))
        .order_by(Evidence.extracted_at, Evidence.id)
    ).all()
    for claim_id, evidence in rows:
        if evidence.source_id not in citations[claim_id]:
            citations[claim_id].append(evidence.source_id)
        if evidence.period_end is not None:
            periods[claim_id].add(evidence.period_end.isoformat())
        supporting[claim_id].append(
            EvidenceOut(
                id=evidence.id,
                source_id=evidence.source_id,
                statement=evidence.content,
                excerpt=evidence.excerpt,
                value_raw=_reported_value(evidence) if evidence.value_raw else None,
                reporting_period=(
                    evidence.period_end.isoformat() if evidence.period_end else None
                ),
            )
        )

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
        for conflict_id, evidence, label, side_source in session.execute(
            select(ConflictEvidence.conflict_id, Evidence, ConflictEvidence.label, Source)
            .join(Evidence, Evidence.id == ConflictEvidence.evidence_id)
            .join(Source, Source.id == Evidence.source_id)
            .where(ConflictEvidence.conflict_id.in_(sides.keys()))
        ).all():
            sides[conflict_id].append(
                ConflictSideOut(
                    evidence_id=evidence.id,
                    source_id=evidence.source_id,
                    label=label,
                    source_name=side_source.name,
                    # `REQ-DOC-007 AC-2`.
                    from_your_document=(
                        side_source.category is SourceCategory.DOCUMENT
                    ),
                    # The reported form, never the normalised one
                    # (`REQ-EVID-008 AC-2`): a reader comparing two values must
                    # see what each source actually published. The currency is
                    # rejoined because the parser separates it out, and "1.2bn"
                    # against "1.9bn" with no unit is a comparison the reader
                    # cannot check.
                    value=_reported_value(evidence),
                )
            )

    # Visualizations, each with the evidence its points came from
    # (`REQ-VIZ-004 AC-1`, `AC-2`).
    viz_rows = (
        session.execute(
            select(Visualization)
            .where(Visualization.version_id == version.id)
            .order_by(Visualization.ordering)
        )
        .scalars()
        .all()
    )
    viz_evidence: dict[UUID, list[UUID]] = {row.id: [] for row in viz_rows}
    if viz_evidence:
        for viz_id, evidence_id in session.execute(
            select(
                VisualizationEvidence.visualization_id,
                VisualizationEvidence.evidence_id,
            ).where(VisualizationEvidence.visualization_id.in_(viz_evidence.keys()))
        ).all():
            viz_evidence[viz_id].append(evidence_id)

    return VersionOut(
        id=version.id,
        session_id=version.session_id,
        version_number=version.version_number,
        status=version.status,
        created_at=version.created_at,
        closed_at=version.closed_at,
        origin=RunRepository(session).kinds_by_version([version.id]).get(version.id),
        change_summary=_change_summary(version.change_summary),
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
                reporting_period=_single_period(periods[claim.id]),
                is_important=claim.is_important,
                source_ids=citations[claim.id],
                evidence=supporting[claim.id],
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
                # `REQ-DOC-008 AC-2`. Without these two the frontend sees a
                # source with a name and no URL and has no way to tell the
                # reader's own file apart from an unlinkable web result.
                category=source.category,
                upload_id=source.upload_id,
                document_removed=source.upload_id in removed_uploads,
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
        visualizations=[
            VisualizationOut(
                id=row.id,
                section_id=row.section_id,
                kind=row.kind,
                spec=dict(row.spec),
                ordering=row.ordering,
                evidence_ids=viz_evidence[row.id],
            )
            for row in viz_rows
        ],
    )


def _change_summary(stored: dict[str, object] | None) -> ChangeSummaryOut | None:
    """The stored What's Changed, or nothing when there is none to show.

    A summary that no longer parses — written by an older schema, say — is
    dropped rather than failing the whole report: the version is still
    readable, and the workspace shows no summary rather than a broken one.
    """
    if not stored:
        return None
    try:
        return ChangeSummaryOut.model_validate(stored)
    except ValidationError:
        logger.warning("an unreadable change summary was skipped")
        return None


def _reported_value(evidence: Evidence) -> str:
    """The figure as published, with its currency back in front of it."""
    figure = evidence.value_raw or evidence.content
    if evidence.currency and evidence.currency not in figure:
        return f"{evidence.currency} {figure}"
    return figure


def _single_period(found: set[str]) -> str | None:
    """The one period a claim's figures cover, or nothing.

    A claim citing two years has no single period, and showing one of them
    would tell the reader something untrue about the other. `DEC-10 §4.1`
    already refuses to compare across periods; this refuses to label across
    them.
    """
    return next(iter(found)) if len(found) == 1 else None


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


# --------------------------------------------------------------------------
# Conversation (`REQ-CONV-001..008`, `REQ-WORK-007`)
# --------------------------------------------------------------------------


@router.get("/{session_id}/messages")
def get_messages(
    session_id: UUID,
    research: Research,
    session: DbSession,
) -> list[MessageOut]:
    """The conversation so far (`REQ-CONV-007 AC-2`).

    Ownership-scoped like everything else: a conversation is about someone's
    research and `REQ-SEC-002` does not stop applying because the surface is a
    chat.
    """
    if research.get_session(session_id) is None:
        raise _not_found()

    conversation = ConversationRepository(session)
    return [
        _message_out(message, conversation.evidence_ids_for(message.id))
        for message in conversation.for_session(session_id)
    ]


@router.post("/{session_id}/messages", status_code=201)
async def ask(
    session_id: UUID,
    body: AskIn,
    research: Research,
    session: DbSession,
) -> AskOut:
    """Ask a follow-up, and answer it from the research (`REQ-CONV-001`).

    Answered synchronously, unlike a research run. A question against evidence
    already gathered is one model call, and pushing it through `run_steps`
    would make the reader wait on a poll for something that takes a second.

    **Follow-up research is the exception, and it is deliberately not done
    here.** `REQ-CONV-003` requires fresh retrieval when the evidence is
    insufficient, and retrieval belongs to the pipeline — it needs the budget,
    the tool registry, the termination gate and the activity stream, none of
    which a request handler should own. So the intent is classified, the answer
    says plainly that new research is needed, and enqueuing that run is the
    next piece of work. Answering "I researched that" without having done so
    would be the one thing this product must never do.
    """
    found = research.get_session(session_id)
    if found is None:
        raise _not_found()

    version_id = found.current_version_id
    if version_id is None:
        raise _not_found()

    conversation = ConversationRepository(session)
    context = _conversation_context(session, found, version_id, conversation)
    provider = build_provider(get_settings())

    # Classified before anything is written, because the answer for a research
    # question is produced by a different mechanism than the answer for a
    # question the evidence already covers.
    verdict = await classify_intent(body.question, context, provider)
    researched = verdict.intent is Intent.RESEARCH

    if researched:
        # `REQ-CONV-003`: fresh retrieval, run by the pipeline. A new version,
        # because `REQ-VER-002` makes the current one immutable and adding
        # evidence to a closed version would break the promise that an answer
        # stays checkable against what it actually read.
        # Measured against the last version that finished, never a failure
        # or a run still building (`DEC-20`).
        next_version = research.open_version(
            session_id, compare_with=research.latest_completed_version(session_id)
        )
        if next_version is None:
            raise _not_found()

        # Written against the *new* version, so `_objective_of` finds it when
        # the interpret step runs and researches the follow-up rather than
        # re-running the original objective unchanged.
        question = conversation.append(
            session_id,
            next_version.id,
            MessageRole.USER,
            body.question,
            context_ref=body.context_ref,
        )
        RunRepository(session).create_run(
            session_id,
            next_version.id,
            CONVERSATION_STAGES
            if next_version.previous_version_id is not None
            else STAGES,
            kind=RunKind.CONVERSATION,
        )

        grounded = GroundedAnswer(
            text=(
                "The research does not cover this yet, so I have started "
                "looking. Watch the activity list; the answer will be in the "
                "report when it finishes."
            ),
            claim_type=ClaimType.UNCERTAINTY,
            evidence_ids=(),
        )
        answer_version = next_version.id
    else:
        question = conversation.append(
            session_id,
            version_id,
            MessageRole.USER,
            body.question,
            context_ref=body.context_ref,
        )
        grounded = await answer_question(
            body.question,
            context,
            provider,
            reframe=verdict.intent is Intent.REFRAME,
        )
        answer_version = version_id

    answer = conversation.append(
        session_id,
        answer_version,
        MessageRole.AGENT,
        grounded.text,
        evidence_ids=grounded.evidence_ids,
        context_ref={"claim_type": grounded.claim_type.value},
    )
    session.commit()

    return AskOut(
        question=_message_out(question, []),
        answer=_message_out(answer, grounded.evidence_ids),
        researched=researched,
    )


def _conversation_context(
    session: DbSession,
    found: ResearchSession,
    version_id: UUID,
    conversation: ConversationRepository,
) -> ConversationContext:
    """Everything the agent knows, read from rows.

    `REQ-CONV-001 AC-2` requires access to the session's claims, evidence and
    sources; `AC-3` requires that survive a reload. Assembling it from the
    database on every turn is what makes both true at once — there is no
    in-process state to lose.
    """
    rows = session.execute(
        select(Evidence, Source)
        .join(Source, Source.id == Evidence.source_id)
        .where(Evidence.version_id == version_id)
        .order_by(Evidence.extracted_at, Evidence.id)
    ).all()

    return ConversationContext(
        subject=found.subject or found.objective,
        objective=found.objective,
        evidence=[
            EvidenceRef(
                evidence_id=evidence.id,
                statement=evidence.content,
                excerpt=evidence.excerpt or "",
                source_name=source.name,
            )
            for evidence, source in rows
        ],
        recent_turns=conversation.recent_turns(found.id),
    )


def _message_out(
    message: ConversationMessage, evidence_ids: Sequence[UUID]
) -> MessageOut:
    """One turn on the wire.

    The claim type rides in `context_ref` rather than in its own column: the
    schema was written before `DEC-05` split claim typing across phases, and
    adding a column for a value only agent turns carry would be a migration to
    store an enum in the one place it is already recorded.
    """
    raw = (message.context_ref or {}).get("claim_type")
    return MessageOut(
        id=message.id,
        seq=message.seq,
        role=message.role,
        content=message.content,
        claim_type=ClaimType(raw) if isinstance(raw, str) else None,
        evidence_ids=list(evidence_ids),
        created_at=message.created_at,
    )
