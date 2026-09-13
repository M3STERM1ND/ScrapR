"""Document retrieval and the extraction worker (`REQ-DOC-005`, `-003`, `DEC-15`).

Full-text search runs in Postgres, so these run against Postgres.
`websearch_to_tsquery` and `ts_rank` have no meaningful stand-in, and a test
that faked them would be asserting the shape of a query rather than whether it
finds anything.
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session, sessionmaker

from scrapr_core.config import Settings
from scrapr_core.db.enums import SourceCategory, UploadState
from scrapr_core.db.models import AnonymousSession, ResearchSession
from scrapr_core.db.repositories.uploads import UploadRepository
from scrapr_core.security.trust import Untrusted
from scrapr_core.storage.objects import ObjectStore
from scrapr_core.storage.processing import DocumentProcessor
from scrapr_core.tools.contract import (
    ToolBudget,
    ToolCategory,
    ToolFailure,
    ToolRequest,
    ToolResult,
)
from scrapr_core.tools.impl.documents import DocumentTool

MB = 1024 * 1024
LIMITS = {
    "max_upload_bytes": 25 * MB,
    "max_uploads_per_session": 10,
    "max_session_upload_bytes": 100 * MB,
}


def _settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


@pytest.fixture
def research_id(db_session: Session) -> UUID:
    anonymous = AnonymousSession(token_hash=f"doctool-{id(db_session)}")
    db_session.add(anonymous)
    db_session.flush()
    research = ResearchSession(
        anonymous_session_id=anonymous.id,
        objective="How did Acme Corp perform last year?",
        status="pending",
    )
    db_session.add(research)
    db_session.flush()
    return research.id


@pytest.fixture
def factory(db_session: Session) -> sessionmaker[Session]:
    """Bound to the test's connection, so the tool sees uncommitted rows."""
    return sessionmaker(bind=db_session.connection(), expire_on_commit=False)


def _ready_document(
    session: Session,
    research_id: UUID,
    filename: str,
    chunks: list[tuple[int, str, dict[str, object]]],
) -> UUID:
    uploads = UploadRepository(session, **LIMITS)
    upload = uploads.create_pending(
        session_id=research_id,
        filename=filename,
        content_type="text/plain",
        size_bytes=1024,
        storage_key=f"uploads/{research_id}/{filename}",
    )
    uploads.mark_uploaded(upload, size_bytes=1024)
    uploads.mark_ready(upload, chunks)
    return upload.id


async def _search(
    factory: sessionmaker[Session], research_id: UUID, query: str, limit: int = 10
) -> ToolResult:
    outcome = await DocumentTool(session_factory=factory).invoke(
        ToolRequest(
            category=ToolCategory.DOCUMENTS,
            params={"query": query, "session_id": str(research_id)},
            budget=ToolBudget(max_results=limit),
        )
    )
    assert isinstance(outcome, ToolResult), outcome
    return outcome


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------


async def test_a_matching_passage_is_returned(
    db_session: Session, factory: sessionmaker[Session], research_id: UUID
) -> None:
    _ready_document(
        db_session,
        research_id,
        "annual.txt",
        [
            (0, "Revenue for the year was $1.2bn, up 18 percent.", {"page": 1}),
            (1, "Headcount closed the year at 4,100.", {"page": 2}),
        ],
    )

    result = await _search(factory, research_id, "revenue")

    assert len(result.items) == 1
    assert "1.2bn" in result.items[0].content.text


async def test_english_stemming_finds_an_inflected_word(
    db_session: Session, factory: sessionmaker[Session], research_id: UUID
) -> None:
    """`DEC-15` chose keyword search over embeddings partly on this.

    "reporting" matching "reported" is most of what recall over prose needs, and
    losing it would make the case for vectors much stronger.
    """
    _ready_document(
        db_session,
        research_id,
        "notes.txt",
        [(0, "The company reported a loss in the fourth quarter.", {"line": 1})],
    )

    result = await _search(factory, research_id, "reporting losses")
    assert result.items


async def test_the_orchestrator_s_query_shape_finds_a_passage(
    db_session: Session, factory: sessionmaker[Session], research_id: UUID
) -> None:
    """The query is `"{subject}: {question}"`, and this is what broke.

    Both obvious choices — `plainto_tsquery` and `websearch_to_tsquery` — AND
    every term together. A passage inside the reader's own board pack does not
    repeat the company name, so the subject prefix alone made every real
    document search return nothing. The feature looked implemented and was
    inert, and only an end-to-end run showed it.
    """
    _ready_document(
        db_session,
        research_id,
        "board-pack.txt",
        [(0, "Our internal board pack records revenue of $1.4bn.", {"page": 3})],
    )

    result = await _search(factory, research_id, "Acme Corp: What is Acme's revenue?")

    assert result.items, (
        "the subject prefix suppressed the match; document search is ANDing "
        "its terms again"
    )


async def test_a_query_of_pure_punctuation_matches_nothing(
    db_session: Session, factory: sessionmaker[Session], research_id: UUID
) -> None:
    """Sanitising can empty a query that was not empty when it arrived.

    An empty tsquery matches nothing, so the honest answer is no results — not
    an error, and certainly not every chunk in the session.
    """
    _ready_document(
        db_session, research_id, "a.txt", [(0, "Revenue was $1.2bn.", {"page": 1})]
    )

    assert (await _search(factory, research_id, "!!! ??? ---")).is_empty


async def test_a_query_the_documents_do_not_answer_returns_nothing(
    db_session: Session, factory: sessionmaker[Session], research_id: UUID
) -> None:
    """Empty is a success, not a failure (`ToolResult.is_empty`).

    A search that legitimately found nothing is different from one that could
    not run, and the report treats the two differently.
    """
    _ready_document(
        db_session, research_id, "annual.txt", [(0, "Revenue was $1.2bn.", {"page": 1})]
    )

    result = await _search(factory, research_id, "helicopter procurement")
    assert result.is_empty


async def test_results_are_capped_by_the_budget(
    db_session: Session, factory: sessionmaker[Session], research_id: UUID
) -> None:
    _ready_document(
        db_session,
        research_id,
        "long.txt",
        [(n, f"Revenue in segment {n} grew.", {"line": n}) for n in range(20)],
    )

    result = await _search(factory, research_id, "revenue", limit=5)
    assert len(result.items) == 5


async def test_punctuation_in_a_model_written_query_does_not_raise(
    db_session: Session, factory: sessionmaker[Session], research_id: UUID
) -> None:
    """The query is assembled from a model-written question.

    `plainto_tsquery` would be fine too, but `websearch_to_tsquery` also never
    raises on malformed input — which matters when nobody hand-checks the
    string before it reaches Postgres.
    """
    _ready_document(
        db_session, research_id, "a.txt", [(0, "Revenue was $1.2bn.", {"page": 1})]
    )

    for query in ('Acme Corp: "revenue" & (2025) | -foo', "!!!", "AND OR NOT"):
        await _search(factory, research_id, query)


# --------------------------------------------------------------------------
# Scoping
# --------------------------------------------------------------------------


async def test_another_session_s_documents_are_not_searched(
    db_session: Session, factory: sessionmaker[Session], research_id: UUID
) -> None:
    """The most important assertion in this file.

    Uploads are private to a session. A search that leaked across sessions
    would put one user's document into another user's report as evidence.
    """
    other = ResearchSession(
        anonymous_session_id=db_session.query(AnonymousSession.id).scalar(),
        objective="An entirely different question, stated at length.",
        status="pending",
    )
    db_session.add(other)
    db_session.flush()

    _ready_document(
        db_session,
        other.id,
        "someone-elses.txt",
        [(0, "Revenue for the year was $9.9bn.", {"page": 1})],
    )

    result = await _search(factory, research_id, "revenue")
    assert result.is_empty


async def test_only_ready_documents_are_searched(
    db_session: Session, factory: sessionmaker[Session], research_id: UUID
) -> None:
    """A failed upload has no usable text and a processing one is incomplete.

    Citing half a document would be citing something that is still changing.
    """
    uploads = UploadRepository(db_session, **LIMITS)
    upload_id = _ready_document(
        db_session, research_id, "a.txt", [(0, "Revenue was $1.2bn.", {"page": 1})]
    )
    assert (await _search(factory, research_id, "revenue")).items

    upload = uploads.get(research_id, upload_id)
    assert upload is not None
    upload.processing_state = UploadState.FAILED
    db_session.flush()

    assert (await _search(factory, research_id, "revenue")).is_empty


async def test_a_deleted_document_is_not_searched(
    db_session: Session, factory: sessionmaker[Session], research_id: UUID
) -> None:
    uploads = UploadRepository(db_session, **LIMITS)
    upload_id = _ready_document(
        db_session, research_id, "a.txt", [(0, "Revenue was $1.2bn.", {"page": 1})]
    )
    upload = uploads.get(research_id, upload_id)
    assert upload is not None

    uploads.soft_delete(upload)

    assert (await _search(factory, research_id, "revenue")).is_empty


# --------------------------------------------------------------------------
# What an item carries
# --------------------------------------------------------------------------


async def test_an_item_names_the_file_and_the_place_in_it(
    db_session: Session, factory: sessionmaker[Session], research_id: UUID
) -> None:
    """`REQ-DOC-008 AC-2`: the citation shows the filename and where."""
    upload_id = _ready_document(
        db_session,
        research_id,
        "acme-annual-2025.pdf",
        [(0, "Revenue was $1.2bn.", {"page": 14})],
    )

    item = (await _search(factory, research_id, "revenue")).items[0]

    assert "acme-annual-2025.pdf" in item.source_name
    assert "page 14" in item.source_name
    assert item.source_category is SourceCategory.DOCUMENT
    assert item.source_url is None
    assert item.source_identifier == f"upload:{upload_id}:0"
    assert isinstance(item.content, Untrusted)
    assert item.structured is not None
    assert item.structured["upload_id"] == str(upload_id)


async def test_a_request_with_no_session_fails_rather_than_searching_everything(
    factory: sessionmaker[Session]
) -> None:
    """The dangerous failure mode, made impossible.

    A missing target that fell back to an unfiltered query would return every
    document in the database. It returns a failure instead.
    """
    outcome = await DocumentTool(session_factory=factory).invoke(
        ToolRequest(category=ToolCategory.DOCUMENTS, params={"query": "revenue"})
    )
    assert isinstance(outcome, ToolFailure)


async def test_a_malformed_session_id_fails_rather_than_raising(
    factory: sessionmaker[Session]
) -> None:
    outcome = await DocumentTool(session_factory=factory).invoke(
        ToolRequest(
            category=ToolCategory.DOCUMENTS,
            params={"query": "revenue", "session_id": "not-a-uuid"},
        )
    )
    assert isinstance(outcome, ToolFailure)


async def test_an_empty_query_fails_rather_than_returning_the_corpus(
    factory: sessionmaker[Session], research_id: UUID
) -> None:
    outcome = await DocumentTool(session_factory=factory).invoke(
        ToolRequest(
            category=ToolCategory.DOCUMENTS,
            params={"query": "  ", "session_id": str(research_id)},
        )
    )
    assert isinstance(outcome, ToolFailure)
    assert outcome.kind == "not_found"


# --------------------------------------------------------------------------
# The extraction worker
# --------------------------------------------------------------------------


@pytest.fixture
def processor(
    factory: sessionmaker[Session],
) -> Iterator[tuple[DocumentProcessor, ObjectStore]]:
    settings = _settings()
    store = ObjectStore(settings)
    try:
        store.stored_size("probe/does-not-exist")
    except Exception as exc:
        pytest.skip(f"no object storage reachable ({exc})")
    yield DocumentProcessor(factory, store, settings), store


def _pending_with_bytes(
    session: Session,
    store: ObjectStore,
    research_id: UUID,
    filename: str,
    data: bytes,
) -> UUID:
    """A row awaiting extraction, with its object really in the bucket."""
    uploads = UploadRepository(session, **LIMITS)
    key = f"uploads/{research_id}/{uuid4()}"
    upload = uploads.create_pending(
        session_id=research_id,
        filename=filename,
        content_type="text/plain",
        size_bytes=len(data),
        storage_key=key,
    )
    uploads.mark_uploaded(upload, size_bytes=len(data))

    import httpx

    httpx.put(
        store.presign_put(key, "text/plain").url,
        content=data,
        headers={"content-type": "text/plain"},
        timeout=10.0,
    )
    return upload.id


def test_extraction_reads_the_object_and_marks_it_ready(
    db_session: Session,
    research_id: UUID,
    processor: tuple[DocumentProcessor, ObjectStore],
) -> None:
    """The whole worker step, against real storage and a real database."""
    extractor, store = processor
    upload_id = _pending_with_bytes(
        db_session,
        store,
        research_id,
        "annual.txt",
        b"Acme Corp\n\nRevenue for the year was $1.2bn.\n",
    )

    result = extractor.run_one()

    assert result is not None
    assert result.upload_id == upload_id
    assert result.ready
    assert result.chunks >= 1

    uploads = UploadRepository(db_session, **LIMITS)
    upload = uploads.get(research_id, upload_id)
    assert upload is not None
    assert upload.processing_state is UploadState.READY
    # The hash is computed by the only component that holds the bytes.
    assert len(upload.sha256) == 64
    store.delete(upload.storage_key)


def test_a_file_whose_bytes_are_not_its_declared_type_fails_with_a_reason(
    db_session: Session,
    research_id: UUID,
    processor: tuple[DocumentProcessor, ObjectStore],
) -> None:
    """`DEC-14`. The presign check saw `text/plain` and had no bytes to look at.

    This is the first moment anyone can check, and the answer is a failed
    upload with a message — never a relabelled one that gets parsed as
    something it is not.
    """
    extractor, store = processor
    upload_id = _pending_with_bytes(
        db_session, store, research_id, "notes.txt", b"MZ\x90\x00\x03\x00\x00\x00binary"
    )

    result = extractor.run_one()

    assert result is not None
    assert not result.ready
    assert result.reason == "rejected_type"

    upload = UploadRepository(db_session, **LIMITS).get(research_id, upload_id)
    assert upload is not None
    assert upload.processing_state is UploadState.FAILED
    assert upload.error
    # Written for the user: no library name, no key, no stack.
    assert "Accepted:" in upload.error
    store.delete(upload.storage_key)


def test_a_file_with_no_readable_text_fails_rather_than_going_ready_empty(
    db_session: Session,
    research_id: UUID,
    processor: tuple[DocumentProcessor, ObjectStore],
) -> None:
    """`REQ-DOC-004 AC-3`, at the level that matters.

    An empty `ready` document would be searched, find nothing, and look exactly
    like a file that did not mention the subject. The user would never learn
    their scan was unreadable.
    """
    extractor, store = processor
    upload_id = _pending_with_bytes(
        db_session, store, research_id, "blank.txt", b"   \n\n   \n"
    )

    result = extractor.run_one()

    assert result is not None
    assert not result.ready

    upload = UploadRepository(db_session, **LIMITS).get(research_id, upload_id)
    assert upload is not None
    assert upload.processing_state is UploadState.FAILED
    assert upload.error is not None
    assert "scan" in upload.error
    store.delete(upload.storage_key)


def test_a_missing_object_fails_the_upload_rather_than_the_worker(
    db_session: Session,
    research_id: UUID,
    processor: tuple[DocumentProcessor, ObjectStore],
) -> None:
    """A completion that raced a deletion must not stop the extraction loop."""
    extractor, _ = processor
    uploads = UploadRepository(db_session, **LIMITS)
    upload = uploads.create_pending(
        session_id=research_id,
        filename="gone.txt",
        content_type="text/plain",
        size_bytes=10,
        storage_key=f"uploads/{research_id}/never-written",
    )
    uploads.mark_uploaded(upload, size_bytes=10)

    result = extractor.run_one()

    assert result is not None
    assert not result.ready
    # The processor writes in its own session, so this instance is stale.
    db_session.refresh(upload)
    assert upload.processing_state is UploadState.FAILED
    assert upload.error is not None
    # `REQ-SEC-010`: the user is told their file could not be read back, and
    # never the storage key, which is what the log line above carries.
    assert "uploads/" not in upload.error


def test_nothing_waiting_returns_none(
    processor: tuple[DocumentProcessor, ObjectStore]
) -> None:
    """So the worker loop can tell "no work" from "work done" and sleep."""
    extractor, _ = processor
    assert extractor.run_one() is None
