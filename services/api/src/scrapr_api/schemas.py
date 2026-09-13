"""The wire types. What the OpenAPI schema — and therefore the TS client — is.

**The version payload is denormalised for read.** `GET /versions/{n}` returns
sections, claims and a claim-keyed citation map in one response, so the
workspace renders without a request waterfall (`NFR-PERF-004`,
implementation plan §6.2). Full evidence bodies are deliberately *not* here:
they would bloat every report load, and `REQ-EVID-011 AC-2` allows claim
inspection to cost one interaction, which a separate fetch satisfies.

Input bounds are stated on the models rather than checked in handlers, because
a bound the schema knows about is a bound the generated client knows about too.
"""

from __future__ import annotations

import datetime as dt
from uuid import UUID

from pydantic import BaseModel, Field

from scrapr_core.db.enums import (
    ActivityStatus,
    AuthorityTier,
    ClaimType,
    ConflictCause,
    ConflictStatus,
    ResearchStatus,
    VersionStatus,
)

__all__ = [
    "ActivityEventOut",
    "ActivityPage",
    "ClaimOut",
    "CreateResearchRequest",
    "CreateResearchResponse",
    "ResearchSessionOut",
    "SectionOut",
    "SourceOut",
    "VersionOut",
    "VersionSummary",
]

OBJECTIVE_MAX = 2000
"""Long enough for a genuinely detailed brief, short enough that the field is
not an upload channel. `REQ-INPUT-006` wants the limit stated, not guessed at."""


class CreateResearchRequest(BaseModel):
    """`REQ-INPUT-001..005`. Only the objective is required."""

    objective: str = Field(min_length=10, max_length=OBJECTIVE_MAX)
    instructions: str | None = Field(default=None, max_length=OBJECTIVE_MAX)
    context_url: str | None = Field(default=None, max_length=2048)
    context_company: str | None = Field(default=None, max_length=200)
    context_ticker: str | None = Field(default=None, max_length=20)


class CreateResearchResponse(BaseModel):
    """202: the work is accepted, not finished (`REQ-AGENT-008 AC-1`)."""

    session_id: UUID
    version_id: UUID
    version_number: int


class VersionSummary(BaseModel):
    """One entry in the version list (`REQ-VER-008`)."""

    id: UUID
    version_number: int
    status: VersionStatus
    created_at: dt.datetime
    closed_at: dt.datetime | None


class ResearchSessionOut(BaseModel):
    """The session header the workspace renders around (`REQ-WORK-002`)."""

    id: UUID
    objective: str
    subject: str | None
    subject_interpretation_note: str | None
    status: ResearchStatus
    current_version_id: UUID | None
    created_at: dt.datetime
    updated_at: dt.datetime
    versions: list[VersionSummary]


class SourceOut(BaseModel):
    """Where a claim's evidence came from, and when it was read.

    `retrieved_at` is on the wire because `REQ-EVID-004` makes it part of what
    the user is entitled to see, not internal bookkeeping.
    """

    id: UUID
    name: str
    url: str | None
    publisher: str | None
    authority_tier: AuthorityTier
    retrieved_at: dt.datetime
    published_at: dt.datetime | None


class ConflictSideOut(BaseModel):
    """One value in a disagreement, with what a reader needs to judge it.

    `REQ-EVID-012 AC-3` names the three: the source, its tier, and when it was
    read. Showing the values without them would present a disagreement the
    reader has no way to weigh.
    """

    evidence_id: UUID
    source_id: UUID
    label: str | None
    value: str
    """Exactly as reported (`REQ-EVID-008 AC-2`), never the normalised form."""


class ConflictOut(BaseModel):
    """Evidence that disagrees, surfaced rather than resolved away.

    `REQ-WORK-009`: conflicts are a visible feature of the report, not hidden.
    `explanation` is null when nothing in the evidence accounts for the gap,
    and `status` then reads `unresolved` — `REQ-EVID-013 AC-3` forbids
    inventing a cause, and `REQ-EVID-014` requires saying so plainly.
    """

    id: UUID
    claim_id: UUID
    status: ConflictStatus
    explanation: str | None
    explanation_category: ConflictCause | None
    sides: list[ConflictSideOut]


class ClaimOut(BaseModel):
    """One assertion, typed, with the sources behind it (`REQ-SYNTH-002..003`)."""

    id: UUID
    text: str
    claim_type: ClaimType
    confidence: str | None
    """`REQ-EVID-015`: every claim carries one. Null only on a version written
    before Phase 2, which the client renders as absent rather than inventing a
    level for research nobody assessed."""
    confidence_rationale: str | None
    """Why it got that level (`REQ-DATA-012`). Generated from the same inputs
    as the level itself, so it cannot drift from what it explains."""
    reporting_period: str | None
    """The fiscal period this claim's figures cover (`REQ-EVID-009 AC-2`).

    Null when the evidence carries no period, and null when it carries more
    than one: a claim drawing on two years has no single period, and printing
    one of them beside the citation would be worse than printing none."""
    is_important: bool
    source_ids: list[UUID]


class SectionOut(BaseModel):
    """A report section and the claims it renders."""

    id: UUID
    title: str
    ordering: int
    is_executive_summary: bool
    claim_ids: list[UUID]


class VersionOut(BaseModel):
    """One whole version, in one response."""

    id: UUID
    session_id: UUID
    version_number: int
    status: VersionStatus
    created_at: dt.datetime
    closed_at: dt.datetime | None
    sections: list[SectionOut]
    claims: list[ClaimOut]
    sources: list[SourceOut]
    conflicts: list[ConflictOut]


class ActivityEventOut(BaseModel):
    """One timeline entry. User-facing text only (`REQ-ACT-003`)."""

    seq: int
    label: str
    status: ActivityStatus
    tool_category: str | None
    created_at: dt.datetime


class ActivityPage(BaseModel):
    """Events after a sequence number, plus where to resume from.

    `next_after` is what the client sends back, so the polling loop never has to
    reason about timestamps — and the Phase 3 SSE upgrade resumes from exactly
    the same value (implementation plan §6.3).
    """

    events: list[ActivityEventOut]
    next_after: int
