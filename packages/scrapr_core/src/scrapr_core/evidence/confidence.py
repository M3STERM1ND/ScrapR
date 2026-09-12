"""Confidence assignment (`DEC-09`, `REQ-EVID-015`, `REQ-EVID-016`).

Three discrete levels, computed by a **pure function** of four inputs: source
tier, distinct corroboration, conflict state, and evidence recency. Those are
exactly the inputs `AC-3` names, and computing rather than asking a model is
what makes `REQ-EVID-016 AC-2` — the relationship is consistent across runs —
true by construction rather than by hope.

**Discrete, not numeric** (`DEC-09 §2`). The inputs are a three-valued enum, a
small integer and two booleans; a score of `0.73` derived from those is
arithmetic theatre, and users read `0.73` against `0.71` as meaningful when it
is not. Three levels are what a reader can act on: trust it, check it, treat it
as a lead.

**Every claim carries one.** `REQ-EVID-015` says so without exception, and an
earlier draft of `DEC-09` exempted forecasts and uncertainties — the intuitive
move, and a reinterpretation of a MUST. What varies by claim type is the
ceiling, not whether a level exists.

**The explanation is free.** It is generated from the same inputs as the level,
so it cannot drift from the thing it explains — the same argument `DEC-04 §3.5`
makes for the sufficiency rationale, and what `REQ-DATA-012` needs.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique
from typing import final

from scrapr_core.db.enums import AuthorityTier, ClaimType

__all__ = [
    "Confidence",
    "ConfidenceInputs",
    "assess",
]


@unique
class Confidence(StrEnum):
    """What a reader should do with a claim (`DEC-09 §3`)."""

    HIGH = "high"
    """Well-sourced and uncontested. Act on it."""

    MODERATE = "moderate"
    """Sourced, but thinly or with a caveat. Check before acting."""

    LOW = "low"
    """Weakly sourced or contested. A lead, not a finding."""


_ORDER: tuple[Confidence, ...] = (Confidence.LOW, Confidence.MODERATE, Confidence.HIGH)


def _demote(level: Confidence, steps: int = 1) -> Confidence:
    """Lower a level, never below `LOW`."""
    index = _ORDER.index(level)
    return _ORDER[max(0, index - steps)]


def _cap(level: Confidence, ceiling: Confidence) -> Confidence:
    """Hold a level at or below `ceiling`."""
    return level if _ORDER.index(level) <= _ORDER.index(ceiling) else ceiling


@final
@dataclass(frozen=True, slots=True)
class ConfidenceInputs:
    """Everything the level is computed from.

    All four are rows a reader can open, which is what makes `REQ-DATA-012`
    explainability achievable rather than aspirational.
    """

    claim_type: ClaimType
    distinct_sources: int = 0
    best_tier: AuthorityTier | None = None
    above_lower_sources: int = 0
    has_unresolved_conflict: bool = False
    has_explained_conflict: bool = False
    has_stale_evidence: bool = False
    cites_inaccessible_source: bool = False


@final
@dataclass(frozen=True, slots=True)
class ConfidenceVerdict:
    """The level, and why it landed there."""

    level: Confidence
    rationale: str

    @property
    def as_json(self) -> dict[str, str]:
        return {"level": self.level.value, "rationale": self.rationale}


def _base(inputs: ConfidenceInputs) -> tuple[Confidence, str]:
    """Sourcing alone, before any demotion (`DEC-09 §4.1`).

    The first two rows deliberately mirror `DEC-04 §3.2`'s resolution rule. A
    question the run declared answered and a claim the report calls weak must
    not be able to disagree — if they could, neither number would mean
    anything.
    """
    if inputs.best_tier is AuthorityTier.PRIMARY:
        return Confidence.HIGH, "a primary source"

    if inputs.distinct_sources >= 2 and inputs.above_lower_sources >= 1:
        return (
            Confidence.HIGH,
            f"{inputs.distinct_sources} distinct sources, at least one above lower tier",
        )

    if inputs.above_lower_sources >= 1:
        return Confidence.MODERATE, "one source above lower tier, uncorroborated"

    if inputs.distinct_sources >= 2:
        return (
            Confidence.MODERATE,
            f"{inputs.distinct_sources} distinct sources, all lower tier",
        )

    if inputs.distinct_sources == 1:
        return Confidence.LOW, "a single lower-tier source"

    return Confidence.LOW, "no evidence cited"


def assess(inputs: ConfidenceInputs) -> ConfidenceVerdict:
    """Compute a claim's confidence and the sentence explaining it."""
    # `DEC-09 §4.3`. An uncertainty claim's content is something the evidence
    # does not settle, so the level describes that directly. Reading it as "how
    # sure are we that this is unknown" inverts the scale and puts HIGH beside
    # every gap in the report.
    if inputs.claim_type is ClaimType.UNCERTAINTY:
        return ConfidenceVerdict(
            Confidence.LOW, "an uncertainty: the evidence does not settle this"
        )

    level, why = _base(inputs)
    reasons = [why]

    # `REQ-EVID-014 AC-3`: an unresolved conflict reduces confidence. Capped
    # rather than demoted, because a fact two credible sources disagree about
    # is not high-confidence however good those sources are — the disagreement
    # is the finding.
    if inputs.has_unresolved_conflict:
        level = _cap(level, Confidence.LOW)
        reasons.append("an unresolved conflict touches it")
    elif inputs.has_explained_conflict:
        level = _demote(level)
        reasons.append("a conflict was found and explained")

    if inputs.has_stale_evidence:
        level = _demote(level)
        reasons.append("its evidence is outside the currency window")

    # `REQ-EVID-018` forbids citing a source that was never readable; if one
    # slipped through, the claim cannot be better than a lead.
    if inputs.cites_inaccessible_source:
        level = _cap(level, Confidence.LOW)
        reasons.append("it cites a source that could not be read")

    # `DEC-09 §4.3`: neither an analysis nor a forecast can be better
    # established than the facts under it. HIGH on an interpretation tells the
    # reader the wrong thing about what kind of statement they are reading.
    if inputs.claim_type in (ClaimType.ANALYSIS, ClaimType.FORECAST):
        capped = _cap(level, Confidence.MODERATE)
        if capped is not level:
            reasons.append(f"a {inputs.claim_type.value} is capped at moderate")
        level = capped

    return ConfidenceVerdict(level, "; ".join(reasons))
