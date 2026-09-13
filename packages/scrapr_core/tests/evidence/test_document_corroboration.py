"""Uploads are support, never corroboration (`REQ-DOC-008 AC-3`, `DEC-09`).

A behaviour change worth its own file, because it is the kind of rule that gets
quietly undone. Every branch of `_base` is exercised with and without documents,
so a future edit that folds `document_sources` back into `distinct_sources`
fails here rather than shipping a report where a reader's own spreadsheet made
their claim high-confidence.
"""

from __future__ import annotations

import pytest

from scrapr_core.db.enums import AuthorityTier, ClaimType
from scrapr_core.evidence.confidence import Confidence, ConfidenceInputs, assess


def _inputs(**overrides: object) -> ConfidenceInputs:
    base: dict[str, object] = {"claim_type": ClaimType.FACT}
    base.update(overrides)
    return ConfidenceInputs(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# A document alone
# --------------------------------------------------------------------------


def test_one_document_alone_is_low() -> None:
    """The reader supplied it. It cannot verify itself."""
    verdict = assess(_inputs(document_sources=1))

    assert verdict.level is Confidence.LOW
    assert "cannot corroborate itself" in verdict.rationale


def test_two_documents_alone_are_still_low() -> None:
    """The rule that matters. Two lower-tier *web* sources reach MODERATE, so
    without the exclusion a reader could upload the same figure twice — or one
    file the pipeline chunked in two — and manufacture corroboration."""
    verdict = assess(_inputs(document_sources=2))

    assert verdict.level is Confidence.LOW
    assert "nothing found elsewhere" in verdict.rationale


def test_ten_documents_alone_are_still_low() -> None:
    assert assess(_inputs(document_sources=10)).level is Confidence.LOW


def test_a_document_only_claim_is_not_described_as_unsourced() -> None:
    """LOW is right; "no evidence cited" is not.

    The reader gave us evidence. Telling them nothing was cited when their own
    document was read and quoted is a different, and wrong, statement.
    """
    verdict = assess(_inputs(document_sources=1))
    assert "no evidence cited" not in verdict.rationale


def test_no_evidence_at_all_still_says_so() -> None:
    verdict = assess(_inputs())
    assert verdict.level is Confidence.LOW
    assert verdict.rationale == "no evidence cited"


# --------------------------------------------------------------------------
# A document beside real sources
# --------------------------------------------------------------------------


def test_a_document_does_not_lift_one_web_source_to_high() -> None:
    """One source above lower tier plus a document is still uncorroborated.

    This is the exact substitution the rule forbids: without it, the document
    would be the second distinct source and the claim would read HIGH.
    """
    with_document = assess(
        _inputs(
            distinct_sources=1,
            above_lower_sources=1,
            best_tier=AuthorityTier.SECONDARY,
            document_sources=1,
        )
    )
    without = assess(
        _inputs(
            distinct_sources=1,
            above_lower_sources=1,
            best_tier=AuthorityTier.SECONDARY,
        )
    )

    assert with_document.level is Confidence.MODERATE
    assert with_document.level is without.level


def test_a_document_never_changes_the_level_only_the_sentence() -> None:
    """Across every sourcing shape. If a document can move a level anywhere,
    it is corroboration by another name."""
    shapes = [
        {},
        {"distinct_sources": 1},
        {"distinct_sources": 2},
        {"distinct_sources": 1, "above_lower_sources": 1},
        {"distinct_sources": 2, "above_lower_sources": 1},
        {"distinct_sources": 3, "above_lower_sources": 2},
        {"best_tier": AuthorityTier.PRIMARY, "distinct_sources": 1},
    ]

    for shape in shapes:
        if not shape.get("distinct_sources"):
            # The document-only branch is deliberately different, and is
            # covered above.
            continue
        assert (
            assess(_inputs(**shape, document_sources=2)).level
            is assess(_inputs(**shape)).level
        ), shape


def test_the_document_is_named_in_the_rationale_when_it_supported_a_claim() -> None:
    """`REQ-DATA-012`. A reader who attached a file and saw no mention of it
    would reasonably conclude it was ignored. It was read; it just does not
    vote."""
    verdict = assess(
        _inputs(distinct_sources=2, above_lower_sources=1, document_sources=1)
    )

    assert "your uploaded document" in verdict.rationale
    assert "not counted as corroboration" in verdict.rationale


@pytest.mark.parametrize(("count", "noun"), [(1, "document"), (3, "documents")])
def test_the_rationale_counts_files_not_passages(count: int, noun: str) -> None:
    verdict = assess(
        _inputs(distinct_sources=2, above_lower_sources=1, document_sources=count)
    )
    assert f"your uploaded {noun}" in verdict.rationale


def test_no_document_leaves_the_rationale_untouched() -> None:
    """The wording of every existing claim must not change because a feature
    that does not apply to it now exists."""
    verdict = assess(_inputs(distinct_sources=2, above_lower_sources=1))
    assert verdict.rationale == (
        "2 distinct sources, at least one above lower tier"
    )


# --------------------------------------------------------------------------
# Interaction with the rest of the rules
# --------------------------------------------------------------------------


def test_a_conflict_still_caps_a_document_backed_claim() -> None:
    verdict = assess(
        _inputs(
            distinct_sources=2,
            above_lower_sources=1,
            document_sources=1,
            has_unresolved_conflict=True,
        )
    )
    assert verdict.level is Confidence.LOW


def test_a_primary_source_beside_a_document_is_still_high() -> None:
    """The exclusion removes a document's vote; it does not penalise a claim
    for having one."""
    verdict = assess(
        _inputs(
            best_tier=AuthorityTier.PRIMARY, distinct_sources=1, document_sources=1
        )
    )
    assert verdict.level is Confidence.HIGH
