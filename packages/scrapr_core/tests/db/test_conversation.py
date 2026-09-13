"""Conversation persistence (`REQ-CONV-007`).

The repository was at 50% coverage when the conversation first worked end to
end, which in this codebase has meant the same thing six times running: a path
that is correct and unreached. So these exercise the parts a single happy-path
request never touches — the second turn, the ordering, the citation rows, and
the sequence allocation that only matters when there is already a transcript.

`AC-2` is the requirement that makes all of it load-bearing: returning to saved
research restores the conversation. Everything here is about what comes back,
not about what was sent.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from scrapr_core.db.enums import (
    Accessibility,
    AuthorityTier,
    MessageRole,
    NormalizationStatus,
    SourceCategory,
)
from scrapr_core.db.models import Base, Evidence, Source
from scrapr_core.db.repositories import (
    AnonymousSessionRepository,
    ConversationRepository,
    ResearchRepository,
)
from scrapr_core.domain.ownership import OwnerContext

pytestmark = pytest.mark.integration


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
def research(session_factory: sessionmaker[Session]) -> tuple[UUID, UUID]:
    """A session and a version for messages to hang off."""
    with session_factory() as session:
        owner = OwnerContext.for_anonymous(
            AnonymousSessionRepository(session).issue().session.id
        )
        repository = ResearchRepository(session, owner)
        created = repository.create_session(objective="How is Acme performing?")
        version = repository.open_version(created.id)
        assert version is not None
        session.commit()
        return created.id, version.id


def add_evidence(session: Session, version_id: UUID) -> Evidence:
    source = Source(
        version_id=version_id,
        url="https://reuters.com/acme",
        url_normalized="https://reuters.com/acme",
        name="reuters.com",
        category=SourceCategory.WEB,
        authority_tier=AuthorityTier.SECONDARY,
        tier_rationale={"rule": "publisher"},
        retrieved_at=dt.datetime.now(dt.UTC),
        accessibility=Accessibility.ACCESSIBLE,
    )
    session.add(source)
    session.flush()

    evidence = Evidence(
        version_id=version_id,
        source_id=source.id,
        content="Acme reported $1.2bn revenue for FY2025.",
        excerpt="revenue of $1.2bn",
        normalization=NormalizationStatus.NORMALIZED,
        extracted_at=dt.datetime.now(dt.UTC),
    )
    session.add(evidence)
    session.flush()
    return evidence


# --------------------------------------------------------------------------


def test_a_turn_comes_back_after_the_write(
    session_factory: sessionmaker[Session], research: tuple[UUID, UUID]
) -> None:
    """`AC-2`: returning to saved research restores the conversation."""
    session_id, version_id = research

    with session_factory() as session:
        ConversationRepository(session).append(
            session_id, version_id, MessageRole.USER, "Why is growth strong?"
        )
        session.commit()

    with session_factory() as session:
        turns = ConversationRepository(session).for_session(session_id)

    assert [turn.content for turn in turns] == ["Why is growth strong?"]


def test_turns_come_back_in_the_order_they_were_asked(
    session_factory: sessionmaker[Session], research: tuple[UUID, UUID]
) -> None:
    """A transcript out of order is a different conversation."""
    session_id, version_id = research

    with session_factory() as session:
        conversation = ConversationRepository(session)
        for index in range(5):
            conversation.append(
                session_id,
                version_id,
                MessageRole.USER if index % 2 == 0 else MessageRole.AGENT,
                f"turn {index}",
            )
        session.commit()

    with session_factory() as session:
        turns = ConversationRepository(session).for_session(session_id)

    assert [turn.seq for turn in turns] == [1, 2, 3, 4, 5]
    assert [turn.content for turn in turns] == [f"turn {index}" for index in range(5)]


def test_the_sequence_continues_across_requests(
    session_factory: sessionmaker[Session], research: tuple[UUID, UUID]
) -> None:
    """The case a single happy-path request never reaches.

    Each turn is its own transaction in production, so the allocator has to
    read what is already there rather than counting within one unit of work.
    """
    session_id, version_id = research

    for index in range(3):
        with session_factory() as session:
            ConversationRepository(session).append(
                session_id, version_id, MessageRole.USER, f"question {index}"
            )
            session.commit()

    with session_factory() as session:
        turns = ConversationRepository(session).for_session(session_id)

    assert [turn.seq for turn in turns] == [1, 2, 3]


def test_an_answer_keeps_the_evidence_it_cited(
    session_factory: sessionmaker[Session], research: tuple[UUID, UUID]
) -> None:
    """`REQ-CONV-008 AC-1`, as a row rather than a marker in the prose.

    A citation stored in the text would not survive the answer being
    re-rendered, and could not be resolved against the same evidence the report
    cites.
    """
    session_id, version_id = research

    with session_factory() as session:
        evidence = add_evidence(session, version_id)
        message = ConversationRepository(session).append(
            session_id,
            version_id,
            MessageRole.AGENT,
            "Revenue was $1.2bn.",
            evidence_ids=[evidence.id],
        )
        session.commit()
        message_id, evidence_id = message.id, evidence.id

    with session_factory() as session:
        cited = ConversationRepository(session).evidence_ids_for(message_id)

    assert list(cited) == [evidence_id]


def test_a_repeated_citation_is_stored_once(
    session_factory: sessionmaker[Session], research: tuple[UUID, UUID]
) -> None:
    """The link table is keyed on the pair, so a model citing the same id twice
    would otherwise violate the primary key and fail the whole answer."""
    session_id, version_id = research

    with session_factory() as session:
        evidence = add_evidence(session, version_id)
        message = ConversationRepository(session).append(
            session_id,
            version_id,
            MessageRole.AGENT,
            "Revenue was $1.2bn.",
            evidence_ids=[evidence.id, evidence.id, evidence.id],
        )
        session.commit()
        message_id = message.id

    with session_factory() as session:
        cited = ConversationRepository(session).evidence_ids_for(message_id)

    assert len(cited) == 1


def test_recent_turns_are_bounded_and_oldest_first(
    session_factory: sessionmaker[Session], research: tuple[UUID, UUID]
) -> None:
    """The prompt window. Unbounded, the transcript would push the evidence —
    the part that actually grounds the answer — further from the model's
    attention with every turn."""
    session_id, version_id = research

    with session_factory() as session:
        conversation = ConversationRepository(session)
        for index in range(10):
            conversation.append(
                session_id, version_id, MessageRole.USER, f"turn {index}"
            )
        session.commit()

    with session_factory() as session:
        recent = ConversationRepository(session).recent_turns(session_id, limit=4)

    assert len(recent) == 4
    assert [content for _, content in recent] == [
        "turn 6",
        "turn 7",
        "turn 8",
        "turn 9",
    ]


def test_the_context_the_reader_pointed_at_is_kept(
    session_factory: sessionmaker[Session], research: tuple[UUID, UUID]
) -> None:
    """`REQ-CONV-007 AC-1`: the message records the relevant research context.

    A question asked while looking at one claim means something different from
    the same words typed in the abstract.
    """
    session_id, version_id = research
    claim_id = str(uuid4())

    with session_factory() as session:
        ConversationRepository(session).append(
            session_id,
            version_id,
            MessageRole.USER,
            "Why this one?",
            context_ref={"claim_id": claim_id},
        )
        session.commit()

    with session_factory() as session:
        turn = ConversationRepository(session).for_session(session_id)[0]

    assert turn.context_ref == {"claim_id": claim_id}


def test_a_message_records_the_version_it_was_answered_from(
    session_factory: sessionmaker[Session], research: tuple[UUID, UUID]
) -> None:
    """A follow-up asked before an Update Research was answered from different
    evidence than the same question asked after, and `REQ-VER-002` makes the
    earlier version immutable — so the answer stays checkable against what it
    actually read."""
    session_id, version_id = research

    with session_factory() as session:
        ConversationRepository(session).append(
            session_id, version_id, MessageRole.AGENT, "An answer."
        )
        session.commit()

    with session_factory() as session:
        turn = ConversationRepository(session).for_session(session_id)[0]

    assert turn.version_id == version_id
