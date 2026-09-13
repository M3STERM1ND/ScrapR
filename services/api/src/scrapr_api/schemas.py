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
    MessageRole,
    ResearchStatus,
    SourceCategory,
    UploadState,
    VersionStatus,
    VizKind,
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
    "UploadCompleteRequest",
    "UploadOut",
    "UploadTicket",
    "UploadTicketRequest",
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

    defer_start: bool = False
    """Create the session but do not enqueue the run yet.

    For intake with attachments (`REQ-INPUT-004`). An upload needs a session to
    belong to, so the session must exist first — and if the run started here,
    retrieval could reach the documents category before the file finished
    extracting, and `AC-2` ("available to the agent during the first run") would
    hold only when the upload happened to win a race.

    The client uploads, waits for each file to leave `processing`, and then
    calls `POST /v1/research/{id}/start`. Defaults false, so research with no
    documents is still one request.
    """


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

    category: SourceCategory
    """What kind of source this is. On the wire because `REQ-DOC-008 AC-2`
    requires a citation to a user's own document to be visibly distinct from a
    citation to something ScrapR found, and the frontend cannot tell them apart
    from a name and a null URL."""

    upload_id: UUID | None = None
    """Which uploaded file, when `category` is `document`. Null otherwise."""


class EvidenceOut(BaseModel):
    """One piece of evidence behind a claim, as the inspector shows it.

    `REQ-EVID-019 AC-1` names what inspection must show, and "the relevant
    evidence" is the item the payload did not carry: source, tier and retrieval
    time were all reachable through `source_ids`, but the *excerpt* — the words
    the source actually printed — existed only in the database.

    That excerpt is the whole point of the citation surface. A reader checking
    a claim is checking it against what was written, not against a link.
    """

    id: UUID
    source_id: UUID
    statement: str
    """The extracted fact, in ScrapR's words."""

    excerpt: str | None
    """The verbatim span that supports it (`REQ-EVID-007`), checked against the
    source at extraction time so it cannot be a paraphrase."""

    value_raw: str | None
    """The figure exactly as published, where the evidence carries one."""

    reporting_period: str | None
    """`REQ-EVID-019 AC-1`: the period, where applicable."""


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

    source_name: str
    """Who said it. The publisher, or the filename for an uploaded document."""

    from_your_document: bool
    """Whether this side came from a file the reader uploaded.

    `REQ-DOC-007 AC-2` requires the presentation to identify which side is the
    user's own document, and a reader looking at "1.2bn against 1.9bn" needs to
    know that one of those numbers is from their own spreadsheet before they can
    judge the disagreement. Sent explicitly rather than left to the client to
    derive by joining sources: a rule that has to be re-implemented in the
    frontend is a rule that will be missed there.
    """


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
    evidence: list[EvidenceOut]
    """What the claim rests on (`REQ-EVID-019 AC-1`). Inline on the claim
    rather than in a version-level list, because `AC-2` requires inspection be
    available *at the claim*, not only in a separate bibliography."""


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
    visualizations: list[VisualizationOut]


class MessageOut(BaseModel):
    """One conversation turn (`REQ-CONV-007`)."""

    id: UUID
    seq: int
    role: MessageRole
    content: str
    claim_type: ClaimType | None
    """How the answer is typed (`REQ-CONV-008 AC-2`). Null on a user turn: the
    taxonomy describes what the agent asserted, not what was asked."""

    evidence_ids: list[UUID]
    """What the answer cited (`REQ-CONV-008 AC-1`). Resolves against the
    version's evidence, so an answer inspects the same way a claim does."""

    created_at: dt.datetime


class AskIn(BaseModel):
    """A follow-up question."""

    question: str = Field(min_length=1, max_length=2000)
    context_ref: dict[str, str] | None = None
    """What the reader was pointing at, if anything (`REQ-CONV-002`)."""


class AskOut(BaseModel):
    """The turn pair a question produces."""

    question: MessageOut
    answer: MessageOut
    researched: bool
    """Whether answering required fresh retrieval (`REQ-CONV-003`). Surfaced so
    the client can say the research grew rather than leaving the reader to
    notice new sources appearing."""


class VisualizationOut(BaseModel):
    """A chart or table, as a spec the client renders (`DEC-11`)."""

    id: UUID
    section_id: UUID
    kind: VizKind
    spec: dict[str, object]
    ordering: int
    evidence_ids: list[UUID]
    """`REQ-VIZ-004 AC-1`: a chart exposes the sources of its data, and `AC-2`
    requires all of them when it combines several."""


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


# --------------------------------------------------------------------------
# Uploads — `REQ-DOC-001..010`, `DEC-12`, `DEC-13`
# --------------------------------------------------------------------------

FILENAME_MAX = 255
"""What most filesystems allow. Long enough for any real name, bounded because
this string is stored, displayed, and used to derive a key suffix."""


class UploadTicketRequest(BaseModel):
    """Ask for permission to upload one file.

    The size is what the *client* says it is. It is checked here so an
    oversized file is refused before it is transferred rather than after
    (`REQ-DOC-010 AC-2`), and checked again at completion against the object
    that actually arrived, because a declared size is not a limit.
    """

    filename: str = Field(min_length=1, max_length=FILENAME_MAX)
    content_type: str = Field(min_length=1, max_length=200)
    size_bytes: int = Field(gt=0)


class UploadTicket(BaseModel):
    """Where to PUT the bytes, and until when.

    The URL is a short-lived write grant to one key (`DEC-12`). The API never
    sees the file: the browser uploads directly, which is implementation plan
    §10 and the reason a 25 MB file does not have to fit through a request
    handler.
    """

    upload_id: UUID
    url: str
    expires_at: dt.datetime
    content_type: str
    """Echoed back because it is **signed into the URL**. A PUT that sends a
    different `Content-Type` header fails the signature check, so the client
    has to send exactly this."""


class UploadCompleteRequest(BaseModel):
    """Tell the API the bytes are in place. No body beyond that is needed:
    everything worth knowing is read back from storage rather than accepted
    from the client."""


class UploadOut(BaseModel):
    """One attached file as the intake panel shows it (`REQ-DOC-003`)."""

    id: UUID
    filename: str
    content_type: str
    size_bytes: int
    state: UploadState
    error: str | None
    """Why it failed, in words written for the user (`REQ-DOC-004 AC-3`). Null
    unless `state` is `failed`."""

    chunk_count: int
    """How many passages were extracted. Zero until processing finishes, and
    the honest answer to "did anything come out of this file"."""

    created_at: dt.datetime
