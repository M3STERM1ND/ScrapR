"""Report sections and the visualizations attached to them.

`visualization_evidence` is the table that makes `REQ-VIZ-002 AC-2` enforceable.
A chart must be built from evidence rows; with no rows there is nothing to link,
so a decorative visualization is structurally impossible rather than
discouraged.

`report_sections.body` is a JSONB block list rather than prose, because
`REQ-SYNTH-003` requires every claim in the report be traceable to its claim id.
Blocks that reference claim ids keep that mapping intact through rendering,
export and the citation map; a rendered string would lose it.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from scrapr_core.db.base import Base, Json, UuidPk
from scrapr_core.db.enums import VizKind, pg_enum

__all__ = ["ReportSection", "Visualization", "VisualizationEvidence"]


class ReportSection(Base):
    """One section of the report, in a deliberate order (`REQ-SYNTH-005`)."""

    __tablename__ = "report_sections"
    __table_args__ = (
        UniqueConstraint("version_id", "ordering"),
        Index("ix_report_sections_version_id", "version_id"),
    )

    id: Mapped[UuidPk]

    version_id: Mapped[UUID] = mapped_column(ForeignKey("research_versions.id"))

    title: Mapped[str]

    body: Mapped[Json]
    """Ordered blocks, each referencing the claim ids it renders."""

    ordering: Mapped[int]

    is_executive_summary: Mapped[bool] = mapped_column(default=False)
    """`REQ-SYNTH-006`. A flag rather than a reserved ordering, so the summary
    can move without renumbering the report."""


class Visualization(Base):
    """A chart or table generated from sourced data (`REQ-VIZ-001`)."""

    __tablename__ = "visualizations"
    __table_args__ = (
        UniqueConstraint("version_id", "ordering"),
        Index("ix_visualizations_version_id", "version_id"),
        Index("ix_visualizations_section_id", "section_id"),
    )

    id: Mapped[UuidPk]

    version_id: Mapped[UUID] = mapped_column(ForeignKey("research_versions.id"))
    section_id: Mapped[UUID] = mapped_column(ForeignKey("report_sections.id"))

    kind: Mapped[VizKind] = mapped_column(pg_enum(VizKind, "viz_kind"))

    spec: Mapped[Json]
    """Renderer-agnostic. `OPEN-25` picks the renderer, and the point of storing
    a spec rather than an image is that answering it later changes nothing
    here — and that a PDF and a PPTX render the same data (`REQ-EXP-005`)."""

    ordering: Mapped[int]


class VisualizationEvidence(Base):
    """The evidence rows a visualization was built from (`REQ-VIZ-002`)."""

    __tablename__ = "visualization_evidence"
    __table_args__ = (Index("ix_visualization_evidence_evidence_id", "evidence_id"),)

    visualization_id: Mapped[UUID] = mapped_column(
        ForeignKey("visualizations.id", ondelete="CASCADE"), primary_key=True
    )
    evidence_id: Mapped[UUID] = mapped_column(
        ForeignKey("evidence.id", ondelete="CASCADE"), primary_key=True
    )
