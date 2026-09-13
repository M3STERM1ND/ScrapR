"""The HTTP surface over the research pipeline.

`POST /v1/research` writes a session, a version and a run; the runner executes
the stages; `GET /v1/research/{id}/versions/1` returns the report and
`GET .../activity` returns the timeline. Everything below the HTTP layer is real
— real repositories, real database, real runner, real stages — because a test
that mocks the layer underneath only proves the mock.

The other half of this file is the boundary's own rules: ownership resolved from
a cookie, another owner's research reading as absent, and errors that say
nothing internal.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pipeline_support import (
    fixture_registry,
    run_pipeline,
    scripted_provider,
)
from pipeline_support import (
    search_tool as fixture_search_tool,
)
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from scrapr_api import deps
from scrapr_api.deps import ANONYMOUS_COOKIE
from scrapr_core.db.enums import RunStatus, StepStatus
from scrapr_core.db.models import ResearchRun
from scrapr_core.db.repositories import ResearchRepository, RunRepository
from scrapr_core.llm import FakeLLMProvider
from scrapr_core.orchestrator.pipeline import STAGES
from scrapr_core.tools import ToolCategory, ToolRegistry
from scrapr_core.tools.impl import FailingFixtureTool

pytestmark = pytest.mark.integration

OBJECTIVE = "How is Acme Corp positioned against its competitors?"
QUESTION = "What is Acme's revenue?"
HIRING_QUESTION = "How many roles is Acme hiring for?"
AREAS = (("Financial performance", (QUESTION,), ("web_search",)),)


@pytest.fixture
def registry() -> ToolRegistry:
    return fixture_registry()


@pytest.fixture
def provider() -> FakeLLMProvider:
    return scripted_provider("Acme Corp", (QUESTION,), AREAS)


def start(client: TestClient, objective: str = OBJECTIVE) -> dict[str, str]:
    response = client.post("/v1/research", json={"objective": objective})
    assert response.status_code == 202, response.text
    body: dict[str, str] = response.json()
    return body


# --------------------------------------------------------------------------
# Creating research
# --------------------------------------------------------------------------


def test_creating_research_is_accepted_not_completed(client: TestClient) -> None:
    """202: the answer does not exist yet (`REQ-AGENT-008 AC-1`). A 200 here
    would be a promise the system cannot keep."""
    response = client.post("/v1/research", json={"objective": OBJECTIVE})

    assert response.status_code == 202
    body = response.json()
    assert body["version_number"] == 1
    assert body["session_id"] != body["version_id"]


def test_a_first_request_is_given_an_anonymous_session(client: TestClient) -> None:
    """`REQ-AUTH-001`: research starts without an account."""
    response = client.post("/v1/research", json={"objective": OBJECTIVE})

    cookie = response.cookies.get(ANONYMOUS_COOKIE)
    assert cookie is not None
    assert cookie not in response.text


def test_the_session_token_is_not_readable_by_scripts(client: TestClient) -> None:
    """HttpOnly, because a token JavaScript can read is a token an injected
    script can exfiltrate."""
    response = client.post("/v1/research", json={"objective": OBJECTIVE})

    header = response.headers["set-cookie"].lower()
    assert "httponly" in header
    assert "samesite=lax" in header
    assert "secure" in header


def test_a_second_request_reuses_the_same_session(client: TestClient) -> None:
    first = start(client)
    second = start(client, objective="A different question about the same company")

    listed = client.get("/v1/research/" + first["session_id"])
    assert listed.status_code == 200
    assert client.get("/v1/research/" + second["session_id"]).status_code == 200


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"objective": "short"},
        {"objective": "x" * 5000},
    ],
)
def test_an_invalid_objective_is_refused_without_echoing_it(
    client: TestClient, payload: dict[str, str]
) -> None:
    """`REQ-INPUT-006 AC-4`: the input may be hostile, so it is not reflected
    back in the error."""
    response = client.post("/v1/research", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    assert "x" * 5000 not in response.text


# --------------------------------------------------------------------------
# Reading it back
# --------------------------------------------------------------------------


async def test_the_version_reads_back_once_the_run_completes(
    client: TestClient,
    registry: ToolRegistry,
    provider: FakeLLMProvider,
    session_factory: sessionmaker[Session],
) -> None:
    created = start(client)

    await run_pipeline(session_factory, registry, provider)

    response = client.get(f"/v1/research/{created['session_id']}/versions/1")
    assert response.status_code == 200
    body = response.json()

    assert body["status"] == "complete"

    # `REQ-SYNTH-003`: the report opens with the executive summary, and
    # `REQ-SYNTH-005`: the order is explicit and stable.
    assert body["sections"][0]["is_executive_summary"]
    assert [section["ordering"] for section in body["sections"]] == list(
        range(len(body["sections"]))
    )
    assert "Revenue" in [section["title"] for section in body["sections"]]

    # Every claim belongs to a section, and every fact resolves to the source
    # behind it with the time it was read (`REQ-EVID-004`).
    placed = {
        claim_id
        for section in body["sections"]
        for claim_id in section["claim_ids"]
    }
    assert placed == {claim["id"] for claim in body["claims"]}

    facts = [claim for claim in body["claims"] if claim["claim_type"] == "fact"]
    assert facts
    known_sources = {source["id"] for source in body["sources"]}
    assert all(set(fact["source_ids"]) <= known_sources for fact in facts)
    assert all(fact["source_ids"] for fact in facts)
    assert body["sources"][0]["retrieved_at"] is not None


async def test_the_session_header_lists_its_versions(
    client: TestClient,
    registry: ToolRegistry,
    provider: FakeLLMProvider,
    session_factory: sessionmaker[Session],
) -> None:
    created = start(client)
    await run_pipeline(session_factory, registry, provider)

    body = client.get(f"/v1/research/{created['session_id']}").json()

    assert body["objective"] == OBJECTIVE
    assert [version["version_number"] for version in body["versions"]] == [1]
    assert body["current_version_id"] == created["version_id"]


async def test_activity_polls_forward_from_a_sequence_number(
    client: TestClient,
    registry: ToolRegistry,
    provider: FakeLLMProvider,
    session_factory: sessionmaker[Session],
) -> None:
    """The Phase 1 transport, and the resume key the Phase 3 SSE upgrade uses
    unchanged (implementation plan §6.3)."""
    created = start(client)
    await run_pipeline(session_factory, registry, provider)

    first = client.get(f"/v1/research/{created['session_id']}/activity").json()
    labels = [event["label"] for event in first["events"]]
    assert "Understanding the objective" in labels
    assert "Identifying research areas" in labels
    assert "Building the report" in labels
    assert first["next_after"] == len(first["events"])

    later = client.get(
        f"/v1/research/{created['session_id']}/activity",
        params={"after": first["next_after"]},
    ).json()
    assert later["events"] == []
    assert later["next_after"] == first["next_after"]


def test_activity_before_the_run_starts_is_empty_not_missing(
    client: TestClient,
) -> None:
    """A workspace that polls immediately must get an empty timeline, not a
    404 it has to special-case."""
    created = start(client)

    body = client.get(f"/v1/research/{created['session_id']}/activity").json()

    assert body == {"events": [], "next_after": 0}


def test_a_version_that_does_not_exist_is_a_404(client: TestClient) -> None:
    created = start(client)

    response = client.get(f"/v1/research/{created['session_id']}/versions/9")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


# --------------------------------------------------------------------------
# Ownership
# --------------------------------------------------------------------------


def test_another_visitor_cannot_read_someone_else_s_research(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    """`REQ-SEC-002 AC-3`, at the boundary. Absent, not forbidden: an
    identifier that cannot be probed for existence is `REQ-SEC-009` working."""
    created = start(client)
    client.cookies.clear()
    start(client)  # a second visitor, with their own session

    response = client.get("/v1/research/" + created["session_id"])

    assert response.status_code == 404


def test_a_stale_cookie_starts_a_new_session_rather_than_failing(
    client: TestClient,
) -> None:
    """Starting research must work for anyone, including someone holding a
    cookie from a session that no longer exists (`REQ-AUTH-001`). Reading is the
    strict direction; starting is not."""
    client.cookies.set(ANONYMOUS_COOKIE, "a-token-from-a-previous-life")

    response = client.post("/v1/research", json={"objective": OBJECTIVE})

    assert response.status_code == 202
    assert response.cookies.get(ANONYMOUS_COOKIE) is not None


def test_reading_without_a_session_is_refused(client: TestClient) -> None:
    created = start(client)
    client.cookies.clear()

    response = client.get("/v1/research/" + created["session_id"])

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "no_session"


def test_an_unrecognised_session_token_is_refused(client: TestClient) -> None:
    created = start(client)
    client.cookies.set(ANONYMOUS_COOKIE, "not-a-real-token")

    response = client.get("/v1/research/" + created["session_id"])

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unknown_session"


# --------------------------------------------------------------------------
# The envelope
# --------------------------------------------------------------------------


def test_every_error_has_the_same_shape(client: TestClient) -> None:
    """One envelope, one mapping from internal failure to public message
    (`REQ-SEC-010 AC-5`)."""
    responses = [
        client.get("/v1/research/00000000-0000-0000-0000-000000000000"),
        client.post("/v1/research", json={}),
    ]

    for response in responses:
        body = response.json()
        assert set(body) == {"error"}
        assert set(body["error"]) == {"code", "message"}


def test_an_unexpected_failure_says_nothing_internal(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guarantee the envelope exists for: a stack trace, a provider name or
    a fragment of retrieved content must never reach a response
    (`REQ-SEC-010 AC-5`). It is logged instead.
    """
    def explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError("connection string postgresql://scrapr:hunter2@db")

    monkeypatch.setattr(ResearchRepository, "create_session", explode)
    client = TestClient(
        app, base_url="https://testserver", raise_server_exceptions=False
    )

    response = client.post("/v1/research", json={"objective": OBJECTIVE})

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "hunter2" not in response.text
    assert "RuntimeError" not in response.text


def test_activity_for_someone_else_s_research_is_absent(client: TestClient) -> None:
    created = start(client)
    client.cookies.clear()
    start(client)

    response = client.get(f"/v1/research/{created['session_id']}/activity")

    assert response.status_code == 404


def test_the_default_session_factory_reaches_the_configured_database() -> None:
    """The one path the dependency override hides.

    Every other test here swaps the session, so without this the wiring that
    actually runs in production is never executed at all.
    """
    generator = deps.db_session()
    session = next(generator)
    try:
        assert session.execute(text("SELECT 1")).scalar_one() == 1
    finally:
        generator.close()


def test_health_does_not_touch_the_database(client: TestClient) -> None:
    """A health check that fails when Postgres blips takes the API down with
    it, which is the opposite of what it is for."""
    assert client.get("/health").json() == {"status": "ok"}


# --------------------------------------------------------------------------
# Cross-origin access
# --------------------------------------------------------------------------


def test_the_configured_web_origin_may_send_credentials(client: TestClient) -> None:
    """The frontend runs on a different origin in development, and the session
    cookie is the whole authorization story — so it has to be allowed to travel."""
    response = client.post(
        "/v1/research",
        json={"objective": OBJECTIVE},
        headers={"Origin": "http://localhost:3000"},
    )

    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert response.headers["access-control-allow-credentials"] == "true"


def test_an_unlisted_origin_is_not_allowed(client: TestClient) -> None:
    """A wildcard here would let any site on the internet read a visitor's
    research, since the browser would happily attach their cookie."""
    response = client.post(
        "/v1/research",
        json={"objective": OBJECTIVE},
        headers={"Origin": "https://evil.example"},
    )

    assert "access-control-allow-origin" not in response.headers


async def test_a_partial_run_tells_the_reader_what_is_missing(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    """`REQ-AGENT-009 AC-2` through the wire.

    The area that could not be researched is named in the payload the workspace
    renders, as an uncertainty claim in the summary. A status word alone would
    tell a reader that something is missing without telling them what.
    """
    registry = ToolRegistry()
    registry.register(fixture_search_tool())
    registry.register(
        FailingFixtureTool(name="broken_news", category=ToolCategory.NEWS)
    )
    registry.freeze()

    created = start(client)
    await run_pipeline(
        session_factory,
        registry,
        scripted_provider(
            "Acme Corp",
            (QUESTION, HIRING_QUESTION),
            (
                ("Financial performance", (QUESTION,), ("web_search",)),
                ("Hiring", (HIRING_QUESTION,), ("news",)),
            ),
        ),
    )

    header = client.get(f"/v1/research/{created['session_id']}").json()
    assert header["status"] == "partial"

    body = client.get(f"/v1/research/{created['session_id']}/versions/1").json()
    summary = body["sections"][0]
    assert summary["is_executive_summary"]

    summary_claims = [
        claim for claim in body["claims"] if claim["id"] in summary["claim_ids"]
    ]
    uncertainties = [
        claim["text"]
        for claim in summary_claims
        if claim["claim_type"] == "uncertainty"
    ]
    assert any("Hiring" in text for text in uncertainties), uncertainties


def test_research_is_queued_durably_before_the_response_returns(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    """`REQ-AGENT-008 AC-1`, `AC-2`.

    The request returns immediately, and what it leaves behind is rows: a run
    and its steps, waiting for whatever picks them up. Nothing about the work is
    held in the request, so closing the browser cannot terminate it — there is
    no connection for the research to be attached to.
    """
    created = start(client)

    with session_factory() as session:
        run = session.execute(
            select(ResearchRun).where(
                ResearchRun.session_id == UUID(created["session_id"])
            )
        ).scalar_one()
        steps = RunRepository(session).steps(run.id)

    assert run.status is RunStatus.PENDING
    assert [step.stage for step in steps] == list(STAGES)
    assert all(step.status is StepStatus.PENDING for step in steps)
    assert all(step.lease_owner is None for step in steps)


# --------------------------------------------------------------------------
# Phase 2 on the wire
# --------------------------------------------------------------------------


async def test_the_payload_carries_confidence_and_its_reasoning(
    client: TestClient,
    registry: ToolRegistry,
    provider: FakeLLMProvider,
    session_factory: sessionmaker[Session],
) -> None:
    """`REQ-EVID-015 AC-1` needs confidence displayed where the claim appears,
    which it cannot be if it never leaves the server. `REQ-DATA-012` needs the
    reasoning to travel with it."""
    created = start(client)

    await run_pipeline(session_factory, registry, provider)

    body = client.get(f"/v1/research/{created['session_id']}/versions/1").json()

    assert body["claims"]
    for claim in body["claims"]:
        assert claim["confidence"] in {"high", "moderate", "low"}
        assert claim["confidence_rationale"]


async def test_a_conflict_reaches_the_client_with_both_sides(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    """`REQ-WORK-009 AC-1`, and the reason this test exists at all.

    Conflict detection has been correct and unreachable before — the detector
    ran only when a fixture contradicted itself, which none did by default. The
    same trap applies one layer up: the rows can be written perfectly and never
    serialised, and the UI would render nothing while every backend test
    passed.
    """
    from pipeline_support import cite_everything, disputed_registry, disputing_provider

    created = start(client)

    await run_pipeline(
        session_factory,
        disputed_registry(),
        disputing_provider(),
        synthesis=cite_everything,
    )

    body = client.get(f"/v1/research/{created['session_id']}/versions/1").json()

    assert body["conflicts"], "a detected conflict never reached the wire"

    conflict = body["conflicts"][0]
    assert len(conflict["sides"]) == 2, "both values must travel (`AC-1`)"
    assert conflict["status"] in {"explained", "unresolved"}

    # `REQ-EVID-012 AC-3`: each side resolves to a source the client already
    # has, so it can show the tier and retrieval time beside the value.
    known = {source["id"] for source in body["sources"]}
    for side in conflict["sides"]:
        assert side["source_id"] in known
        assert side["value"]
