"""An uploaded document reaches the report (`REQ-DOC-005..008`, `REQ-INPUT-004`).

The test this phase most needs, because the failure it guards against is the one
that has happened repeatedly in this codebase: every layer works, and the value
stops one short of a reader. Uploads can extract correctly, index correctly and
be searchable correctly while no run ever asks for them — and every unit test
still passes.

So this runs the whole pipeline with a document attached and asserts on the
rows a report is rendered from: a `sources` row categorised as a document and
carrying its `upload_id`, evidence linked to it, and a claim whose confidence
did not go up because of it.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from uuid import UUID

import pytest
from pipeline_support import (
    REVENUE_EXCERPT,
    REVENUE_TEXT,
    fixture_registry,
    run_pipeline,
    scripted_provider,
    search_tool,
)
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from scrapr_core.db.enums import SourceCategory
from scrapr_core.db.models import (
    Base,
    Claim,
    ClaimEvidence,
    Evidence,
    Source,
)
from scrapr_core.db.repositories import (
    AnonymousSessionRepository,
    ResearchRepository,
    RunRepository,
    UploadRepository,
)
from scrapr_core.domain.ownership import OwnerContext
from scrapr_core.llm import FakeLLMProvider
from scrapr_core.orchestrator.extract import ExtractedEvidence, Extraction
from scrapr_core.orchestrator.pipeline import STAGES
from scrapr_core.orchestrator.synthesize import DraftClaim, DraftSection, SynthesisDraft
from scrapr_core.tools import ToolCategory, ToolRegistry
from scrapr_core.tools.impl.documents import DocumentTool

pytestmark = pytest.mark.integration

MB = 1024 * 1024
LIMITS = {
    "max_upload_bytes": 25 * MB,
    "max_uploads_per_session": 10,
    "max_session_upload_bytes": 100 * MB,
}

OBJECTIVE = "How is Acme Corp performing?"
QUESTION = "What is Acme's revenue?"
AREAS = (("Financial performance", (QUESTION,), ("web_search",)),)

DOCUMENT_TEXT = "Our internal board pack records revenue of $1.4bn for fiscal 2025."
DOCUMENT_EXCERPT = "revenue of $1.4bn"


@pytest.fixture
def session_factory(migrated_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=migrated_engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
def _empty_afterwards(session_factory: sessionmaker[Session]) -> Iterator[None]:
    yield
    tables = ", ".join(sorted(Base.metadata.tables))
    with session_factory() as session:
        session.execute(text(f"TRUNCATE {tables} CASCADE"))
        session.commit()


@pytest.fixture
def registry(session_factory: sessionmaker[Session]) -> ToolRegistry:
    """Web search plus the real documents tool, as the worker registers them."""
    built = ToolRegistry()
    built.register(search_tool())
    built.register(DocumentTool(session_factory=session_factory))
    built.freeze()
    return built


def _provider() -> FakeLLMProvider:
    """Extraction that pulls a figure from whichever item it is shown.

    One candidate per (item index, excerpt) pair. `_ground` keeps only those
    whose excerpt genuinely appears in that item's content and silently drops
    the rest, so this covers a round containing web results and document chunks
    in any order — without the test depending on the order the tools ran in,
    which is not the behaviour under test.
    """
    provider = scripted_provider("Acme Corp", (QUESTION,), AREAS)
    provider._standing = Extraction(
        evidence=[
            ExtractedEvidence(
                statement=statement, excerpt=excerpt, item_index=index
            )
            for index in range(4)
            for statement, excerpt in (
                ("Acme reported $1.2bn revenue for FY2025.", REVENUE_EXCERPT),
                ("The board pack records $1.4bn revenue for FY2025.", DOCUMENT_EXCERPT),
            )
        ]
    )
    return provider


def _start(session_factory: sessionmaker[Session], *, with_document: bool) -> UUID:
    """Everything intake does: create the session, attach a file, enqueue."""
    with session_factory() as session:
        owner = OwnerContext.for_anonymous(
            AnonymousSessionRepository(session).issue().session.id
        )
        research = ResearchRepository(session, owner)
        created = research.create_session(objective=OBJECTIVE)
        version = research.open_version(created.id)
        assert version is not None

        if with_document:
            uploads = UploadRepository(session, **LIMITS)
            upload = uploads.create_pending(
                session_id=created.id,
                filename="board-pack.txt",
                content_type="text/plain",
                size_bytes=len(DOCUMENT_TEXT),
                storage_key=f"uploads/{created.id}/board-pack.txt",
            )
            uploads.mark_uploaded(upload, size_bytes=len(DOCUMENT_TEXT))
            uploads.mark_ready(upload, [(0, DOCUMENT_TEXT, {"page": 3})])

        RunRepository(session).create_run(created.id, version.id, STAGES)
        session.commit()
        return created.id


def _document_sources(session: Session) -> Sequence[Source]:
    return (
        session.execute(
            select(Source).where(Source.category == SourceCategory.DOCUMENT)
        )
        .scalars()
        .all()
    )


# --------------------------------------------------------------------------
# The path is not dead
# --------------------------------------------------------------------------


async def test_an_attached_document_becomes_a_source_in_the_report(
    session_factory: sessionmaker[Session], registry: ToolRegistry
) -> None:
    """`REQ-INPUT-004 AC-2` and `REQ-DOC-006 AC-1`, at the level that counts.

    Documents are excluded from *planning* because they cannot answer "tell me
    about Acme Corp". Nothing else in the pipeline would notice if they were
    also excluded from retrieval — which is precisely how a whole feature ships
    inert.
    """
    _start(session_factory, with_document=True)

    await run_pipeline(session_factory, registry, _provider())

    with session_factory() as session:
        documents = _document_sources(session)

        assert documents, (
            "no document source was written: the upload was indexed and the "
            "run never asked for it"
        )
        assert "board-pack.txt" in documents[0].name
        # `REQ-DOC-008 AC-2`: the citation resolves to a place in the file.
        assert "page 3" in documents[0].name
        assert documents[0].upload_id is not None
        assert documents[0].url is None


async def test_a_session_with_no_documents_gains_no_document_source(
    session_factory: sessionmaker[Session], registry: ToolRegistry
) -> None:
    """The other half. The documents tool is registered for every run, so
    without the `has_ready_documents` check every session would query an empty
    index and report a gap that is not one."""
    _start(session_factory, with_document=False)

    await run_pipeline(session_factory, registry, _provider())

    with session_factory() as session:
        assert _document_sources(session) == []


async def test_document_and_web_evidence_sit_in_one_report(
    session_factory: sessionmaker[Session], registry: ToolRegistry
) -> None:
    """`REQ-DOC-006 AC-1`: one section, both kinds of evidence."""
    _start(session_factory, with_document=True)

    await run_pipeline(session_factory, registry, _provider())

    with session_factory() as session:
        categories = set(
            session.execute(select(Source.category)).scalars().all()
        )

    assert SourceCategory.DOCUMENT in categories
    assert SourceCategory.WEB in categories


# --------------------------------------------------------------------------
# It does not corroborate
# --------------------------------------------------------------------------


async def test_a_document_is_not_counted_as_a_distinct_source(
    session_factory: sessionmaker[Session], registry: ToolRegistry
) -> None:
    """`REQ-DOC-008 AC-3`, read back off the stored claim.

    `confidence_inputs` is what the citation panel explains a claim from, so
    this is the number a reader is shown. Counting the document here would tell
    them their claim rests on one more independent source than it does.
    """
    _start(session_factory, with_document=True)

    await run_pipeline(
        session_factory, registry, _provider(), synthesis=_cite_all
    )

    with session_factory() as session:
        claims = session.execute(select(Claim)).scalars().all()
        assert claims

        for claim in claims:
            cited = _cited_sources(session, claim.id)
            documents = [
                source
                for source in cited
                if source.category is SourceCategory.DOCUMENT
            ]
            if not documents:
                continue

            inputs = claim.confidence_inputs
            assert isinstance(inputs, dict)
            assert inputs["document_sources"] == len(documents)
            # The document is in `cited` and out of the corroboration count.
            assert inputs["distinct_sources"] == len(cited) - len(documents)
            return

    pytest.fail("no claim cited a document, so the rule was never exercised")


def _cite_all(evidence_ids: Sequence[UUID]) -> SynthesisDraft:
    """A report citing every piece of evidence gathered.

    The default harness synthesis cites only the first, which would leave the
    document out of the claim by accident and make the assertion above vacuous.
    """
    cited = [str(identifier) for identifier in evidence_ids]
    return SynthesisDraft(
        summary=[
            DraftClaim(
                text="Acme reported revenue growth in FY2025.",
                claim_type="fact",
                evidence_ids=cited,
                is_important=True,
            )
        ],
        sections=[
            DraftSection(
                title="Revenue",
                claims=[
                    DraftClaim(
                        text="Acme reported revenue for FY2025.",
                        claim_type="fact",
                        evidence_ids=cited,
                    )
                ],
            )
        ],
    )


def _cited_sources(session: Session, claim_id: UUID) -> Sequence[Source]:
    return (
        session.execute(
            select(Source)
            .join(Evidence, Evidence.source_id == Source.id)
            .join(ClaimEvidence, ClaimEvidence.evidence_id == Evidence.id)
            .where(ClaimEvidence.claim_id == claim_id)
        )
        .scalars()
        .unique()
        .all()
    )


# --------------------------------------------------------------------------
# It is still evidence
# --------------------------------------------------------------------------


async def test_the_documents_category_is_never_planned(
    session_factory: sessionmaker[Session], registry: ToolRegistry
) -> None:
    """It is appended to every area that has documents, never chosen for one.

    A planner that could pick it would produce an area researched entirely from
    the reader's own file, which is not research.
    """
    assert ToolCategory.DOCUMENTS in registry.categories()
    assert ToolCategory.DOCUMENTS not in registry.plannable_categories()


async def test_a_processing_document_is_not_searched_mid_run(
    session_factory: sessionmaker[Session], registry: ToolRegistry
) -> None:
    """A file still extracting must not be half-cited.

    This is why `POST /v1/research` takes `defer_start`: the run waits for the
    upload rather than racing it. Here the upload never becomes ready, and the
    run proceeds without it instead of stalling or citing a partial document.
    """
    with session_factory() as session:
        owner = OwnerContext.for_anonymous(
            AnonymousSessionRepository(session).issue().session.id
        )
        research = ResearchRepository(session, owner)
        created = research.create_session(objective=OBJECTIVE)
        version = research.open_version(created.id)
        assert version is not None

        uploads = UploadRepository(session, **LIMITS)
        upload = uploads.create_pending(
            session_id=created.id,
            filename="still-reading.txt",
            content_type="text/plain",
            size_bytes=10,
            storage_key=f"uploads/{created.id}/still-reading.txt",
        )
        uploads.mark_uploaded(upload, size_bytes=10)

        RunRepository(session).create_run(created.id, version.id, STAGES)
        session.commit()

    await run_pipeline(session_factory, registry, _provider())

    with session_factory() as session:
        assert _document_sources(session) == []
        # The run still produced a report from what it could reach.
        assert session.execute(select(Claim)).scalars().all()


def test_the_fixtures_still_describe_what_they_claim() -> None:
    """The web fixture and the document disagree, on purpose.

    $1.2bn against $1.4bn is what makes `REQ-DOC-007` reachable from this
    harness. If either figure were changed to match, the conflict tests below
    would pass while testing nothing.
    """
    assert "1.2bn" in REVENUE_TEXT
    assert "1.4bn" in DOCUMENT_TEXT
    assert REVENUE_EXCERPT in REVENUE_TEXT
    assert DOCUMENT_EXCERPT in DOCUMENT_TEXT
    assert fixture_registry().is_frozen
