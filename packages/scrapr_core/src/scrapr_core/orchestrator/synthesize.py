"""Stage 10 — Synthesize the report (`REQ-SYNTH-001`, `-003`, `-004`, `-005`).

In: the evidence gathered, and the questions that were never answered. Out:
typed claims grouped into ordered sections, with an executive summary.

Four rules are imposed here rather than asked of the model:

* **Every fact claim carries the evidence it came from** (`REQ-EVID-017`). A
  claim the model typed `fact` while citing nothing is re-typed `analysis` —
  it may still be a reasonable reading, but it is not a fact, and the validation
  gate would reject the version rather than let it through.
* **Every forecast states its assumptions** (`REQ-SYNTH-009 AC-1`). One that
  does not is dropped, not published with an empty list.
* **Unresolved questions become uncertainty claims** (`REQ-SYNTH-010 AC-1`,
  `DEC-04 §6.3`), written from the question text rather than by the model. The
  gap is named, and never filled with speculation dressed as analysis.
* **A section with no claims is not emitted** (`REQ-SYNTH-004 AC-3`), because a
  heading with nothing under it implies coverage that does not exist.

Sections carry an explicit persisted order (`REQ-SYNTH-005`), and the executive
summary is section zero: `REQ-SYNTH-003` requires the report to open with it,
and ordering is what makes that survive a reload and an export.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import final
from uuid import UUID

from pydantic import BaseModel, Field

from scrapr_core.db.enums import ClaimType
from scrapr_core.llm.contract import LLMProvider, ModelTier, UntrustedDocument
from scrapr_core.security.trust import SourceRef, Trusted, Untrusted

__all__ = [
    "DraftClaim",
    "DraftSection",
    "SynthesisDraft",
    "SynthesisInput",
    "SynthesisResult",
    "synthesize",
]

SUMMARY_TITLE = "Executive summary"

INSTRUCTION = Trusted(
    "Write a research report from the evidence in the material.\n\n"
    "Open with an executive summary: the key findings, what they add up to, and "
    "the risks worth knowing. Then write the sections the evidence actually "
    "supports, in the order that answers the objective best. Do not write a "
    "section you have no evidence for.\n\n"
    "Every claim must be typed as exactly one of:\n"
    "- fact: the evidence states it. Cite the evidence ids it came from.\n"
    "- analysis: your reading of the evidence. Cite what you read.\n"
    "- forecast: about the future. State the assumptions it depends on.\n"
    "- uncertainty: something the evidence does not settle.\n\n"
    "Cite by evidence id, using only ids present in the material. A claim you "
    "cannot cite is analysis at best, and if you cannot support it at all, do "
    "not write it.\n\n"
    "Where the evidence does not answer something, say so plainly. Never fill a "
    "gap with speculation and never present speculation as analysis."
)


class DraftClaim(BaseModel):
    """One claim as the model wrote it, before the rules are applied."""

    text: str = Field(min_length=1, max_length=2000)
    claim_type: ClaimType
    evidence_ids: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    is_important: bool = False


class DraftSection(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    claims: list[DraftClaim] = Field(default_factory=list)


class SynthesisDraft(BaseModel):
    """What the model returns."""

    summary: list[DraftClaim] = Field(default_factory=list)
    sections: list[DraftSection] = Field(default_factory=list)


@final
@dataclass(frozen=True, slots=True)
class SynthesisInput:
    """One piece of evidence, as synthesis sees it."""

    evidence_id: UUID
    statement: str
    excerpt: str
    source_name: str
    question: str


@final
@dataclass(frozen=True, slots=True)
class Claim:
    """A claim that has survived the rules."""

    text: str
    claim_type: ClaimType
    evidence_ids: tuple[UUID, ...] = ()
    assumptions: tuple[str, ...] = ()
    is_important: bool = False


@final
@dataclass(frozen=True, slots=True)
class Section:
    """A section with at least one claim, and an explicit place in the report."""

    title: str
    ordering: int
    claims: tuple[Claim, ...]
    is_executive_summary: bool = False


@final
@dataclass(frozen=True, slots=True)
class SynthesisResult:
    sections: tuple[Section, ...] = ()
    dropped: tuple[str, ...] = field(default=())
    """Claims the rules removed, with the reason. Kept so a gate failure or a
    thin report can be explained without re-running the model."""


def _coerce(
    draft: DraftClaim, known_evidence: Mapping[str, UUID]
) -> tuple[Claim | None, str | None]:
    """Apply the claim rules to one drafted claim.

    Returns the claim to keep, or `None` plus the reason it was dropped.
    """
    cited = tuple(
        known_evidence[raw] for raw in dict.fromkeys(draft.evidence_ids) if raw in known_evidence
    )
    claim_type = draft.claim_type
    text = draft.text.strip()

    if claim_type is ClaimType.FACT and not cited:
        # Not a fabrication necessarily — but not a fact either. Re-typing is
        # honest where dropping would lose a reasonable reading, and the
        # validation gate would reject the version if it stayed a fact.
        claim_type = ClaimType.ANALYSIS

    if claim_type is ClaimType.FORECAST:
        assumptions = tuple(a.strip() for a in draft.assumptions if a.strip())
        if not assumptions:
            return None, f"forecast without assumptions: {text[:80]}"
        return (
            Claim(
                text=text,
                claim_type=claim_type,
                evidence_ids=cited,
                assumptions=assumptions,
                is_important=draft.is_important,
            ),
            None,
        )

    if claim_type is ClaimType.ANALYSIS and not cited:
        # Analysis is a reading *of evidence*. With nothing read, it is
        # speculation, and `REQ-SYNTH-010 AC-2` forbids presenting that as
        # analysis. It becomes an uncertainty instead, which is what it is.
        claim_type = ClaimType.UNCERTAINTY

    return (
        Claim(
            text=text,
            claim_type=claim_type,
            evidence_ids=cited,
            is_important=draft.is_important,
        ),
        None,
    )


def _uncertainty_claims(unresolved: Sequence[str]) -> tuple[Claim, ...]:
    """Turn every unanswered question into a named gap (`DEC-04 §6.3`).

    Written from the question, not by the model: the report is stating what it
    could not establish, and that sentence must not be an opportunity to
    speculate about the answer.
    """
    return tuple(
        Claim(
            text=(
                f"The evidence gathered does not answer this: {question}"
            ),
            claim_type=ClaimType.UNCERTAINTY,
            is_important=True,
        )
        for question in unresolved
    )


async def synthesize(
    evidence: Sequence[SynthesisInput],
    unresolved_questions: Sequence[str],
    objective: str,
    provider: LLMProvider,
    tier: ModelTier = ModelTier.STANDARD,
) -> SynthesisResult:
    """Turn evidence into a report.

    With no evidence at all there is nothing to synthesize, and the honest
    output is a report that is entirely gaps rather than a model's best guess at
    what the answer probably is.
    """
    gaps = _uncertainty_claims(unresolved_questions)

    if not evidence:
        if not gaps:
            return SynthesisResult()
        return SynthesisResult(
            sections=(
                Section(
                    title="What could not be established",
                    ordering=0,
                    claims=gaps,
                    is_executive_summary=True,
                ),
            )
        )

    known = {str(item.evidence_id): item.evidence_id for item in evidence}
    origin = SourceRef(kind="synthesis", locator="evidence")

    material = [
        UntrustedDocument(
            content=Untrusted(objective, origin), label="research objective"
        ),
        UntrustedDocument(
            content=Untrusted(
                "\n\n".join(
                    f"evidence id: {item.evidence_id}\n"
                    f"question: {item.question}\n"
                    f"source: {item.source_name}\n"
                    f"statement: {item.statement}\n"
                    f'excerpt: "{item.excerpt}"'
                    for item in evidence
                ),
                origin,
            ),
            label="evidence",
        ),
    ]

    draft = (
        await provider.complete_structured(INSTRUCTION, material, SynthesisDraft, tier)
    ).value

    dropped: list[str] = []
    sections: list[Section] = []

    summary_claims = _apply(draft.summary, known, dropped)
    if summary_claims or gaps:
        sections.append(
            Section(
                title=SUMMARY_TITLE,
                ordering=0,
                claims=(*summary_claims, *gaps),
                is_executive_summary=True,
            )
        )

    for drafted in draft.sections:
        claims = _apply(drafted.claims, known, dropped)
        if not claims:
            # A heading with nothing under it implies coverage that does not
            # exist (`REQ-SYNTH-004 AC-3`).
            dropped.append(f"empty section: {drafted.title}")
            continue
        sections.append(
            Section(
                title=drafted.title.strip(),
                ordering=len(sections),
                claims=claims,
            )
        )

    return SynthesisResult(sections=tuple(sections), dropped=tuple(dropped))


def _apply(
    drafts: Sequence[DraftClaim],
    known: Mapping[str, UUID],
    dropped: list[str],
) -> tuple[Claim, ...]:
    kept: list[Claim] = []
    for draft in drafts:
        claim, reason = _coerce(draft, known)
        if claim is None:
            dropped.append(reason or "dropped")
        else:
            kept.append(claim)
    return tuple(kept)
