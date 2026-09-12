"""Stage 1.8 — What a run amounts to (`REQ-AGENT-009`, `REQ-TOOL-011`).

A research run rarely fails outright and rarely succeeds completely. What it
usually does is partly work, and the difference between an honest product and a
plausible one is entirely in how that middle case is reported.

Three outcomes, and they are not interchangeable:

* **complete** — every area was researched and every question resolved.
* **partial** — evidence was gathered, but something is missing: an area that
  could not be researched, a question nothing answered, a ceiling reached. The
  report ships, and it says what is missing (`AC-2`, `AC-4`).
* **failed** — nothing was gathered at all. This is an error state, never an
  empty report presented as complete (`AC-3`).

The distinction between `partial` and `failed` is deliberately drawn at *any
evidence at all*. A run that found one usable fact has something to show and a
gap to name; a run that found none has nothing to show, and dressing that up as
a report would be the clearest possible breach of the product's promise.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import final

from scrapr_core.db.enums import TerminationReason, VersionStatus
from scrapr_core.orchestrator.sufficiency import SufficiencyVerdict
from scrapr_core.tools.contract import ToolCategory, ToolFailure

__all__ = ["AreaOutcome", "RunOutcome", "summarise_run"]


@final
@dataclass(frozen=True, slots=True)
class AreaOutcome:
    """How one area finished."""

    area_name: str
    verdict: SufficiencyVerdict
    evidence_count: int
    failures: Sequence[ToolFailure] = ()
    skipped_categories: Sequence[ToolCategory] = ()

    @property
    def was_researched(self) -> bool:
        """Whether this area produced anything at all.

        An area that produced nothing is a gap the report owes the reader by
        name (`AC-2`), regardless of why.
        """
        return self.evidence_count > 0

    @property
    def hit_a_ceiling(self) -> bool:
        return self.verdict.decision == "ceiling_reached"


@final
@dataclass(frozen=True, slots=True)
class RunOutcome:
    """What the run produced, and what it owes the reader."""

    status: VersionStatus
    termination_reason: TerminationReason
    gaps: Sequence[str] = ()
    """Plain sentences naming what could not be researched. These reach the
    report; they are not a log line."""

    @property
    def is_failure(self) -> bool:
        return self.status is VersionStatus.FAILED


def summarise_run(
    areas: Sequence[AreaOutcome],
    unresolved_questions: Sequence[str],
) -> RunOutcome:
    """Decide what the run amounts to, and name every gap in it.

    Ordering matters here. Total failure is checked first, because a run with no
    evidence cannot be partial — there is no part to deliver.
    """
    researched = [area for area in areas if area.was_researched]
    gaps = _gaps(areas)

    if not researched:
        return RunOutcome(
            status=VersionStatus.FAILED,
            termination_reason=TerminationReason.FAILURE,
            gaps=tuple(gaps) or ("No evidence could be gathered for this research.",),
        )

    if gaps or unresolved_questions:
        # A ceiling anywhere makes the *reason* a ceiling: it is the most
        # specific true thing that can be said about why the run stopped
        # (`REQ-AGENT-005 AC-4`, `DEC-04 §6.2`).
        reason = (
            TerminationReason.CEILING
            if any(area.hit_a_ceiling for area in areas)
            else TerminationReason.SUFFICIENCY
        )
        return RunOutcome(
            status=VersionStatus.PARTIAL, termination_reason=reason, gaps=tuple(gaps)
        )

    return RunOutcome(
        status=VersionStatus.COMPLETE,
        termination_reason=TerminationReason.SUFFICIENCY,
    )


def _gaps(areas: Sequence[AreaOutcome]) -> list[str]:
    """Name what went wrong, per area, in terms a reader can act on.

    Tool names and provider errors never appear here: the sentence a user reads
    says what was not covered, not which vendor returned a 503
    (`REQ-ACT-003`, `REQ-SEC-010`).
    """
    gaps: list[str] = []

    for area in areas:
        if not area.was_researched:
            gaps.append(f"{area.area_name} could not be researched.")
            continue

        if area.skipped_categories:
            kinds = ", ".join(
                category.value.replace("_", " ") for category in area.skipped_categories
            )
            gaps.append(
                f"{area.area_name} was researched only in part: {kinds} was not "
                "reached before the effort limit."
            )
        elif area.hit_a_ceiling:
            gaps.append(
                f"{area.area_name} reached its effort limit before every question "
                "was answered."
            )
        elif area.failures:
            gaps.append(
                f"{area.area_name} was researched, but some sources could not be "
                "read."
            )

    return gaps
