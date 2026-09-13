"""The orchestrator: the stages, wired to the durable step machine.

Four steps, because a step is the unit that survives a worker restart (§5.6) and
these are the four points where resuming is worth the bookkeeping:

| step | stages | why it ends here |
|---|---|---|
| `interpret` | 1 | the subject is worth keeping before any tool is called |
| `plan` | 2 | the question rows are what everything after is measured against |
| `research` | 3-7 | the loop |
| `synthesize` | 10, 12 | writes the report and runs the gate |

Stages 5, 6, 8, 9 and 11 — normalise, dedupe and tier, conflict, confidence,
visualize — are Phase 2 and 3 work. They slot in between without moving these
boundaries, which is why the steps are grouped rather than one per stage.

**Update Research swaps the first two steps and adds a last one** (`DEC-19`,
`DEC-20`): `prioritize` carries the previous version's questions forward in
the order most likely to have changed, and `compare` writes What's Changed
before the runner closes the version. Follow-up research keeps the four and
adds `compare`.

**Steps read each other's checkpoints, not each other's memory.** The plan step
may run in a different process from the interpret step, so it reads what that
step durably recorded. That is also what makes a re-run cheap: the work is on
disk, not in a variable that died with the worker.

**Activity is emitted as the work happens** (`REQ-ACT-001 AC-1`), in
user-meaningful language (`REQ-ACT-002`), and by tool *category* rather than
tool or query (`REQ-ACT-003`, `REQ-AGENT-003 AC-3`).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import final
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from scrapr_core.config import get_settings
from scrapr_core.db.base import utcnow
from scrapr_core.db.enums import (
    ActivityStatus,
    ClaimType,
    EvidenceRole,
    MessageRole,
    NormalizationStatus,
    RunKind,
)
from scrapr_core.db.models import (
    Claim,
    ClaimEvidence,
    ConversationMessage,
    Evidence,
    QuestionEvidence,
    QuestionState,
    ReportSection,
    ResearchQuestion,
    ResearchSession,
    ResearchVersion,
    RunStep,
    Source,
    Visualization,
    VisualizationEvidence,
)
from scrapr_core.db.repositories.activity import ActivityRepository
from scrapr_core.db.repositories.evidence import EvidenceRepository
from scrapr_core.db.repositories.questions import QuestionRepository
from scrapr_core.db.repositories.uploads import UploadRepository
from scrapr_core.domain.json import JsonMapping
from scrapr_core.evidence.normalize import classify_metric
from scrapr_core.evidence.tiering import registrable_host
from scrapr_core.jobs.contract import StepContext, StepHandler, StepPermanentError
from scrapr_core.llm.contract import LLMProvider
from scrapr_core.orchestrator.budget import AreaReservation, RunBudget
from scrapr_core.orchestrator.extract import extract_from_items
from scrapr_core.orchestrator.interpret import (
    Interpretation,
    ObjectiveInput,
    interpret,
)
from scrapr_core.orchestrator.outcome import AreaOutcome, RunOutcome, summarise_run
from scrapr_core.orchestrator.plan import plan_research
from scrapr_core.orchestrator.retrieve import RetrievalCache, RoundResult, retrieve_area
from scrapr_core.orchestrator.sufficiency import (
    AreaProgress,
    CoverageGatePolicy,
    SufficiencyVerdict,
    TerminationPolicy,
    area_rounds,
    is_resolved,
)
from scrapr_core.orchestrator.synthesize import (
    Section,
    SynthesisInput,
    synthesize,
    with_area_gaps,
)
from scrapr_core.orchestrator.trust import apply_trust
from scrapr_core.orchestrator.visualize import Chartable, select_visualization
from scrapr_core.synthesis.validation import validate_version
from scrapr_core.tools.contract import ToolCategory, ToolFailure
from scrapr_core.tools.registry import ToolRegistry
from scrapr_core.versioning import (
    AreaHistory,
    CitedEvidence,
    compare_versions,
    prioritize_areas,
)

logger = logging.getLogger(__name__)

__all__ = [
    "COMPARE_STAGE",
    "CONVERSATION_STAGES",
    "INTERPRET_STAGE",
    "PLAN_STAGE",
    "PRIORITIZE_STAGE",
    "RESEARCH_STAGE",
    "STAGES",
    "SYNTHESIZE_STAGE",
    "UPDATE_STAGES",
    "CompareHandler",
    "InterpretHandler",
    "PlanHandler",
    "PrioritizeHandler",
    "ResearchHandler",
    "SynthesizeHandler",
    "build_handlers",
]

INTERPRET_STAGE = "interpret"
PLAN_STAGE = "plan"
RESEARCH_STAGE = "research"
SYNTHESIZE_STAGE = "synthesize"
PRIORITIZE_STAGE = "prioritize"
COMPARE_STAGE = "compare"

STAGES = (INTERPRET_STAGE, PLAN_STAGE, RESEARCH_STAGE, SYNTHESIZE_STAGE)
"""A first run: nothing to compare against."""

UPDATE_STAGES = (PRIORITIZE_STAGE, RESEARCH_STAGE, SYNTHESIZE_STAGE, COMPARE_STAGE)
"""Update Research (`REQ-VER-001..007`, `DEC-19`). No interpret and no plan:
the subject and the questions are exactly what is being re-checked, and
re-planning would move the target the comparison measures against."""

CONVERSATION_STAGES = (*STAGES, COMPARE_STAGE)
"""Follow-up research (`REQ-CONV-003`). It plans afresh for the follow-up, and
its version still gets What's Changed (`DEC-20`, "what counts as a path")."""

# `REQ-ACT-002 AC-1` sets the register: plain language a non-technical reader
# follows. Kept together so the vocabulary cannot drift apart.
LABEL_INTERPRET = "Understanding the objective"
LABEL_PLAN = "Identifying research areas"
LABEL_SYNTHESIZE = "Building the report"
LABEL_PRIORITIZE = "Checking what may have changed"
LABEL_COMPARE = "Comparing with the previous version"


def _area_label(area_name: str) -> str:
    return f"Researching {area_name[0].lower()}{area_name[1:]}"


# --------------------------------------------------------------------------
# Stage 1
# --------------------------------------------------------------------------


@final
@dataclass(frozen=True, slots=True)
class InterpretHandler:
    """Stage 1. Objective to subject and questions."""

    provider: LLMProvider

    async def execute(self, context: StepContext) -> JsonMapping:
        activity = ActivityRepository(context.session)
        research = _research_session(context)

        activity.append(
            research.id,
            LABEL_INTERPRET,
            ActivityStatus.IN_PROGRESS,
            version_id=context.run.version_id,
        )

        interpretation = await interpret(
            _objective_of(research, context), self.provider
        )

        # `REQ-AGENT-001 AC-2`: the workspace header shows the subject, and
        # `AC-3` shows how an ambiguous one was read, so both belong on the
        # session rather than only in this step's checkpoint.
        research.subject = interpretation.subject
        research.subject_interpretation_note = interpretation.interpretation_note
        context.session.flush()

        activity.append(
            research.id,
            LABEL_INTERPRET,
            ActivityStatus.COMPLETE,
            version_id=context.run.version_id,
        )
        return {
            "subject": interpretation.subject,
            "interpretation_note": interpretation.interpretation_note,
            "questions": list(interpretation.questions),
        }


# --------------------------------------------------------------------------
# Stage 2
# --------------------------------------------------------------------------


@final
@dataclass(frozen=True, slots=True)
class PlanHandler:
    """Stage 2, and the question rows termination is measured against."""

    provider: LLMProvider
    registry: ToolRegistry

    async def execute(self, context: StepContext) -> JsonMapping:
        activity = ActivityRepository(context.session)
        questions = QuestionRepository(context.session)
        research = _research_session(context)

        existing = questions.for_version(context.run.version_id)
        if existing:
            # A re-run after a crash. The plan is already committed, and
            # replanning would move the coverage target mid-run.
            return {
                "areas": len({q.area_name for q in existing}),
                "questions": len(existing),
                "replanned": False,
            }

        activity.append(
            research.id,
            LABEL_PLAN,
            ActivityStatus.IN_PROGRESS,
            version_id=context.run.version_id,
        )

        interpretation = _interpretation_from_checkpoint(context, research)
        plan = await plan_research(
            # Plannable, not all: page fetch answers a URL rather than a
            # question, and an area planned against it reports itself
            # unresearchable for every question in it.
            interpretation,
            self.registry.plannable_categories(),
            self.provider,
        )

        questions.persist_plan(
            context.run.version_id,
            [
                (area.name, area.questions, [c.value for c in area.tool_categories])
                for area in plan.areas
            ],
        )

        activity.append(
            research.id,
            LABEL_PLAN,
            ActivityStatus.COMPLETE,
            version_id=context.run.version_id,
        )
        return {
            "areas": len(plan.areas),
            "questions": plan.question_count,
            "replanned": True,
        }


# --------------------------------------------------------------------------
# Stages 3 to 7
# --------------------------------------------------------------------------


@final
@dataclass(frozen=True, slots=True)
class ResearchHandler:
    """The loop: retrieve, extract, assess, repeat until the gate says stop."""

    provider: LLMProvider
    registry: ToolRegistry
    policy: TerminationPolicy = field(default_factory=CoverageGatePolicy)

    async def execute(self, context: StepContext) -> JsonMapping:
        questions = QuestionRepository(context.session)
        planned = questions.for_version(context.run.version_id)
        if not planned:
            raise StepPermanentError(
                "research reached the retrieval stage with no planned questions"
            )

        budget = RunBudget()
        # Empty at the start of every run, which is what makes Update Research
        # re-fetch rather than remember (`DEC-19`, `REQ-VER-003 AC-1`).
        cache = RetrievalCache(ttl_seconds=get_settings().retrieval_cache_ttl_seconds)
        summaries: list[JsonMapping] = []

        # Areas in plan order: a single area cannot consume more than its
        # reservation plus what the pool has returned (`DEC-04 §5`).
        for area_name in dict.fromkeys(question.area_name for question in planned):
            summaries.append(
                await self._research_area(
                    context, area_name, questions, budget, cache
                )
            )

        return {
            "areas": summaries,
            "tool_calls_left": budget.tool_calls_remaining,
        }

    async def _research_area(
        self,
        context: StepContext,
        area_name: str,
        questions: QuestionRepository,
        budget: RunBudget,
        cache: RetrievalCache,
    ) -> JsonMapping:
        activity = ActivityRepository(context.session)
        research = _research_session(context)
        evidence_repo = EvidenceRepository(
            context.session,
            # `DEC-08` rule 4: the subject's own site is primary. Resolved from
            # what the user supplied, once, so a re-run cannot retier evidence
            # written earlier under a different reading of the subject.
            subject_hosts=_subject_hosts(research),
        )
        version_id = context.run.version_id

        area_questions = questions.for_area(version_id, area_name)
        categories = _categories_for(area_questions)
        subject = research.subject or research.objective

        # `REQ-DOC-005`. Documents are excluded from *planning*
        # (`TARGETED_CATEGORIES`) because they cannot answer "tell me about
        # Acme Corp" — the planner would pick them for an area and every
        # question in it would come back `not_found`. They are still searched,
        # by being appended to every area that has documents to search, which
        # is the "caller that already has the target" path the tool contract
        # describes. Without this the whole document pipeline is dead: files
        # upload, extract, index, and are never once read.
        if _has_documents(context, research.id):
            categories = [*categories, ToolCategory.DOCUMENTS]

        activity.append(
            research.id,
            _area_label(area_name),
            ActivityStatus.IN_PROGRESS,
            version_id=version_id,
            # Category, never the tool or the query (`REQ-ACT-003`).
            tool_category=categories[0].value if categories else None,
        )

        # A round retrieves for every open question, and each question spans
        # every category the plan chose, so a round costs questions by
        # categories. Sizing the reservation by rounds and categories alone
        # left any area of two or more questions unable to afford even its
        # first round: the questions past the cap were skipped silently,
        # without a tool call, a failure, or a word in the report.
        reservation = budget.reserve(
            area_rounds(len(area_questions))
            * max(1, len(area_questions))
            * max(1, len(categories))
        )
        rounds = 0
        new_sources_this_round = 0
        all_sources: set[UUID] = set()
        # What the area could not reach. Accumulated here because the report
        # owes the reader both by name (`REQ-AGENT-009 AC-2`), and a step
        # checkpoint is the only place a later step can read them from.
        skipped: set[ToolCategory] = set()
        unread_sources = False
        verdict = self._assess(
            questions, area_name, version_id, rounds, new_sources_this_round, budget
        )

        while not verdict.is_terminal:
            open_questions = questions.open_for_area(version_id, area_name)
            if not open_questions:
                break

            before = len(all_sources)
            for question in open_questions:
                result = await self._research_question(
                    context,
                    question,
                    categories,
                    subject,
                    questions,
                    evidence_repo,
                    reservation,
                    cache,
                    all_sources,
                    research.id,
                )
                skipped.update(result.skipped)
                unread_sources = unread_sources or bool(result.failures)

            rounds += 1
            new_sources_this_round = len(all_sources) - before
            self._resolve_covered(questions, version_id)
            verdict = self._assess(
                questions, area_name, version_id, rounds, new_sources_this_round, budget
            )

        reservation.release()

        activity.append(
            research.id,
            _area_label(area_name),
            ActivityStatus.COMPLETE if all_sources else ActivityStatus.FAILED,
            version_id=version_id,
            tool_category=categories[0].value if categories else None,
        )

        return {
            "area": area_name,
            "rounds": rounds,
            "sources": len(all_sources),
            "decision": verdict.decision,
            "rationale": verdict.rationale,
            # Read back by `_area_outcomes`, which cannot recover either from
            # the question rows: a category never reached leaves no trace in
            # them, and neither does a source that could not be read.
            "skipped_categories": sorted(category.value for category in skipped),
            "unread_sources": unread_sources,
        }

    async def _research_question(
        self,
        context: StepContext,
        question: ResearchQuestion,
        categories: Sequence[ToolCategory],
        subject: str,
        questions: QuestionRepository,
        evidence_repo: EvidenceRepository,
        reservation: AreaReservation,
        cache: RetrievalCache,
        all_sources: set[UUID],
        research_session_id: UUID,
    ) -> RoundResult:
        """One question, one round: retrieve, extract, record.

        Returns the round so the area can accumulate what went unreached. The
        caller needs it: a skipped category and an unreadable source are both
        gaps the report owes the reader, and neither leaves a trace anywhere
        else.
        """
        result = await retrieve_area(
            question.area_name,
            categories,
            f"{subject}: {question.text}",
            self.registry,
            reservation,
            cache,
            session_id=research_session_id,
        )

        items = [item for hit in result.results for item in hit.items]
        grounded = await extract_from_items(items, question.text, self.provider)

        for found in grounded:
            row = evidence_repo.record(
                context.run.version_id, found.item, found.statement, found.excerpt
            )
            questions.link_evidence(question.id, row.id)
            all_sources.add(row.source_id)

        if not items and result.failures:
            # Every category returned a failure and nothing readable came back.
            # Terminal and honest rather than left open to burn the allocation
            # (`DEC-04 §3.3`), and it becomes an uncertainty in the report.
            questions.mark_unanswerable(
                question,
                {
                    "kinds": sorted({failure.kind for failure in result.failures}),
                    "categories": [category.value for category in categories],
                },
            )

        return result

    def _assess(
        self,
        questions: QuestionRepository,
        area_name: str,
        version_id: UUID,
        rounds: int,
        new_sources: int,
        budget: RunBudget,
    ) -> SufficiencyVerdict:
        return self.policy.assess(
            area_name,
            questions.for_area(version_id, area_name),
            questions.coverage(version_id),
            AreaProgress(
                rounds_used=rounds,
                new_sources_this_round=new_sources,
                budget_exhausted=budget.exhausted() is not None,
            ),
        )

    @staticmethod
    def _resolve_covered(questions: QuestionRepository, version_id: UUID) -> None:
        """Persist resolution as it is earned.

        Recomputing it later would read a different world: a question resolved
        in round one stays resolved even if round two's query returns less.
        """
        coverage = questions.coverage(version_id)
        for question in questions.for_version(version_id):
            if question.resolution_state is QuestionState.OPEN and is_resolved(
                coverage.get(question.id)
            ):
                questions.mark_resolved(question)


# --------------------------------------------------------------------------
# Stages 10 and 12
# --------------------------------------------------------------------------


@final
@dataclass(frozen=True, slots=True)
class SynthesizeHandler:
    """Write the report, then judge it before anyone sees it."""

    provider: LLMProvider

    async def execute(self, context: StepContext) -> JsonMapping:
        activity = ActivityRepository(context.session)
        questions = QuestionRepository(context.session)
        research = _research_session(context)
        version_id = context.run.version_id

        activity.append(
            research.id,
            LABEL_SYNTHESIZE,
            ActivityStatus.IN_PROGRESS,
            version_id=version_id,
        )

        inputs = _synthesis_inputs(context, questions)
        unresolved = [q.text for q in questions.unresolved_for_version(version_id)]

        # The outcome is computed first, because the gaps it names belong in
        # the report rather than only in this step's checkpoint
        # (`REQ-AGENT-009 AC-2`).
        outcome = summarise_run(_area_outcomes(context, questions), unresolved)

        result = await synthesize(
            inputs, unresolved, research.objective, self.provider
        )
        _persist_report(context, with_area_gaps(result.sections, outcome.gaps))

        # Stages 6, 8 and 9. Runs on the persisted claims, because conflict
        # detection compares evidence rows and confidence reads the conflict
        # state — neither can work on the in-memory draft.
        apply_trust(context.session, version_id)

        # Stage 11. After trust, because a chart of contested values is a
        # chart of a disagreement, and `REQ-VIZ-004 AC-3` wants that indicated
        # rather than smoothed over.
        _persist_visualizations(context, version_id)

        report = validate_version(context.session, version_id)
        if not report.passed:
            # A gate failure is a generation defect and never ships
            # (`REQ-EVID-017 AC-3`).
            raise StepPermanentError(report.summary())

        _close_version(context, outcome)

        activity.append(
            research.id,
            LABEL_SYNTHESIZE,
            ActivityStatus.COMPLETE,
            version_id=version_id,
        )
        return {
            "sections": len(result.sections),
            "dropped": list(result.dropped),
            "status": outcome.status.value,
            "gaps": list(outcome.gaps),
        }


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------


def _research_session(context: StepContext) -> ResearchSession:
    research = context.session.get(ResearchSession, context.run.session_id)
    if research is None:  # pragma: no cover - enforced by the foreign key
        raise StepPermanentError("the run references a missing research session")
    return research


def _objective_of(
    research: ResearchSession, context: StepContext
) -> ObjectiveInput:
    """What this run is researching.

    Normally the session's objective. On a **conversation run**
    (`REQ-CONV-003`) it is the objective *plus* the follow-up that triggered
    it: "find newer information about hiring" is not a new research project,
    it is the same subject with one question brought to the front.

    The follow-up is read from the message row already written against this
    version rather than passed through the run, because it is a user utterance
    and the conversation table is where those live. It reaches the model as
    material either way — `ObjectiveInput.as_material` wraps it `Untrusted`,
    and a question the reader typed is no more trusted than a page a tool
    fetched.
    """
    follow_up = ""
    if context.run.kind is RunKind.CONVERSATION:
        found = context.session.execute(
            select(ConversationMessage)
            .where(
                ConversationMessage.version_id == context.run.version_id,
                ConversationMessage.role == MessageRole.USER,
            )
            .order_by(ConversationMessage.seq)
        ).scalars().first()
        if found is not None:
            follow_up = found.content

    return ObjectiveInput(
        objective=(
            f"{research.objective}\n\nFollow-up to answer: {follow_up}"
            if follow_up
            else research.objective
        ),
        instructions=research.instructions,
        context_company=research.context_company,
        context_ticker=research.context_ticker,
        context_url=research.context_url,
    )


def _interpretation_from_checkpoint(
    context: StepContext, research: ResearchSession
) -> Interpretation:
    """Read stage 1's output from the durable record.

    The interpret step may have run in another process, so its result is read
    from `run_steps` rather than remembered. If the checkpoint is unreadable —
    an older run, a hand-edited row — the step fails rather than quietly
    planning against a different question set than the one stage 1 produced.
    """
    step = context.session.execute(
        select(RunStep).where(
            RunStep.run_id == context.run.id, RunStep.stage == INTERPRET_STAGE
        )
    ).scalar_one_or_none()

    checkpoint: Mapping[str, object] = step.checkpoint if step else {}
    raw_questions = checkpoint.get("questions")
    if not isinstance(raw_questions, list) or not raw_questions:
        raise StepPermanentError(
            "planning has no interpretation to work from; stage 1 recorded no questions"
        )

    note = checkpoint.get("interpretation_note")
    return Interpretation(
        subject=str(checkpoint.get("subject") or research.objective),
        interpretation_note=note if isinstance(note, str) else None,
        questions=[str(question) for question in raw_questions],
    )


def _subject_hosts(research: ResearchSession) -> frozenset[str]:
    """The research subject's own domains (`DEC-08` rule 4).

    Only from a URL the user actually supplied. A subject *name* is not a
    domain, and guessing `acme.com` from "Acme Corp" would hand `PRIMARY` tier
    to whoever owns that domain — which may be nobody related to the subject.
    """
    host = registrable_host(research.context_url)
    return frozenset({host}) if host else frozenset()


def _has_documents(context: StepContext, research_session_id: UUID) -> bool:
    """Whether this session has any document worth searching.

    Asked once per area rather than per question, and false for the vast
    majority of runs. The alternative — always appending the category — spends
    a tool call per question querying an empty index and then reports
    `not_found` as a gap in a report that has no gap.
    """
    settings = get_settings()
    return UploadRepository(
        context.session,
        max_upload_bytes=settings.max_upload_bytes,
        max_uploads_per_session=settings.max_uploads_per_session,
        max_session_upload_bytes=settings.max_session_upload_bytes,
    ).has_ready_documents(research_session_id)


def _categories_for(questions: Sequence[ResearchQuestion]) -> list[ToolCategory]:
    """The categories the plan chose for an area, in plan order."""
    categories: list[ToolCategory] = []
    for question in questions:
        for name in question.tool_categories:
            try:
                category = ToolCategory(str(name))
            except ValueError:
                # A category that existed when the plan was made but not now.
                continue
            if category not in categories:
                categories.append(category)
    return categories


def _synthesis_inputs(
    context: StepContext, questions: QuestionRepository
) -> list[SynthesisInput]:
    """Every piece of evidence in the version, with the question it answers."""
    version_id = context.run.version_id

    rows = context.session.execute(
        select(Evidence, Source)
        .join(Source, Source.id == Evidence.source_id)
        .where(Evidence.version_id == version_id)
        .order_by(Evidence.extracted_at, Evidence.id)
    ).all()

    asked: dict[UUID, str] = {}
    for question in questions.for_version(version_id):
        for evidence_id in questions.evidence_ids_for(question.id):
            asked.setdefault(evidence_id, question.text)

    return [
        SynthesisInput(
            evidence_id=evidence.id,
            statement=evidence.content,
            excerpt=evidence.excerpt or "",
            source_name=source.name,
            question=asked.get(evidence.id, ""),
        )
        for evidence, source in rows
    ]


def _persist_report(context: StepContext, sections: Sequence[Section]) -> None:
    """Write sections and claims, replacing anything a previous attempt left.

    A retry must not double the report. The version is not closed yet, so
    clearing the previous attempt is safe, and it is what keeps this step
    idempotent under the runner's reclaim rules.
    """
    session = context.session
    version_id = context.run.version_id

    for claim in session.execute(
        select(Claim).where(Claim.version_id == version_id)
    ).scalars():
        session.delete(claim)
    for existing in session.execute(
        select(ReportSection).where(ReportSection.version_id == version_id)
    ).scalars():
        session.delete(existing)
    session.flush()

    for section in sections:
        row = ReportSection(
            version_id=version_id,
            title=section.title,
            body={"blocks": [{"kind": "claims"}]},
            ordering=section.ordering,
            is_executive_summary=section.is_executive_summary,
        )
        session.add(row)
        session.flush()

        for drafted in section.claims:
            claim_row = Claim(
                version_id=version_id,
                section_id=row.id,
                text=drafted.text,
                claim_type=drafted.claim_type,
                assumptions=(
                    {"stated": list(drafted.assumptions)}
                    if drafted.claim_type is ClaimType.FORECAST
                    else None
                ),
                is_important=drafted.is_important,
            )
            session.add(claim_row)
            session.flush()

            for evidence_id in drafted.evidence_ids:
                session.add(
                    ClaimEvidence(
                        claim_id=claim_row.id,
                        evidence_id=evidence_id,
                        role=EvidenceRole.SUPPORTING,
                    )
                )
            session.flush()


def _area_outcomes(
    context: StepContext, questions: QuestionRepository
) -> list[AreaOutcome]:
    """Rebuild per-area outcomes from what was persisted.

    Read back rather than carried forward: the research step may have run in a
    different process, and what the report says about coverage has to come from
    the durable record rather than from a variable that happened to survive.

    Two of the three inputs are question rows. The third — which categories an
    area never reached, and whether a source could not be read — is read from
    the research step's checkpoint, because neither leaves a mark on a question
    row: a category that was skipped retrieved nothing to record.
    """
    version_id = context.run.version_id
    coverage = questions.coverage(version_id)
    unreached = _unreached_by_area(context)
    outcomes: list[AreaOutcome] = []

    for area_name in dict.fromkeys(
        question.area_name for question in questions.for_version(version_id)
    ):
        area_questions = questions.for_area(version_id, area_name)
        sources = sum(
            coverage[question.id].distinct_sources
            for question in area_questions
            if question.id in coverage
        )
        # `unanswerable` is terminal, not outstanding. `CoverageGatePolicy`
        # treats it that way (`DEC-04 §3.3`) and this must agree with it:
        # counting it as open reported an area that concluded honestly as one
        # that ran out of road, and turned the run's termination reason from
        # sufficiency into a ceiling.
        still_open = [
            question
            for question in area_questions
            if question.resolution_state is QuestionState.OPEN
        ]
        resolved = len(area_questions) - len(still_open)
        skipped, unread = unreached.get(area_name, ((), False))
        outcomes.append(
            AreaOutcome(
                area_name=area_name,
                verdict=SufficiencyVerdict(
                    decision="ceiling_reached" if still_open else "sufficient",
                    rationale=(
                        f"{resolved} of {len(area_questions)} questions resolved"
                    ),
                    area_name=area_name,
                ),
                evidence_count=sources,
                failures=_UNREAD_MARKER if unread else (),
                skipped_categories=skipped,
            )
        )

    return outcomes


def _unreached_by_area(
    context: StepContext,
) -> dict[str, tuple[tuple[ToolCategory, ...], bool]]:
    """What each area could not reach, from the research step's checkpoint.

    A missing or unreadable checkpoint yields nothing rather than raising: the
    gaps it carries make the report more honest, and failing the run for want
    of them would be the less honest outcome.
    """
    step = context.session.execute(
        select(RunStep).where(
            RunStep.run_id == context.run.id, RunStep.stage == RESEARCH_STAGE
        )
    ).scalar_one_or_none()

    areas = (step.checkpoint or {}).get("areas") if step else None
    if not isinstance(areas, list):
        return {}

    unreached: dict[str, tuple[tuple[ToolCategory, ...], bool]] = {}
    for entry in areas:
        if not isinstance(entry, dict):
            continue
        name = entry.get("area")
        if not isinstance(name, str):
            continue

        raw = entry.get("skipped_categories")
        categories: list[ToolCategory] = []
        if isinstance(raw, list):
            for value in raw:
                try:
                    categories.append(ToolCategory(str(value)))
                except ValueError:
                    # A category that existed when the run started but not now.
                    continue

        unreached[name] = (tuple(categories), bool(entry.get("unread_sources")))

    return unreached


_UNREAD_MARKER: tuple[ToolFailure, ...] = (
    ToolFailure(
        kind="error",
        message="a source could not be read during this area's research",
        tool="",
        category=ToolCategory.WEB_SEARCH,
    ),
)
"""A stand-in failure, so `outcome._gaps` can say a source went unread.

`AreaOutcome.failures` is only ever asked *whether* it is empty, and the
provider detail that would fill it truthfully must never reach a user
(`REQ-SEC-010`, `REQ-ACT-003`). One classified marker says the true thing
without carrying anything that cannot be shown.
"""


def _close_version(context: StepContext, outcome: RunOutcome) -> None:
    """Record which outcome the version reached, and why the run stopped.

    The runner closes the version when the last step completes; this is what
    says whether it closed as complete or as complete-with-gaps, which is the
    distinction `REQ-AGENT-009` exists to protect.

    The termination reason is written here because the runner defers to it:
    `JobRunner._finish_run` only defaults to `sufficiency` when the handler
    recorded nothing, so a ceiling this step observed has to be written now or
    be recorded as sufficiency forever (`REQ-AGENT-005 AC-4`, `DEC-04 §6.2`).
    """
    version = context.session.get(ResearchVersion, context.run.version_id)
    if version is not None:
        version.status = outcome.status
    context.run.termination_reason = outcome.termination_reason
    context.session.flush()


def build_handlers(
    provider: LLMProvider, registry: ToolRegistry
) -> dict[str, StepHandler]:
    """The stage-to-handler map the runner is wired with."""
    return {
        INTERPRET_STAGE: InterpretHandler(provider=provider),
        PLAN_STAGE: PlanHandler(provider=provider, registry=registry),
        RESEARCH_STAGE: ResearchHandler(provider=provider, registry=registry),
        SYNTHESIZE_STAGE: SynthesizeHandler(provider=provider),
        PRIORITIZE_STAGE: PrioritizeHandler(),
        COMPARE_STAGE: CompareHandler(),
    }


# --------------------------------------------------------------------------
# Update Research — `REQ-VER-003..007`, `DEC-19`, `DEC-20`
# --------------------------------------------------------------------------


@final
@dataclass(frozen=True, slots=True)
class PrioritizeHandler:
    """Carry the previous plan forward, most-likely-changed areas first.

    Replaces interpret and plan on an update. The questions are copied rather
    than re-planned so both versions answer the same questions; the order is
    what changes, so the budget reaches volatile and stale areas before stable
    ones (`REQ-VER-003 AC-3`). No model call: the ordering rules are three
    tiers a person can read in the checkpoint.
    """

    async def execute(self, context: StepContext) -> JsonMapping:
        session = context.session
        questions = QuestionRepository(session)
        research = _research_session(context)
        version_id = context.run.version_id

        base = _base_version(context)

        existing = questions.for_version(version_id)
        if existing:
            # A re-run after a crash: the plan is already committed.
            return {
                "base_version_id": str(base.id),
                "areas": len({question.area_name for question in existing}),
                "replanned": False,
            }

        activity = ActivityRepository(session)
        activity.append(
            research.id, LABEL_PRIORITIZE, ActivityStatus.IN_PROGRESS, version_id=version_id
        )

        histories = _area_histories(session, base.id)
        if not histories:
            raise StepPermanentError(
                "the version being updated planned no questions to re-check"
            )

        ordered = prioritize_areas(histories, now=utcnow())
        questions.persist_plan(
            version_id,
            [(entry.area.name, entry.area.questions, entry.area.categories) for entry in ordered],
        )

        activity.append(
            research.id, LABEL_PRIORITIZE, ActivityStatus.COMPLETE, version_id=version_id
        )
        return {
            "base_version_id": str(base.id),
            "areas": [
                {
                    "area": entry.area.name,
                    "priority": entry.priority.name.lower(),
                    "reason": entry.reason,
                }
                for entry in ordered
            ],
            "replanned": True,
        }


@final
@dataclass(frozen=True, slots=True)
class CompareHandler:
    """Write What's Changed onto the new version (`REQ-VER-004`, `REQ-VER-006`).

    Runs after synthesis, before the runner closes the version, so the summary
    is part of the immutable version rather than something added to it later.

    **A comparison that cannot be made does not fail the version.** The report
    itself is complete and validated by the time this runs; losing it because a
    summary could not be assembled would punish the reader for a defect in the
    summary. The version records that What's Changed is unavailable instead.
    """

    async def execute(self, context: StepContext) -> JsonMapping:
        session = context.session
        research = _research_session(context)
        version_id = context.run.version_id
        version = session.get(ResearchVersion, version_id)
        if version is None:  # pragma: no cover - enforced by the foreign key
            raise StepPermanentError("the run references a missing version")

        base = _base_version(context)
        activity = ActivityRepository(session)
        activity.append(
            research.id, LABEL_COMPARE, ActivityStatus.IN_PROGRESS, version_id=version_id
        )

        try:
            summary = compare_versions(session, base, version)
        except Exception:
            logger.exception("comparison against version %s failed", base.version_number)
            version.change_summary = {
                "schema_version": 1,
                "available": False,
                "compared_with": {
                    "version_id": str(base.id),
                    "version_number": base.version_number,
                    "created_at": base.created_at.isoformat(),
                },
            }
            session.flush()
            activity.append(
                research.id, LABEL_COMPARE, ActivityStatus.FAILED, version_id=version_id
            )
            return {"available": False}

        version.change_summary = {"available": True, **summary.as_json()}
        session.flush()
        activity.append(
            research.id, LABEL_COMPARE, ActivityStatus.COMPLETE, version_id=version_id
        )
        return {
            "available": True,
            "changes": len(summary.changes),
            "unchanged": summary.unchanged,
        }


def _base_version(context: StepContext) -> ResearchVersion:
    """The version this run's version is measured against.

    `previous_version_id`, set when the version was opened: for an update, the
    most recent version that completed, so a failed attempt in between is never
    the baseline.
    """
    version = context.session.get(ResearchVersion, context.run.version_id)
    base_id = version.previous_version_id if version else None
    base = context.session.get(ResearchVersion, base_id) if base_id else None
    if base is None:
        raise StepPermanentError("there is no earlier version to compare or update from")
    return base


def _area_histories(session: Session, version_id: UUID) -> list[AreaHistory]:
    """The previous version's areas, in its plan order, with their evidence."""
    planned = QuestionRepository(session).for_version(version_id)

    cited: dict[str, list[CitedEvidence]] = {}
    for area_name, content, published_at, retrieved_at in session.execute(
        select(ResearchQuestion.area_name, Evidence.content, Source.published_at, Source.retrieved_at)
        .join(QuestionEvidence, QuestionEvidence.question_id == ResearchQuestion.id)
        .join(Evidence, Evidence.id == QuestionEvidence.evidence_id)
        .join(Source, Source.id == Evidence.source_id)
        .where(ResearchQuestion.version_id == version_id)
    ).all():
        cited.setdefault(area_name, []).append(
            CitedEvidence(content=content, published_at=published_at, retrieved_at=retrieved_at)
        )

    histories: list[AreaHistory] = []
    for area_name in dict.fromkeys(question.area_name for question in planned):
        area_questions = [question for question in planned if question.area_name == area_name]
        histories.append(
            AreaHistory(
                name=area_name,
                questions=tuple(question.text for question in area_questions),
                categories=tuple(
                    dict.fromkeys(
                        str(category)
                        for question in area_questions
                        for category in question.tool_categories
                    )
                ),
                evidence=tuple(cited.get(area_name, ())),
            )
        )
    return histories


def _persist_visualizations(context: StepContext, version_id: UUID) -> None:
    """Offer each section a chart, and take no for an answer.

    `REQ-VIZ-001 AC-1`: charts appear without the reader asking. `AC-2`: data
    unsuited to visualization is not forced into one — which is most sections
    most of the time, and `select_visualization` returning `None` is the normal
    case rather than a failure.

    Built from the evidence a section's claims already cite, so `REQ-VIZ-002
    AC-1` holds without a second path to evidence: a point can only exist if a
    claim in that section was supported by the row it came from.
    """
    session = context.session

    sections = (
        session.execute(
            select(ReportSection)
            .where(ReportSection.version_id == version_id)
            .order_by(ReportSection.ordering)
        )
        .scalars()
        .all()
    )

    ordering = 0
    for section in sections:
        rows = session.execute(
            select(Evidence)
            .join(ClaimEvidence, ClaimEvidence.evidence_id == Evidence.id)
            .join(Claim, Claim.id == ClaimEvidence.claim_id)
            .where(Claim.section_id == section.id)
            .order_by(Evidence.period_end, Evidence.extracted_at)
        ).all()

        chartable = [
            Chartable(
                evidence_id=evidence.id,
                # The period is the axis. Evidence without one cannot be placed
                # in a series, and inventing a position for it would be drawing
                # a trend out of unordered numbers.
                label=evidence.period_end.isoformat() if evidence.period_end else "",
                value=evidence.value_normalized,
                metric=classify_metric(evidence.content),
                currency=evidence.currency,
                comparable=evidence.normalization is NormalizationStatus.NORMALIZED,
            )
            for (evidence,) in rows
        ]

        spec = select_visualization(section.title, chartable)
        if spec is None:
            continue

        visualization = Visualization(
            version_id=version_id,
            section_id=section.id,
            kind=spec.kind,
            spec=spec.as_json(),
            ordering=ordering,
        )
        session.add(visualization)
        session.flush()
        ordering += 1

        # `REQ-VIZ-004 AC-1`, `AC-2`: the chart exposes its sources, and all of
        # them.
        for evidence_id in spec.evidence_ids:
            session.add(
                VisualizationEvidence(
                    visualization_id=visualization.id, evidence_id=evidence_id
                )
            )
        session.flush()
