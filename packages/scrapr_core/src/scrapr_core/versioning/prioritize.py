"""Which areas an update should re-check first (`REQ-VER-003 AC-3`, `DEC-19`).

An update re-asks the questions the version it updates planned, so the two are
measured against the same questions. What it changes is the **order**: areas
whose evidence is most likely to have moved go first, so a budget that runs out
cuts the least volatile area rather than the most.

Pure, and deliberately simple. Three tiers, each with a reason a person can
read in the activity feed, rather than a score whose weights nobody can explain:

1. **Stale** — evidence the area relied on is past its `DEC-10 §5` staleness
   window for its metric class.
2. **Volatile** — the area is researched through news, financial data or job
   postings, which move between any two dates.
3. **Stable** — everything else.

Ties keep the previous plan's order.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import IntEnum, unique
from typing import Final, final

from scrapr_core.evidence.conflict import is_stale
from scrapr_core.evidence.normalize import classify_metric

__all__ = [
    "AreaHistory",
    "CitedEvidence",
    "PrioritizedArea",
    "Priority",
    "prioritize_areas",
]

VOLATILE_CATEGORIES: Final = frozenset({"news", "financial", "jobs"})
"""Tool categories whose answers change between any two retrievals."""


@unique
class Priority(IntEnum):
    """Lower runs first."""

    STALE = 1
    VOLATILE = 2
    STABLE = 3


@final
@dataclass(frozen=True, slots=True)
class CitedEvidence:
    """What an area's previous evidence said, and when it was current."""

    content: str
    published_at: dt.datetime | None
    retrieved_at: dt.datetime


@final
@dataclass(frozen=True, slots=True)
class AreaHistory:
    """One area as the previous version planned and researched it."""

    name: str
    questions: tuple[str, ...]
    categories: tuple[str, ...]
    evidence: tuple[CitedEvidence, ...] = field(default_factory=tuple)


@final
@dataclass(frozen=True, slots=True)
class PrioritizedArea:
    """An area, where it falls, and why."""

    area: AreaHistory
    priority: Priority
    reason: str


def _stale_count(area: AreaHistory, now: dt.datetime) -> int:
    """How much of the area's evidence has aged past its window.

    Publication date where the source gave one, retrieval date otherwise: a
    figure read six months ago is at least six months old, whatever it was
    published as.
    """
    return sum(
        is_stale(item.published_at or item.retrieved_at, classify_metric(item.content), now=now)
        for item in area.evidence
    )


def prioritize_areas(
    areas: Sequence[AreaHistory], *, now: dt.datetime
) -> list[PrioritizedArea]:
    """Order areas for an update, most likely to have changed first."""
    ranked: list[tuple[Priority, int, PrioritizedArea]] = []

    for position, area in enumerate(areas):
        stale = _stale_count(area, now)
        volatile = sorted(set(area.categories) & VOLATILE_CATEGORIES)

        if stale:
            noun = "figure has" if stale == 1 else "figures have"
            priority = Priority.STALE
            reason = f"{stale} {noun} aged past the point where it is still current"
        elif volatile:
            priority = Priority.VOLATILE
            reason = f"researched through {', '.join(volatile)}, which change often"
        else:
            priority = Priority.STABLE
            reason = "sources that change slowly"

        ranked.append(
            (priority, position, PrioritizedArea(area=area, priority=priority, reason=reason))
        )

    ranked.sort(key=lambda item: (item[0], item[1]))
    return [entry for _, _, entry in ranked]
