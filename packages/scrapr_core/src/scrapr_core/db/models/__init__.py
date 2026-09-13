"""Every mapped table, imported here so `Base.metadata` is always complete.

Alembic's autogenerate compares `Base.metadata` against the live database. A
model file that nothing imports is therefore not merely unused — it is a table
autogenerate will offer to *drop*. Importing all of them in one place makes that
failure impossible.
"""

from __future__ import annotations

from scrapr_core.db.base import Base
from scrapr_core.db.models.activity import ActivityEvent
from scrapr_core.db.models.claims import Claim, ClaimEvidence, Conflict, ConflictEvidence
from scrapr_core.db.models.conversation import ConversationMessage, MessageEvidence
from scrapr_core.db.models.documents import Upload, UploadChunk
from scrapr_core.db.models.evidence import Evidence, Source
from scrapr_core.db.models.exports import Export
from scrapr_core.db.models.identity import AnonymousSession, User, UserSession
from scrapr_core.db.models.limits import RateLimitCounter
from scrapr_core.db.models.questions import (
    QuestionEvidence,
    QuestionState,
    ResearchQuestion,
)
from scrapr_core.db.models.report import ReportSection, Visualization, VisualizationEvidence
from scrapr_core.db.models.research import ResearchSession, ResearchVersion
from scrapr_core.db.models.runs import ResearchRun, RunStep, ToolInvocation

__all__ = [
    "ActivityEvent",
    "AnonymousSession",
    "Base",
    "Claim",
    "ClaimEvidence",
    "Conflict",
    "ConflictEvidence",
    "ConversationMessage",
    "Evidence",
    "Export",
    "MessageEvidence",
    "QuestionEvidence",
    "QuestionState",
    "RateLimitCounter",
    "ReportSection",
    "ResearchQuestion",
    "ResearchRun",
    "ResearchSession",
    "ResearchVersion",
    "RunStep",
    "Source",
    "ToolInvocation",
    "Upload",
    "UploadChunk",
    "User",
    "UserSession",
    "Visualization",
    "VisualizationEvidence",
]
