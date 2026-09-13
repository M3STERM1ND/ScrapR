"""A hostile document, carried the whole way (`REQ-DOC-009`, `REQ-SEC-013..015`).

`REQ-DOC-009 AC-3` asks for an explicit adversarial test case, and "explicit"
has to mean the real path: a file of bytes, extracted by the real extractor,
stored in the real table, retrieved by the real tool, and handed to the real
extraction stage. A test that constructs an `Untrusted` by hand and asserts it
raises on `str()` proves the type works. It does not prove the document pipeline
uses it, and that is the part that can regress.

Each test names the property it is defending, because "test injection" is not a
property and a suite of those tells you nothing when one of them goes red.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Final
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from tests.adversarial.payloads import PAYLOADS, Payload

from scrapr_core.config import Settings
from scrapr_core.db.enums import (
    AuthorityTier,
    SourceCategory,
    UploadState,
    VersionStatus,
)
from scrapr_core.db.models import (
    AnonymousSession,
    ResearchSession,
    ResearchVersion,
    Source,
    Upload,
    UploadChunk,
)
from scrapr_core.db.repositories.evidence import EvidenceRepository
from scrapr_core.db.repositories.uploads import UploadRepository
from scrapr_core.llm.fake import FakeLLMProvider
from scrapr_core.orchestrator.extract import Extraction, extract_from_items
from scrapr_core.security.trust import Untrusted, UntrustedContentError
from scrapr_core.storage.extract import detect_content_type, extract_chunks
from scrapr_core.tools.builder import build_registry
from scrapr_core.tools.contract import (
    ToolBudget,
    ToolCategory,
    ToolRequest,
    ToolResult,
)
from scrapr_core.tools.impl.documents import DocumentTool
from scrapr_core.tools.registry import RegistryFrozenError

pytestmark = pytest.mark.adversarial

_LIMITS: Final = {
    "max_upload_bytes": 25 * 1024 * 1024,
    "max_uploads_per_session": 10,
    "max_session_upload_bytes": 100 * 1024 * 1024,
}

_SUBJECT: Final = "Acme Corp"


def _owned_session(session: Session) -> ResearchSession:
    """A research session with an owner, because the schema requires one."""
    anonymous = AnonymousSession(token_hash=f"adversarial-{id(session)}")
    session.add(anonymous)
    session.flush()

    research = ResearchSession(
        anonymous_session_id=anonymous.id,
        objective="How did Acme Corp perform last year?",
        status="pending",
    )
    session.add(research)
    session.flush()
    return research


def _document_bytes(payload: Payload) -> bytes:
    """A plausible business document with the payload buried inside it.

    Buried rather than alone: a file that is *only* an attack is the easy case.
    The realistic one is a genuine report a contributor has tampered with, where
    the extractor has real content to return and the payload rides along with
    it.
    """
    return (
        "Acme Corp Annual Summary\n\n"
        "Revenue for the year was $1.2bn, up 18 percent.\n\n"
        f"{payload.text}\n\n"
        "Headcount closed the year at 4,100.\n"
    ).encode()


@pytest.fixture
def uploaded(db_session: Session) -> Iterator[tuple[ResearchSession, sessionmaker[Session]]]:
    """A research session and a factory bound to the test's transaction.

    The documents tool opens its own session, so it is given a factory bound to
    the same connection — otherwise it would query outside the test's
    transaction and see none of the rows the test just wrote.
    """
    research = _owned_session(db_session)
    factory = sessionmaker(bind=db_session.connection(), expire_on_commit=False)
    yield research, factory


def _store_document(
    session: Session, research_id: UUID, payload: Payload
) -> Upload:
    """Run the real extractor over hostile bytes and store what it produced."""
    uploads = UploadRepository(session, **_LIMITS)
    data = _document_bytes(payload)

    detected = detect_content_type(data, "acme-annual-summary.txt")
    assert detected == "text/plain"

    upload = uploads.create_pending(
        session_id=research_id,
        filename="acme-annual-summary.txt",
        content_type=detected,
        size_bytes=len(data),
        storage_key=f"uploads/{research_id}/adversarial.txt",
    )
    uploads.mark_uploaded(upload, size_bytes=len(data))
    uploads.mark_ready(
        upload,
        [(chunk.ordinal, chunk.text, chunk.locator) for chunk in extract_chunks(data, detected)],
        sha256="0" * 64,
        content_type=detected,
    )
    return upload


# --------------------------------------------------------------------------
# The payload survives as evidence, and only as evidence
# --------------------------------------------------------------------------


@pytest.mark.parametrize("payload", PAYLOADS, ids=lambda item: item.name)
def test_payload_is_stored_as_data_not_dropped(
    db_session: Session,
    uploaded: tuple[ResearchSession, sessionmaker[Session]],
    payload: Payload,
) -> None:
    """The document is processed as evidence (`REQ-DOC-009 AC-2`).

    Silently discarding the payload would look like security and would not be:
    a document that says "ignore your instructions" is a fact about that
    document, and a reader is entitled to see that their file contains it. The
    defence is that it never becomes an instruction, not that it never exists.
    """
    research, _ = uploaded
    upload = _store_document(db_session, research.id, payload)

    chunks = (
        db_session.execute(
            select(UploadChunk).where(UploadChunk.upload_id == upload.id)
        )
        .scalars()
        .all()
    )

    assert chunks, "the document was rejected outright instead of processed"
    assert any(payload.marker in chunk.text for chunk in chunks), (
        "the payload was stripped; it must survive as data so the reader can "
        "see what their document contains"
    )
    assert upload.processing_state is UploadState.READY


@pytest.mark.parametrize("payload", PAYLOADS, ids=lambda item: item.name)
async def test_retrieved_payload_is_untrusted(
    db_session: Session,
    uploaded: tuple[ResearchSession, sessionmaker[Session]],
    payload: Payload,
) -> None:
    """Retrieval wraps document text in `Untrusted` (`REQ-DOC-009 AC-1`).

    The tool is the boundary. If it returned a `str`, every downstream type
    check would pass and the payload would be one f-string away from an
    instruction channel.
    """
    research, factory = uploaded
    _store_document(db_session, research.id, payload)

    tool = DocumentTool(session_factory=factory)
    outcome = await tool.invoke(
        ToolRequest(
            category=ToolCategory.DOCUMENTS,
            params={"query": "revenue", "session_id": str(research.id)},
            budget=ToolBudget(max_results=10),
        )
    )

    assert isinstance(outcome, ToolResult)
    assert outcome.items, "the document was indexed but returned nothing"

    for item in outcome.items:
        assert isinstance(item.content, Untrusted)
        # The escape hatch is a property read, and nothing else works.
        with pytest.raises(UntrustedContentError):
            str(item.content)
        with pytest.raises(UntrustedContentError):
            f"{item.content}"
        # `repr` is the sanctioned logging path and must not leak the payload.
        assert payload.marker not in repr(item.content)


@pytest.mark.parametrize("payload", PAYLOADS, ids=lambda item: item.name)
async def test_payload_reaches_the_model_only_inside_the_envelope(
    db_session: Session,
    uploaded: tuple[ResearchSession, sessionmaker[Session]],
    payload: Payload,
) -> None:
    """The instruction stays ours (`REQ-SEC-012 AC-1`, implementation plan §9).

    This is the assertion the whole trust boundary exists to make true: after a
    hostile document has travelled the entire retrieval path, its text appears
    in the untrusted half of the model request and nowhere else.
    """
    research, factory = uploaded
    _store_document(db_session, research.id, payload)

    tool = DocumentTool(session_factory=factory)
    outcome = await tool.invoke(
        ToolRequest(
            category=ToolCategory.DOCUMENTS,
            params={"query": "revenue", "session_id": str(research.id)},
        )
    )
    assert isinstance(outcome, ToolResult)

    provider = FakeLLMProvider()
    provider.enqueue(Extraction(evidence=[]))
    await extract_from_items(list(outcome.items), "What was revenue?", provider)

    assert provider.calls, "extraction never reached the provider"
    for call in provider.calls:
        assert payload.marker not in call.instruction, (
            f"{payload.name} reached the instruction channel: {payload.goal}"
        )
        assert payload.marker in call.rendered, (
            "the payload should be present as fenced material; if it is absent "
            "the test is no longer exercising anything"
        )


@pytest.mark.parametrize("payload", PAYLOADS, ids=lambda item: item.name)
async def test_payload_cannot_forge_its_own_provenance(
    db_session: Session,
    uploaded: tuple[ResearchSession, sessionmaker[Session]],
    payload: Payload,
) -> None:
    """Content does not get to say what kind of source it is.

    `confidence-forgery` asks to be treated as a primary filing. Category and
    tier are set by the tool that retrieved the item and by a static rule table
    (`DEC-08`), neither of which reads the content — so the request is inert by
    construction rather than by anyone noticing it.
    """
    research, factory = uploaded
    _store_document(db_session, research.id, payload)

    tool = DocumentTool(session_factory=factory)
    outcome = await tool.invoke(
        ToolRequest(
            category=ToolCategory.DOCUMENTS,
            params={"query": "revenue", "session_id": str(research.id)},
        )
    )
    assert isinstance(outcome, ToolResult)

    for item in outcome.items:
        assert item.source_category is SourceCategory.DOCUMENT
        assert item.source_url is None, (
            "a document must not acquire a URL: a URL is what tiering reads"
        )


# --------------------------------------------------------------------------
# The payload cannot reach machinery
# --------------------------------------------------------------------------


def test_document_content_cannot_add_a_tool(db_session: Session) -> None:
    """`REQ-SEC-015 AC-1`: availability is fixed before any content is read.

    `tool-injection` names `export_credentials`. The registry is frozen at
    startup, so even a stage that was talked into registering something would
    fail — and the name it asked for was never registered in the first place.
    """
    factory = sessionmaker(bind=db_session.connection(), expire_on_commit=False)
    registry, _ = build_registry(_settings(), factory)

    assert registry.is_frozen
    with pytest.raises(RegistryFrozenError):
        registry.register(DocumentTool(session_factory=factory, name="export_credentials"))


def test_documents_are_not_plannable(db_session: Session) -> None:
    """A document cannot be chosen as a research strategy.

    Not a security property so much as a correctness one, but it belongs here:
    if content could steer planning, "research this by reading my file and
    nothing else" would be a sentence an attacker could write into the file.
    """
    factory = sessionmaker(bind=db_session.connection(), expire_on_commit=False)
    registry, _ = build_registry(_settings(), factory)

    assert ToolCategory.DOCUMENTS in registry.categories()
    assert ToolCategory.DOCUMENTS not in registry.plannable_categories()


@pytest.mark.parametrize("payload", PAYLOADS, ids=lambda item: item.name)
async def test_recorded_source_is_attributed_to_the_upload(
    db_session: Session,
    uploaded: tuple[ResearchSession, sessionmaker[Session]],
    payload: Payload,
) -> None:
    """`REQ-DOC-008 AC-2`: the stored citation names the file.

    The attack here is indirect. A payload whose content got attributed to
    Reuters would inherit Reuters' tier, and the confidence machinery would do
    the rest. Attribution is written from the retrieving tool's own metadata, so
    the content has no say in it — this asserts the row that actually lands in
    the database, because the tool getting it right and the repository dropping
    it would look identical everywhere except here.
    """
    research, factory = uploaded
    upload = _store_document(db_session, research.id, payload)

    version = _open_version(db_session, research.id)

    tool = DocumentTool(session_factory=factory)
    outcome = await tool.invoke(
        ToolRequest(
            category=ToolCategory.DOCUMENTS,
            params={"query": "revenue", "session_id": str(research.id)},
        )
    )
    assert isinstance(outcome, ToolResult)
    assert outcome.items

    repository = EvidenceRepository(db_session)
    recorded = repository.record(
        version.id,
        outcome.items[0],
        statement="Revenue for the year was $1.2bn.",
        excerpt="Revenue for the year was $1.2bn",
    )

    source = db_session.get(Source, recorded.source_id)
    assert source is not None
    assert source.category is SourceCategory.DOCUMENT
    assert source.upload_id == upload.id, (
        "the upload id did not reach the source row, so a citation cannot say "
        "which of the reader's files it came from"
    )
    assert source.url is None
    assert source.authority_tier is AuthorityTier.LOWER, (
        "a document must not be tiered above an unlisted web source; DEC-08 "
        "tiers on host and category, and a document has neither claim to "
        "authority"
    )


def _open_version(session: Session, research_id: UUID) -> ResearchVersion:
    version = ResearchVersion(
        session_id=research_id,
        version_number=1,
        status=VersionStatus.BUILDING,
    )
    session.add(version)
    session.flush()
    return version


def _settings() -> Settings:
    """Defaults only. Reading the developer's `.env` would make this suite's
    result depend on which keys happen to be configured on one machine."""
    return Settings(_env_file=None)  # type: ignore[call-arg]
