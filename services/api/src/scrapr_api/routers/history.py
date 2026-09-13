"""Saved research, for the account that saved it (`REQ-AUTH-005`).

History is an account feature (masterplan §17 lists "access history" among the
account benefits), so an anonymous visitor is told an account is what is
missing rather than shown an empty list that looks like lost research.

The list comes from `ResearchRepository.list_sessions`, which is scoped by the
`OwnerContext` like every other read — `AC-2`, "history contains only the
requesting user's research", holds for the same reason every other isolation
property does, rather than because this route filters.
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from scrapr_api.deps import CurrentAccount, DbSession
from scrapr_api.schemas import HistoryItemOut
from scrapr_core.db.models import ResearchVersion
from scrapr_core.db.repositories import ResearchRepository
from scrapr_core.domain.ownership import OwnerContext

__all__ = ["router"]

router = APIRouter(prefix="/v1/me", tags=["history"])

HISTORY_MAX = 100
"""The most sessions one page returns. V1 has no folders or search (§5.2), so
the list is the whole interface, and a hundred is more than a person scrolls."""


@router.get("/research")
def list_history(
    account: CurrentAccount,
    session: DbSession,
    limit: int = Query(default=50, ge=1, le=HISTORY_MAX),
) -> list[HistoryItemOut]:
    """The account's research, most recently updated first."""
    research = ResearchRepository(session, OwnerContext.for_user(account.id))
    sessions = research.list_sessions(limit=limit)

    # Version counts in one grouped query rather than one per row.
    counts: dict[object, int] = {}
    if sessions:
        counts = {
            session_id: int(count)
            for session_id, count in session.execute(
                select(ResearchVersion.session_id, func.count(ResearchVersion.id))
                .where(ResearchVersion.session_id.in_([found.id for found in sessions]))
                .group_by(ResearchVersion.session_id)
            ).all()
        }

    return [
        HistoryItemOut(
            id=found.id,
            objective=found.objective,
            subject=found.subject,
            status=found.status,
            created_at=found.created_at,
            updated_at=found.updated_at,
            version_count=counts.get(found.id, 0),
        )
        for found in sessions
    ]
