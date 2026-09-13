"""Accounts: sign up, sign in, sign out, and who am I (`DEC-16`).

**Signing up or in claims this browser's anonymous research** (`DEC-17`,
`REQ-AUTH-004`). A visitor who researched without an account and then creates
one expects that research to be in it, and Flow E makes that step E-3. The
claim runs in the same transaction as the sign-in, so there is no moment where
the account exists and the research is still somewhere else.

**Every failure to sign in reads the same.** Unknown email and wrong password
return one message, after one full password verification either way.

**Counts are committed before credentials are checked** (`REQ-AUTH-009 AC-3`).
A refused sign-in raises, the request rolls back, and a count taken inside that
transaction would vanish with it.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from scrapr_api.deps import (
    ACCOUNT_COOKIE,
    ANONYMOUS_COOKIE,
    CallersDep,
    CurrentAccount,
    DbSession,
    OptionalAccount,
    clear_cookie,
    client_address,
    enforce_rate_limit,
    set_account_cookie,
)
from scrapr_api.errors import ApiError
from scrapr_api.schemas import (
    AccountOut,
    AuthOut,
    CredentialsIn,
    OwnershipClaimOut,
    SessionStateOut,
)
from scrapr_core.db.models import User
from scrapr_core.db.repositories import AccountRepository, EmailTakenError
from scrapr_core.db.repositories.accounts import normalize_email
from scrapr_core.db.repositories.claiming import (
    ClaimRefusedError,
    claim_anonymous_research,
)
from scrapr_core.security.limits import (
    SIGNIN_PER_ADDRESS,
    SIGNIN_PER_EMAIL,
    SIGNUP_PER_ADDRESS,
)
from scrapr_core.security.passwords import password_problem

__all__ = ["claim_router", "router"]

router = APIRouter(prefix="/v1/auth", tags=["auth"])
claim_router = APIRouter(prefix="/v1/research", tags=["auth"])


def _account_out(user: User) -> AccountOut:
    return AccountOut(email=user.email, created_at=user.created_at)


def _claim_into(
    callers: CallersDep, user: User, session: DbSession, response: Response
) -> int:
    """Move this browser's anonymous research into `user`, if there is any.

    A refused claim — the anonymous session lapsed between resolving and
    locking — is not a sign-in failure. The account is fine; there was simply
    nothing left to bring into it.
    """
    if callers.anonymous is None:
        return 0
    try:
        outcome = claim_anonymous_research(
            session, anonymous_session_id=callers.anonymous.id, user_id=user.id
        )
    except ClaimRefusedError:
        return 0
    finally:
        # Spent either way: claimed, or no longer valid.
        clear_cookie(response, ANONYMOUS_COOKIE)
    return outcome.research_sessions


@router.post("/signup", status_code=status.HTTP_201_CREATED)
def sign_up(
    body: CredentialsIn,
    request: Request,
    response: Response,
    session: DbSession,
    callers: CallersDep,
) -> AuthOut:
    """Create an account, sign this browser in, and bring its research along."""
    enforce_rate_limit(session, SIGNUP_PER_ADDRESS, client_address(request))

    email = normalize_email(body.email)
    if email is None:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "invalid_email",
            "That does not look like an email address.",
        )
    problem = password_problem(body.password)
    if problem is not None:
        raise ApiError(status.HTTP_422_UNPROCESSABLE_CONTENT, "weak_password", problem)

    accounts = AccountRepository(session)
    try:
        user = accounts.create(email, body.password)
    except EmailTakenError as exc:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "email_taken",
            "An account already uses that email. Sign in instead.",
        ) from exc

    issued = accounts.issue_session(user)
    claimed = _claim_into(callers, user, session, response)
    set_account_cookie(response, issued.token)
    return AuthOut(account=_account_out(user), claimed=claimed)


@router.post("/signin")
def sign_in(
    body: CredentialsIn,
    request: Request,
    response: Response,
    session: DbSession,
    callers: CallersDep,
) -> AuthOut:
    """Sign in, and bring this browser's anonymous research into the account."""
    enforce_rate_limit(session, SIGNIN_PER_ADDRESS, client_address(request))
    enforce_rate_limit(
        session, SIGNIN_PER_EMAIL, normalize_email(body.email) or body.email
    )

    accounts = AccountRepository(session)
    user = accounts.authenticate(body.email, body.password)
    if user is None:
        raise ApiError(
            status.HTTP_401_UNAUTHORIZED,
            "invalid_credentials",
            "That email and password do not match an account.",
        )

    issued = accounts.issue_session(user)
    claimed = _claim_into(callers, user, session, response)
    set_account_cookie(response, issued.token)
    return AuthOut(account=_account_out(user), claimed=claimed)


@router.post("/signout", status_code=status.HTTP_204_NO_CONTENT)
def sign_out(request: Request, response: Response, session: DbSession) -> None:
    """Revoke this browser's account session. Idempotent.

    Revoked in the database, not only cleared in the browser: a copy of the
    cookie taken before sign-out must stop working too.
    """
    token = request.cookies.get(ACCOUNT_COOKIE)
    if token:
        AccountRepository(session).revoke_session(token)
    clear_cookie(response, ACCOUNT_COOKIE)


@router.get("/session")
def get_session_state(account: OptionalAccount) -> SessionStateOut:
    """Who this browser is signed in as. Never a 401."""
    return SessionStateOut(account=_account_out(account) if account else None)


@claim_router.post("/claim")
def claim_research(
    account: CurrentAccount,
    response: Response,
    session: DbSession,
    callers: CallersDep,
) -> OwnershipClaimOut:
    """Attach this browser's anonymous research to the signed-in account.

    For a visitor who researched while signed out and is already signed in on
    this browser. Needs both halves: an account to receive, and an anonymous
    session to give. With no anonymous session it moves nothing and says so.
    """
    return OwnershipClaimOut(claimed=_claim_into(callers, account, session, response))
