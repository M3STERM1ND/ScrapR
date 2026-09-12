"""Writing sources and evidence, with deduplication where it belongs.

**Deduplication is within a version** (`REQ-EVID-006`, §4.1). The same URL seen
twice in one run is one source; the same URL seen again in a later version is a
different record with a different retrieval timestamp, because `REQ-VER-002 AC-2`
requires the earlier reading to survive untouched.

**A source is written before the evidence that depends on it** and in the same
flush, so `REQ-EVID-001 AC-2` — no evidence without a source — holds even if the
step dies between the two.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from scrapr_core.db.base import utcnow
from scrapr_core.db.enums import AuthorityTier, NormalizationStatus
from scrapr_core.db.models import Evidence, Source
from scrapr_core.tools.contract import ToolItem

__all__ = ["DEFAULT_TIER", "EvidenceRepository"]

DEFAULT_TIER = AuthorityTier.SECONDARY
"""How authority is assigned until `OPEN-15` decides.

The middle tier, recorded with a rationale that says it is a default. The
termination gate reads this column (`DEC-04 §3.2`), so it cannot be null; what it
must not do is pretend to a judgement nobody has made yet.
"""

TIER_RATIONALE = {
    "basis": "OPEN-15 unresolved; a default tier is recorded rather than guessed"
}


class EvidenceRepository:
    """Persists what retrieval found."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def source_for(self, version_id: UUID, item: ToolItem) -> Source:
        """Find or create the source for a retrieved item.

        Idempotent by design, because steps are re-run after a crash and the
        unique index on `(version_id, url_normalized)` would otherwise turn a
        retry into an error.
        """
        existing = self._session.execute(
            select(Source).where(
                Source.version_id == version_id,
                Source.url_normalized == item.source_url,
                Source.identifier == item.source_identifier,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        source = Source(
            version_id=version_id,
            url=item.source_url,
            url_normalized=item.source_url,
            identifier=item.source_identifier,
            name=item.source_name,
            category=item.source_category,
            authority_tier=DEFAULT_TIER,
            tier_rationale=dict(TIER_RATIONALE),
            # The time the tool actually read it, never the time this row was
            # written (`REQ-EVID-004`, `REQ-TOOL-013 AC-2`).
            retrieved_at=item.retrieved_at,
            published_at=item.published_at,
            accessibility=item.accessibility,
        )
        self._session.add(source)
        self._session.flush()
        return source

    def record(
        self,
        version_id: UUID,
        item: ToolItem,
        statement: str,
        excerpt: str,
    ) -> Evidence:
        """Write one piece of evidence against its source.

        Returns the existing row when the same statement was already recorded
        from the same source in this version: a re-run of a step must not
        inflate the coverage count that termination is measured against.
        """
        source = self.source_for(version_id, item)

        existing = self._session.execute(
            select(Evidence).where(
                Evidence.version_id == version_id,
                Evidence.source_id == source.id,
                Evidence.content == statement,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        evidence = Evidence(
            version_id=version_id,
            source_id=source.id,
            content=statement,
            excerpt=excerpt,
            # Normalisation is stage 5, which is Phase 2 work. Recording
            # `not_applicable` says so honestly rather than implying a
            # comparison that has not happened.
            normalization=NormalizationStatus.NOT_APPLICABLE,
            extracted_at=utcnow(),
        )
        self._session.add(evidence)
        self._session.flush()
        return evidence

    def count_for_version(self, version_id: UUID) -> int:
        return len(
            self._session.execute(
                select(Evidence.id).where(Evidence.version_id == version_id)
            )
            .scalars()
            .all()
        )
