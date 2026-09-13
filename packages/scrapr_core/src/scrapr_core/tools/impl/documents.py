"""The document retrieval tool (`REQ-TOOL-008`, `REQ-DOC-005`, `DEC-15`).

**Uploads reach the agent through the same tool interface as everything else.**
`REQ-TOOL-008` says so, and the reason is that the orchestrator should not grow
a second retrieval path with its own budget, failure and provenance rules —
document evidence obeys the same claim, confidence and citation rules as web
evidence (`REQ-DOC-006 AC-2`), and the cheapest way to guarantee that is for it
to arrive by the same road.

**Postgres full-text search, no vectors** (`DEC-15`). The corpus is one user's
ten documents, which is where keyword recall is high and embeddings are
infrastructure for a problem nobody has yet.

**The session comes from the request, not the constructor.** The registry is
built once per worker process and frozen before any run starts
(`REQ-SEC-015 AC-1`), so a tool bound to one research session could only ever
serve one job. `params["session_id"]` is the target, which is what
`TARGETED_CATEGORIES` means by a category that answers a specific thing rather
than a query.

**A document is never independent corroboration** (`REQ-DOC-008 AC-3`). This
tool sets `SourceCategory.DOCUMENT`, and the confidence layer counts those
separately from sources the agent found for itself. A file the reader supplied
cannot verify itself, and two chunks of it cannot verify each other.

**Untrusted, always** (`REQ-DOC-009`). The reader chose the file, which says
nothing about who wrote it — they may have been sent it. A document is evidence
about the document, never a command.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, final
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from scrapr_core.db.enums import Accessibility, SourceCategory, UploadState
from scrapr_core.db.models import Upload, UploadChunk
from scrapr_core.security.trust import SourceRef, Untrusted
from scrapr_core.storage.extract import describe_locator
from scrapr_core.tools.contract import (
    ToolCategory,
    ToolFailure,
    ToolItem,
    ToolOutcome,
    ToolRequest,
    ToolResult,
)

__all__ = ["DOCUMENT_TOOL_NAME", "DocumentTool"]

DOCUMENT_TOOL_NAME: Final = "uploaded_documents"

_CONFIG: Final = "english"
"""The text-search configuration. English stemming, so "reporting" matches
"reported" — which is most of what a keyword search over prose needs, and the
recall `DEC-15` is relying on in place of embeddings."""


@final
@dataclass(frozen=True, slots=True)
class DocumentTool:
    """Search the uploads attached to one research session."""

    session_factory: sessionmaker[Session]
    name: str = DOCUMENT_TOOL_NAME
    category: ToolCategory = ToolCategory.DOCUMENTS

    async def invoke(self, request: ToolRequest) -> ToolOutcome:
        """Return the passages matching a query (`REQ-DOC-005 AC-2`).

        Synchronous work inside an async signature, deliberately: this is a
        local index query measured in milliseconds, and handing it to a thread
        would cost more than it saves. The signature matches the contract
        because every tool has the same shape, not because every tool talks to
        a network.
        """
        session_id = _session_id(request)
        if session_id is None:
            # A wiring bug rather than a provider condition, but the contract
            # says failures return (`REQ-TOOL-010 AC-1`) and a raise here would
            # be converted by the registry anyway — with a worse message.
            return self._failed("error", "no session_id in the request")

        query = str(request.params.get("query", "")).strip()
        if not query:
            return self._failed("not_found", "no query supplied")

        with self.session_factory() as session:
            matches = search_chunks(
                session, session_id, query, request.budget.max_results
            )

        return ToolResult(
            items=tuple(_as_item(chunk, upload) for chunk, upload in matches),
            retrieved_at=dt.datetime.now(dt.UTC),
            tool=self.name,
            category=self.category,
        )

    def _failed(self, kind: str, message: str) -> ToolFailure:
        return ToolFailure(
            kind="not_found" if kind == "not_found" else "error",
            message=message,
            tool=self.name,
            category=self.category,
        )


def _session_id(request: ToolRequest) -> UUID | None:
    raw = request.params.get("session_id")
    if not isinstance(raw, str):
        return None
    try:
        return UUID(raw)
    except ValueError:
        return None


def _terms(query: str) -> str:
    """The query as an OR of its words, for `to_tsquery`.

    **Any term, ranked — not every term.** The obvious choices,
    `plainto_tsquery` and `websearch_to_tsquery`, both AND the terms together,
    and the query the orchestrator builds is `"{subject}: {question}"`. A
    passage inside the reader's own board pack does not repeat the company's
    name in every paragraph, so an AND query finds nothing in almost every real
    document — the feature would look implemented and return empty for
    everybody. This was caught by an end-to-end test and by nothing else.

    Recall over precision is the right trade at this scale (`DEC-15`): the
    corpus is one user's ten files, `ts_rank` orders what matches, and the tool
    budget caps how many come back.

    Tokens are reduced to alphanumerics before they are joined, so nothing a
    model wrote can reach `to_tsquery` as an operator. That matters because the
    query text derives from the user's objective, which is untrusted input.
    """
    words = [
        cleaned
        for word in query.split()
        if (cleaned := "".join(ch for ch in word if ch.isalnum()))
    ]
    # A bound on the query, not on the corpus. A very long question is the
    # planner being verbose, and its first two dozen words carry the subject.
    return " | ".join(dict.fromkeys(words[:24]))


def search_chunks(
    session: Session, research_session_id: UUID, query: str, limit: int
) -> Sequence[tuple[UploadChunk, Upload]]:
    """Rank a session's chunks against the query, best first.

    Only `READY` uploads are searched. A `failed` one has no usable text, and a
    `processing` one is incomplete — citing half a document would be citing
    something that is still changing.
    """
    terms = _terms(query)
    if not terms:
        # Nothing left after sanitising: punctuation only. An empty tsquery
        # matches nothing, so returning early is the same answer without a
        # round trip.
        return []

    vector = func.to_tsvector(_CONFIG, UploadChunk.text)
    match = func.to_tsquery(_CONFIG, terms)

    return (
        session.execute(
            select(UploadChunk, Upload)
            .join(Upload, Upload.id == UploadChunk.upload_id)
            .where(
                Upload.session_id == research_session_id,
                Upload.deleted_at.is_(None),
                Upload.processing_state == UploadState.READY,
                vector.op("@@")(match),
            )
            .order_by(func.ts_rank(vector, match).desc(), UploadChunk.ordinal)
            .limit(limit)
        )
        .tuples()
        .all()
    )


def _as_item(chunk: UploadChunk, upload: Upload) -> ToolItem:
    """One matching passage, as evidence.

    `source_identifier` addresses the chunk rather than the file, so two
    passages of one document are two sources' worth of provenance and one
    citation each resolves to a place (implementation plan §10.3). The filename
    goes in `source_name`, which is what `REQ-DOC-008 AC-2` requires a document
    citation to show.

    `structured["upload_id"]` is not decoration: `EvidenceRepository.source_for`
    reads it to set `sources.upload_id`, which is how a stored citation knows
    which of the reader's files it came from.
    """
    where = describe_locator(chunk.locator)
    return ToolItem(
        source_name=f"{upload.filename} ({where})",
        # `DEC-08` and the confidence layer both key on this: a document is
        # categorised as one so it can be attributed and counted differently
        # from anything the agent found for itself.
        source_category=SourceCategory.DOCUMENT,
        retrieved_at=dt.datetime.now(dt.UTC),
        accessibility=Accessibility.ACCESSIBLE,
        content=Untrusted(chunk.text, SourceRef(kind="document", locator=str(chunk.id))),
        source_identifier=f"upload:{upload.id}:{chunk.ordinal}",
        structured={
            "upload_id": str(upload.id),
            "filename": upload.filename,
            "locator": where,
        },
    )
