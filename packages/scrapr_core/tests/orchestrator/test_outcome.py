"""Stage 1.8: the difference between partial and failed (`REQ-AGENT-009`).

Every case here is a way the product could lie by omission. A run that gathered
nothing and reports as complete; a run that skipped an area and never says so; a
gap named in vendor error codes the reader cannot act on. The tests exist to
make each of those impossible rather than unlikely.
"""

from __future__ import annotations

import pytest

from scrapr_core.db.enums import TerminationReason, VersionStatus
from scrapr_core.orchestrator.outcome import AreaOutcome, summarise_run
from scrapr_core.orchestrator.sufficiency import SufficiencyVerdict
from scrapr_core.tools import ToolCategory
from scrapr_core.tools.contract import ToolFailure


def verdict(decision: str = "sufficient", area: str = "Financials") -> SufficiencyVerdict:
    return SufficiencyVerdict(
        decision=decision,  # type: ignore[arg-type]
        rationale="2 of 2 questions resolved",
        area_name=area,
    )


def area(
    name: str = "Financials",
    *,
    decision: str = "sufficient",
    evidence: int = 4,
    failures: list[ToolFailure] | None = None,
    skipped: list[ToolCategory] | None = None,
) -> AreaOutcome:
    return AreaOutcome(
        area_name=name,
        verdict=verdict(decision, name),
        evidence_count=evidence,
        failures=failures or [],
        skipped_categories=skipped or [],
    )


def failure(kind: str = "timeout") -> ToolFailure:
    return ToolFailure(
        kind=kind,  # type: ignore[arg-type]
        message="upstream returned 503 from provider-x",
        tool="provider_x",
        category=ToolCategory.NEWS,
    )


# --------------------------------------------------------------------------
# Complete
# --------------------------------------------------------------------------


def test_everything_researched_and_answered_is_complete() -> None:
    outcome = summarise_run([area(), area("Hiring")], [])

    assert outcome.status is VersionStatus.COMPLETE
    assert outcome.termination_reason is TerminationReason.SUFFICIENCY
    assert outcome.gaps == ()


# --------------------------------------------------------------------------
# Failed
# --------------------------------------------------------------------------


def test_no_evidence_anywhere_is_a_failure_not_an_empty_report() -> None:
    """`AC-3`. A run with nothing to show has no part to deliver, and dressing
    that up as a report is the clearest breach of the product's promise."""
    outcome = summarise_run([area(evidence=0), area("Hiring", evidence=0)], [])

    assert outcome.status is VersionStatus.FAILED
    assert outcome.termination_reason is TerminationReason.FAILURE
    assert outcome.is_failure


def test_a_total_failure_still_names_the_areas() -> None:
    outcome = summarise_run([area("Financials", evidence=0)], [])

    assert "Financials could not be researched." in outcome.gaps


def test_a_failure_with_no_areas_at_all_still_says_something() -> None:
    """An empty gap list would leave the UI with nothing to render and the user
    with no idea what happened."""
    outcome = summarise_run([], [])

    assert outcome.is_failure
    assert outcome.gaps == ("No evidence could be gathered for this research.",)


# --------------------------------------------------------------------------
# Partial
# --------------------------------------------------------------------------


def test_one_dead_area_leaves_the_rest_deliverable() -> None:
    """`AC-1`, `AC-2`. The run does not fail, and the gap is named."""
    outcome = summarise_run([area(), area("News", evidence=0)], [])

    assert outcome.status is VersionStatus.PARTIAL
    assert outcome.gaps == ("News could not be researched.",)


def test_an_unresolved_question_makes_the_version_partial() -> None:
    """Evidence was gathered, but not enough of it: the report ships and says
    what is missing."""
    outcome = summarise_run([area()], ["What is FY2025 segment revenue?"])

    assert outcome.status is VersionStatus.PARTIAL


def test_a_ceiling_anywhere_makes_the_reason_a_ceiling() -> None:
    """`REQ-AGENT-005 AC-4`: the most specific true thing about why it stopped."""
    outcome = summarise_run(
        [area(), area("Hiring", decision="ceiling_reached")],
        ["What is headcount?"],
    )

    assert outcome.status is VersionStatus.PARTIAL
    assert outcome.termination_reason is TerminationReason.CEILING


def test_a_ceiling_area_is_named_even_when_it_found_something() -> None:
    """`AC-4`: no section may imply coverage the evidence does not support."""
    outcome = summarise_run([area(decision="ceiling_reached")], [])

    assert any("effort limit" in gap for gap in outcome.gaps)


def test_a_skipped_category_is_reported_as_partial_coverage() -> None:
    outcome = summarise_run([area(skipped=[ToolCategory.FILINGS])], [])

    assert outcome.status is VersionStatus.PARTIAL
    assert any("filings was not reached" in gap for gap in outcome.gaps)


def test_unread_sources_are_mentioned_without_naming_the_vendor() -> None:
    """`REQ-ACT-003`, `REQ-SEC-010`. The reader is told what was not covered,
    never which provider returned a 503."""
    outcome = summarise_run([area(failures=[failure()])], [])

    assert outcome.status is VersionStatus.PARTIAL
    gap = outcome.gaps[0]
    assert "some sources could not be read" in gap
    assert "provider_x" not in gap
    assert "503" not in gap


@pytest.mark.parametrize("kind", ["timeout", "paywalled", "blocked", "rate_limited"])
def test_no_failure_kind_leaks_into_a_gap_sentence(kind: str) -> None:
    outcome = summarise_run([area(failures=[failure(kind)])], [])

    assert kind not in outcome.gaps[0]
