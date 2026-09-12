"""Adversarial fixtures for the validation gate.

Implementation plan §15 names them: a fact claim with no evidence, a forecast
with no assumptions, a claim citing a source that was never readable. Each must
be **rejected**, because `REQ-EVID-017 AC-3` puts the constraint in the pipeline
rather than in a prompt, and a gate that only logs is a gate that ships the
defect.

The corresponding good cases are here too. A gate that rejects everything is as
useless as one that rejects nothing, and only the pair proves it discriminates.
"""

from __future__ import annotations

import datetime as dt
from uuid import UUID

import pytest
from sqlalchemy.orm import Session

from scrapr_core.db.enums import (
    Accessibility,
    AuthorityTier,
    ClaimType,
    EvidenceRole,
    NormalizationStatus,
    ResearchStatus,
    SourceCategory,
    VersionStatus,
)
from scrapr_core.db.models import (
    AnonymousSession,
    Claim,
    ClaimEvidence,
    Evidence,
    QuestionState,
    ResearchQuestion,
    ResearchSession,
    ResearchVersion,
    Source,
)
from scrapr_core.domain.ids import new_id
from scrapr_core.synthesis import validate_version

pytestmark = pytest.mark.integration


@pytest.fixture
def version_id(db_session: Session) -> UUID:
    owner = AnonymousSession(token_hash=f"hash-{new_id()}")
    db_session.add(owner)
    db_session.flush()

    research = ResearchSession(
        anonymous_session_id=owner.id,
        objective="Acme competitive position",
        status=ResearchStatus.RUNNING,
    )
    db_session.add(research)
    db_session.flush()

    version = ResearchVersion(
        session_id=research.id, version_number=1, status=VersionStatus.BUILDING
    )
    db_session.add(version)
    db_session.flush()
    return version.id


def add_evidence(
    db_session: Session,
    version_id: UUID,
    accessibility: Accessibility = Accessibility.ACCESSIBLE,
) -> Evidence:
    source = Source(
        version_id=version_id,
        url=f"https://acme.example/{new_id()}",
        url_normalized=f"https://acme.example/{new_id()}",
        name="Acme FY2025 results",
        category=SourceCategory.FILING,
        authority_tier=AuthorityTier.PRIMARY,
        retrieved_at=dt.datetime.now(dt.UTC),
        accessibility=accessibility,
    )
    db_session.add(source)
    db_session.flush()

    evidence = Evidence(
        version_id=version_id,
        source_id=source.id,
        content="Acme reported $1.2bn revenue for FY2025.",
        normalization=NormalizationStatus.NOT_APPLICABLE,
        extracted_at=dt.datetime.now(dt.UTC),
    )
    db_session.add(evidence)
    db_session.flush()
    return evidence


def add_claim(
    db_session: Session,
    version_id: UUID,
    claim_type: ClaimType,
    *,
    evidence: Evidence | None = None,
    role: EvidenceRole = EvidenceRole.SUPPORTING,
    assumptions: dict[str, str] | None = None,
) -> Claim:
    claim = Claim(
        version_id=version_id,
        text="Acme reported $1.2bn revenue for FY2025.",
        claim_type=claim_type,
        assumptions=assumptions,
    )
    db_session.add(claim)
    db_session.flush()

    if evidence is not None:
        db_session.add(
            ClaimEvidence(claim_id=claim.id, evidence_id=evidence.id, role=role)
        )
        db_session.flush()
    return claim


# --------------------------------------------------------------------------
# What must pass
# --------------------------------------------------------------------------


def test_an_empty_version_passes(db_session: Session, version_id: UUID) -> None:
    """Nothing asserted is nothing to verify. Emptiness is a synthesis problem,
    not a trust one."""
    assert validate_version(db_session, version_id).passed


def test_an_evidenced_fact_passes(db_session: Session, version_id: UUID) -> None:
    evidence = add_evidence(db_session, version_id)
    add_claim(db_session, version_id, ClaimType.FACT, evidence=evidence)

    assert validate_version(db_session, version_id).passed


def test_a_forecast_with_assumptions_passes(
    db_session: Session, version_id: UUID
) -> None:
    evidence = add_evidence(db_session, version_id)
    add_claim(
        db_session,
        version_id,
        ClaimType.FORECAST,
        evidence=evidence,
        assumptions={"basis": "three quarters of 18-22% growth"},
    )

    assert validate_version(db_session, version_id).passed


@pytest.mark.parametrize("claim_type", [ClaimType.ANALYSIS, ClaimType.UNCERTAINTY])
def test_analysis_and_uncertainty_need_no_evidence_link(
    db_session: Session, version_id: UUID, claim_type: ClaimType
) -> None:
    """`REQ-EVID-017 AC-1` scopes the rule to fact claims deliberately: naming
    an uncertainty is honest, and demanding evidence for it would push the
    system toward asserting things instead."""
    add_claim(db_session, version_id, claim_type)

    assert validate_version(db_session, version_id).passed


# --------------------------------------------------------------------------
# What must be rejected
# --------------------------------------------------------------------------


def test_a_fact_without_evidence_is_rejected(
    db_session: Session, version_id: UUID
) -> None:
    """The invariant the whole product rests on (`REQ-EVID-017`)."""
    claim = add_claim(db_session, version_id, ClaimType.FACT)

    report = validate_version(db_session, version_id)

    assert not report.passed
    assert [violation.rule for violation in report.violations] == ["REQ-EVID-017"]
    assert report.violations[0].claim_id == claim.id


def test_a_fact_supported_only_by_conflicting_evidence_is_rejected(
    db_session: Session, version_id: UUID
) -> None:
    """Evidence that *disagrees* is not support. Counting it would let a claim
    cite the very thing that contradicts it."""
    evidence = add_evidence(db_session, version_id)
    add_claim(
        db_session,
        version_id,
        ClaimType.FACT,
        evidence=evidence,
        role=EvidenceRole.CONFLICTING,
    )

    report = validate_version(db_session, version_id)

    assert not report.passed
    assert report.violations[0].rule == "REQ-EVID-017"


def test_a_forecast_with_empty_assumptions_is_rejected(
    db_session: Session, version_id: UUID
) -> None:
    """The check constraint refuses a null; an empty object satisfies it while
    stating nothing, which is the same failure with better manners."""
    evidence = add_evidence(db_session, version_id)
    add_claim(
        db_session,
        version_id,
        ClaimType.FORECAST,
        evidence=evidence,
        assumptions={},
    )

    report = validate_version(db_session, version_id)

    assert not report.passed
    assert report.violations[0].rule == "REQ-SYNTH-009"


@pytest.mark.parametrize(
    "accessibility",
    [Accessibility.PAYWALLED, Accessibility.BLOCKED, Accessibility.FAILED],
)
def test_a_claim_citing_an_unreadable_source_is_rejected(
    db_session: Session, version_id: UUID, accessibility: Accessibility
) -> None:
    """`REQ-EVID-018`: a citation a reader cannot check is not a citation."""
    evidence = add_evidence(db_session, version_id, accessibility=accessibility)
    add_claim(db_session, version_id, ClaimType.FACT, evidence=evidence)

    report = validate_version(db_session, version_id)

    assert not report.passed
    assert report.violations[0].rule == "REQ-EVID-018"
    assert accessibility.value in report.violations[0].detail


def test_every_violation_is_reported_not_just_the_first(
    db_session: Session, version_id: UUID
) -> None:
    """A gate that stops at the first defect turns one fix into three round
    trips through a model that is not cheap to run."""
    add_claim(db_session, version_id, ClaimType.FACT)
    add_claim(db_session, version_id, ClaimType.FACT)

    report = validate_version(db_session, version_id)

    assert len(report.violations) == 2


def test_the_summary_names_the_rule_and_the_claim(
    db_session: Session, version_id: UUID
) -> None:
    """The summary is what lands in the run record, so it has to be enough to
    debug from without re-running anything."""
    claim = add_claim(db_session, version_id, ClaimType.FACT)

    report = validate_version(db_session, version_id)

    assert "REQ-EVID-017" in report.summary()
    assert str(claim.id) in report.summary()


def test_a_passing_summary_says_so(db_session: Session, version_id: UUID) -> None:
    assert "passed" in validate_version(db_session, version_id).summary()


def test_the_gate_ignores_other_versions(db_session: Session, version_id: UUID) -> None:
    """Versions are independent (`REQ-VER-002`): a defect in one must not
    condemn another, or an update could never ship."""
    add_claim(db_session, version_id, ClaimType.FACT)

    other = ResearchVersion(
        session_id=db_session.get(ResearchVersion, version_id).session_id,  # type: ignore[union-attr]
        version_number=2,
        status=VersionStatus.BUILDING,
    )
    db_session.add(other)
    db_session.flush()

    assert validate_version(db_session, other.id).passed


# --------------------------------------------------------------------------
# Unanswered questions must be stated (`REQ-SYNTH-010`, `DEC-04 §6.3`)
# --------------------------------------------------------------------------


def add_question(
    db_session: Session,
    version_id: UUID,
    text: str,
    state: QuestionState = QuestionState.OPEN,
) -> ResearchQuestion:
    question = ResearchQuestion(
        version_id=version_id,
        area_name="Financials",
        text=text,
        ordering=new_id().int % 1000,
        resolution_state=state,
        tool_categories=["web_search"],
    )
    db_session.add(question)
    db_session.flush()
    return question


def test_an_unanswered_question_stated_as_an_uncertainty_passes(
    db_session: Session, version_id: UUID
) -> None:
    add_question(db_session, version_id, "What is segment revenue?")
    add_claim(
        db_session,
        version_id,
        ClaimType.UNCERTAINTY,
    ).text = "The evidence does not answer this: What is segment revenue?"
    db_session.flush()

    assert validate_version(db_session, version_id).passed


def test_an_unanswered_question_nobody_mentioned_is_rejected(
    db_session: Session, version_id: UUID
) -> None:
    """The rule that stops a thin run reading like a complete one. A report that
    omits the gap is claiming coverage it does not have."""
    evidence = add_evidence(db_session, version_id)
    add_claim(db_session, version_id, ClaimType.FACT, evidence=evidence)
    add_question(db_session, version_id, "What is segment revenue?")

    report = validate_version(db_session, version_id)

    assert not report.passed
    assert report.violations[0].rule == "REQ-SYNTH-010"
    assert "segment revenue" in report.violations[0].detail


def test_an_unanswerable_question_must_also_be_stated(
    db_session: Session, version_id: UUID
) -> None:
    """Unanswerable is terminal and honest, not quiet: it becomes an
    uncertainty claim exactly as a ceiling-terminated question does."""
    add_question(
        db_session, version_id, "What is pricing?", QuestionState.UNANSWERABLE
    )

    report = validate_version(db_session, version_id)

    assert not report.passed
    assert report.violations[0].rule == "REQ-SYNTH-010"


def test_a_resolved_question_needs_no_uncertainty(
    db_session: Session, version_id: UUID
) -> None:
    evidence = add_evidence(db_session, version_id)
    add_claim(db_session, version_id, ClaimType.FACT, evidence=evidence)
    add_question(db_session, version_id, "What is revenue?", QuestionState.RESOLVED)

    assert validate_version(db_session, version_id).passed


def test_each_missing_gap_is_reported_separately(
    db_session: Session, version_id: UUID
) -> None:
    add_question(db_session, version_id, "What is segment revenue?")
    add_question(db_session, version_id, "What is pricing?")

    report = validate_version(db_session, version_id)

    assert len(report.violations) == 2
