"""The invariants the database refuses to break.

Implementation plan section 1.2 names three invariants the architecture exists
to protect. Two of them are enforced here, in the schema, rather than by code
that has to remember:

* **Exactly one owner.** `research_sessions` cannot exist unowned, which is what
  makes the anonymous identity half of `OPEN-17` a Phase 0 concern.
* **A forecast states its assumptions** (`REQ-SYNTH-009`).

Plus the version-scoping decision from section 4.1: deduplication is *within* a
version, so the same URL retrieved for a later version is a new record with a
new retrieval timestamp (`REQ-VER-003 AC-2`) rather than an overwrite.

These are integration tests because a check constraint that exists only in
`Base.metadata` protects nothing.
"""

from __future__ import annotations

import datetime as dt
from uuid import UUID

import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError, StatementError
from sqlalchemy.orm import Session

from scrapr_core.db.enums import (
    Accessibility,
    AuthorityTier,
    ClaimType,
    ResearchStatus,
    SourceCategory,
    VersionStatus,
)
from scrapr_core.db.models import (
    AnonymousSession,
    Claim,
    ResearchSession,
    ResearchVersion,
    Source,
    User,
)
from scrapr_core.domain.ids import new_id

pytestmark = pytest.mark.integration


def make_anonymous_session(db_session: Session) -> AnonymousSession:
    owner = AnonymousSession(token_hash=f"hash-{new_id()}")
    db_session.add(owner)
    db_session.flush()
    return owner


def make_research_session(db_session: Session) -> ResearchSession:
    session = ResearchSession(
        anonymous_session_id=make_anonymous_session(db_session).id,
        objective="How is Acme Corp positioned against its competitors?",
        status=ResearchStatus.PENDING,
    )
    db_session.add(session)
    db_session.flush()
    return session


def make_version(
    db_session: Session, session_id: UUID, version_number: int = 1
) -> ResearchVersion:
    version = ResearchVersion(
        session_id=session_id,
        version_number=version_number,
        status=VersionStatus.BUILDING,
    )
    db_session.add(version)
    db_session.flush()
    return version


def make_source(db_session: Session, version_id: UUID, url: str) -> Source:
    source = Source(
        version_id=version_id,
        url=url,
        url_normalized=url,
        name="Acme Corp Q3 results",
        category=SourceCategory.FILING,
        authority_tier=AuthorityTier.PRIMARY,
        retrieved_at=dt.datetime.now(dt.UTC),
        accessibility=Accessibility.ACCESSIBLE,
    )
    db_session.add(source)
    db_session.flush()
    return source


# --------------------------------------------------------------------------
# Ownership
# --------------------------------------------------------------------------


def test_research_session_accepts_a_single_anonymous_owner(db_session: Session) -> None:
    session = make_research_session(db_session)

    assert session.anonymous_session_id is not None
    assert session.owner_user_id is None


def test_research_session_rejects_no_owner(db_session: Session) -> None:
    db_session.add(ResearchSession(objective="ownerless", status=ResearchStatus.PENDING))

    with pytest.raises(IntegrityError, match="one_owner"):
        db_session.flush()


def test_research_session_rejects_two_owners(db_session: Session) -> None:
    """An account *and* an anonymous session is ambiguous ownership, and
    ambiguous ownership is how research leaks between accounts."""
    user = User(email=f"{new_id()}@example.com")
    db_session.add(user)
    owner = make_anonymous_session(db_session)
    db_session.flush()

    db_session.add(
        ResearchSession(
            owner_user_id=user.id,
            anonymous_session_id=owner.id,
            objective="two owners",
            status=ResearchStatus.PENDING,
        )
    )

    with pytest.raises(IntegrityError, match="one_owner"):
        db_session.flush()


# --------------------------------------------------------------------------
# Claim typing
# --------------------------------------------------------------------------


def test_forecast_without_assumptions_is_rejected(db_session: Session) -> None:
    version = make_version(db_session, make_research_session(db_session).id)
    db_session.add(
        Claim(
            version_id=version.id,
            text="Revenue will grow 20% next year.",
            claim_type=ClaimType.FORECAST,
        )
    )

    with pytest.raises(IntegrityError, match="forecast_needs_assumptions"):
        db_session.flush()


def test_forecast_with_assumptions_is_accepted(db_session: Session) -> None:
    version = make_version(db_session, make_research_session(db_session).id)
    claim = Claim(
        version_id=version.id,
        text="Revenue will grow 20% next year.",
        claim_type=ClaimType.FORECAST,
        assumptions={"basis": "three consecutive quarters of 18-22% growth"},
    )

    db_session.add(claim)
    db_session.flush()

    assert claim.assumptions == {"basis": "three consecutive quarters of 18-22% growth"}


def test_non_forecast_claims_need_no_assumptions(db_session: Session) -> None:
    """The constraint is scoped to forecasts. A fact carries evidence, not
    assumptions, and the validation gate is what checks that (`REQ-EVID-017`)."""
    version = make_version(db_session, make_research_session(db_session).id)

    db_session.add(
        Claim(
            version_id=version.id,
            text="Acme reported $1.2bn revenue in FY2025.",
            claim_type=ClaimType.FACT,
        )
    )
    db_session.flush()


def test_claim_type_rejects_a_value_outside_the_taxonomy(db_session: Session) -> None:
    """A native enum, so an unrecognised claim type cannot reach the validation
    gate and be waved through as "not a fact"."""
    version = make_version(db_session, make_research_session(db_session).id)
    db_session.add(
        Claim(
            version_id=version.id,
            text="Acme will probably do well.",
            claim_type="speculation",  # type: ignore[arg-type]
        )
    )

    with pytest.raises((StatementError, DBAPIError)):
        db_session.flush()


# --------------------------------------------------------------------------
# Version scoping
# --------------------------------------------------------------------------


def test_version_numbers_are_unique_within_a_session(db_session: Session) -> None:
    session = make_research_session(db_session)
    make_version(db_session, session.id, version_number=1)

    with pytest.raises(IntegrityError, match="uq_research_versions_session_id"):
        make_version(db_session, session.id, version_number=1)


def test_a_source_is_deduplicated_within_one_version(db_session: Session) -> None:
    """`REQ-EVID-006`: the same URL twice in one version is one source."""
    version = make_version(db_session, make_research_session(db_session).id)
    make_source(db_session, version.id, "https://acme.example/ir/q3")

    with pytest.raises(IntegrityError, match="uq_sources_version_id_url_normalized"):
        make_source(db_session, version.id, "https://acme.example/ir/q3")


def test_the_same_url_is_a_new_record_in_a_new_version(db_session: Session) -> None:
    """The whole point of version scoping.

    `REQ-VER-002 AC-2` requires the earlier version's retrieval timestamp to
    survive an update untouched, so a re-fetch is a different row rather than a
    mutation of the old one.
    """
    session = make_research_session(db_session)
    first = make_version(db_session, session.id, version_number=1)
    second = make_version(db_session, session.id, version_number=2)
    url = "https://acme.example/ir/q3"

    original = make_source(db_session, first.id, url)
    refetched = make_source(db_session, second.id, url)

    assert original.id != refetched.id
    assert original.retrieved_at <= refetched.retrieved_at


# --------------------------------------------------------------------------
# Round-tripping
# --------------------------------------------------------------------------


def test_identifiers_round_trip_as_uuid_version_7(db_session: Session) -> None:
    session = make_research_session(db_session)
    db_session.expire_all()

    loaded = db_session.get(ResearchSession, session.id)

    assert loaded is not None
    assert isinstance(loaded.id, UUID)
    assert loaded.id.version == 7


def test_timestamps_round_trip_with_their_timezone(db_session: Session) -> None:
    """A naive value coming back would mean the column is not `timestamptz`, and
    every cross-source recency comparison would be quietly wrong."""
    version = make_version(db_session, make_research_session(db_session).id)
    source = make_source(db_session, version.id, "https://acme.example/ir/q3")
    db_session.expire_all()

    loaded = db_session.get(Source, source.id)

    assert loaded is not None
    assert loaded.retrieved_at.tzinfo is not None
