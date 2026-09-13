"""The rate limits, in one table (`REQ-AUTH-009 AC-3`, `REQ-SEC-010`, `DEC-23`).

Kept together so the numbers can be read side by side and changed in one
review. Each action is counted against up to three subjects — the browser's
anonymous session, the account, and the client address — and a request must be
within every limit that applies to it. Clearing cookies buys at most the address
limit; a shared office address still gives each person their own session
allowance up to the address ceiling (`DEC-23`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, final

from scrapr_core.db.repositories.rate_limits import RateLimit

__all__ = [
    "EXPORT",
    "QUESTION",
    "RESEARCH",
    "SIGNIN_PER_ADDRESS",
    "SIGNIN_PER_EMAIL",
    "SIGNUP_PER_ADDRESS",
    "UPLOAD",
    "Action",
]

HOUR: Final = 3600
DAY: Final = 86_400


@final
@dataclass(frozen=True, slots=True)
class Action:
    """The limits one kind of request is held to, by subject."""

    name: str
    anonymous: tuple[RateLimit, ...]
    account: tuple[RateLimit, ...]
    address: tuple[RateLimit, ...]


SIGNIN_PER_ADDRESS: Final = RateLimit(scope="signin-address", limit=20, window_seconds=900)
"""Twenty attempts in fifteen minutes from one address. A household or office
behind one address still has room to mistype."""

SIGNIN_PER_EMAIL: Final = RateLimit(scope="signin-email", limit=10, window_seconds=900)
"""Ten attempts at one account in fifteen minutes, from anywhere. Low enough
that online guessing of a twelve-character password is hopeless."""

SIGNUP_PER_ADDRESS: Final = RateLimit(scope="signup-address", limit=10, window_seconds=HOUR)
"""Ten new accounts an hour from one address. Accounts are free to make and each
one is a fresh set of research allowances, so this is abuse control first."""

RESEARCH: Final = Action(
    name="research",
    anonymous=(
        RateLimit(scope="research-anon-hour", limit=5, window_seconds=HOUR),
        RateLimit(scope="research-anon-day", limit=15, window_seconds=DAY),
    ),
    account=(
        RateLimit(scope="research-account-hour", limit=20, window_seconds=HOUR),
        RateLimit(scope="research-account-day", limit=100, window_seconds=DAY),
    ),
    address=(RateLimit(scope="research-address-hour", limit=30, window_seconds=HOUR),),
)
"""Starting research of any kind: new, follow-up research, Update Research. The
most expensive thing a request can do, so the tightest limits."""

QUESTION: Final = Action(
    name="question",
    anonymous=(RateLimit(scope="question-anon-hour", limit=30, window_seconds=HOUR),),
    account=(RateLimit(scope="question-account-hour", limit=120, window_seconds=HOUR),),
    address=(RateLimit(scope="question-address-hour", limit=150, window_seconds=HOUR),),
)

UPLOAD: Final = Action(
    name="upload",
    anonymous=(RateLimit(scope="upload-anon-hour", limit=20, window_seconds=HOUR),),
    account=(RateLimit(scope="upload-account-hour", limit=60, window_seconds=HOUR),),
    address=(RateLimit(scope="upload-address-hour", limit=80, window_seconds=HOUR),),
)

EXPORT: Final = Action(
    name="export",
    anonymous=(RateLimit(scope="export-anon-hour", limit=20, window_seconds=HOUR),),
    account=(RateLimit(scope="export-account-hour", limit=60, window_seconds=HOUR),),
    address=(RateLimit(scope="export-address-hour", limit=80, window_seconds=HOUR),),
)
