"""Password hashing (`DEC-16`, `REQ-AUTH-009 AC-1`).

Pure functions, no database. The properties worth pinning are the ones a
refactor could silently lose: the plaintext never survives into the stored
value, a missing account still costs a verification, and weaker parameters are
flagged for rehashing.
"""

from __future__ import annotations

import pytest
from argon2 import PasswordHasher

from scrapr_core.security.passwords import (
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    hash_password,
    password_problem,
    verify_password,
)

GOOD = "correct horse battery staple"


def test_the_stored_value_is_not_the_password() -> None:
    stored = hash_password(GOOD)

    assert GOOD not in stored
    assert stored.startswith("$argon2id$")


def test_the_right_password_verifies() -> None:
    check = verify_password(hash_password(GOOD), GOOD)

    assert check.valid
    assert not check.needs_rehash


def test_a_wrong_password_does_not() -> None:
    assert not verify_password(hash_password(GOOD), GOOD + "!").valid


def test_no_stored_hash_never_verifies() -> None:
    """An unknown account runs a real verification against a decoy and fails."""
    assert not verify_password(None, GOOD).valid
    assert not verify_password("", GOOD).valid


def test_a_corrupt_hash_is_a_failure_not_a_crash() -> None:
    assert not verify_password("not-an-argon2-hash", GOOD).valid


def test_weaker_parameters_are_flagged_for_rehash() -> None:
    weak = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash(GOOD)

    check = verify_password(weak, GOOD)

    assert check.valid
    assert check.needs_rehash


@pytest.mark.parametrize(
    ("password", "acceptable"),
    [
        ("x" * (PASSWORD_MIN_LENGTH - 1), False),
        ("x" * PASSWORD_MIN_LENGTH, True),
        ("x" * PASSWORD_MAX_LENGTH, True),
        ("x" * (PASSWORD_MAX_LENGTH + 1), False),
    ],
)
def test_length_is_the_only_rule(password: str, acceptable: bool) -> None:
    assert (password_problem(password) is None) is acceptable


def test_hashing_refuses_an_unacceptable_password() -> None:
    with pytest.raises(ValueError, match="at least"):
        hash_password("short")


def test_an_oversized_candidate_is_refused_before_hashing() -> None:
    assert not verify_password(hash_password(GOOD), "x" * (PASSWORD_MAX_LENGTH + 1)).valid
