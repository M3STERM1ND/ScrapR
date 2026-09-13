"""Sources and evidence — the tables the whole trust story rests on.

Two rules are structural here rather than procedural.

**Deduplication is scoped within a version** (`REQ-EVID-006`): unique on
`(version_id, url_normalized)`. Across versions the same URL is a *different*
record, because it was retrieved at a different time and `REQ-VER-002 AC-2`
requires the older retrieval timestamp to survive untouched.

**Normalisation is non-destructive** (`REQ-EVID-008`). `value_raw` is what the
source said; `value_normalized` is what it means in comparable terms. The raw
value is never overwritten, so a normalisation bug is recoverable and an
inspection surface can always show the user the original.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from uuid import UUID

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from scrapr_core.db.base import Base, CreatedAt, Json, UuidPk
from scrapr_core.db.enums import (
    Accessibility,
    AuthorityTier,
    NormalizationStatus,
    SourceCategory,
    pg_enum,
)

__all__ = ["Evidence", "Source"]


class Source(Base):
    """Where a piece of evidence came from (`REQ-EVID-001`, `REQ-EVID-003`)."""

    __tablename__ = "sources"
    __table_args__ = (
        # Within-version deduplication (`REQ-EVID-006`).
        UniqueConstraint("version_id", "url_normalized"),
        Index("ix_sources_version_id", "version_id"),
    )

    id: Mapped[UuidPk]

    version_id: Mapped[UUID] = mapped_column(ForeignKey("research_versions.id"))

    url: Mapped[str | None]
    url_normalized: Mapped[str | None]
    """Canonical form used for deduplication. Null for sources that have no URL
    — an upload, or a provider record identified by `identifier`."""

    identifier: Mapped[str | None]
    """Provider-native identifier: an accession number, a filing id, a ticker."""

    name: Mapped[str]
    publisher: Mapped[str | None]

    category: Mapped[SourceCategory] = mapped_column(
        pg_enum(SourceCategory, "source_category")
    )

    authority_tier: Mapped[AuthorityTier] = mapped_column(
        pg_enum(AuthorityTier, "authority_tier")
    )
    """Assignment rules are `OPEN-15`; the vocabulary is fixed by
    `REQ-EVID-002`."""

    tier_rationale: Mapped[Json] = mapped_column(default=dict)
    """Why this tier was assigned. `REQ-EVID-002 AC-3` requires it be
    inspectable, so it is stored rather than recomputed."""

    retrieved_at: Mapped[dt.datetime]
    """When ScrapR fetched it (`REQ-EVID-004`)."""

    published_at: Mapped[dt.datetime | None]
    """When the source published it. Deliberately distinct from `retrieved_at`
    (`REQ-TOOL-007 AC-1`); conflating the two produces false staleness."""

    accessibility: Mapped[Accessibility] = mapped_column(
        pg_enum(Accessibility, "accessibility")
    )
    """`REQ-EVID-018` forbids a claim citing a source that was never readable,
    so this is a gate input rather than metadata."""

    upload_id: Mapped[UUID | None] = mapped_column(ForeignKey("uploads.id"))
    """Set when the source is a user-provided document (`REQ-DOC-001`)."""


class Evidence(Base):
    """One extracted statement, tied to the source that supports it."""

    __tablename__ = "evidence"
    __table_args__ = (
        Index("ix_evidence_version_id", "version_id"),
        Index("ix_evidence_source_id", "source_id"),
    )

    id: Mapped[UuidPk]

    version_id: Mapped[UUID] = mapped_column(ForeignKey("research_versions.id"))
    source_id: Mapped[UUID] = mapped_column(ForeignKey("sources.id"))

    content: Mapped[str]
    """The extracted statement, in ScrapR's words."""

    excerpt: Mapped[str | None]
    """The verbatim span that supports it (`REQ-EVID-007`). Untrusted content in
    origin: it is rendered as data and never reaches an instruction channel."""

    value_raw: Mapped[str | None]
    """Exactly as published: `"$1.2bn"`, `"12,345"`, `"(3.4)%"`."""

    value_numeric: Mapped[Decimal | None]
    """The raw value parsed, with no unit or currency conversion applied."""

    unit: Mapped[str | None]
    currency: Mapped[str | None] = mapped_column(String(3))
    """ISO 4217, hence exactly three characters."""

    scale: Mapped[Decimal | None]
    """Multiplier the source implied: thousands, millions, billions."""

    value_normalized: Mapped[Decimal | None]
    """Comparable form. Never overwrites `value_raw`."""

    normalization: Mapped[NormalizationStatus] = mapped_column(
        pg_enum(NormalizationStatus, "normalization_status")
    )

    value_basis: Mapped[str | None]
    """`'reported'` or `'estimate'`, as the provider stated it
    (`REQ-TOOL-004 AC-3`). `DEC-10 §4.2` reads it: an estimate disagreeing with
    a filed figure is not a conflict, and without this there is no way to tell
    the two apart."""

    period_start: Mapped[dt.date | None]
    period_end: Mapped[dt.date | None]
    """The fiscal period a figure covers (`REQ-EVID-009 AC-1`). `DEC-10 §4.1`
    compares only within a period, so a value with none is compared against
    nothing rather than against a different year."""
    period_label: Mapped[str | None]
    """As published: `"FY2025"`, `"Q3 2025"`. Kept alongside the resolved dates
    because a period mismatch is a named conflict cause (`REQ-EVID-013`)."""

    is_estimate: Mapped[bool] = mapped_column(default=False)
    """Estimated rather than reported (`REQ-TOOL-004 AC-3`). Also a conflict
    cause, which is why it is a column and not a metadata key."""

    extracted_at: Mapped[CreatedAt]

    metadata_: Mapped[Json] = mapped_column("metadata", default=dict)
    """Named `metadata_` in Python: `metadata` is taken by the declarative base."""
