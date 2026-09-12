"""Evidence and trust — Phase 2.

Where retrieved material becomes something a reader can weigh. Five pure
modules, none of which calls a model:

* `tiering` — how close a source is to the origin (`DEC-08`)
* `normalize` — units, currency, scale and metric class (`REQ-EVID-008`)
* `dedupe` — one URL is one source; syndication does not corroborate itself
* `conflict` — what counts as disagreement, and what explains it (`DEC-10`)
* `confidence` — what a reader should do with a claim (`DEC-09`)

**All deterministic, on purpose.** `REQ-EVID-002 AC-3` requires tiering be
inspectable, `REQ-EVID-016 AC-2` requires confidence be consistent across runs,
and `REQ-EVID-013 AC-3` forbids invented explanations. A model in any of these
paths would break all three, and would make "why did this claim get moderate"
unanswerable.
"""

from __future__ import annotations

from scrapr_core.evidence.confidence import (
    Confidence,
    ConfidenceInputs,
    ConfidenceVerdict,
    assess,
)
from scrapr_core.evidence.conflict import (
    TOLERANCES,
    ComparisonResult,
    ConflictReason,
    compare,
    explain,
    is_stale,
)
from scrapr_core.evidence.dedupe import content_fingerprint, normalize_url
from scrapr_core.evidence.normalize import (
    MetricClass,
    NormalizedValue,
    classify_metric,
    normalize_value,
)
from scrapr_core.evidence.tiering import TierDecision, assign_tier, registrable_host

__all__ = [
    "TOLERANCES",
    "ComparisonResult",
    "Confidence",
    "ConfidenceInputs",
    "ConfidenceVerdict",
    "ConflictReason",
    "MetricClass",
    "NormalizedValue",
    "TierDecision",
    "assess",
    "assign_tier",
    "classify_metric",
    "compare",
    "content_fingerprint",
    "explain",
    "is_stale",
    "normalize_url",
    "normalize_value",
    "registrable_host",
]
