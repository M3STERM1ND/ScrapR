"""Attaching anonymous research to an account (`REQ-AUTH-004`, `DEC-17`).

"Claim" here is ownership transfer, not an evidence claim; the module name is
the verb because the noun is already taken.

**One transaction, one selector.** Every research session whose owner is the
caller's anonymous session moves to the account; nothing else can. The
anonymous session id comes from a token the caller presented and this module
resolved, never from a request parameter — so `AC-2`, "cannot transfer research
owned by a different account or a different anonymous session", holds because
there is no way to name any other research, not because a check remembers to
refuse it.

**The anonymous session is spent afterwards.** `claimed_at` makes it resolve to
nobody (`anonymous_sessions.is_valid`), so the token that owned the research
cannot go on to own new research once its old research has moved.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import final
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from scrapr_core.db.base import utcnow
from scrapr_core.db.models import AnonymousSession, ResearchSession
from scrapr_core.db.repositories.anonymous_sessions import is_valid

__all__ = ["ClaimOutcome", "ClaimRefusedError", "claim_anonymous_research"]


class ClaimRefusedError(RuntimeError):
    """The anonymous session cannot be claimed: expired, or already claimed."""


@final
@dataclass(frozen=True, slots=True)
class ClaimOutcome:
    """What a claim moved."""

    research_sessions: int


def claim_anonymous_research(
    session: Session,
    *,
    anonymous_session_id: UUID,
    user_id: UUID,
    now: dt.datetime | None = None,
) -> ClaimOutcome:
    """Move every research session of one anonymous session to one account.

    The anonymous row is locked first, so two sign-ins racing on the same
    browser cannot both claim it: the second waits, then finds it claimed.
    """
    moment = now or utcnow()

    anonymous = session.execute(
        select(AnonymousSession)
        .where(AnonymousSession.id == anonymous_session_id)
        .with_for_update()
    ).scalar_one_or_none()

    if anonymous is None or not is_valid(anonymous, now=moment):
        raise ClaimRefusedError("that anonymous session can no longer be claimed")

    # Owner set and anonymous owner cleared in the same statement, so the
    # `one_owner` check constraint holds for every row at every moment.
    result = session.execute(
        update(ResearchSession)
        .where(ResearchSession.anonymous_session_id == anonymous_session_id)
        .values(
            owner_user_id=user_id,
            anonymous_session_id=None,
            updated_at=moment,
        )
        .execution_options(synchronize_session=False)
    )
    moved = int(getattr(result, "rowcount", 0) or 0)

    anonymous.claimed_by_user_id = user_id
    anonymous.claimed_at = moment
    session.flush()

    # Objects already loaded in this session still hold the old owner. Nothing
    # in the claim path reads them, but a caller that does would otherwise see
    # research owned by an anonymous session that no longer owns anything.
    session.expire_all()

    return ClaimOutcome(research_sessions=moved)
