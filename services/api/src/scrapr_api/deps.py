"""Dependencies every route resolves before it does anything else.

**Ownership is resolved once, here, and passed to repositories.** A route never
filters by owner itself (implementation plan §4.3): it asks for an
`OwnerContext` and hands it over. That is what makes the cross-account test
suite meaningful rather than a spot check of one handler in twenty.

The anonymous session carrier is an opaque token in an `HttpOnly`, `Secure`,
`SameSite=Lax` cookie, stored hashed. A request without one that is *creating*
research gets a new session; a request without one that is *reading* gets
nothing, and therefore sees nothing. Lifetime and expiry are `OPEN-17` and are
deliberately not set here.
"""

from __future__ import annotations

from collections.abc import Generator
from functools import lru_cache
from typing import Annotated

from fastapi import Cookie, Depends, Response, status
from sqlalchemy.orm import Session, sessionmaker

from scrapr_api.errors import ApiError
from scrapr_core.db.engine import build_engine, build_session_factory
from scrapr_core.db.repositories import AnonymousSessionRepository, ResearchRepository
from scrapr_core.domain.ownership import OwnerContext

__all__ = [
    "ANONYMOUS_COOKIE",
    "CurrentOwner",
    "DbSession",
    "OwnerOrNew",
    "Research",
    "get_session_factory",
]

ANONYMOUS_COOKIE = "scrapr_session"


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


DbSession = Annotated[Session, Depends(db_session)]


def current_owner(
    session: DbSession,
    scrapr_session: Annotated[str | None, Cookie(alias=ANONYMOUS_COOKIE)] = None,
) -> OwnerContext:
    """Who is asking. Refuses the request if nobody is.

    A caller with no cookie owns no research, so there is nothing for them to
    read. Answering 401 here rather than 404 per-resource keeps the reason
    honest: the request could not be attributed at all.
    """
    if scrapr_session is None:
        raise ApiError(
            status.HTTP_401_UNAUTHORIZED,
            "no_session",
            "No research session. Start research before reading it.",
        )

    owner = AnonymousSessionRepository(session).owner_context(scrapr_session)
    if owner is None:
        raise ApiError(
            status.HTTP_401_UNAUTHORIZED,
            "unknown_session",
            "This session is no longer valid. Start new research.",
        )
    return owner


CurrentOwner = Annotated[OwnerContext, Depends(current_owner)]


def owner_or_new(
    response: Response,
    session: DbSession,
    scrapr_session: Annotated[str | None, Cookie(alias=ANONYMOUS_COOKIE)] = None,
) -> OwnerContext:
    """The owner for a *creating* request, minting a session if there is none.

    `REQ-AUTH-001`: research starts without an account. The token is set on the
    response exactly once, when it is created; it is never returned in a body,
    because a value readable by JavaScript is a value an injected script can
    exfiltrate.
    """
    repository = AnonymousSessionRepository(session)

    if scrapr_session is not None:
        existing = repository.owner_context(scrapr_session)
        if existing is not None:
            return existing

    issued = repository.issue()
    response.set_cookie(
        ANONYMOUS_COOKIE,
        issued.token,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
        # No max-age: lifetime and expiry are OPEN-17, and a number invented
        # here would be a product decision disguised as a default.
    )
    return OwnerContext.for_anonymous(issued.session.id)


OwnerOrNew = Annotated[OwnerContext, Depends(owner_or_new)]


def research_repository(session: DbSession, owner: CurrentOwner) -> ResearchRepository:
    return ResearchRepository(session, owner)


Research = Annotated[ResearchRepository, Depends(research_repository)]
