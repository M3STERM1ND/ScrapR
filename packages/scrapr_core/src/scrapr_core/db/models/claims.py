"""Claims, their evidence links, and recorded conflicts.

**`claims` is a Phase 1 table, not Phase 2.** `REQ-EVID-017` is a Phase 1
requirement and its `AC-1` rejects "any *fact-type* claim lacking evidence
linkage", which needs both the claim entity and claim typing in Phase 1. The PRD
was internally inconsistent about this; `DEC-05` resolved it by moving
`REQ-DATA-006`, `REQ-EVID-010`, `REQ-SYNTH-001` and three others into Phase 1,
with claim *confidence* left in Phase 2. `N-08` records the closure.

So Phase 1 populates `text`, `claim_type` and the evidence links. `confidence`
and `confidence_inputs` stay empty until `REQ-EVID-015` in Phase 2, which is why
both are nullable or defaulted rather than required.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from scrapr_core.db.base import Base, CreatedAt, Json, UuidPk
from scrapr_core.db.enums import (
    ClaimType,
    ConflictCause,
    ConflictStatus,
    EvidenceRole,
    pg_enum,
)

__all__ = ["Claim", "ClaimEvidence", "Conflict", "ConflictEvidence"]


class Claim(Base):
    """One assertion in the report, typed and evidence-linked."""

    __tablename__ = "claims"
    __table_args__ = (
        # `REQ-SYNTH-009`: a forecast without stated assumptions is not a
        # forecast, it is a guess. The database refuses to store one.
        CheckConstraint(
            "claim_type <> 'forecast' OR assumptions IS NOT NULL",
            name="forecast_needs_assumptions",
        ),
        Index("ix_claims_version_id", "version_id"),
        Index("ix_claims_section_id", "section_id"),
    )

    id: Mapped[UuidPk]

    version_id: Mapped[UUID] = mapped_column(ForeignKey("research_versions.id"))

    section_id: Mapped[UUID | None] = mapped_column(ForeignKey("report_sections.id"))
    """Null while synthesis is still assembling sections."""

    text: Mapped[str]

    claim_type: Mapped[ClaimType] = mapped_column(pg_enum(ClaimType, "claim_type"))
    """`REQ-SYNTH-002`. The validation gate keys off this, so it is not
    decoration: an unevidenced `fact` fails the gate, an `uncertainty` does not."""

    confidence: Mapped[str | None]
    """Phase 2. Text rather than an enum because the scale itself is `OPEN-14`,
    and inventing one here would be a product decision this layer must not make."""

    confidence_inputs: Mapped[Json] = mapped_column(default=dict)
    """What produced the confidence: tier, corroboration, conflict, recency
    (`REQ-EVID-015 AC-3`). Empty until Phase 2."""

    assumptions: Mapped[Json | None]
    """Required when `claim_type` is `forecast`; see the check constraint."""

    is_important: Mapped[bool] = mapped_column(default=False)
    """Marks the claims an executive summary and the What's Changed diff draw
    from (`REQ-SYNTH-006`, `REQ-VER-006`)."""

    created_at: Mapped[CreatedAt]


class ClaimEvidence(Base):
    """The evidence linkage `REQ-EVID-010` requires, with its role.

    `role` is part of the primary key so one piece of evidence can be both
    supporting and conflicting for the same claim — which is exactly the state a
    conflict is, and collapsing it would erase the disagreement.
    """

    __tablename__ = "claim_evidence"
    __table_args__ = (Index("ix_claim_evidence_evidence_id", "evidence_id"),)

    claim_id: Mapped[UUID] = mapped_column(
        ForeignKey("claims.id", ondelete="CASCADE"), primary_key=True
    )
    evidence_id: Mapped[UUID] = mapped_column(
        ForeignKey("evidence.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[EvidenceRole] = mapped_column(
        pg_enum(EvidenceRole, "evidence_role"), primary_key=True
    )


class Conflict(Base):
    """Evidence that disagrees, surfaced rather than silently resolved.

    `REQ-EVID-014` requires an unexplained conflict be shown as unresolved, so
    `explanation` is nullable and `status` carries the truth.
    """

    __tablename__ = "conflicts"
    __table_args__ = (
        Index("ix_conflicts_version_id", "version_id"),
        Index("ix_conflicts_claim_id", "claim_id"),
    )

    id: Mapped[UuidPk]

    version_id: Mapped[UUID] = mapped_column(ForeignKey("research_versions.id"))
    claim_id: Mapped[UUID] = mapped_column(ForeignKey("claims.id"))

    status: Mapped[ConflictStatus] = mapped_column(
        pg_enum(ConflictStatus, "conflict_status")
    )

    explanation: Mapped[str | None]

    explanation_category: Mapped[ConflictCause | None] = mapped_column(
        pg_enum(ConflictCause, "conflict_cause")
    )
    """One of the named causes in `REQ-EVID-013 AC-1`."""

    tolerance_applied: Mapped[Decimal | None]
    """The threshold under which a numeric difference was not treated as a
    conflict. The values themselves are `OPEN-16`; recording what was applied is
    what makes a later change auditable."""


class ConflictEvidence(Base):
    """The two or more pieces of evidence that disagree, with display labels."""

    __tablename__ = "conflict_evidence"
    __table_args__ = (Index("ix_conflict_evidence_evidence_id", "evidence_id"),)

    conflict_id: Mapped[UUID] = mapped_column(
        ForeignKey("conflicts.id", ondelete="CASCADE"), primary_key=True
    )
    evidence_id: Mapped[UUID] = mapped_column(
        ForeignKey("evidence.id", ondelete="CASCADE"), primary_key=True
    )
    label: Mapped[str | None]
    """How this side is described when the disagreement is rendered."""
