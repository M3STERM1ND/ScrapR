"""Ownership isolation, tested against a real database.

This is the suite `REQ-SEC-002 AC-3` asks for. It is meaningful precisely
because the filter lives in one place: these tests exercise the implementation
every route and every job handler shares, rather than spot-checking handlers.

The rule under test throughout: **research owned by someone else is
indistinguishable from research that does not exist.**
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from scrapr_core.db.enums import VersionStatus
from scrapr_core.db.models import User
from scrapr_core.db.repositories import AnonymousSessionRepository, ResearchRepository
from scrapr_core.domain.ids import new_id
from scrapr_core.domain.ownership import OwnerContext

pytestmark = pytest.mark.integration


@pytest.fixture
def owner(db_session: Session) -> OwnerContext:
    issued = AnonymousSessionRepository(db_session).issue()
    return OwnerContext.for_anonymous(issued.session.id)


@pytest.fixture
def other_owner(db_session: Session) -> OwnerContext:
    issued = AnonymousSessionRepository(db_session).issue()
    return OwnerContext.for_anonymous(issued.session.id)


@pytest.fixture
def repo(db_session: Session, owner: OwnerContext) -> ResearchRepository:
    return ResearchRepository(db_session, owner)


@pytest.fixture
def other_repo(db_session: Session, other_owner: OwnerContext) -> ResearchRepository:
    return ResearchRepository(db_session, other_owner)


# --------------------------------------------------------------------------
# Anonymous sessions
# --------------------------------------------------------------------------


def test_issued_token_resolves_to_its_session(db_session: Session) -> None:
    repository = AnonymousSessionRepository(db_session)

    issued = repository.issue()
    resolved = repository.resolve(issued.token)

    assert resolved is not None
    assert resolved.id == issued.session.id


def test_the_raw_token_is_never_stored(db_session: Session) -> None:
    """Disclosure of the database must not hand over the ability to impersonate
    a session."""
    repository = AnonymousSessionRepository(db_session)

    issued = repository.issue()

    assert issued.session.token_hash != issued.token
    assert issued.token not in issued.session.token_hash


def test_an_unknown_token_resolves_to_nothing(db_session: Session) -> None:
    """An absent or stale cookie is the ordinary case for a first-time visitor,
    not an error."""
    repository = AnonymousSessionRepository(db_session)

    assert repository.resolve("not-a-real-token") is None
    assert repository.owner_context("not-a-real-token") is None


def test_two_sessions_get_different_tokens(db_session: Session) -> None:
    repository = AnonymousSessionRepository(db_session)

    first = repository.issue()
    second = repository.issue()

    assert first.token != second.token
    assert first.session.id != second.session.id


def test_touch_advances_last_seen(db_session: Session) -> None:
    repository = AnonymousSessionRepository(db_session)
    issued = repository.issue()
    before = issued.session.last_seen_at

    repository.touch(issued.session)

    assert issued.session.last_seen_at >= before


# --------------------------------------------------------------------------
# Ownership isolation
# --------------------------------------------------------------------------


def test_a_session_is_readable_by_its_owner(repo: ResearchRepository) -> None:
    created = repo.create_session(objective="Acme competitive position")

    assert repo.get_session(created.id) is not None


def test_a_session_is_invisible_to_another_owner(
    repo: ResearchRepository, other_repo: ResearchRepository
) -> None:
    """Not forbidden — absent. An identifier that cannot be probed for
    existence is `REQ-SEC-009` working."""
    created = repo.create_session(objective="Acme competitive position")

    assert other_repo.get_session(created.id) is None


def test_history_lists_only_the_owner_s_research(
    repo: ResearchRepository, other_repo: ResearchRepository
) -> None:
    mine = repo.create_session(objective="mine")
    theirs = other_repo.create_session(objective="theirs")

    listed = {session.id for session in repo.list_sessions()}

    assert mine.id in listed
    assert theirs.id not in listed


def test_an_unknown_session_id_reads_as_absent(repo: ResearchRepository) -> None:
    assert repo.get_session(new_id()) is None


def test_ownership_comes_from_the_context_not_the_caller(
    repo: ResearchRepository, owner: OwnerContext
) -> None:
    """There is no call shape that creates research for somebody else."""
    created = repo.create_session(objective="Acme competitive position")

    assert created.anonymous_session_id == owner.anonymous_session_id
    assert created.owner_user_id is None


def test_account_ownership_isolates_the_same_way(db_session: Session) -> None:
    """Phase 1 only ever has anonymous owners, but the account branch of the
    filter exists now and must behave identically — that is the whole point of
    building `OwnerContext` before accounts arrive (implementation plan §8)."""
    users = [User(email=f"{new_id()}@example.com") for _ in range(2)]
    db_session.add_all(users)
    db_session.flush()
    mine = ResearchRepository(db_session, OwnerContext.for_user(users[0].id))
    theirs = ResearchRepository(db_session, OwnerContext.for_user(users[1].id))

    created = mine.create_session(objective="Acme competitive position")

    assert created.owner_user_id == users[0].id
    assert mine.get_session(created.id) is not None
    assert theirs.get_session(created.id) is None


# --------------------------------------------------------------------------
# Versions
# --------------------------------------------------------------------------


def test_the_first_version_is_number_one(repo: ResearchRepository) -> None:
    session = repo.create_session(objective="Acme competitive position")

    version = repo.open_version(session.id)

    assert version is not None
    assert version.version_number == 1
    assert version.previous_version_id is None
    assert version.status is VersionStatus.BUILDING


def test_each_version_points_back_at_the_previous_one(repo: ResearchRepository) -> None:
    """`REQ-VER-006` compares a version against its predecessor, so the chain is
    part of the data rather than something reconstructed by ordering."""
    session = repo.create_session(objective="Acme competitive position")
    first = repo.open_version(session.id)
    assert first is not None

    second = repo.open_version(session.id)

    assert second is not None
    assert second.version_number == 2
    assert second.previous_version_id == first.id


def test_opening_a_version_moves_the_current_pointer(repo: ResearchRepository) -> None:
    session = repo.create_session(objective="Acme competitive position")

    version = repo.open_version(session.id)

    assert version is not None
    assert session.current_version_id == version.id


def test_another_owner_cannot_open_a_version(
    repo: ResearchRepository, other_repo: ResearchRepository
) -> None:
    session = repo.create_session(objective="Acme competitive position")

    assert other_repo.open_version(session.id) is None


def test_a_version_is_readable_by_its_owner(repo: ResearchRepository) -> None:
    session = repo.create_session(objective="Acme competitive position")
    opened = repo.open_version(session.id)
    assert opened is not None

    loaded = repo.get_version(session.id, 1)

    assert loaded is not None
    assert loaded.id == opened.id


def test_another_owner_cannot_read_a_version(
    repo: ResearchRepository, other_repo: ResearchRepository
) -> None:
    session = repo.create_session(objective="Acme competitive position")
    repo.open_version(session.id)

    assert other_repo.get_version(session.id, 1) is None
    assert other_repo.list_versions(session.id) == []


def test_versions_list_oldest_first(repo: ResearchRepository) -> None:
    session = repo.create_session(objective="Acme competitive position")
    repo.open_version(session.id)
    repo.open_version(session.id)

    numbers = [version.version_number for version in repo.list_versions(session.id)]

    assert numbers == [1, 2]


def test_closing_a_version_records_when(repo: ResearchRepository) -> None:
    session = repo.create_session(objective="Acme competitive position")
    version = repo.open_version(session.id)
    assert version is not None

    closed = repo.close_version(version, VersionStatus.COMPLETE)

    assert closed.status is VersionStatus.COMPLETE
    assert closed.closed_at is not None
