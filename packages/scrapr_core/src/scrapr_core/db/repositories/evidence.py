"""Writing sources and evidence, with deduplication where it belongs.

**Deduplication is within a version** (`REQ-EVID-006`, §4.1). The same URL seen
twice in one run is one source; the same URL seen again in a later version is a
different record with a different retrieval timestamp, because `REQ-VER-002 AC-2`
requires the earlier reading to survive untouched.

The URL is **canonicalised before comparison** (`AC-1`). Until Phase 2 this
column held the raw URL, so `?utm_source=news` produced a second source out of
the same page and the unique constraint never fired -- corroboration invented
out of a tracking parameter.

**Tier is assigned here, at insert** (`DEC-08`), from a static rule table.
Every consumer of `authority_tier` -- termination, confidence, conflict
explanation -- reads what this writes, and `REQ-EVID-002 AC-3` requires the
rule that fired be inspectable afterwards, so it is stored rather than
recomputed.

**A document source carries its `upload_id`** (`REQ-DOC-008 AC-2`). The
documents tool puts it in the item's structured payload and this is where it
becomes a column, so a stored citation can name which of the reader's files it
came from. Categorising the source as a `DOCUMENT` and then losing the id would
leave the report able to say "an uploaded file" and never which one.

**A source is written before the evidence that depends on it** and in the same
flush, so `REQ-EVID-001 AC-2` — no evidence without a source — holds even if the
step dies between the two.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from scrapr_core.db.base import utcnow
from scrapr_core.db.enums import AuthorityTier, SourceCategory
from scrapr_core.db.models import Evidence, Source
from scrapr_core.domain.json import JsonValue
from scrapr_core.evidence.dedupe import normalize_url
from scrapr_core.evidence.normalize import normalize_value, parse_period
from scrapr_core.evidence.tiering import assign_tier
from scrapr_core.tools.contract import ToolItem

__all__ = ["EvidenceRepository"]


class EvidenceRepository:
    """Persists what retrieval found."""

    def __init__(
        self,
        session: Session,
        *,
        subject_hosts: frozenset[str] = frozenset(),
        default_tier: AuthorityTier = AuthorityTier.LOWER,
    ) -> None:
        """Build a repository.

        `subject_hosts` are the research subject's own domains, which `DEC-08`
        rule 4 tiers as `PRIMARY` -- resolved once and passed in, so a re-run
        cannot silently retier evidence written earlier.

        `default_tier` is what an unlisted source gets. `LOWER` by decision
        (`DEC-08 §4`), and injectable so the threshold can be dialled back
        without editing the rule table: at `SECONDARY` two sources nobody
        vouched for resolve a question between them, which is pre-`DEC-08`
        behaviour.
        """
        self._session = session
        self._subject_hosts = subject_hosts
        self._default_tier = default_tier

    def source_for(self, version_id: UUID, item: ToolItem) -> Source:
        """Find or create the source for a retrieved item.

        Idempotent by design, because steps are re-run after a crash and the
        unique index on `(version_id, url_normalized)` would otherwise turn a
        retry into an error.
        """
        canonical = normalize_url(item.source_url)

        existing = self._session.execute(
            select(Source).where(
                Source.version_id == version_id,
                Source.url_normalized == canonical,
                Source.identifier == item.source_identifier,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        tier = assign_tier(
            url=item.source_url,
            category=item.source_category,
            subject_hosts=self._subject_hosts,
            default=self._default_tier,
        )

        source = Source(
            version_id=version_id,
            url=item.source_url,
            url_normalized=canonical,
            identifier=item.source_identifier,
            name=item.source_name,
            category=item.source_category,
            authority_tier=tier.tier,
            tier_rationale=tier.rationale,
            # The time the tool actually read it, never the time this row was
            # written (`REQ-EVID-004`, `REQ-TOOL-013 AC-2`).
            retrieved_at=item.retrieved_at,
            published_at=item.published_at,
            accessibility=item.accessibility,
            upload_id=_upload_id(item),
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

        # `REQ-EVID-008`. Non-destructive by construction: the reported form
        # goes in `value_raw` and is never overwritten, and a figure that
        # cannot be normalised is marked rather than guessed at (`AC-2`,
        # `AC-3`).
        normalized = normalize_value(statement, currency_hint=_currency_hint(item))
        period_start, period_end = parse_period(_structured(item, "period"))
        if period_start is None and period_end is None:
            # EDGAR spells it differently from FMP. Reading both here rather
            # than making every provider agree on a key keeps the tool layer
            # free to describe its own payload.
            period_start, period_end = parse_period(
                _structured(item, "period_ending")
            )

        evidence = Evidence(
            version_id=version_id,
            source_id=source.id,
            content=statement,
            excerpt=excerpt,
            value_raw=normalized.reported if normalized.value is not None else None,
            value_numeric=normalized.value,
            value_normalized=normalized.value if normalized.comparable else None,
            currency=normalized.currency,
            normalization=normalized.status,
            # `REQ-TOOL-004 AC-3` and `REQ-EVID-009 AC-1`. Both were being
            # reported by the providers and dropped on the floor, which left
            # two of `DEC-10 §4`'s three conflict exclusions unable to fire.
            value_basis=_basis(item),
            period_start=period_start,
            period_end=period_end,
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


def _structured(item: ToolItem, key: str) -> JsonValue:
    """One field of a provider's structured payload, or `None`."""
    structured = item.structured
    return structured.get(key) if structured else None


def _upload_id(item: ToolItem) -> UUID | None:
    """Which uploaded file this came from, for a document source.

    Null for everything else, which is what `sources.upload_id` means. A value
    that is present but unparseable is treated as absent rather than raised on:
    a malformed id from a tool is a wiring bug, and failing the whole run over
    it would lose the evidence as well as the attribution.
    """
    if item.source_category is not SourceCategory.DOCUMENT:
        return None
    raw = _structured(item, "upload_id")
    if not isinstance(raw, str):
        return None
    try:
        return UUID(raw)
    except ValueError:
        return None


def _basis(item: ToolItem) -> str | None:
    """Whether the provider called this figure reported or estimated.

    Null when it said nothing. `DEC-10 §4.2` treats an absent basis as unknown
    rather than assuming "reported": assuming would make a real estimate
    comparable against a filed figure, which is the exact false conflict the
    exclusion exists to prevent.
    """
    basis = _structured(item, "basis")
    if isinstance(basis, str) and basis.strip():
        return basis.strip().lower()
    return None


def _currency_hint(item: ToolItem) -> str | None:
    """The currency a provider stated beside the figure, if any.

    Used only when the statement text does not name one. A hint never overrides
    what the source wrote: `REQ-EVID-008 AC-2` is about retaining what was
    reported, and silently relabelling a figure's currency would break that
    more thoroughly than failing to normalise it at all.
    """
    structured = item.structured
    if not structured:
        return None
    currency = structured.get("currency")
    if isinstance(currency, str) and currency.strip():
        return currency.strip().upper()
    return None
