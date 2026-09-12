"""Confidence assignment (`DEC-09`, `REQ-EVID-015`, `REQ-EVID-016`).

A pure function, tested as a table. Two properties are worth asserting directly
rather than case by case, because they are what make the scale mean anything:

* **No demotion ever raises a level.** If one could, the rationale would be
  describing a different computation than the one that ran.
* **Tier ordering holds** (`REQ-EVID-016 AC-1`): the same claim on a primary
  source outranks one on a lower-tier source, every time.

The alignment test matters most. `DEC-09 §4.1`'s first two rows deliberately
mirror `DEC-04 §3.2`'s resolution rule, so a question the run declared answered
cannot carry a claim the report calls weakly sourced. If those two ever drift,
neither number means anything.
"""

from __future__ import annotations

import pytest

from scrapr_core.db.enums import AuthorityTier, ClaimType
from scrapr_core.evidence.confidence import Confidence, ConfidenceInputs, assess

ORDER = {Confidence.LOW: 0, Confidence.MODERATE: 1, Confidence.HIGH: 2}


def inputs(**overrides: object) -> ConfidenceInputs:
    base: dict[str, object] = {
        "claim_type": ClaimType.FACT,
        "distinct_sources": 2,
        "best_tier": AuthorityTier.SECONDARY,
        "above_lower_sources": 1,
    }
    return ConfidenceInputs(**{**base, **overrides})  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# The base table — `DEC-09 §4.1`
# --------------------------------------------------------------------------


def test_a_primary_source_alone_is_high() -> None:
    """A figure read straight from a filing needs no corroborator, which is the
    same allowance `DEC-04 §3.2` makes for resolution."""
    verdict = assess(
        inputs(best_tier=AuthorityTier.PRIMARY, distinct_sources=1, above_lower_sources=1)
    )

    assert verdict.level is Confidence.HIGH


def test_two_sources_with_one_above_lower_is_high() -> None:
    assert assess(inputs()).level is Confidence.HIGH


def test_one_uncorroborated_secondary_is_moderate() -> None:
    assert (
        assess(inputs(distinct_sources=1, above_lower_sources=1)).level
        is Confidence.MODERATE
    )


def test_two_lower_tier_sources_are_moderate_not_high() -> None:
    """`DEC-08 §4` in its consequence: sources nobody vouched for corroborate
    each other only so far."""
    verdict = assess(
        inputs(best_tier=AuthorityTier.LOWER, distinct_sources=2, above_lower_sources=0)
    )

    assert verdict.level is Confidence.MODERATE


def test_a_single_lower_tier_source_is_low() -> None:
    verdict = assess(
        inputs(best_tier=AuthorityTier.LOWER, distinct_sources=1, above_lower_sources=0)
    )

    assert verdict.level is Confidence.LOW


def test_no_evidence_is_low() -> None:
    assert (
        assess(inputs(distinct_sources=0, above_lower_sources=0, best_tier=None)).level
        is Confidence.LOW
    )


# --------------------------------------------------------------------------
# Demotions — `DEC-09 §4.2`
# --------------------------------------------------------------------------


def test_an_unresolved_conflict_caps_at_low() -> None:
    """`REQ-EVID-014 AC-3`. A fact two credible sources disagree about is not
    high-confidence however good those sources are — the disagreement is the
    finding."""
    verdict = assess(inputs(best_tier=AuthorityTier.PRIMARY, has_unresolved_conflict=True))

    assert verdict.level is Confidence.LOW


def test_an_explained_conflict_demotes_one_level() -> None:
    """Explained is better than unresolved, and still worse than uncontested."""
    assert assess(inputs(has_explained_conflict=True)).level is Confidence.MODERATE


def test_stale_evidence_demotes() -> None:
    assert assess(inputs(has_stale_evidence=True)).level is Confidence.MODERATE


def test_an_inaccessible_source_caps_at_low() -> None:
    """`REQ-EVID-018` forbids citing one at all; if one slipped through, the
    claim cannot be better than a lead."""
    verdict = assess(
        inputs(best_tier=AuthorityTier.PRIMARY, cites_inaccessible_source=True)
    )

    assert verdict.level is Confidence.LOW


def test_demotions_stack() -> None:
    verdict = assess(inputs(has_explained_conflict=True, has_stale_evidence=True))

    assert verdict.level is Confidence.LOW


# --------------------------------------------------------------------------
# Claim types — `DEC-09 §4.3`
# --------------------------------------------------------------------------


def test_every_claim_type_gets_a_level() -> None:
    """`REQ-EVID-015`: every claim MUST carry one, with no exempt type.

    An earlier draft of `DEC-09` exempted forecasts and uncertainties — the
    intuitive move, and a reinterpretation of a MUST.
    """
    for claim_type in ClaimType:
        assert assess(inputs(claim_type=claim_type)).level in Confidence


@pytest.mark.parametrize(
    "claim_type",
    [
        pytest.param(ClaimType.ANALYSIS, id="analysis"),
        pytest.param(ClaimType.FORECAST, id="forecast"),
    ],
)
def test_analysis_and_forecast_cap_at_moderate(claim_type: ClaimType) -> None:
    """Neither can be better established than the facts under it. `HIGH` on an
    interpretation tells the reader the wrong thing about what they are
    reading."""
    verdict = assess(inputs(claim_type=claim_type, best_tier=AuthorityTier.PRIMARY))

    assert verdict.level is Confidence.MODERATE


def test_an_uncertainty_is_always_low() -> None:
    """The level describes how well established the claim's content is, and an
    uncertainty's content is something the evidence does not settle. Reading it
    as "how sure are we this is unknown" inverts the scale and puts HIGH beside
    every gap in the report."""
    verdict = assess(
        inputs(claim_type=ClaimType.UNCERTAINTY, best_tier=AuthorityTier.PRIMARY)
    )

    assert verdict.level is Confidence.LOW


# --------------------------------------------------------------------------
# Properties
# --------------------------------------------------------------------------


def test_no_demotion_ever_raises_a_level() -> None:
    """If one could, the rationale would describe a different computation than
    the one that ran."""
    clean = assess(inputs()).level

    for flag in (
        "has_unresolved_conflict",
        "has_explained_conflict",
        "has_stale_evidence",
        "cites_inaccessible_source",
    ):
        demoted = assess(inputs(**{flag: True})).level
        assert ORDER[demoted] <= ORDER[clean], flag


def test_a_primary_source_outranks_a_lower_tier_one() -> None:
    """`REQ-EVID-016 AC-1`, stated exactly as the criterion does."""
    primary = assess(
        inputs(best_tier=AuthorityTier.PRIMARY, distinct_sources=1, above_lower_sources=1)
    ).level
    lower = assess(
        inputs(best_tier=AuthorityTier.LOWER, distinct_sources=1, above_lower_sources=0)
    ).level

    assert ORDER[primary] > ORDER[lower]


def test_the_relationship_is_consistent_across_runs() -> None:
    """`REQ-EVID-016 AC-2`. A pure function of persisted rows is how this is
    guaranteed rather than hoped — a model judge could not promise it."""
    levels = {assess(inputs()).level for _ in range(10)}

    assert len(levels) == 1


def test_confidence_agrees_with_what_termination_called_resolved() -> None:
    """The alignment that keeps both numbers meaningful.

    `DEC-04 §3.2` resolves a question on two distinct sources with one above
    lower, or one primary. `DEC-09 §4.1`'s first two rows are the same shape,
    so a resolved question cannot carry a weakly-sourced claim.
    """
    two_sources = assess(inputs(distinct_sources=2, above_lower_sources=1)).level
    one_primary = assess(
        inputs(best_tier=AuthorityTier.PRIMARY, distinct_sources=1, above_lower_sources=1)
    ).level

    assert two_sources is Confidence.HIGH
    assert one_primary is Confidence.HIGH


def test_the_rationale_explains_the_level() -> None:
    """`REQ-DATA-012`. Generated from the same inputs as the level, so it
    cannot drift from what it explains."""
    verdict = assess(inputs(has_unresolved_conflict=True))

    assert "unresolved conflict" in verdict.rationale
    assert verdict.as_json["level"] == "low"
