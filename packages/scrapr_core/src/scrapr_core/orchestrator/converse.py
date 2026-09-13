"""Follow-up conversation (`REQ-CONV-001..008`).

The workspace's promise is that a reader can interrogate the research, not just
read it. That means answering from what was already gathered where the evidence
allows, and going and finding more where it does not.

**Answers are grounded, not generated** (`REQ-CONV-002`). The question is
answered from the session's evidence, the answer cites what it used, and
`AC-3` forbids inventing evidence to fill a gap. This is the same rule the
report lives under, applied to a surface where it is much easier to break: a
chatty answer that sounds right is the most natural thing a model produces.

**The claim taxonomy holds in conversation** (`REQ-CONV-008 AC-2`). An answer
is typed the way a report claim is — fact, analysis, forecast, uncertainty —
so "the evidence does not say" remains a first-class answer rather than
something the model has to be talked out of avoiding.

**Insufficient evidence triggers real research** (`REQ-CONV-003`), not an
apology. "Find newer information about hiring" runs the Phase 1 pipeline
against the same tools under the same rules, and the new evidence joins the
session under the same evidence rules (`AC-2`) with activity emitted (`AC-3`).
Deciding *whether* to research is a separate, cheap judgement made before any
retrieval, because the expensive mistake is researching a question the report
already answers.

**The user's question is untrusted** (§9). It arrives from outside and is
rendered as material, never as instruction — the same boundary the objective
crosses at interpretation.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum, unique
from typing import final
from uuid import UUID

from pydantic import BaseModel, Field

from scrapr_core.db.enums import ClaimType
from scrapr_core.llm.contract import LLMProvider, ModelTier, UntrustedDocument
from scrapr_core.security.trust import SourceRef, Trusted, Untrusted

MAX_ANSWER_CHARS: int = 4000
"""Where an answer is trimmed. A bound the code applies, never one the
schema enforces — see `Answer.text`."""

__all__ = [
    "MAX_ANSWER_CHARS",
    "Answer",
    "ConversationContext",
    "Intent",
    "IntentVerdict",
    "answer_question",
    "classify_intent",
]


@unique
class Intent(StrEnum):
    """What the user is asking for.

    Four, because they lead to genuinely different work. Collapsing them would
    mean either researching every question — expensive, and wrong for "explain
    this differently" — or researching none, which fails `REQ-CONV-003`.
    """

    ANSWER = "answer"
    """Answerable from evidence already gathered (`REQ-CONV-002`)."""

    RESEARCH = "research"
    """Needs new retrieval (`REQ-CONV-003`, `REQ-CONV-005`)."""

    REFRAME = "reframe"
    """Same findings, different words (`REQ-CONV-004`). Explicitly not a
    re-analysis: `AC-1` says reframing must not change the underlying claims."""

    VISUALIZE = "visualize"
    """A chart or table of data already held (`REQ-CONV-006`, `REQ-VIZ-005`)."""


INTENT_INSTRUCTION = Trusted(
    "Decide what the reader is asking for. The material contains their "
    "question and a summary of what the existing research already covers.\n\n"
    "- answer: the existing research can answer it.\n"
    "- research: it asks for information the research does not have, or asks "
    "for something newer, or asks about a different company or subject.\n"
    "- reframe: it asks for the same findings explained differently, more "
    "simply, or at a different length.\n"
    "- visualize: it asks for a chart, graph, table or other visual.\n\n"
    "Choose research only when the existing evidence genuinely cannot answer. "
    "Researching a question the report already answers wastes the reader's "
    "time and money."
)

ANSWER_INSTRUCTION = Trusted(
    "Answer the reader's question from the evidence in the material.\n\n"
    "Cite the evidence ids you rely on. Every factual statement needs one.\n\n"
    "Type your answer as exactly one of:\n"
    "- fact: the evidence states it. Cite the ids.\n"
    "- analysis: your reading of the evidence. Cite what you read.\n"
    "- forecast: about the future. State the assumptions.\n"
    "- uncertainty: the evidence does not settle it.\n\n"
    "If the evidence does not answer the question, say so plainly and type the "
    "answer uncertainty. Do not fill the gap with something that sounds right. "
    "Never invent an evidence id, and never describe evidence that is not in "
    "the material.\n\n"
    "Write for a reader who is looking at the report. Do not restate the "
    "objective back to them."
)

REFRAME_INSTRUCTION = Trusted(
    "Restate the findings in the material as the reader asked.\n\n"
    "Change the words, never the substance. A fact stays a fact, an "
    "uncertainty stays an uncertainty, and a hedge stays hedged — a simpler "
    "explanation is not a more confident one. Cite the same evidence the "
    "original findings cited."
)


@final
@dataclass(frozen=True, slots=True)
class ConversationContext:
    """What the agent knows when answering.

    Assembled from persisted rows rather than carried in memory: `REQ-CONV-001
    AC-3` requires context survive a reload and a new session, so there is no
    in-process state to lose.
    """

    subject: str
    objective: str
    evidence: Sequence[EvidenceRef]
    recent_turns: Sequence[tuple[str, str]] = ()
    """Prior turns as (role, content), oldest first. Bounded by the caller."""


@final
@dataclass(frozen=True, slots=True)
class EvidenceRef:
    """One piece of evidence, as the conversation sees it."""

    evidence_id: UUID
    statement: str
    excerpt: str
    source_name: str


class IntentVerdict(BaseModel):
    """What the model decided the question needs."""

    intent: Intent
    reason: str = ""
    """Diagnostic only, and deliberately unbounded.

    A `max_length` here rejected a real answer in testing: the model wrote a
    considered explanation, Pydantic refused it, and the reader's question
    failed outright over a string nothing reads. A length cap on a field the
    *model* fills turns a stylistic overrun into an outage, and this one was
    not even load-bearing."""


class Answer(BaseModel):
    """A conversational answer, typed and cited like a report claim."""

    text: str = Field(min_length=1)
    """No upper bound at the schema. An over-long answer is trimmed below;
    rejecting it would fail the question entirely, and a reader who asked
    something reasonable would see an error because the answer was wordy."""
    claim_type: ClaimType
    evidence_ids: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


@final
@dataclass(frozen=True, slots=True)
class GroundedAnswer:
    """An answer after the citation rules have been applied."""

    text: str
    claim_type: ClaimType
    evidence_ids: tuple[UUID, ...]
    assumptions: tuple[str, ...] = ()
    dropped: tuple[str, ...] = ()


def _material(
    question: str, context: ConversationContext, *, with_evidence: bool
) -> list[UntrustedDocument]:
    """Render the question and the session's knowledge as material.

    The question is `Untrusted` (§9). It came from outside, and the fact that
    the outside is the product's own user changes nothing — a rule with one
    exception is a rule nobody can rely on, and a user can paste a page into a
    question as easily as a tool can retrieve one.
    """
    origin = SourceRef(kind="conversation", locator="question")
    documents = [
        UntrustedDocument(
            content=Untrusted(question, origin), label="the reader's question"
        ),
        UntrustedDocument(
            content=Untrusted(
                f"Subject: {context.subject}\nObjective: {context.objective}",
                origin,
            ),
            label="what this research is about",
        ),
    ]

    if context.recent_turns:
        documents.append(
            UntrustedDocument(
                content=Untrusted(
                    "\n\n".join(
                        f"{role}: {content}" for role, content in context.recent_turns
                    ),
                    origin,
                ),
                label="earlier in this conversation",
            )
        )

    if with_evidence and context.evidence:
        documents.append(
            UntrustedDocument(
                content=Untrusted(
                    "\n\n".join(
                        f"evidence id: {item.evidence_id}\n"
                        f"source: {item.source_name}\n"
                        f"statement: {item.statement}\n"
                        f'excerpt: "{item.excerpt}"'
                        for item in context.evidence
                    ),
                    origin,
                ),
                label="evidence gathered for this research",
            )
        )

    return documents


async def classify_intent(
    question: str,
    context: ConversationContext,
    provider: LLMProvider,
    tier: ModelTier = ModelTier.CHEAP,
) -> IntentVerdict:
    """Decide what the question needs before spending anything on it.

    `CHEAP` tier and no evidence in the material: this is a routing decision
    over a short question, and sending the whole evidence set to make it would
    cost more than answering.
    """
    result = await provider.complete_structured(
        INTENT_INSTRUCTION,
        _material(question, context, with_evidence=False),
        IntentVerdict,
        tier,
    )
    return result.value


async def answer_question(
    question: str,
    context: ConversationContext,
    provider: LLMProvider,
    *,
    reframe: bool = False,
    tier: ModelTier = ModelTier.STANDARD,
) -> GroundedAnswer:
    """Answer from the session's evidence, and enforce the citation rules.

    The rules are the report's, because an answer is a claim: a `fact` citing
    nothing is re-typed `analysis`, an `analysis` citing nothing becomes an
    `uncertainty`, and a `forecast` without assumptions is not published as
    one. `REQ-CONV-008 AC-2` requires the distinction hold in conversation, and
    holding it by rule rather than by instruction is what makes that true.
    """
    if not context.evidence:
        # `REQ-CONV-002 AC-3`: nothing to ground an answer in, so the honest
        # answer is that there is nothing to answer from.
        return GroundedAnswer(
            text=(
                "There is no evidence in this research yet, so there is "
                "nothing to answer from."
            ),
            claim_type=ClaimType.UNCERTAINTY,
            evidence_ids=(),
        )

    known = {str(item.evidence_id): item.evidence_id for item in context.evidence}

    result = await provider.complete_structured(
        REFRAME_INSTRUCTION if reframe else ANSWER_INSTRUCTION,
        _material(question, context, with_evidence=True),
        Answer,
        tier,
    )
    return _apply_rules(result.value, known)


def _apply_rules(answer: Answer, known: dict[str, UUID]) -> GroundedAnswer:
    """The report's claim rules, applied to an answer."""
    dropped: list[str] = []

    cited = tuple(
        known[raw] for raw in dict.fromkeys(answer.evidence_ids) if raw in known
    )
    invented = [raw for raw in answer.evidence_ids if raw not in known]
    if invented:
        # `REQ-CONV-002 AC-3`. An id that is not in the material was made up,
        # and a citation pointing nowhere is worse than no citation: it looks
        # checkable and is not.
        dropped.append(f"{len(invented)} invented evidence id(s)")

    claim_type = answer.claim_type
    text = answer.text.strip()
    if len(text) > MAX_ANSWER_CHARS:
        # Trimmed, not rejected. The alternative is a reader losing their
        # question because the model was verbose.
        text = text[:MAX_ANSWER_CHARS].rstrip() + "…"
        dropped.append("answer trimmed to length")

    if claim_type is ClaimType.FORECAST:
        assumptions = tuple(a.strip() for a in answer.assumptions if a.strip())
        if not assumptions:
            # `REQ-SYNTH-009` in conversation: a forecast without assumptions
            # is an opinion wearing a prediction's clothes.
            return GroundedAnswer(
                text=text,
                claim_type=ClaimType.UNCERTAINTY,
                evidence_ids=cited,
                dropped=(*dropped, "forecast without assumptions, re-typed"),
            )
        return GroundedAnswer(
            text=text,
            claim_type=claim_type,
            evidence_ids=cited,
            assumptions=assumptions,
            dropped=tuple(dropped),
        )

    if claim_type is ClaimType.FACT and not cited:
        claim_type = ClaimType.ANALYSIS
        dropped.append("fact citing nothing, re-typed as analysis")
    elif claim_type is ClaimType.ANALYSIS and not cited:
        claim_type = ClaimType.UNCERTAINTY
        dropped.append("analysis citing nothing, re-typed as uncertainty")

    return GroundedAnswer(
        text=text,
        claim_type=claim_type,
        evidence_ids=cited,
        dropped=tuple(dropped),
    )
