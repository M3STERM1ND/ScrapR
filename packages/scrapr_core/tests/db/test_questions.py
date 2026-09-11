"""The coverage query, against real rows (`DEC-04 §3.2`).

This is the input the termination decision is computed from, so the cases that
matter are the ones where a naive count would be wrong: the same source twice,
an unreadable source, evidence linked to a different question. Each of those
would make an area look better covered than it is, and an area that looks
covered stops being researched.
"""

from __future__ import annotations

import datetime as dt
from uuid import UUID

import pytest
from sqlalchemy.orm import Session

from scrapr_core.db.enums import (
    Accessibility,
    AuthorityTier,
    NormalizationStatus,
    ResearchStatus,
    SourceCategory,
    VersionStatus,
)
from scrapr_core.db.models import (
    AnonymousSession,
    Evidence,
    QuestionState,
    ResearchSession,
    ResearchVersion,
    Source,
)
from scrapr_core.db.repositories import QuestionRepository
from scrapr_core.domain.ids import new_id

pytestmark = pytest.mark.integration

PLAN = [
    ("Financial performance", ["What is revenue?", "What is margin?"], ["financial"]),
    ("Hiring", ["Who is being hired?"], ["jobs"]),
]


@pytest.fixture
def version_id(db_session: Session) -> UUID:
    owner = AnonymousSession(token_hash=f"hash-{new_id()}")
    db_session.add(owner)
    db_session.flush()
    research = ResearchSession(
        anonymous_session_id=owner.id,
        objective="Acme",
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


@pytest.fixture
def repo(db_session: Session) -> QuestionRepository:
    return QuestionRepository(db_session)


def add_source(
    db_session: Session,
    version_id: UUID,
    url: str | None = None,
    tier: AuthorityTier = AuthorityTier.SECONDARY,
    accessibility: Accessibility = Accessibility.ACCESSIBLE,
    identifier: str | None = None,
) -> Source:
    source = Source(
        version_id=version_id,
        url=url,
        url_normalized=url,
        identifier=identifier,
        name=url or identifier or "a source",
        category=SourceCategory.WEB,
        authority_tier=tier,
        retrieved_at=dt.datetime.now(dt.UTC),
        accessibility=accessibility,
    )
    db_session.add(source)
    db_session.flush()
    return source


def add_evidence(db_session: Session, version_id: UUID, source: Source) -> Evidence:
    evidence = Evidence(
        version_id=version_id,
        source_id=source.id,
        content="Revenue was $1.2bn.",
        normalization=NormalizationStatus.NOT_APPLICABLE,
        extracted_at=dt.datetime.now(dt.UTC),
    )
    db_session.add(evidence)
    db_session.flush()
    return evidence


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------


def test_a_plan_is_stored_in_plan_order(
    repo: QuestionRepository, version_id: UUID
) -> None:
    """Areas are assessed in plan order, so ordering is the sequence budget is
    spent in, not a display preference."""
    repo.persist_plan(version_id, PLAN)

    stored = repo.for_version(version_id)

    assert [q.text for q in stored] == [
        "What is revenue?",
        "What is margin?",
        "Who is being hired?",
    ]
    assert [q.ordering for q in stored] == [0, 1, 2]
    assert all(q.resolution_state is QuestionState.OPEN for q in stored)


def test_questions_remember_their_area_and_categories(
    repo: QuestionRepository, version_id: UUID
) -> None:
    repo.persist_plan(version_id, PLAN)

    hiring = repo.for_area(version_id, "Hiring")

    assert len(hiring) == 1
    assert hiring[0].tool_categories == ["jobs"]


def test_resolving_a_question_records_when(
    repo: QuestionRepository, version_id: UUID
) -> None:
    question = repo.persist_plan(version_id, PLAN)[0]

    repo.mark_resolved(question)

    assert question.resolution_state is QuestionState.RESOLVED
    assert question.resolved_at is not None
    assert repo.open_for_area(version_id, "Financial performance") == [
        *repo.for_area(version_id, "Financial performance")[1:]
    ]


def test_an_unanswerable_question_records_what_was_tried(
    repo: QuestionRepository, version_id: UUID
) -> None:
    """"Could not be established" is a claim the report makes, and a reader is
    entitled to know what was attempted (`DEC-04 §3.3`)."""
    question = repo.persist_plan(version_id, PLAN)[0]

    repo.mark_unanswerable(question, {"kinds": ["paywalled"], "sources": 2})

    assert question.resolution_state is QuestionState.UNANSWERABLE
    assert question.unanswerable_reason == {"kinds": ["paywalled"], "sources": 2}


def test_unresolved_gathers_open_and_unanswerable_together(
    repo: QuestionRepository, version_id: UUID
) -> None:
    """Both become `uncertainty` claims: one because nothing was found in the
    effort available, the other because nothing could be."""
    questions = repo.persist_plan(version_id, PLAN)
    repo.mark_resolved(questions[0])
    repo.mark_unanswerable(questions[1], {"kinds": ["blocked"]})

    unresolved = repo.unresolved_for_version(version_id)

    assert {q.text for q in unresolved} == {"What is margin?", "Who is being hired?"}


def test_linking_evidence_twice_is_harmless(
    repo: QuestionRepository, db_session: Session, version_id: UUID
) -> None:
    """Extraction is idempotent because steps are re-run after a crash."""
    question = repo.persist_plan(version_id, PLAN)[0]
    evidence = add_evidence(db_session, version_id, add_source(db_session, version_id, "https://a.example"))

    repo.link_evidence(question.id, evidence.id)
    repo.link_evidence(question.id, evidence.id)

    assert repo.coverage(version_id)[question.id].distinct_sources == 1


# --------------------------------------------------------------------------
# Coverage counting
# --------------------------------------------------------------------------


def test_a_question_with_no_evidence_is_absent_from_coverage(
    repo: QuestionRepository, version_id: UUID
) -> None:
    repo.persist_plan(version_id, PLAN)

    assert repo.coverage(version_id) == {}


def test_two_sources_count_as_two(
    repo: QuestionRepository, db_session: Session, version_id: UUID
) -> None:
    question = repo.persist_plan(version_id, PLAN)[0]
    for url in ("https://a.example", "https://b.example"):
        source = add_source(db_session, version_id, url)
        repo.link_evidence(question.id, add_evidence(db_session, version_id, source).id)

    coverage = repo.coverage(version_id)[question.id]

    assert coverage.distinct_sources == 2
    assert coverage.above_lower_sources == 2


def test_the_same_source_twice_counts_once(
    repo: QuestionRepository, db_session: Session, version_id: UUID
) -> None:
    """Two extractions from one page are not two corroborating sources, and
    counting them as such is how a single blog post resolves a question."""
    question = repo.persist_plan(version_id, PLAN)[0]
    source = add_source(db_session, version_id, "https://a.example")
    for _ in range(2):
        repo.link_evidence(question.id, add_evidence(db_session, version_id, source).id)

    assert repo.coverage(version_id)[question.id].distinct_sources == 1


def test_an_unreadable_source_does_not_count(
    repo: QuestionRepository, db_session: Session, version_id: UUID
) -> None:
    """`REQ-EVID-018` forbids citing one, so it cannot corroborate either."""
    question = repo.persist_plan(version_id, PLAN)[0]
    blocked = add_source(
        db_session, version_id, "https://paywall.example", accessibility=Accessibility.PAYWALLED
    )
    repo.link_evidence(question.id, add_evidence(db_session, version_id, blocked).id)

    assert repo.coverage(version_id) == {}


def test_tiers_are_counted_separately(
    repo: QuestionRepository, db_session: Session, version_id: UUID
) -> None:
    question = repo.persist_plan(version_id, PLAN)[0]
    for url, tier in (
        ("https://filing.example", AuthorityTier.PRIMARY),
        ("https://press.example", AuthorityTier.SECONDARY),
        ("https://forum.example", AuthorityTier.LOWER),
    ):
        source = add_source(db_session, version_id, url, tier=tier)
        repo.link_evidence(question.id, add_evidence(db_session, version_id, source).id)

    coverage = repo.coverage(version_id)[question.id]

    assert coverage.distinct_sources == 3
    assert coverage.above_lower_sources == 2
    assert coverage.primary_sources == 1


def test_evidence_for_one_question_does_not_cover_another(
    repo: QuestionRepository, db_session: Session, version_id: UUID
) -> None:
    questions = repo.persist_plan(version_id, PLAN)
    source = add_source(db_session, version_id, "https://a.example")
    repo.link_evidence(questions[0].id, add_evidence(db_session, version_id, source).id)

    coverage = repo.coverage(version_id)

    assert questions[0].id in coverage
    assert questions[1].id not in coverage


def test_one_fact_can_answer_two_questions(
    repo: QuestionRepository, db_session: Session, version_id: UUID
) -> None:
    """A revenue figure speaks to both the revenue question and the valuation
    one, which is why this is a relation rather than a column."""
    questions = repo.persist_plan(version_id, PLAN)
    source = add_source(db_session, version_id, "https://a.example")
    evidence = add_evidence(db_session, version_id, source)
    repo.link_evidence(questions[0].id, evidence.id)
    repo.link_evidence(questions[1].id, evidence.id)

    coverage = repo.coverage(version_id)

    assert coverage[questions[0].id].distinct_sources == 1
    assert coverage[questions[1].id].distinct_sources == 1


def test_sources_without_urls_are_distinguished_by_identifier(
    repo: QuestionRepository, db_session: Session, version_id: UUID
) -> None:
    """A filing has an accession number, not a URL, and two different filings
    must still count as two sources."""
    question = repo.persist_plan(version_id, PLAN)[0]
    for accession in ("0000320193-25-000106", "0000320193-24-000123"):
        source = add_source(
            db_session, version_id, url=None, identifier=accession, tier=AuthorityTier.PRIMARY
        )
        repo.link_evidence(question.id, add_evidence(db_session, version_id, source).id)

    assert repo.coverage(version_id)[question.id].distinct_sources == 2
