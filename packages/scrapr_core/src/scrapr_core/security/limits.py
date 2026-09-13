"""The rate limits, in one table (`REQ-AUTH-009 AC-3`, `REQ-SEC-010`).

Kept together so the numbers can be read side by side and changed in one
review. Each limit counts one kind of action by one kind of subject; a request
may be counted against several — sign-in counts once by address and once by the
email being tried, because an attacker spraying one password across many
accounts and one guessing many passwords for one account are different shapes,
and each limit only catches one of them.
"""

from __future__ import annotations

from typing import Final

from scrapr_core.db.repositories.rate_limits import RateLimit

__all__ = [
    "SIGNIN_PER_ADDRESS",
    "SIGNIN_PER_EMAIL",
    "SIGNUP_PER_ADDRESS",
]

SIGNIN_PER_ADDRESS: Final = RateLimit(scope="signin-address", limit=20, window_seconds=900)
"""Twenty attempts in fifteen minutes from one address. A household or office
behind one address still has room to mistype."""

SIGNIN_PER_EMAIL: Final = RateLimit(scope="signin-email", limit=10, window_seconds=900)
"""Ten attempts at one account in fifteen minutes, from anywhere. Low enough
that online guessing of a twelve-character password is hopeless."""

SIGNUP_PER_ADDRESS: Final = RateLimit(scope="signup-address", limit=10, window_seconds=3600)
"""Ten new accounts an hour from one address. Accounts are free to make and each
one is a fresh set of research allowances, so this is abuse control first."""
