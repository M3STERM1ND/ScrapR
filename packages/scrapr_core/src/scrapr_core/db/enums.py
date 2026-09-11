"""The database enumerations, one per `enum` named in implementation plan §4.2.

**Native PostgreSQL enums, not check constraints or free text.** A native enum
makes an invalid state unrepresentable at the storage layer, which matters most
for the columns the trust story rests on: a `claim_type` outside
`fact|analysis|forecast|uncertainty` would let the validation gate (`REQ-EVID-017`)
be bypassed by a typo.

The cost is that adding a value is a migration. That is the right trade here —
every one of these vocabularies comes from a requirement, so a new value is a
product change and deserves the visibility.

Each Python enum subclasses `str`, so `Claim.claim_type == ClaimType.FACT` and
comparison against the wire value both behave, and serialisation to JSON needs
no encoder.
"""

from __future__ import annotations

from enum import StrEnum, unique

from sqlalchemy import Enum as SAEnum

__all__ = [
    "Accessibility",
    "ActivityStatus",
    "AuthorityTier",
    "ClaimType",
    "ConflictCause",
    "ConflictStatus",
    "EvidenceRole",
    "ExportFormat",
    "ExportStatus",
    "ExportTheme",
    "MessageRole",
    "NormalizationStatus",
    "ResearchStatus",
    "RunKind",
    "RunStatus",
    "SourceCategory",
    "StepStatus",
    "TerminationReason",
    "UploadState",
    "VersionStatus",
    "VizKind",
    "pg_enum",
]


@unique
class ResearchStatus(StrEnum):
    """Lifecycle of a research session."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    PARTIAL = "partial"
    """Delivered with named gaps rather than silently short (`REQ-AGENT-009`)."""
    FAILED = "failed"


@unique
class VersionStatus(StrEnum):
    """Lifecycle of one immutable version (`REQ-VER-002`)."""

    BUILDING = "building"
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


@unique
class SourceCategory(StrEnum):
    """Which tool category produced the source (`REQ-TOOL-002..007`)."""

    FILING = "filing"
    FINANCIAL = "financial"
    NEWS = "news"
    JOBS = "jobs"
    WEB = "web"
    OFFICIAL = "official"
    DOCUMENT = "document"
    """A user upload (`REQ-DOC-001`)."""


@unique
class AuthorityTier(StrEnum):
    """Source authority (`REQ-EVID-002`). Assignment rules are `OPEN-15`."""

    PRIMARY = "primary"
    SECONDARY = "secondary"
    LOWER = "lower"


@unique
class Accessibility(StrEnum):
    """Whether the source could actually be read (`REQ-EVID-018`)."""

    ACCESSIBLE = "accessible"
    PAYWALLED = "paywalled"
    BLOCKED = "blocked"
    FAILED = "failed"


@unique
class NormalizationStatus(StrEnum):
    """Outcome of unit and currency normalisation (`REQ-EVID-008`).

    Normalisation is non-destructive: the raw value is always retained, so
    `NON_COMPARABLE` records a fact about the data rather than a lost value.
    """

    NORMALIZED = "normalized"
    NON_COMPARABLE = "non_comparable"
    NOT_APPLICABLE = "not_applicable"


@unique
class ClaimType(StrEnum):
    """Claim taxonomy (`REQ-SYNTH-002`).

    The gate in `REQ-EVID-017 AC-1` is scoped to `FACT`, and
    `REQ-SYNTH-009` requires assumptions on `FORECAST`, so these two values are
    load-bearing rather than descriptive.
    """

    FACT = "fact"
    ANALYSIS = "analysis"
    FORECAST = "forecast"
    UNCERTAINTY = "uncertainty"


@unique
class EvidenceRole(StrEnum):
    """How a piece of evidence relates to the claim it is linked to."""

    SUPPORTING = "supporting"
    CONFLICTING = "conflicting"


@unique
class ConflictStatus(StrEnum):
    """Whether a conflict was explained or is surfaced unresolved (`REQ-EVID-014`)."""

    EXPLAINED = "explained"
    UNRESOLVED = "unresolved"


@unique
class ConflictCause(StrEnum):
    """Why two pieces of evidence disagree (`REQ-EVID-013 AC-1`)."""

    PERIOD = "period"
    DEFINITION = "definition"
    CURRENCY = "currency"
    ESTIMATE_VS_REPORTED = "estimate_vs_reported"
    METHODOLOGY = "methodology"
    STALENESS = "staleness"


@unique
class VizKind(StrEnum):
    """Visualization forms (`REQ-VIZ-001`). The renderer is `OPEN-25`."""

    LINE = "line"
    BAR = "bar"
    TABLE = "table"
    MATRIX = "matrix"
    METRIC = "metric"
    COMPARISON = "comparison"


@unique
class MessageRole(StrEnum):
    """Author of a conversation message (`REQ-CONV-007`)."""

    USER = "user"
    AGENT = "agent"


@unique
class UploadState(StrEnum):
    """Document processing lifecycle (`REQ-DOC-003..004`)."""

    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


@unique
class ExportFormat(StrEnum):
    """Export formats (`REQ-EXP-001`, `REQ-EXP-002`)."""

    PDF = "pdf"
    PPTX = "pptx"


@unique
class ExportTheme(StrEnum):
    """The six predefined themes (`REQ-EXP-003`). Visual definitions are `OPEN-22`."""

    PROFESSIONAL = "professional"
    INVESTOR = "investor"
    MODERN = "modern"
    CORPORATE = "corporate"
    MINIMAL = "minimal"
    DARK = "dark"


@unique
class ExportStatus(StrEnum):
    """Export job lifecycle. Retryable without re-running research (`NFR-REL-003`)."""

    PENDING = "pending"
    RUNNING = "running"
    READY = "ready"
    FAILED = "failed"


@unique
class ActivityStatus(StrEnum):
    """Per-event status in the activity timeline (`REQ-ACT-004 AC-2`)."""

    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"
    FAILED = "failed"


@unique
class RunKind(StrEnum):
    """Which job produced this run (implementation plan §7.1)."""

    INITIAL = "initial"
    UPDATE = "update"
    CONVERSATION = "conversation"


@unique
class RunStatus(StrEnum):
    """Run lifecycle. Mirrors `VersionStatus` because a run produces a version."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


@unique
class TerminationReason(StrEnum):
    """Why a run stopped (`REQ-AGENT-005 AC-4`, and `DEC-04` for sufficiency)."""

    SUFFICIENCY = "sufficiency"
    CEILING = "ceiling"
    """The effort ceiling was reached before sufficiency."""
    FAILURE = "failure"
    """Including a poison step exhausting `STEP_MAX_ATTEMPTS` (§5.6)."""


@unique
class StepStatus(StrEnum):
    """State of one step in the durable job state machine (§5.6)."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    """Retryable: the runner will reclaim it while attempts remain."""
    DEAD = "dead"
    """Poisoned past `STEP_MAX_ATTEMPTS`; the run terminates visibly."""


def pg_enum(enum_cls: type[StrEnum], name: str) -> SAEnum:
    """Build the SQLAlchemy type for a native Postgres enum.

    `values_callable` is what makes the stored labels the enum *values*
    (`"in_progress"`) rather than the member *names* (`"IN_PROGRESS"`), which is
    what every requirement, API payload and fixture in this project is written
    in.
    """
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=True,
        create_type=True,
        values_callable=lambda cls: [member.value for member in cls],
    )
