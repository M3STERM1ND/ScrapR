"""Accounts, their sessions, anonymous expiry, claiming and rate limits.

`DEC-16`, `DEC-17` and `REQ-AUTH-003..004`, `REQ-AUTH-009` against a real
database, inside the rolled-back session every core test uses.
"""

from __future__ import annotations

import datetime as dt
from uuid import UUID

import pytest
from argon2 import PasswordHasher
from sqlalchemy import select
from sqlalchemy.orm import Session

from scrapr_core.db.base import utcnow
from scrapr_core.db.models import AnonymousSession, ResearchSession, User, UserSession
from scrapr_core.db.repositories import (
    AccountRepository,
    AnonymousSessionRepository,
    EmailTakenError,
    ResearchRepository,
)
from scrapr_core.db.repositories.accounts import ACCOUNT_SESSION_LIFETIME, normalize_email
from scrapr_core.db.repositories.anonymous_sessions import (
    ANONYMOUS_LIFETIME,
    TOUCH_INTERVAL,
    is_valid,
)
from scrapr_core.db.repositories.claiming import (
    ClaimRefusedError,
    claim_anonymous_research,
)
from scrapr_core.db.repositories.rate_limits import RateLimit, RateLimiter
from scrapr_core.domain.ownership import OwnerContext

pytestmark = pytest.mark.integration

PASSWORD = "a long enough password"


@pytest.fixture
def accounts(db_session: Session) -> AccountRepository:
    return AccountRepository(db_session)


# --------------------------------------------------------------------------
# Accounts
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Reader@Example.COM ", "reader@example.com"),
        ("first+tag@sub.example.org", "first+tag@sub.example.org"),
        ("no-at-sign.example.com", None),
        ("two@@example.com", None),
        ("", None),
    ],
)
def test_emails_are_normalised_or_refused(raw: str, expected: str | None) -> None:
    assert normalize_email(raw) == expected


def test_an_account_stores_a_hash_and_never_the_password(
    accounts: AccountRepository,
) -> None:
    """`REQ-AUTH-009 AC-1`, `REQ-DATA-001`."""
    user = accounts.create("reader@example.com", PASSWORD)

    assert user.password_hash is not None
    assert PASSWORD not in user.password_hash
    assert user.preferences == {}
    assert user.usage_meta == {}


def test_a_second_account_on_one_email_is_refused(
    accounts: AccountRepository, db_session: Session
) -> None:
    """Case-insensitively, and without breaking the caller's transaction."""
    accounts.create("reader@example.com", PASSWORD)

    with pytest.raises(EmailTakenError):
        accounts.create("READER@example.com", PASSWORD)

    # The savepoint kept the collision from poisoning the session.
    assert db_session.execute(select(User)).scalars().all()


def test_the_right_credentials_open_the_account(accounts: AccountRepository) -> None:
    created = accounts.create("reader@example.com", PASSWORD)

    found = accounts.authenticate(" Reader@Example.com", PASSWORD)

    assert found is not None
    assert found.id == created.id


@pytest.mark.parametrize(
    ("email", "password"),
    [
        ("reader@example.com", "the wrong password"),
        ("nobody@example.com", PASSWORD),
        ("not an email", PASSWORD),
    ],
)
def test_wrong_credentials_open_nothing(
    accounts: AccountRepository, email: str, password: str
) -> None:
    accounts.create("reader@example.com", PASSWORD)

    assert accounts.authenticate(email, password) is None


def test_a_weakly_hashed_password_is_upgraded_on_sign_in(
    accounts: AccountRepository,
) -> None:
    user = accounts.create("reader@example.com", PASSWORD)
    user.password_hash = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash(
        PASSWORD
    )
    weak = user.password_hash

    assert accounts.authenticate("reader@example.com", PASSWORD) is not None
    assert user.password_hash != weak
    assert accounts.authenticate("reader@example.com", PASSWORD) is not None


def test_a_deleted_account_cannot_sign_in(accounts: AccountRepository) -> None:
    user = accounts.create("reader@example.com", PASSWORD)
    user.deleted_at = utcnow()

    assert accounts.authenticate("reader@example.com", PASSWORD) is None


# --------------------------------------------------------------------------
# Account sessions
# --------------------------------------------------------------------------


def test_a_session_token_resolves_and_is_stored_hashed(
    accounts: AccountRepository,
) -> None:
    user = accounts.create("reader@example.com", PASSWORD)
    issued = accounts.issue_session(user)

    assert issued.token not in issued.session.token_hash
    resolved = accounts.resolve_session(issued.token)
    assert resolved is not None and resolved.id == user.id


def test_every_sign_in_gets_a_new_token(accounts: AccountRepository) -> None:
    user = accounts.create("reader@example.com", PASSWORD)

    assert accounts.issue_session(user).token != accounts.issue_session(user).token


def test_expiry_is_absolute(accounts: AccountRepository) -> None:
    """`DEC-16`: activity never extends an account session."""
    user = accounts.create("reader@example.com", PASSWORD)
    start = utcnow()
    issued = accounts.issue_session(user, now=start)

    halfway = start + ACCOUNT_SESSION_LIFETIME / 2
    assert accounts.resolve_session(issued.token, now=halfway) is not None
    assert issued.session.expires_at == start + ACCOUNT_SESSION_LIFETIME

    after = start + ACCOUNT_SESSION_LIFETIME + dt.timedelta(seconds=1)
    assert accounts.resolve_session(issued.token, now=after) is None


def test_a_revoked_session_stops_working(accounts: AccountRepository) -> None:
    user = accounts.create("reader@example.com", PASSWORD)
    issued = accounts.issue_session(user)

    accounts.revoke_session(issued.token)
    accounts.revoke_session(issued.token)  # idempotent

    assert accounts.resolve_session(issued.token) is None
    assert issued.session.revoked_at is not None


def test_an_unknown_token_resolves_to_nobody(accounts: AccountRepository) -> None:
    assert accounts.resolve_session("not-a-token") is None
    accounts.revoke_session("not-a-token")


# --------------------------------------------------------------------------
# Anonymous session lifetime — `DEC-17`
# --------------------------------------------------------------------------


def test_an_anonymous_session_expires_after_its_lifetime(db_session: Session) -> None:
    repository = AnonymousSessionRepository(db_session)
    start = utcnow()
    issued = repository.issue(now=start)

    assert issued.session.expires_at == start + ANONYMOUS_LIFETIME
    assert repository.owner_context(issued.token, now=start) is not None
    assert (
        repository.owner_context(
            issued.token, now=start + ANONYMOUS_LIFETIME + dt.timedelta(seconds=1)
        )
        is None
    )


def test_activity_slides_the_expiry(db_session: Session) -> None:
    repository = AnonymousSessionRepository(db_session)
    start = utcnow()
    issued = repository.issue(now=start)

    # Within the touch interval nothing is written.
    assert not repository.touch(issued.session, now=start + TOUCH_INTERVAL / 2)

    later = start + dt.timedelta(days=20)
    assert repository.touch(issued.session, now=later)
    assert issued.session.expires_at == later + ANONYMOUS_LIFETIME
    assert repository.owner_context(issued.token, now=start + dt.timedelta(days=45)) is not None


def test_a_session_written_before_expiry_existed_reads_by_last_activity(
    db_session: Session,
) -> None:
    repository = AnonymousSessionRepository(db_session)
    issued = repository.issue()
    issued.session.expires_at = None

    assert is_valid(issued.session)
    assert not is_valid(
        issued.session,
        now=issued.session.last_seen_at + ANONYMOUS_LIFETIME + dt.timedelta(seconds=1),
    )


# --------------------------------------------------------------------------
# Claiming — `REQ-AUTH-004`
# --------------------------------------------------------------------------


def _anonymous_research(db_session: Session, objective: str) -> tuple[AnonymousSession, UUID]:
    anonymous = AnonymousSessionRepository(db_session).issue().session
    research = ResearchRepository(db_session, OwnerContext.for_anonymous(anonymous.id))
    created = research.create_session(objective=objective)
    assert research.open_version(created.id) is not None
    return anonymous, created.id


def test_claiming_moves_every_session_of_the_anonymous_owner(
    db_session: Session, accounts: AccountRepository
) -> None:
    """`AC-1`: after sign-up the research is in the account's history."""
    user = accounts.create("reader@example.com", PASSWORD)
    anonymous, first = _anonymous_research(db_session, "First question about Acme")
    second = ResearchRepository(
        db_session, OwnerContext.for_anonymous(anonymous.id)
    ).create_session(objective="Second question about Acme")

    outcome = claim_anonymous_research(
        db_session, anonymous_session_id=anonymous.id, user_id=user.id
    )

    assert outcome.research_sessions == 2
    history = ResearchRepository(db_session, OwnerContext.for_user(user.id))
    assert {found.id for found in history.list_sessions()} == {first, second.id}
    # `REQ-AUTH-006 AC-1`: the version came with it.
    assert history.latest_version(first) is not None

    # The anonymous owner no longer reaches anything, and cannot own anew.
    assert ResearchRepository(
        db_session, OwnerContext.for_anonymous(anonymous.id)
    ).list_sessions() == []
    assert not is_valid(anonymous)


def test_claiming_cannot_reach_anyone_elses_research(
    db_session: Session, accounts: AccountRepository
) -> None:
    """`AC-2`: another anonymous session's and another account's research stay put."""
    user = accounts.create("reader@example.com", PASSWORD)
    other_user = accounts.create("someone@example.com", PASSWORD)

    mine, _ = _anonymous_research(db_session, "Research to be claimed here")
    _, theirs = _anonymous_research(db_session, "Research in another browser")
    owned_elsewhere = ResearchRepository(
        db_session, OwnerContext.for_user(other_user.id)
    ).create_session(objective="Research owned by another account")

    claim_anonymous_research(db_session, anonymous_session_id=mine.id, user_id=user.id)

    theirs_row = db_session.get(ResearchSession, theirs)
    other_row = db_session.get(ResearchSession, owned_elsewhere.id)
    assert theirs_row is not None and theirs_row.owner_user_id is None
    assert other_row is not None and other_row.owner_user_id == other_user.id


def test_a_claimed_session_cannot_be_claimed_again(
    db_session: Session, accounts: AccountRepository
) -> None:
    user = accounts.create("reader@example.com", PASSWORD)
    rival = accounts.create("rival@example.com", PASSWORD)
    anonymous, research_id = _anonymous_research(db_session, "Research claimed once only")

    claim_anonymous_research(db_session, anonymous_session_id=anonymous.id, user_id=user.id)

    with pytest.raises(ClaimRefusedError):
        claim_anonymous_research(
            db_session, anonymous_session_id=anonymous.id, user_id=rival.id
        )
    row = db_session.get(ResearchSession, research_id)
    assert row is not None and row.owner_user_id == user.id


def test_an_expired_session_cannot_be_claimed(
    db_session: Session, accounts: AccountRepository
) -> None:
    """`AC-3`, `DEC-17`: the eligibility window is the session's lifetime."""
    user = accounts.create("reader@example.com", PASSWORD)
    anonymous, research_id = _anonymous_research(db_session, "Research that lapsed first")

    with pytest.raises(ClaimRefusedError):
        claim_anonymous_research(
            db_session,
            anonymous_session_id=anonymous.id,
            user_id=user.id,
            now=utcnow() + ANONYMOUS_LIFETIME + dt.timedelta(minutes=1),
        )
    row = db_session.get(ResearchSession, research_id)
    assert row is not None and row.anonymous_session_id == anonymous.id


# --------------------------------------------------------------------------
# Rate limits — `REQ-AUTH-009 AC-3`
# --------------------------------------------------------------------------


RULE = RateLimit(scope="test", limit=3, window_seconds=60)


def test_actions_are_allowed_up_to_the_limit_and_refused_past_it(
    db_session: Session,
) -> None:
    limiter = RateLimiter(db_session)
    moment = dt.datetime(2026, 9, 13, 12, 0, 10, tzinfo=dt.UTC)

    decisions = [limiter.hit(RULE, "198.51.100.7", now=moment) for _ in range(4)]

    assert [decision.allowed for decision in decisions] == [True, True, True, False]
    assert decisions[-1].retry_after_seconds == 50


def test_subjects_and_windows_are_counted_separately(db_session: Session) -> None:
    limiter = RateLimiter(db_session)
    moment = dt.datetime(2026, 9, 13, 12, 0, 10, tzinfo=dt.UTC)
    for _ in range(3):
        limiter.hit(RULE, "one", now=moment)

    assert limiter.hit(RULE, "two", now=moment).allowed
    assert not limiter.hit(RULE, "one", now=moment).allowed
    assert limiter.hit(RULE, "one", now=moment + dt.timedelta(minutes=1)).allowed


def test_subjects_are_stored_as_digests(db_session: Session) -> None:
    from scrapr_core.db.models import RateLimitCounter

    RateLimiter(db_session).hit(RULE, "reader@example.com")

    keys = db_session.execute(select(RateLimitCounter.key)).scalars().all()
    assert keys and all("reader@example.com" not in key for key in keys)


def test_old_windows_are_purged(db_session: Session) -> None:
    limiter = RateLimiter(db_session)
    old = utcnow() - dt.timedelta(days=2)
    limiter.hit(RULE, "one", now=old)
    limiter.hit(RULE, "one")

    assert limiter.purge_before(utcnow() - dt.timedelta(days=1)) == 1


def test_account_sessions_cascade_with_their_user(
    db_session: Session, accounts: AccountRepository
) -> None:
    user = accounts.create("reader@example.com", PASSWORD)
    accounts.issue_session(user)
    db_session.delete(user)
    db_session.flush()

    assert db_session.execute(select(UserSession)).scalars().all() == []
