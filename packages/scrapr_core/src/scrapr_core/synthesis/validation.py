"""The validation gate — a hard gate over a version, not a warning log.

`REQ-EVID-017 AC-3` is unusually explicit: the constraint is enforced **in the
pipeline**, not by instructing the model to behave. So this module reads what was
actually persisted and rejects the version if the invariants do not hold. No
prompt wording is involved, and no amount of model drift can weaken it.

**The gate grows across phases** (implementation plan §5.5). Phase 1 enforces
only the rules whose requirements exist by Phase 1:

* a `fact` claim has at least one supporting evidence row (`REQ-EVID-017`)
* a `forecast` claim states its assumptions (`REQ-SYNTH-009`)
* no claim cites a source that was never readable (`REQ-EVID-018`)

Conflict, confidence and visualization rules switch on in Phases 2 and 3 with
the requirements that define them; each arrives here as another `Rule`, and the
list is the gate's whole definition.

A failure is a generation defect, not a user error. The caller retries synthesis
once and then completes the version as `partial` with the defect recorded — it
never ships silently.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import final
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from scrapr_core.db.enums import Accessibility, ClaimType, EvidenceRole
from scrapr_core.db.models import Claim, ClaimEvidence, Evidence, Source

__all__ = ["GateViolation", "ValidationReport", "validate_version"]


@final
@dataclass(frozen=True, slots=True)
class GateViolation:
    """One rule broken by one claim."""

    rule: str
    claim_id: UUID
    detail: str

    def __str__(self) -> str:
        return f"{self.rule}: claim {self.claim_id} {self.detail}"


@final
@dataclass(frozen=True, slots=True)
class ValidationReport:
    """The gate's verdict on a version."""

    version_id: UUID
    violations: Sequence[GateViolation]

    @property
    def passed(self) -> bool:
        return not self.violations

    def summary(self) -> str:
        """A one-line description, for the run record and internal logs."""
        if self.passed:
            return f"version {self.version_id} passed the validation gate"
        return (
            f"version {self.version_id} failed the validation gate with "
            f"{len(self.violations)} violation(s): "
            + "; ".join(str(violation) for violation in self.violations)
        )


def validate_version(session: Session, version_id: UUID) -> ValidationReport:
    """Check every claim in a version against the Phase 1 rules.

    Reads persisted rows rather than in-memory objects on purpose: the gate's
    job is to judge what a reader would actually be shown, and anything a stage
    forgot to save is exactly the defect it exists to catch.
    """
    claims = (
        session.execute(select(Claim).where(Claim.version_id == version_id))
        .scalars()
        .all()
    )

    violations: list[GateViolation] = []
    for claim in claims:
        violations.extend(_check_claim(session, claim))

    return ValidationReport(version_id=version_id, violations=tuple(violations))


def _check_claim(session: Session, claim: Claim) -> Sequence[GateViolation]:
    violations: list[GateViolation] = []

    supporting = _supporting_sources(session, claim.id)

    if claim.claim_type is ClaimType.FACT and not supporting:
        violations.append(
            GateViolation(
                rule="REQ-EVID-017",
                claim_id=claim.id,
                detail="is a fact claim with no supporting evidence",
            )
        )

    if claim.claim_type is ClaimType.FORECAST and not claim.assumptions:
        # The database refuses a null; an empty object satisfies the constraint
        # while stating nothing, which is the same failure with better manners.
        violations.append(
            GateViolation(
                rule="REQ-SYNTH-009",
                claim_id=claim.id,
                detail="is a forecast with no stated assumptions",
            )
        )

    for source in supporting:
        if source.accessibility is not Accessibility.ACCESSIBLE:
            violations.append(
                GateViolation(
                    rule="REQ-EVID-018",
                    claim_id=claim.id,
                    detail=(
                        f"cites source {source.id} which was "
                        f"{source.accessibility.value}"
                    ),
                )
            )

    return violations


def _supporting_sources(session: Session, claim_id: UUID) -> Sequence[Source]:
    """Sources behind a claim's *supporting* evidence.

    Conflicting evidence is deliberately excluded: a claim that cites a
    paywalled source as the thing it disagrees with has not made an
    unverifiable assertion.
    """
    statement = (
        select(Source)
        .join(Evidence, Evidence.source_id == Source.id)
        .join(ClaimEvidence, ClaimEvidence.evidence_id == Evidence.id)
        .where(
            ClaimEvidence.claim_id == claim_id,
            ClaimEvidence.role == EvidenceRole.SUPPORTING,
        )
    )
    return session.execute(statement).scalars().all()
