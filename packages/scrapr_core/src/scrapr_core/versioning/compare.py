"""What changed between two versions (`REQ-VER-004`, `REQ-VER-006`, `REQ-VER-007`, `DEC-20`).

**A difference is meaningful when the evidence changed, not when the words
did.** Two runs of the same model over the same facts phrase them differently
every time, so this module never reports rewording. It pairs claims across
versions by their wording *with every figure removed*, then decides what — if
anything — changed by comparing what each claim rests on: its figures under the
`DEC-10` tolerances, its type, its confidence, its assessment words, its
forecast assumptions, and whether its sources were found again.

**Pure over snapshots.** `compare_claims` takes plain values and returns plain
values, so every kind of change in `DEC-20` is a unit test with no database.
`load_snapshot` is the one function that reads rows, and `compare_versions`
wires the two together for the pipeline.

**No model writes any of it.** Every sentence in a summary is assembled from the
rows it describes. A model asked "what changed" finds something to say, which
is exactly the failure `OPEN-27` was registered to prevent.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum, unique
from typing import Final, final
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from scrapr_core.db.enums import ClaimType, EvidenceRole, SourceCategory
from scrapr_core.db.models import Claim, ClaimEvidence, Evidence, ResearchVersion, Source
from scrapr_core.domain.json import JsonMapping, JsonValue
from scrapr_core.evidence.conflict import TOLERANCES, compare
from scrapr_core.evidence.normalize import MetricClass, NormalizedValue, classify_metric

__all__ = [
    "MATCH_THRESHOLD",
    "Change",
    "ChangeCategory",
    "ChangeKind",
    "ChangeSummary",
    "ClaimSnapshot",
    "Figure",
    "compare_claims",
    "compare_versions",
    "load_snapshot",
    "similarity",
]

SCHEMA_VERSION: Final = 1
"""Stored with every summary, so a reader of old versions knows which shape it
is reading if this one ever changes."""

MATCH_THRESHOLD: Final = 0.6
"""`DEC-20`: the share of significant words two claims must have in common,
figures removed, to be treated as the same claim."""


@unique
class ChangeKind(StrEnum):
    """The closed set of changes `DEC-20` recognises. Order is display order."""

    CONCLUSION_CHANGED = "conclusion_changed"
    FIGURE_CHANGED = "figure_changed"
    NEWER_PERIOD = "newer_period"
    ASSUMPTIONS_CHANGED = "assumptions_changed"
    NEW_FINDING = "new_finding"
    GAP_CLOSED = "gap_closed"
    GAP_OPENED = "gap_opened"
    NO_LONGER_FOUND = "no_longer_found"


_KIND_ORDER: Final = {kind: index for index, kind in enumerate(ChangeKind)}


@unique
class ChangeCategory(StrEnum):
    """The masterplan's kinds of change (`REQ-VER-006 AC-2`), plus a remainder."""

    FINANCIAL_FIGURES = "financial_figures"
    STOCK_INFORMATION = "stock_information"
    JOB_POSTINGS = "job_postings"
    NEW_PRODUCTS = "new_products"
    NEW_COMPETITORS = "new_competitors"
    FORECAST_ASSUMPTIONS = "forecast_assumptions"
    OTHER = "other"


# --------------------------------------------------------------------------
# Snapshots
# --------------------------------------------------------------------------


@final
@dataclass(frozen=True, slots=True)
class Figure:
    """One comparable value a claim rests on."""

    evidence_id: UUID
    value: NormalizedValue
    period: str | None
    basis: str | None
    source_name: str
    source_category: SourceCategory


@final
@dataclass(frozen=True, slots=True)
class ClaimSnapshot:
    """A claim as comparison sees it: what it says and what it rests on."""

    id: UUID
    text: str
    claim_type: ClaimType
    confidence: str | None = None
    assumptions: tuple[str, ...] = ()
    figures: tuple[Figure, ...] = ()
    evidence_ids: tuple[UUID, ...] = ()
    source_keys: frozenset[str] = frozenset()
    source_categories: frozenset[SourceCategory] = frozenset()


@final
@dataclass(frozen=True, slots=True)
class Side:
    """One side of a change, as the summary stores it."""

    claim_id: UUID
    text: str
    claim_type: ClaimType
    confidence: str | None
    value: str | None = None
    period: str | None = None

    def as_json(self) -> JsonMapping:
        return {
            "claim_id": str(self.claim_id),
            "text": self.text,
            "claim_type": self.claim_type.value,
            "confidence": self.confidence,
            "value": self.value,
            "period": self.period,
        }


@final
@dataclass(frozen=True, slots=True)
class Change:
    """One meaningful difference, and the evidence behind it."""

    kind: ChangeKind
    category: ChangeCategory
    summary: str
    before: Side | None
    after: Side | None
    evidence_ids: tuple[UUID, ...] = ()
    """The new version's evidence responsible (`REQ-VER-007 AC-1`)."""

    confidence_from: str | None = None
    confidence_to: str | None = None

    def as_json(self) -> JsonMapping:
        confidence: JsonValue = (
            {"from": self.confidence_from, "to": self.confidence_to}
            if self.confidence_from != self.confidence_to
            and (self.confidence_from or self.confidence_to)
            else None
        )
        return {
            "kind": self.kind.value,
            "category": self.category.value,
            "summary": self.summary,
            "before": self.before.as_json() if self.before else None,
            "after": self.after.as_json() if self.after else None,
            "evidence_ids": [str(evidence_id) for evidence_id in self.evidence_ids],
            "confidence_change": confidence,
        }


@final
@dataclass(frozen=True, slots=True)
class ChangeSummary:
    """What's Changed for one version (`REQ-VER-006`)."""

    compared_version_id: UUID
    compared_version_number: int
    compared_created_at: dt.datetime
    changes: tuple[Change, ...] = field(default_factory=tuple)
    unchanged: int = 0
    new_sources: int = 0

    @property
    def has_changes(self) -> bool:
        return bool(self.changes)

    @property
    def headline(self) -> str:
        """The one line the workspace leads with. Explicit when nothing moved
        (`REQ-VER-006 AC-3`), rather than an empty list the reader has to
        interpret."""
        number = self.compared_version_number
        if not self.changes:
            when = self.compared_created_at.strftime("%b %d, %Y").replace(" 0", " ")
            return f"No meaningful changes since version {number}, from {when}."
        count = len(self.changes)
        noun = "change" if count == 1 else "changes"
        return f"{count} meaningful {noun} since version {number}."

    def as_json(self) -> JsonMapping:
        return {
            "schema_version": SCHEMA_VERSION,
            "compared_with": {
                "version_id": str(self.compared_version_id),
                "version_number": self.compared_version_number,
                "created_at": self.compared_created_at.isoformat(),
            },
            "has_changes": self.has_changes,
            "headline": self.headline,
            "changes": [change.as_json() for change in self.changes],
            "counts": {"unchanged": self.unchanged, "new_sources": self.new_sources},
        }


# --------------------------------------------------------------------------
# Wording
# --------------------------------------------------------------------------

_FIGURE: Final = re.compile(
    r"[$€£¥]?\(?[-+]?\d[\d,.]*\)?\s*(?:%|percent|bn|billion|mn|m|million|k|thousand|x)?\b",
    re.IGNORECASE,
)
_WORD: Final = re.compile(r"[a-z][a-z'-]+")
_STOPWORDS: Final = frozenset(
    """a an the and or but of in on at to for from by with as is are was were be been
    being has have had its it this that these those than then there their they them
    which who whom whose what when where while into over under about after before
    during per also such not no nor so very more most less least same other some any
    each all both either neither can could may might will would should shall must
    usd eur gbp jpy cad aud chf cny inr fiscal fy year years quarter quarters
    """.split()
)

_POSITIVE: Final = frozenset(
    """strong stronger strongest robust rising rise rises rose grew growing growth
    increase increased increasing higher improve improved improving improvement ahead
    leading leads outperform outperforms outperformed gain gains gained expand
    expanding expanded expansion accelerate accelerating accelerated healthy positive
    upside""".split()
)
_NEGATIVE: Final = frozenset(
    """weak weaker weakest falling fall falls fell declining decline declined
    decrease decreased decreasing lower deteriorate deteriorating deteriorated worsen
    worsening worsened behind lagging lags lagged underperform underperforms
    underperformed loss losses shrink shrinking shrank contraction contracting slowing
    slowed slowdown negative downside""".split()
)
_RATINGS: Final = frozenset("strong moderate weak limited solid poor exceptional".split())


def _words(text: str) -> frozenset[str]:
    """Significant words, figures and dates removed.

    Figures go first, so "revenue was $1.2bn" and "revenue was $1.4bn" share
    every word that is left and pair up — the difference between them is then
    found by comparing their evidence, which is where it belongs.
    """
    without_figures = _FIGURE.sub(" ", text.lower())
    return frozenset(
        word
        for word in _WORD.findall(without_figures)
        if len(word) >= 3 and word not in _STOPWORDS
    )


def similarity(left: str, right: str) -> float:
    """The share of significant words two texts have in common (Dice).

    Dice rather than Jaccard: a claim that adds a clause keeps most of its
    meaning, and Jaccard punishes the longer side twice for it.
    """
    a, b = _words(left), _words(right)
    if not a or not b:
        return 1.0 if a == b else 0.0
    return 2 * len(a & b) / (len(a) + len(b))


def _assessment(text: str) -> tuple[int, frozenset[str]]:
    """Direction (+1, 0, -1) and rating words, from the fixed lists."""
    words = set(_WORD.findall(text.lower()))
    positive, negative = len(words & _POSITIVE), len(words & _NEGATIVE)
    direction = (positive > negative) - (negative > positive)
    return direction, frozenset(words & _RATINGS)


def _assessment_changed(before: str, after: str) -> bool:
    """Whether the judgement moved, not merely its phrasing.

    Opposite directions both stated, or two different rating words. A
    direction appearing where there was none is rewording, not a reversal.
    """
    before_direction, before_ratings = _assessment(before)
    after_direction, after_ratings = _assessment(after)
    if before_direction and after_direction and before_direction != after_direction:
        return True
    return bool(before_ratings and after_ratings and before_ratings != after_ratings)


# --------------------------------------------------------------------------
# Matching and comparing
# --------------------------------------------------------------------------


def _match(
    before: Sequence[ClaimSnapshot], after: Sequence[ClaimSnapshot]
) -> list[tuple[ClaimSnapshot, ClaimSnapshot]]:
    """Pair claims one-to-one, best similarity first."""
    scored = sorted(
        (
            (similarity(old.text, new.text), index_old, index_new)
            for index_old, old in enumerate(before)
            for index_new, new in enumerate(after)
        ),
        key=lambda item: (-item[0], item[1], item[2]),
    )
    used_before: set[int] = set()
    used_after: set[int] = set()
    pairs: list[tuple[ClaimSnapshot, ClaimSnapshot]] = []
    for score, index_old, index_new in scored:
        if score < MATCH_THRESHOLD:
            break
        if index_old in used_before or index_new in used_after:
            continue
        used_before.add(index_old)
        used_after.add(index_new)
        pairs.append((before[index_old], after[index_new]))
    return pairs


_JOB_WORDS: Final = re.compile(r"\b(hiring|hires?|roles?|openings?|postings?|jobs?|vacanc\w*|headcount)\b", re.I)
_PRODUCT_WORDS: Final = re.compile(r"\b(launch\w*|releas\w*|unveil\w*|product\w*|introduc\w*|debut\w*)\b", re.I)
_COMPETITOR_WORDS: Final = re.compile(r"\b(competitor\w*|rival\w*|entrant\w*|challenger\w*|compet\w*)\b", re.I)


def _category(claim: ClaimSnapshot, figure: Figure | None = None) -> ChangeCategory:
    """Which of the masterplan's kinds this is, from what the evidence is."""
    metric = figure.value.metric_class if figure else None
    metrics = {item.value.metric_class for item in claim.figures}
    if metric is MetricClass.SHARE_PRICE or MetricClass.SHARE_PRICE in metrics:
        return ChangeCategory.STOCK_INFORMATION
    if SourceCategory.JOBS in claim.source_categories or _JOB_WORDS.search(claim.text):
        return ChangeCategory.JOB_POSTINGS
    if claim.claim_type is ClaimType.FORECAST:
        return ChangeCategory.FORECAST_ASSUMPTIONS
    if (
        metric in {MetricClass.CURRENCY, MetricClass.RATIO}
        or metrics & {MetricClass.CURRENCY, MetricClass.RATIO}
        or claim.source_categories & {SourceCategory.FINANCIAL, SourceCategory.FILING}
    ):
        return ChangeCategory.FINANCIAL_FIGURES
    if _PRODUCT_WORDS.search(claim.text):
        return ChangeCategory.NEW_PRODUCTS
    if _COMPETITOR_WORDS.search(claim.text):
        return ChangeCategory.NEW_COMPETITORS
    return ChangeCategory.OTHER


def _side(claim: ClaimSnapshot, figure: Figure | None = None) -> Side:
    return Side(
        claim_id=claim.id,
        text=claim.text,
        claim_type=claim.claim_type,
        confidence=claim.confidence,
        value=figure.value.reported if figure else None,
        period=figure.period if figure else None,
    )


def _quote(text: str, limit: int = 140) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= limit else compact[: limit - 1].rstrip() + "…"


def _figure_change(
    old: ClaimSnapshot, new: ClaimSnapshot
) -> tuple[ChangeKind, Figure, Figure] | None:
    """The first figure that moved beyond tolerance, or moved to a later period."""
    for after in new.figures:
        if not after.value.comparable:
            continue
        for before in old.figures:
            if not before.value.comparable:
                continue
            if before.value.metric_class is not after.value.metric_class:
                continue
            if (before.basis or None) != (after.basis or None):
                continue
            if before.period and after.period and before.period != after.period:
                if after.period > before.period:
                    return ChangeKind.NEWER_PERIOD, before, after
                continue
            outcome = compare(
                before.value,
                after.value,
                left_period=before.period,
                right_period=after.period,
            )
            if outcome.is_conflict:
                return ChangeKind.FIGURE_CHANGED, before, after
    return None


def _tolerance_words(metric: MetricClass) -> str:
    tolerance = TOLERANCES.get(metric, TOLERANCES[MetricClass.UNKNOWN])
    if tolerance.absolute is not None:
        return f"{tolerance.absolute} percentage points"
    relative = (tolerance.relative or Decimal(0)) * 100
    return f"{relative.normalize():f}%"


def _pair_change(old: ClaimSnapshot, new: ClaimSnapshot) -> Change | None:
    """What changed between two claims already judged to be the same claim."""
    moved = _figure_change(old, new)
    if moved is not None:
        kind, before, after = moved
        period = f" for the period ending {after.period}" if after.period else ""
        if kind is ChangeKind.NEWER_PERIOD:
            summary = (
                f"A newer figure is available: {after.value.reported} for the period "
                f"ending {after.period}, superseding {before.value.reported} for "
                f"{before.period}. Source: {after.source_name}."
            )
        else:
            summary = (
                f"Moved from {before.value.reported} to {after.value.reported}{period}, "
                f"beyond the {_tolerance_words(after.value.metric_class)} tolerance for "
                f"this kind of figure. Source: {after.source_name}."
            )
        return Change(
            kind=kind,
            category=_category(new, after),
            summary=summary,
            before=_side(old, before),
            after=_side(new, after),
            evidence_ids=(after.evidence_id,),
            confidence_from=old.confidence,
            confidence_to=new.confidence,
        )

    old_gap = old.claim_type is ClaimType.UNCERTAINTY
    new_gap = new.claim_type is ClaimType.UNCERTAINTY
    if old_gap and not new_gap:
        return Change(
            kind=ChangeKind.GAP_CLOSED,
            category=_category(new),
            summary=f"Previously unconfirmed, now evidenced: {_quote(new.text)}",
            before=_side(old),
            after=_side(new),
            evidence_ids=new.evidence_ids,
            confidence_from=old.confidence,
            confidence_to=new.confidence,
        )
    if new_gap and not old_gap:
        return Change(
            kind=ChangeKind.GAP_OPENED,
            category=_category(old),
            summary=f"Could not be confirmed this time: {_quote(old.text)}",
            before=_side(old),
            after=_side(new),
            confidence_from=old.confidence,
            confidence_to=new.confidence,
        )

    conclusion = new.claim_type in {ClaimType.ANALYSIS, ClaimType.FORECAST} or old.claim_type in {
        ClaimType.ANALYSIS,
        ClaimType.FORECAST,
    }
    if conclusion:
        reasons: list[str] = []
        if old.claim_type is not new.claim_type:
            reasons.append(f"it is now {new.claim_type.value} rather than {old.claim_type.value}")
        if (old.confidence or new.confidence) and old.confidence != new.confidence:
            reasons.append(
                f"confidence went from {old.confidence or 'unassessed'} to "
                f"{new.confidence or 'unassessed'}"
            )
        if _assessment_changed(old.text, new.text):
            reasons.append("the assessment itself changed")
        if reasons:
            return Change(
                kind=ChangeKind.CONCLUSION_CHANGED,
                category=_category(new),
                summary=(
                    f"The conclusion changed: {'; '.join(reasons)}. "
                    f"Was: {_quote(old.text)} Now: {_quote(new.text)}"
                ),
                before=_side(old),
                after=_side(new),
                evidence_ids=new.evidence_ids,
                confidence_from=old.confidence,
                confidence_to=new.confidence,
            )

    if new.claim_type is ClaimType.FORECAST and old.claim_type is ClaimType.FORECAST:
        before_assumptions = " ".join(old.assumptions)
        after_assumptions = " ".join(new.assumptions)
        if (before_assumptions or after_assumptions) and similarity(
            before_assumptions, after_assumptions
        ) < MATCH_THRESHOLD:
            return Change(
                kind=ChangeKind.ASSUMPTIONS_CHANGED,
                category=ChangeCategory.FORECAST_ASSUMPTIONS,
                summary=(
                    "The forecast now rests on different assumptions. "
                    f"Was: {_quote('; '.join(old.assumptions) or 'none stated')} "
                    f"Now: {_quote('; '.join(new.assumptions) or 'none stated')}"
                ),
                before=_side(old),
                after=_side(new),
                evidence_ids=new.evidence_ids,
                confidence_from=old.confidence,
                confidence_to=new.confidence,
            )

    return None


_ASSERTIVE: Final = frozenset({ClaimType.FACT, ClaimType.ANALYSIS, ClaimType.FORECAST})


def compare_claims(
    before: Sequence[ClaimSnapshot],
    after: Sequence[ClaimSnapshot],
    *,
    before_sources: Iterable[str] | None = None,
    after_sources: Iterable[str] | None = None,
) -> tuple[list[Change], int]:
    """Every meaningful change from `before` to `after`, and how many pairs held.

    `before_sources` and `after_sources` are every source key each version
    retrieved, cited or not. They decide the two unmatched cases: a new claim is
    a finding only if it rests on a source the old version never had, and an
    old claim is gone only if none of its sources came back.
    """
    old_keys = frozenset(before_sources or (key for claim in before for key in claim.source_keys))
    new_keys = frozenset(after_sources or (key for claim in after for key in claim.source_keys))

    pairs = _match(before, after)
    changes: list[Change] = []
    unchanged = 0
    for old, new in pairs:
        change = _pair_change(old, new)
        if change is None:
            unchanged += 1
        else:
            changes.append(change)

    matched_before = {old.id for old, _ in pairs}
    matched_after = {new.id for _, new in pairs}

    for new in after:
        if new.id in matched_after or new.claim_type not in _ASSERTIVE:
            continue
        fresh = new.source_keys - old_keys
        if not fresh:
            continue
        changes.append(
            Change(
                kind=ChangeKind.NEW_FINDING,
                category=_category(new),
                summary=f"New: {_quote(new.text)}",
                before=None,
                after=_side(new),
                evidence_ids=new.evidence_ids,
                confidence_to=new.confidence,
            )
        )

    for old in before:
        if old.id in matched_before or old.claim_type not in _ASSERTIVE:
            continue
        if not old.source_keys or old.source_keys & new_keys:
            continue
        changes.append(
            Change(
                kind=ChangeKind.NO_LONGER_FOUND,
                category=_category(old),
                summary=(
                    "Not found again in this update. The sources behind it were not "
                    f"retrieved this time: {_quote(old.text)}"
                ),
                before=_side(old),
                after=None,
                confidence_from=old.confidence,
            )
        )

    changes.sort(key=lambda change: _KIND_ORDER[change.kind])
    return changes, unchanged


# --------------------------------------------------------------------------
# Reading rows
# --------------------------------------------------------------------------


def source_key(source: Source) -> str:
    """How one source is recognised across versions.

    Versions re-fetch rather than share rows (`DEC-19`), so ids never match;
    the normalised URL does, and a source with none — a filing by identifier,
    a user's document — falls back to what does identify it.
    """
    if source.url_normalized:
        return f"url:{source.url_normalized}"
    if source.upload_id:
        return f"upload:{source.upload_id}"
    if source.identifier:
        return f"id:{source.identifier}"
    return f"name:{source.name.strip().lower()}"


def load_snapshot(session: Session, version_id: UUID) -> tuple[list[ClaimSnapshot], set[str]]:
    """Every claim in a version, with what it rests on, plus every source key."""
    claims = (
        session.execute(
            select(Claim).where(Claim.version_id == version_id).order_by(Claim.created_at, Claim.id)
        )
        .scalars()
        .all()
    )
    rows = session.execute(
        select(ClaimEvidence.claim_id, Evidence, Source)
        .join(Evidence, Evidence.id == ClaimEvidence.evidence_id)
        .join(Source, Source.id == Evidence.source_id)
        .where(
            Evidence.version_id == version_id,
            ClaimEvidence.role == EvidenceRole.SUPPORTING,
        )
        .order_by(Evidence.extracted_at, Evidence.id)
    ).all()

    by_claim: dict[UUID, list[tuple[Evidence, Source]]] = {}
    for claim_id, evidence, source in rows:
        by_claim.setdefault(claim_id, []).append((evidence, source))

    snapshots: list[ClaimSnapshot] = []
    for claim in claims:
        cited = by_claim.get(claim.id, [])
        figures = tuple(
            Figure(
                evidence_id=evidence.id,
                value=NormalizedValue(
                    reported=evidence.value_raw or evidence.content,
                    status=evidence.normalization,
                    metric_class=classify_metric(evidence.content),
                    value=evidence.value_normalized,
                    currency=evidence.currency,
                ),
                period=(
                    evidence.period_end.isoformat()
                    if evidence.period_end
                    else evidence.period_start.isoformat()
                    if evidence.period_start
                    else None
                ),
                basis=evidence.value_basis,
                source_name=source.name,
                source_category=source.category,
            )
            for evidence, source in cited
            if evidence.value_normalized is not None
        )
        stated = (claim.assumptions or {}).get("stated")
        snapshots.append(
            ClaimSnapshot(
                id=claim.id,
                text=claim.text,
                claim_type=claim.claim_type,
                confidence=claim.confidence,
                assumptions=tuple(str(item) for item in stated) if isinstance(stated, list) else (),
                figures=figures,
                evidence_ids=tuple(dict.fromkeys(evidence.id for evidence, _ in cited)),
                source_keys=frozenset(source_key(source) for _, source in cited),
                source_categories=frozenset(source.category for _, source in cited),
            )
        )

    all_sources = {
        source_key(source)
        for source in session.execute(select(Source).where(Source.version_id == version_id)).scalars()
    }
    return snapshots, all_sources


def compare_versions(
    session: Session, base: ResearchVersion, current: ResearchVersion
) -> ChangeSummary:
    """What's Changed for `current`, measured against `base`."""
    before, before_sources = load_snapshot(session, base.id)
    after, after_sources = load_snapshot(session, current.id)
    changes, unchanged = compare_claims(
        before, after, before_sources=before_sources, after_sources=after_sources
    )
    return ChangeSummary(
        compared_version_id=base.id,
        compared_version_number=base.version_number,
        compared_created_at=base.created_at,
        changes=tuple(changes),
        unchanged=unchanged,
        new_sources=len(after_sources - before_sources),
    )
