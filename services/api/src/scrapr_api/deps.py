"""Dependencies every route resolves before it does anything else.

**Ownership is resolved once, here, and passed to repositories.** A route never
filters by owner itself (implementation plan §4.3): it asks for an
`OwnerContext` and hands it over. That is what makes the cross-account test
suite meaningful rather than a spot check of one handler in twenty.

**Two carriers, one precedence** (`DEC-16`, `DEC-17`). An account session
(`scrapr_account`) outranks an anonymous one (`scrapr_session`): a request that
carries both acts as the account. Both are opaque tokens in `HttpOnly`,
`Secure`, `SameSite=Lax` cookies, stored hashed, and neither is ever returned in
a body — a value JavaScript can read is a value an injected script can
exfiltrate.

A request with no valid carrier that is *creating* research gets a new
anonymous session; one that is *reading* gets nothing, and therefore sees
nothing.
"""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated, Final, final

from fastapi import Cookie, Depends, Request, Response, status
from sqlalchemy.orm import Session, sessionmaker

from scrapr_api.errors import ApiError
from scrapr_core.config import get_settings
from scrapr_core.db.engine import build_engine, build_session_factory
from scrapr_core.db.models import AnonymousSession, User
from scrapr_core.db.repositories import (
    AccountRepository,
    AnonymousSessionRepository,
    ResearchRepository,
)
from scrapr_core.db.repositories.accounts import ACCOUNT_SESSION_LIFETIME
from scrapr_core.db.repositories.anonymous_sessions import ANONYMOUS_LIFETIME
from scrapr_core.db.repositories.rate_limits import RateLimit, RateLimiter
from scrapr_core.domain.ownership import OwnerContext

__all__ = [
    "ACCOUNT_COOKIE",
    "ANONYMOUS_COOKIE",
    "Callers",
    "CallersDep",
    "CurrentAccount",
    "CurrentOwner",
    "DbSession",
    "OptionalAccount",
    "OwnerOrNew",
    "Research",
    "clear_cookie",
    "client_address",
    "enforce_rate_limit",
    "get_session_factory",
    "set_account_cookie",
]

ANONYMOUS_COOKIE: Final = "scrapr_session"
ACCOUNT_COOKIE: Final = "scrapr_account"


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    """One engine per process, built lazily.

    Lazily because importing the app must not require a database — the OpenAPI
    schema is generated in CI, where there is none.
    """
    return build_session_factory(build_engine())


def db_session() -> Generator[Session, None, None]:
    """A session per request, committed on success and rolled back on failure.

    Committing here rather than in each route is what keeps "nothing mutates on
    a GET" checkable: a read route simply never adds anything to commit.
    """
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


DbSession = Annotated[Session, Depends(db_session, scope="function")]
"""**`scope="function"`: committed before the response is sent.**

FastAPI's default for a `yield` dependency is to run its teardown *after* the
response has gone out. Here that teardown is the commit, so the default told a
client "created" before anything was committed: a browser that signed up and
immediately asked who it was signed in as read a database without the session
it had just been issued, and a commit that failed after the response became a
201 for something that never existed. Ending the session with the handler makes
the response mean what it says."""

AnonymousCookie = Annotated[str | None, Cookie(alias=ANONYMOUS_COOKIE)]
AccountCookie = Annotated[str | None, Cookie(alias=ACCOUNT_COOKIE)]


# --------------------------------------------------------------------------
# Cookies
# --------------------------------------------------------------------------


def _set_cookie(response: Response, name: str, token: str, max_age: int) -> None:
    response.set_cookie(
        name,
        token,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
        max_age=max_age,
    )


def set_account_cookie(response: Response, token: str) -> None:
    """Sign a browser in. `Max-Age` matches the session's absolute lifetime."""
    _set_cookie(response, ACCOUNT_COOKIE, token, int(ACCOUNT_SESSION_LIFETIME.total_seconds()))


def set_anonymous_cookie(response: Response, token: str) -> None:
    """Issue or refresh the anonymous carrier. `Max-Age` tracks `DEC-17`."""
    _set_cookie(response, ANONYMOUS_COOKIE, token, int(ANONYMOUS_LIFETIME.total_seconds()))


def clear_cookie(response: Response, name: str) -> None:
    """Remove a carrier, with the same attributes it was set with.

    A deletion whose `Secure` or `SameSite` differs from the original is, to a
    browser, a different cookie — and the original survives.
    """
    response.delete_cookie(name, path="/", secure=True, httponly=True, samesite="lax")


# --------------------------------------------------------------------------
# Callers
# --------------------------------------------------------------------------


@final
@dataclass(frozen=True, slots=True)
class Callers:
    """Everyone a request could be acting as, resolved from its cookies.

    Both halves are kept rather than collapsed into one owner, because the
    claim path needs exactly that: an account *and* the anonymous session whose
    research it is about to take.
    """

    account: User | None
    anonymous: AnonymousSession | None
    account_cookie_present: bool
    anonymous_cookie_present: bool

    @property
    def owner(self) -> OwnerContext | None:
        if self.account is not None:
            return OwnerContext.for_user(self.account.id)
        if self.anonymous is not None:
            return OwnerContext.for_anonymous(self.anonymous.id)
        return None


def resolve_callers(
    response: Response,
    session: DbSession,
    scrapr_account: AccountCookie = None,
    scrapr_session: AnonymousCookie = None,
) -> Callers:
    """Resolve both carriers. Refreshes the anonymous cookie as it slides."""
    account = (
        AccountRepository(session).resolve_session(scrapr_account)
        if scrapr_account
        else None
    )

    anonymous: AnonymousSession | None = None
    if scrapr_session:
        repository = AnonymousSessionRepository(session)
        anonymous = repository.resolve_valid(scrapr_session)
        if anonymous is not None and repository.touch(anonymous):
            # `DEC-17`: the expiry slid, so the cookie's lifetime slides with
            # it. Without this the browser would drop a session the database
            # still considers alive.
            set_anonymous_cookie(response, scrapr_session)

    return Callers(
        account=account,
        anonymous=anonymous,
        account_cookie_present=bool(scrapr_account),
        anonymous_cookie_present=bool(scrapr_session),
    )


CallersDep = Annotated[Callers, Depends(resolve_callers)]


def current_owner(callers: CallersDep) -> OwnerContext:
    """Who is asking. Refuses the request if nobody is.

    A caller with no valid carrier owns no research, so there is nothing for
    them to read. Answering 401 here rather than 404 per-resource keeps the
    reason honest: the request could not be attributed at all.
    """
    owner = callers.owner
    if owner is not None:
        return owner

    if callers.account_cookie_present:
        raise ApiError(
            status.HTTP_401_UNAUTHORIZED,
            "signed_out",
            "Your sign-in has expired. Sign in again to see your research.",
        )
    if callers.anonymous_cookie_present:
        raise ApiError(
            status.HTTP_401_UNAUTHORIZED,
            "unknown_session",
            "This session is no longer valid. Start new research.",
        )
    raise ApiError(
        status.HTTP_401_UNAUTHORIZED,
        "no_session",
        "No research session. Start research before reading it.",
    )


CurrentOwner = Annotated[OwnerContext, Depends(current_owner)]


def owner_or_new(
    response: Response,
    session: DbSession,
    callers: CallersDep,
) -> OwnerContext:
    """The owner for a *creating* request, minting a session if there is none.

    `REQ-AUTH-001`: research starts without an account. The token is set on the
    response exactly once, when it is created.
    """
    owner = callers.owner
    if owner is not None:
        return owner

    issued = AnonymousSessionRepository(session).issue()
    set_anonymous_cookie(response, issued.token)
    return OwnerContext.for_anonymous(issued.session.id)


OwnerOrNew = Annotated[OwnerContext, Depends(owner_or_new)]


def optional_account(callers: CallersDep) -> User | None:
    return callers.account


OptionalAccount = Annotated[User | None, Depends(optional_account)]


def current_account(callers: CallersDep) -> User:
    """An account, or a 401 that says an account is what is missing."""
    if callers.account is None:
        raise ApiError(
            status.HTTP_401_UNAUTHORIZED,
            "account_required",
            "Sign in to see saved research.",
        )
    return callers.account


CurrentAccount = Annotated[User, Depends(current_account)]


def research_repository(session: DbSession, owner: CurrentOwner) -> ResearchRepository:
    return ResearchRepository(session, owner)


Research = Annotated[ResearchRepository, Depends(research_repository)]


# --------------------------------------------------------------------------
# Rate limiting
# --------------------------------------------------------------------------


def client_address(request: Request) -> str:
    """The address a request came from, for counting — never for trusting.

    `X-Forwarded-For` is read only when `TRUST_PROXY_HEADERS` says a proxy we
    control sets it. Otherwise any client could name a fresh address on every
    request and never be limited at all.
    """
    if get_settings().trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for", "")
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


def enforce_rate_limit(
    session: Session, rule: RateLimit, subject: str
) -> None:
    """Count one action, commit the count, and refuse if it is over the limit.

    **Committed before deciding.** The request session rolls back on an error,
    and a refused or failed request is exactly the one that must stay counted —
    otherwise a wrong password would un-count itself on the way out.
    """
    decision = RateLimiter(session).hit(rule, subject)
    session.commit()
    if not decision.allowed:
        raise ApiError(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "rate_limited",
            "Too many attempts. Wait a few minutes and try again.",
            headers={"Retry-After": str(decision.retry_after_seconds)},
        )
