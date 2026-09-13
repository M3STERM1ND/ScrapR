"""Password hashing for accounts (`DEC-16`, `REQ-AUTH-009 AC-1`).

**Argon2id, through `argon2-cffi`, with the library's own defaults.** Those
defaults track the RFC 9106 recommendation and move when it does, so choosing
them here is choosing to follow the people whose job this is rather than
freezing a set of numbers that were right in one year.

**The stored value encodes its own parameters.** When the defaults change, a
row hashed under the old ones still verifies, and `needs_rehash` says so; the
caller rewrites it on the next successful sign-in. No migration ever has to
touch a password column.

**A verification against nothing costs the same as one against something.**
`verify_password(None, ...)` checks the password against a fixed decoy hash, so
"no such account" and "wrong password" take the same time. Returning early for
an unknown email would tell anyone with a stopwatch which emails have accounts.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Final, final

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

__all__ = [
    "PASSWORD_MAX_LENGTH",
    "PASSWORD_MIN_LENGTH",
    "PasswordCheck",
    "hash_password",
    "password_problem",
    "verify_password",
]

PASSWORD_MIN_LENGTH: Final = 12
"""NIST SP 800-63B puts the floor at 8 for a user-chosen secret. Twelve,
because this one guards research a user chose to keep, and length is the only
rule that measurably helps."""

PASSWORD_MAX_LENGTH: Final = 256
"""Bounded so a request cannot hand the hasher a megabyte to chew on. Long
enough for any passphrase or password manager output."""

_HASHER: Final = PasswordHasher()


@final
@dataclass(frozen=True, slots=True)
class PasswordCheck:
    """The outcome of one verification."""

    valid: bool
    needs_rehash: bool = False
    """True when the stored hash was made with parameters weaker than the
    current defaults. Only meaningful when `valid`."""


@lru_cache(maxsize=1)
def _decoy_hash() -> str:
    """A real hash of nothing anyone knows, built once per process.

    Built lazily rather than at import: hashing is deliberately slow, and
    importing this module must not cost a quarter of a second.
    """
    return _HASHER.hash("scrapr-decoy-password-never-issued")


def password_problem(password: str) -> str | None:
    """Why a password is unacceptable, in words a person can act on, or `None`.

    Length only. Composition rules ("one symbol, one digit") push people toward
    predictable substitutions and are no longer recommended by NIST.
    """
    if len(password) < PASSWORD_MIN_LENGTH:
        return f"Use at least {PASSWORD_MIN_LENGTH} characters."
    if len(password) > PASSWORD_MAX_LENGTH:
        return f"Use no more than {PASSWORD_MAX_LENGTH} characters."
    return None


def hash_password(password: str) -> str:
    """Hash a password for storage. Refuses one that fails `password_problem`."""
    problem = password_problem(password)
    if problem is not None:
        raise ValueError(problem)
    return _HASHER.hash(password)


def verify_password(stored: str | None, password: str) -> PasswordCheck:
    """Whether `password` matches `stored`.

    `stored` may be `None` — an unknown account, or one with no password — and
    the call still performs a full verification against a decoy, so the two
    cases cannot be told apart by timing.
    """
    if len(password) > PASSWORD_MAX_LENGTH:
        # Refused before hashing, so an oversized input cannot be used to make
        # the server do arbitrary work. Length is not secret.
        return PasswordCheck(valid=False)

    target = stored if stored else _decoy_hash()
    try:
        _HASHER.verify(target, password)
    except (VerificationError, InvalidHashError):
        return PasswordCheck(valid=False)

    if not stored:
        # The decoy matched, which means someone guessed the decoy password.
        # It still is not an account.
        return PasswordCheck(valid=False)

    return PasswordCheck(valid=True, needs_rehash=_HASHER.check_needs_rehash(stored))
