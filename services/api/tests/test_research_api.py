"""The HTTP surface of the Phase 0 exit condition.

`POST /v1/research` writes a session, a version and a run; the runner executes
them; `GET /v1/research/{id}/versions/1` returns the report and
`GET .../activity` returns the two stages. Everything below the HTTP layer is
real — real repositories, real database, real runner — because the seams are
what Phase 0 exists to prove.

The other half of this file is the boundary's own rules: ownership resolved from
a cookie, another owner's research reading as absent, and errors that say
nothing internal.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from scrapr_api import deps
from scrapr_api.deps import ANONYMOUS_COOKIE
from scrapr_core.db.repositories import ResearchRepository
from scrapr_core.jobs import JobRunner
from scrapr_core.llm import FakeLLMProvider
from scrapr_core.orchestrator.skeleton import (
    RETRIEVE_STAGE,
    SYNTHESIZE_STAGE,
    RetrieveHandler,
    SkeletonClaim,
    SynthesizeHandler,
)
from scrapr_core.tools import ToolCategory, ToolRegistry
from scrapr_core.tools.impl import FixtureTool, fixture_item

pytestmark = pytest.mark.integration

OBJECTIVE = "How is Acme Corp positioned against its competitors?"
RETRIEVED_TEXT = "Acme reported $1.2bn revenue for FY2025, up 18% year over year."


@pytest.fixture
def runner(session_factory: sessionmaker[Session]) -> JobRunner:
    """The same runner the worker process builds, with fixtures wired in."""
    registry = ToolRegistry()
    registry.register(
        FixtureTool(
            name="fixture_search",
            category=ToolCategory.WEB_SEARCH,
            items=(
                fixture_item(
                    source_name="Acme FY2025 results",
                    text=RETRIEVED_TEXT,
                    source_url="https://acme.example/ir/fy2025",
                ),
            ),
        )
    )
    registry.freeze()

    provider = FakeLLMProvider()
    provider.enqueue(
        SkeletonClaim(
            section_title="Revenue",
            claim_text="Acme reported $1.2bn revenue for FY2025.",
        )
    )

    return JobRunner(
        session_factory,
        {
            RETRIEVE_STAGE: RetrieveHandler(registry=registry),
            SYNTHESIZE_STAGE: SynthesizeHandler(provider=provider),
        },
        worker_id="api-test-worker",
    )


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
    client: TestClient, runner: JobRunner
) -> None:
    created = start(client)

    await runner.run_until_idle()

    response = client.get(f"/v1/research/{created['session_id']}/versions/1")
    assert response.status_code == 200
    body = response.json()

    assert body["status"] == "complete"
    assert len(body["sections"]) == 1
    assert body["sections"][0]["title"] == "Revenue"

    claim = body["claims"][0]
    assert claim["claim_type"] == "fact"
    assert claim["id"] in body["sections"][0]["claim_ids"]

    # The citation map: every fact claim resolves to the source behind it, and
    # the source carries the retrieval timestamp (`REQ-EVID-004`).
    assert claim["source_ids"] == [body["sources"][0]["id"]]
    assert body["sources"][0]["retrieved_at"] is not None


async def test_the_session_header_lists_its_versions(
    client: TestClient, runner: JobRunner
) -> None:
    created = start(client)
    await runner.run_until_idle()

    body = client.get(f"/v1/research/{created['session_id']}").json()

    assert body["objective"] == OBJECTIVE
    assert [version["version_number"] for version in body["versions"]] == [1]
    assert body["current_version_id"] == created["version_id"]


async def test_activity_polls_forward_from_a_sequence_number(
    client: TestClient, runner: JobRunner
) -> None:
    """The Phase 1 transport, and the resume key the Phase 3 SSE upgrade uses
    unchanged (implementation plan §6.3)."""
    created = start(client)
    await runner.run_until_idle()

    first = client.get(f"/v1/research/{created['session_id']}/activity").json()
    assert [event["label"] for event in first["events"]] == [
        "Searching for information",
        "Searching for information",
        "Building the report",
        "Building the report",
    ]
    assert first["next_after"] == 4

    later = client.get(
        f"/v1/research/{created['session_id']}/activity",
        params={"after": first["next_after"]},
    ).json()
    assert later["events"] == []
    assert later["next_after"] == 4


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
