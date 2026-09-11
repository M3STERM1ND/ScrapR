"""`OwnerContext` mirrors the `one_owner` check constraint, in the type system.

A context carrying both a user and an anonymous session, or neither, is
ambiguous ownership — and ambiguous ownership is how research leaks between
accounts. It cannot be constructed.
"""

from __future__ import annotations

import pytest

from scrapr_core.domain.ids import new_id
from scrapr_core.domain.ownership import OwnerContext


def test_a_user_context_carries_only_a_user() -> None:
    user_id = new_id()

    owner = OwnerContext.for_user(user_id)

    assert owner.user_id == user_id
    assert owner.anonymous_session_id is None
    assert not owner.is_anonymous


def test_an_anonymous_context_carries_only_a_session() -> None:
    session_id = new_id()

    owner = OwnerContext.for_anonymous(session_id)

    assert owner.anonymous_session_id == session_id
    assert owner.user_id is None
    assert owner.is_anonymous


def test_no_owner_is_rejected() -> None:
    with pytest.raises(ValueError, match="exactly one owner"):
        OwnerContext()


def test_two_owners_are_rejected() -> None:
    with pytest.raises(ValueError, match="exactly one owner"):
        OwnerContext(user_id=new_id(), anonymous_session_id=new_id())


def test_the_context_is_immutable() -> None:
    """An owner that can be reassigned mid-request is an authorization bug
    waiting for a careless line of code."""
    owner = OwnerContext.for_anonymous(new_id())

    with pytest.raises(AttributeError):
        owner.user_id = new_id()  # type: ignore[misc]


def test_repr_does_not_disclose_the_identifier() -> None:
    """For an anonymous session the id *is* the access-control key, so it does
    not belong in a log line."""
    session_id = new_id()

    owner = OwnerContext.for_anonymous(session_id)

    assert str(session_id) not in repr(owner)
    assert str(session_id) not in str(owner)
    assert repr(owner) == "OwnerContext(anonymous)"
