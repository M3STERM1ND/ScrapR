"""Update Research: what to re-check first, and what changed (`REQ-VER-001..009`).

`prioritize` orders an update's areas by how likely their evidence is to have
moved (`DEC-19`). `compare` decides what, between two versions, is a meaningful
difference rather than rewording (`DEC-20`). Neither calls a model.
"""

from __future__ import annotations

from scrapr_core.versioning.compare import (
    Change,
    ChangeCategory,
    ChangeKind,
    ChangeSummary,
    ClaimSnapshot,
    Figure,
    compare_claims,
    compare_versions,
    load_snapshot,
)
from scrapr_core.versioning.prioritize import (
    AreaHistory,
    CitedEvidence,
    PrioritizedArea,
    Priority,
    prioritize_areas,
)

__all__ = [
    "AreaHistory",
    "Change",
    "ChangeCategory",
    "ChangeKind",
    "ChangeSummary",
    "CitedEvidence",
    "ClaimSnapshot",
    "Figure",
    "PrioritizedArea",
    "Priority",
    "compare_claims",
    "compare_versions",
    "load_snapshot",
    "prioritize_areas",
]
