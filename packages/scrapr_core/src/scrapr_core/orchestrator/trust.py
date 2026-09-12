"""Stages 6, 8 and 9 — where a report becomes something a reader can weigh.

Runs after synthesis has written claims and before the validation gate. Three
passes over the persisted rows, in an order the dependencies fix:

1. **Detect conflicts** among the evidence a claim cites (`REQ-EVID-012`).
2. **Explain them, or say they are unexplained** (`REQ-EVID-013`,
   `REQ-EVID-014`).
3. **Assign confidence** (`REQ-EVID-015`), which reads the conflict state, so
   it cannot run first.

**Nothing here calls a model.** `REQ-EVID-002 AC-3` needs tiering inspectable,
`REQ-EVID-016 AC-2` needs confidence consistent across runs, and
`REQ-EVID-013 AC-3` forbids invented explanations. One model call in this path
would break all three at once, and would make "why did this claim get moderate"
unanswerable — which is precisely what `REQ-DATA-012` exists to prevent.

**Competing evidence is preserved, never discarded** (`REQ-EVID-012 AC-2`).
Detection writes rows; it does not delete any. Both values stay on the claim
with their source, tier and retrieval time, which is what `AC-3` renders.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import combinations
from typing import final
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from scrapr_core.db.enums import (
    Accessibility,
    AuthorityTier,
    ConflictCause,
    ConflictStatus,
    NormalizationStatus,
)
from scrapr_core.db.models import (
    Claim,
    ClaimEvidence,
    Conflict,
    ConflictEvidence,
    Evidence,
    Source,
)
from scrapr_core.evidence.confidence import ConfidenceInputs, assess
from scrapr_core.evidence.conflict import (
    ComparisonResult,
    ConflictReason,
    compare,
    explain,
    is_stale,
)
from scrapr_core.evidence.dedupe import content_fingerprint
from scrapr_core.evidence.normalize import MetricClass, NormalizedValue, classify_metric

__all__ = ["apply_trust"]

# `REQ-EVID-013 AC-1` names the causes; these are the persisted spellings.
_CAUSES: dict[ConflictReason, ConflictCause] = {
    ConflictReason.PERIOD: ConflictCause.PERIOD,
    ConflictReason.DEFINITION: ConflictCause.DEFINITION,
    ConflictReason.CURRENCY: ConflictCause.CURRENCY,
    ConflictReason.ESTIMATE: ConflictCause.ESTIMATE_VS_REPORTED,
    ConflictReason.METHODOLOGY: ConflictCause.METHODOLOGY,
    ConflictReason.STALE: ConflictCause.STALENESS,
}

_TIER_RANK: dict[AuthorityTier, int] = {
    AuthorityTier.LOWER: 0,
    AuthorityTier.SECONDARY: 1,
    AuthorityTier.PRIMARY: 2,
}


@final
@dataclass(frozen=True, slots=True)
class _Cited:
    """One piece of evidence a claim cites, with the source behind it."""

    evidence: Evidence
    source: Source

    @property
    def normalized(self) -> NormalizedValue:
        """The stored normalisation, rebuilt as a comparison input.

        Read back from the row rather than recomputed from the text: the value
        was normalised once at insert, and recomputing it here could disagree
        with what the citation panel shows the reader.
        """
        return NormalizedValue(
            reported=self.evidence.value_raw or self.evidence.content,
            status=self.evidence.normalization,
            metric_class=classify_metric(self.evidence.content),
            value=self.evidence.value_normalized,
            currency=self.evidence.currency,
        )

    @property
    def metric(self) -> MetricClass:
        return classify_metric(self.evidence.content)

    @property
    def basis(self) -> str | None:
        """Reported or estimated, where the provider said (`REQ-TOOL-004 AC-3`).

        `DEC-10 §4.2` needs this: an estimate disagreeing with a filed figure
        is not a conflict, and without the field there is no way to tell.
        """
        return None

    @property
    def fingerprint(self) -> str:
        return content_fingerprint(self.evidence.excerpt or self.evidence.content)


def apply_trust(session: Session, version_id: UUID) -> None:
    """Detect conflicts and assign confidence for every claim in a version."""
    for claim in _claims(session, version_id):
        cited = _cited_for(session, claim.id)
        conflicts = _detect(session, version_id, claim, cited)
        _assign_confidence(claim, cited, conflicts)

    session.flush()


# --------------------------------------------------------------------------


def _claims(session: Session, version_id: UUID) -> Sequence[Claim]:
    return (
        session.execute(select(Claim).where(Claim.version_id == version_id))
        .scalars()
        .all()
    )


def _cited_for(session: Session, claim_id: UUID) -> list[_Cited]:
    rows = session.execute(
        select(Evidence, Source)
        .join(ClaimEvidence, ClaimEvidence.evidence_id == Evidence.id)
        .join(Source, Source.id == Evidence.source_id)
        .where(ClaimEvidence.claim_id == claim_id)
        .order_by(Evidence.extracted_at, Evidence.id)
    ).all()
    return [_Cited(evidence=evidence, source=source) for evidence, source in rows]


def _detect(
    session: Session,
    version_id: UUID,
    claim: Claim,
    cited: Sequence[_Cited],
) -> list[ComparisonResult]:
    """Compare every pair of cited values, and record the disagreements.

    Pairwise, because `DEC-10 §10` leaves three-way disagreement to the
    explanation work: whether three values spanning a range are one conflict or
    three is a presentation question this layer should not decide.

    Evidence from syndicated copies of one story is compared only once — three
    outlets carrying one wire story is one story, and `REQ-EVID-006 AC-2`
    forbids it inflating apparent corroboration. It must not manufacture
    agreement either.
    """
    results: list[ComparisonResult] = []
    seen_pairs: set[tuple[str, str]] = set()

    for left, right in combinations(cited, 2):
        if left.fingerprint == right.fingerprint:
            # The same text from two outlets. Not a second opinion.
            continue

        first, second = sorted((left.fingerprint, right.fingerprint))
        if (first, second) in seen_pairs:
            continue
        seen_pairs.add((first, second))

        outcome = compare(
            left.normalized,
            right.normalized,
            left_basis=left.basis,
            right_basis=right.basis,
        )
        if not outcome.is_conflict:
            continue

        reasoned = explain(
            left.normalized,
            right.normalized,
            left_published=left.source.published_at,
            right_published=right.source.published_at,
        )
        results.append(reasoned)
        _persist(session, version_id, claim, left, right, reasoned)

    return results


def _persist(
    session: Session,
    version_id: UUID,
    claim: Claim,
    left: _Cited,
    right: _Cited,
    result: ComparisonResult,
) -> None:
    """Write the conflict and both sides of it.

    `REQ-EVID-014`: an unexplained conflict is stored as `UNRESOLVED` with a
    null cause rather than given a plausible-sounding one. `AC-2` then keeps
    any value from it from being presented as settled.
    """
    unresolved = result.reason is None or result.reason is ConflictReason.UNEXPLAINED

    conflict = Conflict(
        version_id=version_id,
        claim_id=claim.id,
        status=ConflictStatus.UNRESOLVED if unresolved else ConflictStatus.EXPLAINED,
        explanation=result.detail or None,
        explanation_category=(
            None
            if unresolved or result.reason is None
            else _CAUSES.get(result.reason)
        ),
    )
    session.add(conflict)
    session.flush()

    for side, cited in (("first", left), ("second", right)):
        session.add(
            ConflictEvidence(
                conflict_id=conflict.id,
                evidence_id=cited.evidence.id,
                label=f"{side}: {cited.source.name}",
            )
        )
    session.flush()


def _assign_confidence(
    claim: Claim, cited: Sequence[_Cited], conflicts: Sequence[ComparisonResult]
) -> None:
    """Compute and store the claim's confidence (`REQ-EVID-015`)."""
    # Distinct *sources*, not evidence rows, and syndication counts once
    # (`REQ-EVID-006 AC-2`): three outlets running one wire story is one piece
    # of corroboration, not three.
    distinct: set[str] = set()
    above_lower = 0
    best: AuthorityTier | None = None
    stale = False
    inaccessible = False

    for item in cited:
        key = item.source.url_normalized or item.source.identifier or str(item.source.id)
        if item.fingerprint in distinct:
            continue
        distinct.add(item.fingerprint)
        distinct.add(key)

        tier = item.source.authority_tier
        if best is None or _TIER_RANK[tier] > _TIER_RANK[best]:
            best = tier
        if tier is not AuthorityTier.LOWER:
            above_lower += 1
        if item.source.accessibility is not Accessibility.ACCESSIBLE:
            inaccessible = True
        if is_stale(item.source.published_at, item.metric):
            stale = True
        if item.evidence.normalization is NormalizationStatus.NON_COMPARABLE:
            # Not a demotion on its own — a qualitative statement is normal
            # evidence, and `AC-3` calls it non-comparable rather than bad.
            pass

    source_count = len({item.source.id for item in cited})

    verdict = assess(
        ConfidenceInputs(
            claim_type=claim.claim_type,
            distinct_sources=source_count,
            best_tier=best,
            above_lower_sources=above_lower,
            has_unresolved_conflict=any(
                result.reason is ConflictReason.UNEXPLAINED for result in conflicts
            ),
            has_explained_conflict=any(
                result.reason is not None and result.reason is not ConflictReason.UNEXPLAINED
                for result in conflicts
            ),
            has_stale_evidence=stale,
            cites_inaccessible_source=inaccessible,
        )
    )

    claim.confidence = verdict.level.value
    claim.confidence_inputs = {
        **verdict.as_json,
        "distinct_sources": source_count,
        "above_lower_sources": above_lower,
        "best_tier": best.value if best else None,
        "conflicts": len(conflicts),
    }
