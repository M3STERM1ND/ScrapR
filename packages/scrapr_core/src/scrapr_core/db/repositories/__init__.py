"""Ownership-scoped data access.

Every read repository takes an `OwnerContext` and every query filters on it
(implementation plan §4.3). Route handlers and job handlers do not filter
themselves — they pass the context in — so the cross-account test suite
(`REQ-SEC-002 AC-3`) tests one implementation rather than spot-checking twenty.
"""

from __future__ import annotations

from scrapr_core.db.repositories.activity import ActivityRepository
from scrapr_core.db.repositories.anonymous_sessions import (
    AnonymousSessionRepository,
    IssuedSession,
    hash_session_token,
)
from scrapr_core.db.repositories.questions import QuestionCoverage, QuestionRepository
from scrapr_core.db.repositories.research import ResearchRepository
from scrapr_core.db.repositories.runs import RunRepository

__all__ = [
    "ActivityRepository",
    "AnonymousSessionRepository",
    "IssuedSession",
    "QuestionCoverage",
    "QuestionRepository",
    "ResearchRepository",
    "RunRepository",
    "hash_session_token",
]
