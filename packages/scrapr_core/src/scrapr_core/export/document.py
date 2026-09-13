"""The export's content, read from a version and nothing else (`REQ-EXP-005`, `DEC-21`).

`build_document` is the one step of an export that touches the database, and it
produces a plain value: sections, claims with their type, confidence and
numbered citations, conflicts, charts, sources and What's Changed. Renderers
turn that value into a PDF or a deck; they never read rows and never decide
what the report says.

**Nothing is added.** Every string here is a string the version holds, or a
fixed label ("Fact", "Sources disagree") that the workspace shows too.
`REQ-EXP-005 AC-1` — every claim in an export exists in the source version — is
true because there is no other place a claim could come from, and `AC-2` — no
new analysis at export time — because nothing here can call a model.

**Text is made safe for the formats once, here.** Retrieved content can hold
characters XML 1.0 cannot represent, and a PowerPoint part containing one is a
file PowerPoint offers to "repair" (`REQ-EXP-002 AC-3`). They are removed at
the boundary so neither renderer has to remember.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Final, final
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from scrapr_core.db.enums import (
    ClaimType,
    EvidenceRole,
    SourceCategory,
    VersionStatus,
    VizKind,
)
from scrapr_core.db.models import (
    Claim,
    ClaimEvidence,
    Conflict,
    ConflictEvidence,
    Evidence,
    ReportSection,
    ResearchSession,
    ResearchVersion,
    Source,
    Upload,
    Visualization,
)

__all__ = [
    "CLAIM_WORD",
    "ExportChange",
    "ExportChart",
    "ExportClaim",
    "ExportConflict",
    "ExportDocument",
    "ExportReference",
    "ExportSection",
    "ExportSeries",
    "build_document",
    "clean_text",
    "format_date",
    "format_value",
    "safe_link",
]

CLAIM_WORD: Final[dict[ClaimType, str]] = {
    ClaimType.FACT: "Fact",
    ClaimType.ANALYSIS: "Analysis",
    ClaimType.FORECAST: "Forecast",
    ClaimType.UNCERTAINTY: "Uncertain",
}
"""The same words the workspace uses, so a reader moving between the two is
never translating."""

CONFIDENCE_WORD: Final[dict[str, str]] = {
    "high": "High confidence",
    "moderate": "Moderate confidence",
    "low": "Low confidence",
}

TIER_WORD: Final[dict[str, str]] = {
    "primary": "Primary source",
    "secondary": "Established source",
    "lower": "Unverified source",
}

CAUSE_WORD: Final[dict[str, str]] = {
    "period": "the figures cover different reporting periods",
    "definition": "the sources are measuring different things",
    "currency": "the figures are in different currencies",
    "estimate_vs_reported": "one figure is an estimate and the other is reported",
    "methodology": "the sources used different methods",
    "staleness": "one figure is significantly older than the other",
}

KIND_WORD: Final[dict[str, str]] = {
    "conclusion_changed": "Conclusion changed",
    "figure_changed": "Figure changed",
    "newer_period": "Newer figure",
    "assumptions_changed": "Assumptions changed",
    "new_finding": "New",
    "gap_closed": "Now confirmed",
    "gap_opened": "No longer confirmed",
    "no_longer_found": "Not found again",
}

_XML_INVALID: Final = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")


def clean_text(value: str | None) -> str:
    """Text safe for an XML part and a PDF paragraph: invalid characters out,
    runs of whitespace collapsed."""
    if not value:
        return ""
    return " ".join(_XML_INVALID.sub("", value).split())


def safe_link(url: str | None) -> str | None:
    """A URL a document may make clickable, or `None`.

    Only `http` and `https`. A `javascript:` or `file:` URL in a source record
    is a string to print, never an action to embed in a file someone opens.
    """
    if not url:
        return None
    candidate = clean_text(url)
    lowered = candidate.lower()
    if lowered.startswith(("https://", "http://")) and " " not in candidate:
        return candidate
    return None


@final
@dataclass(frozen=True, slots=True)
class ExportReference:
    """One numbered entry in the source list."""

    number: int
    name: str
    publisher: str | None
    url: str | None
    tier: str
    retrieved_at: dt.datetime
    from_your_document: bool = False
    document_removed: bool = False


@final
@dataclass(frozen=True, slots=True)
class ExportConflictSide:
    value: str
    source_name: str
    reference: int | None
    from_your_document: bool


@final
@dataclass(frozen=True, slots=True)
class ExportConflict:
    """Evidence that disagrees, shown rather than resolved (`REQ-EXP-009 AC-3`)."""

    resolved: bool
    explanation: str
    sides: tuple[ExportConflictSide, ...]


@final
@dataclass(frozen=True, slots=True)
class ExportClaim:
    text: str
    claim_type: ClaimType
    confidence: str | None
    """Already in words: "Moderate confidence"."""
    references: tuple[int, ...]
    reporting_period: str | None = None
    assumptions: tuple[str, ...] = ()
    conflicts: tuple[ExportConflict, ...] = ()
    is_important: bool = False

    @property
    def type_word(self) -> str:
        return CLAIM_WORD[self.claim_type]


@final
@dataclass(frozen=True, slots=True)
class ExportSeries:
    name: str
    points: tuple[tuple[str, Decimal], ...]


@final
@dataclass(frozen=True, slots=True)
class ExportChart:
    """A visualization spec, read into typed values (`DEC-11 §4`)."""

    kind: VizKind
    title: str
    unit: str | None
    series: tuple[ExportSeries, ...]
    references: tuple[int, ...] = ()

    @property
    def labels(self) -> tuple[str, ...]:
        """Every category label, in first-seen order across series."""
        seen: dict[str, None] = {}
        for series in self.series:
            for label, _ in series.points:
                seen.setdefault(label, None)
        return tuple(seen)


@final
@dataclass(frozen=True, slots=True)
class ExportSection:
    title: str
    is_executive_summary: bool
    claims: tuple[ExportClaim, ...]
    charts: tuple[ExportChart, ...] = ()


@final
@dataclass(frozen=True, slots=True)
class ExportChange:
    label: str
    summary: str


@final
@dataclass(frozen=True, slots=True)
class ExportDocument:
    """Everything an export shows, and nothing it does not."""

    objective: str
    subject: str | None
    version_number: int
    version_created_at: dt.datetime
    status: VersionStatus
    sections: tuple[ExportSection, ...]
    references: tuple[ExportReference, ...]
    changes_headline: str | None = None
    changes: tuple[ExportChange, ...] = field(default_factory=tuple)

    @property
    def version_line(self) -> str:
        """`REQ-EXP-006 AC-2`: the version and its date, in every export."""
        return (
            f"Version {self.version_number} · generated from research dated "
            f"{format_date(self.version_created_at)}"
        )

    @property
    def is_partial(self) -> bool:
        return self.status is VersionStatus.PARTIAL


def format_date(value: dt.datetime | dt.date) -> str:
    return f"{value:%b} {value.day}, {value.year}"


def format_value(value: Decimal, unit: str | None) -> str:
    """A chart value as a reader reads it: `USD 1.20bn`, `12,400`, `3.5`."""
    magnitude = abs(value)
    if magnitude >= Decimal(1_000_000_000):
        text = f"{value / Decimal(1_000_000_000):.2f}bn"
    elif magnitude >= Decimal(1_000_000):
        text = f"{value / Decimal(1_000_000):.1f}m"
    elif magnitude >= Decimal(10_000):
        text = f"{value:,.0f}"
    else:
        text = f"{value.normalize():f}"
    return f"{unit} {text}" if unit else text


# --------------------------------------------------------------------------
# Building
# --------------------------------------------------------------------------


class ExportSourceError(LookupError):
    """The version cannot be exported: missing, unfinished, or without a report."""


def build_document(session: Session, version_id: UUID) -> ExportDocument:
    """Read one closed version into an `ExportDocument`."""
    version = session.get(ResearchVersion, version_id)
    if version is None or version.closed_at is None:
        raise ExportSourceError("only a finished version can be exported")
    if version.status is VersionStatus.FAILED:
        raise ExportSourceError("a version that failed produced no report to export")
    research = session.get(ResearchSession, version.session_id)
    if research is None:  # pragma: no cover - enforced by the foreign key
        raise ExportSourceError("the version belongs to no research")

    sections = session.execute(
        select(ReportSection).where(ReportSection.version_id == version_id).order_by(ReportSection.ordering)
    ).scalars().all()
    claims = session.execute(
        select(Claim).where(Claim.version_id == version_id).order_by(Claim.created_at, Claim.id)
    ).scalars().all()
    sources = {
        source.id: source
        for source in session.execute(select(Source).where(Source.version_id == version_id)).scalars()
    }

    removed_uploads = _removed_uploads(session, sources.values())
    evidence_source = {
        evidence_id: source_id
        for evidence_id, source_id in session.execute(
            select(Evidence.id, Evidence.source_id).where(Evidence.version_id == version_id)
        ).all()
    }
    supporting: dict[UUID, list[tuple[UUID, dt.date | None]]] = {}
    for claim_id, evidence_id, period_end in session.execute(
        select(ClaimEvidence.claim_id, Evidence.id, Evidence.period_end)
        .join(Evidence, Evidence.id == ClaimEvidence.evidence_id)
        .where(Evidence.version_id == version_id, ClaimEvidence.role == EvidenceRole.SUPPORTING)
        .order_by(Evidence.extracted_at, Evidence.id)
    ).all():
        supporting.setdefault(claim_id, []).append((evidence_id, period_end))

    # Citation numbers in reading order: the executive summary first, then each
    # section, then each chart, so [1] is the first source a reader meets.
    numbering: dict[UUID, int] = {}

    def number_for(source_id: UUID) -> int:
        if source_id not in numbering:
            numbering[source_id] = len(numbering) + 1
        return numbering[source_id]

    conflicts_by_claim = _conflicts(session, version_id)
    charts_by_section = _charts(session, version_id)

    built_sections: list[ExportSection] = []
    for section in sections:
        section_claims: list[ExportClaim] = []
        for claim in (c for c in claims if c.section_id == section.id):
            cited = supporting.get(claim.id, [])
            refs = tuple(
                dict.fromkeys(
                    number_for(evidence_source[evidence_id])
                    for evidence_id, _ in cited
                    if evidence_id in evidence_source
                )
            )
            periods = {period.isoformat() for _, period in cited if period is not None}
            stated = (claim.assumptions or {}).get("stated")
            section_claims.append(
                ExportClaim(
                    text=clean_text(claim.text),
                    claim_type=claim.claim_type,
                    confidence=CONFIDENCE_WORD.get(claim.confidence or ""),
                    references=refs,
                    reporting_period=next(iter(periods)) if len(periods) == 1 else None,
                    assumptions=tuple(clean_text(str(item)) for item in stated)
                    if isinstance(stated, list)
                    else (),
                    conflicts=tuple(
                        _conflict(row, sides, sources, number_for)
                        for row, sides in conflicts_by_claim.get(claim.id, [])
                    ),
                    is_important=claim.is_important,
                )
            )

        charts = tuple(
            ExportChart(
                kind=chart.kind,
                title=chart.title,
                unit=chart.unit,
                series=chart.series,
                references=tuple(
                    dict.fromkeys(
                        number_for(evidence_source[evidence_id])
                        for evidence_id in evidence_ids
                        if evidence_id in evidence_source
                    )
                ),
            )
            for chart, evidence_ids in charts_by_section.get(section.id, [])
        )
        built_sections.append(
            ExportSection(
                title=clean_text(section.title),
                is_executive_summary=section.is_executive_summary,
                claims=tuple(section_claims),
                charts=charts,
            )
        )

    references = tuple(
        ExportReference(
            number=number,
            name=clean_text(sources[source_id].name),
            publisher=clean_text(sources[source_id].publisher) or None,
            url=safe_link(sources[source_id].url),
            tier=TIER_WORD.get(sources[source_id].authority_tier.value, "Source"),
            retrieved_at=sources[source_id].retrieved_at,
            from_your_document=sources[source_id].category is SourceCategory.DOCUMENT,
            document_removed=sources[source_id].upload_id in removed_uploads,
        )
        for source_id, number in sorted(numbering.items(), key=lambda item: item[1])
        if source_id in sources
    )

    headline, changes = _changes(version.change_summary)
    return ExportDocument(
        objective=clean_text(research.objective),
        subject=clean_text(research.subject) or None,
        version_number=version.version_number,
        version_created_at=version.created_at,
        status=version.status,
        sections=tuple(built_sections),
        references=references,
        changes_headline=headline,
        changes=changes,
    )


def _removed_uploads(session: Session, sources: Iterable[Source]) -> set[UUID]:
    upload_ids = {source.upload_id for source in sources if source.upload_id}
    if not upload_ids:
        return set()
    return set(
        session.execute(
            select(Upload.id).where(Upload.id.in_(upload_ids), Upload.deleted_at.is_not(None))
        ).scalars()
    )


type _Sides = list[tuple[Evidence, str | None, Source]]


def _conflicts(session: Session, version_id: UUID) -> dict[UUID, list[tuple[Conflict, _Sides]]]:
    rows = session.execute(
        select(Conflict).where(Conflict.version_id == version_id).order_by(Conflict.id)
    ).scalars().all()
    if not rows:
        return {}
    sides: dict[UUID, _Sides] = {row.id: [] for row in rows}
    for conflict_id, evidence, label, source in session.execute(
        select(ConflictEvidence.conflict_id, Evidence, ConflictEvidence.label, Source)
        .join(Evidence, Evidence.id == ConflictEvidence.evidence_id)
        .join(Source, Source.id == Evidence.source_id)
        .where(ConflictEvidence.conflict_id.in_(sides.keys()))
    ).all():
        sides[conflict_id].append((evidence, label, source))

    by_claim: dict[UUID, list[tuple[Conflict, _Sides]]] = {}
    for row in rows:
        by_claim.setdefault(row.claim_id, []).append((row, sides[row.id]))
    return by_claim


def _conflict(
    row: Conflict,
    sides: _Sides,
    sources: dict[UUID, Source],
    number_for: Callable[[UUID], int],
) -> ExportConflict:
    explanation = (
        clean_text(row.explanation)
        if row.explanation
        else CAUSE_WORD.get(row.explanation_category.value, "")
        if row.explanation_category
        else ""
    )
    return ExportConflict(
        resolved=row.status.value == "explained",
        explanation=explanation
        or "Nothing in the evidence accounts for the difference, so it is shown unresolved.",
        sides=tuple(
            ExportConflictSide(
                value=clean_text(
                    f"{evidence.currency} {evidence.value_raw}"
                    if evidence.currency and evidence.value_raw and evidence.currency not in evidence.value_raw
                    else evidence.value_raw or evidence.content
                ),
                source_name=clean_text(source.name),
                reference=number_for(source.id) if source.id in sources else None,
                from_your_document=source.category is SourceCategory.DOCUMENT,
            )
            for evidence, _label, source in sides
        ),
    )


def _charts(session: Session, version_id: UUID) -> dict[UUID, list[tuple[ExportChart, list[UUID]]]]:
    by_section: dict[UUID, list[tuple[ExportChart, list[UUID]]]] = {}
    for row in session.execute(
        select(Visualization).where(Visualization.version_id == version_id).order_by(Visualization.ordering)
    ).scalars():
        chart, evidence_ids = _read_spec(row.kind, row.spec)
        if chart is not None:
            by_section.setdefault(row.section_id, []).append((chart, evidence_ids))
    return by_section


def _read_spec(kind: VizKind, spec: object) -> tuple[ExportChart | None, list[UUID]]:
    """A stored spec, read defensively.

    A spec that does not parse is left out rather than drawn wrong:
    `REQ-VIZ-002 AC-3` forbids a placeholder chart presented as real, and a
    half-read spec is exactly that.
    """
    if not isinstance(spec, dict):
        return None, []
    series_out: list[ExportSeries] = []
    evidence_ids: list[UUID] = []
    for series in spec.get("series") or []:
        if not isinstance(series, dict):
            continue
        points: list[tuple[str, Decimal]] = []
        for point in series.get("points") or []:
            if not isinstance(point, dict):
                continue
            try:
                value = Decimal(str(point.get("value")))
                evidence_ids.append(UUID(str(point.get("evidence_id"))))
            except (InvalidOperation, ValueError):
                continue
            label = clean_text(str(point.get("label") or ""))
            if label and value.is_finite():
                points.append((label, value))
        if points:
            series_out.append(ExportSeries(name=clean_text(str(series.get("name") or "")), points=tuple(points)))
    if not series_out:
        return None, []
    unit = spec.get("unit")
    return (
        ExportChart(
            kind=kind,
            title=clean_text(str(spec.get("title") or "")),
            unit=clean_text(unit) if isinstance(unit, str) else None,
            series=tuple(series_out),
        ),
        evidence_ids,
    )


def _changes(stored: object) -> tuple[str | None, tuple[ExportChange, ...]]:
    """What's Changed, as the version recorded it. Nothing when there is none."""
    if not isinstance(stored, dict) or not stored.get("available", True):
        return None, ()
    headline = stored.get("headline")
    changes = tuple(
        ExportChange(
            label=KIND_WORD.get(str(change.get("kind")), "Change"),
            summary=clean_text(str(change.get("summary") or "")),
        )
        for change in stored.get("changes") or []
        if isinstance(change, dict)
    )
    return (clean_text(headline) if isinstance(headline, str) else None), changes
