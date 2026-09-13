"""Accounts over HTTP: Flow E and Flow F (`REQ-AUTH-001..009`, `DEC-16..18`).

Real repositories, real database, real cookies. The flows are asserted the way
a browser would live them — research anonymously, sign up, find the research in
history, sign out, sign back in, find it again — because every property here is
about what survives between requests.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from scrapr_api.deps import ACCOUNT_COOKIE, ANONYMOUS_COOKIE
from scrapr_core.db.enums import MessageRole, StepStatus, UploadState
from scrapr_core.db.models import ResearchSession, RunStep, Upload, User
from scrapr_core.db.repositories import ConversationRepository
from scrapr_core.security.limits import SIGNIN_PER_EMAIL

pytestmark = pytest.mark.integration

EMAIL = "reader@example.com"
PASSWORD = "a long enough password"
OBJECTIVE = "How is Acme Corp positioned against its competitors?"


def start(client: TestClient, objective: str = OBJECTIVE) -> str:
    response = client.post("/v1/research", json={"objective": objective, "defer_start": True})
    assert response.status_code == 202, response.text
    return str(response.json()["session_id"])


def sign_up(client: TestClient, email: str = EMAIL, password: str = PASSWORD) -> dict[str, object]:
    response = client.post("/v1/auth/signup", json={"email": email, "password": password})
    assert response.status_code == 201, response.text
    body: dict[str, object] = response.json()
    return body


# --------------------------------------------------------------------------
# No account required — `REQ-AUTH-001`
# --------------------------------------------------------------------------


def test_research_starts_without_an_account(client: TestClient) -> None:
    session_id = start(client)

    assert client.get(f"/v1/research/{session_id}").status_code == 200
    assert client.get("/v1/auth/session").json() == {"account": None}


# --------------------------------------------------------------------------
# Sign up — `REQ-AUTH-003`, `REQ-AUTH-004`
# --------------------------------------------------------------------------


def test_signing_up_saves_this_browsers_research(client: TestClient) -> None:
    """Flow E: E-1 to E-5, and `REQ-AUTH-004 AC-1`."""
    first = start(client)
    second = start(client, "A second question asked before signing up")

    body = sign_up(client)

    assert body["claimed"] == 2
    history = client.get("/v1/me/research")
    assert history.status_code == 200
    assert {item["id"] for item in history.json()} == {first, second}
    # Still readable, now as the account.
    assert client.get(f"/v1/research/{first}").status_code == 200


def test_signing_up_sets_a_hardened_cookie_and_spends_the_anonymous_one(
    client: TestClient,
) -> None:
    start(client)

    response = client.post("/v1/auth/signup", json={"email": EMAIL, "password": PASSWORD})

    headers = response.headers.get_list("set-cookie")
    account = next(header for header in headers if header.startswith(f"{ACCOUNT_COOKIE}="))
    lowered = account.lower()
    assert "httponly" in lowered and "secure" in lowered and "samesite=lax" in lowered
    assert "max-age=2592000" in lowered
    # The anonymous carrier is cleared: its research has moved.
    assert any(
        header.startswith(f'{ANONYMOUS_COOKIE}=""') or header.startswith(f"{ANONYMOUS_COOKIE}=;")
        for header in headers
    )
    # And the token is never in a body.
    token = response.cookies.get(ACCOUNT_COOKIE)
    assert token is not None and token not in response.text


def test_the_account_record_holds_what_req_data_001_names(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    sign_up(client, email="  Reader@Example.com ")

    with session_factory() as session:
        user = session.execute(select(User)).scalar_one()
    assert user.email == EMAIL
    assert user.password_hash and PASSWORD not in user.password_hash
    assert user.preferences == {} and user.usage_meta == {}


@pytest.mark.parametrize(
    ("payload", "status", "code"),
    [
        ({"email": EMAIL, "password": "too short"}, 422, "weak_password"),
        ({"email": "not-an-email", "password": PASSWORD}, 422, "invalid_email"),
        ({"email": EMAIL}, 422, "invalid_request"),
    ],
)
def test_bad_sign_ups_say_what_to_fix_without_echoing_input(
    client: TestClient, payload: dict[str, str], status: int, code: str
) -> None:
    response = client.post("/v1/auth/signup", json=payload)

    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert "too short" not in response.text


def test_an_email_can_only_have_one_account(client: TestClient) -> None:
    sign_up(client)
    client.cookies.clear()

    response = client.post(
        "/v1/auth/signup", json={"email": EMAIL.upper(), "password": PASSWORD}
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "email_taken"


def test_research_created_while_signed_in_belongs_to_the_account(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    sign_up(client)

    session_id = start(client)

    with session_factory() as session:
        row = session.get(ResearchSession, UUID(session_id))
    assert row is not None
    assert row.owner_user_id is not None and row.anonymous_session_id is None
    assert client.cookies.get(ANONYMOUS_COOKIE) is None


# --------------------------------------------------------------------------
# Sign in and out — Flow F, `REQ-AUTH-009`
# --------------------------------------------------------------------------


def test_leaving_and_returning_restores_the_research_and_its_conversation(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    """Flow F and `REQ-AUTH-007 AC-1`: prior messages render on reopening."""
    session_id = start(client)
    sign_up(client)

    with session_factory() as session:
        research = session.get(ResearchSession, UUID(session_id))
        assert research is not None and research.current_version_id is not None
        ConversationRepository(session).append(
            research.id, research.current_version_id, MessageRole.USER, "What about margins?"
        )
        session.commit()

    assert client.post("/v1/auth/signout").status_code == 204
    assert client.get(f"/v1/research/{session_id}").status_code == 401
    assert client.get("/v1/me/research").json()["error"]["code"] == "account_required"

    signed_in = client.post("/v1/auth/signin", json={"email": EMAIL, "password": PASSWORD})
    assert signed_in.status_code == 200
    assert signed_in.json()["account"]["email"] == EMAIL

    assert [item["id"] for item in client.get("/v1/me/research").json()] == [session_id]
    header = client.get(f"/v1/research/{session_id}").json()
    assert len(header["versions"]) == 1  # `REQ-AUTH-006 AC-2`
    messages = client.get(f"/v1/research/{session_id}/messages").json()
    assert [message["content"] for message in messages] == ["What about margins?"]


def test_signing_in_claims_research_done_while_signed_out(client: TestClient) -> None:
    sign_up(client)
    client.post("/v1/auth/signout")

    session_id = start(client)
    body = client.post("/v1/auth/signin", json={"email": EMAIL, "password": PASSWORD}).json()

    assert body["claimed"] == 1
    assert session_id in {item["id"] for item in client.get("/v1/me/research").json()}


def test_an_explicit_claim_moves_anonymous_research_into_the_account(
    client: TestClient, app: object
) -> None:
    """`POST /v1/research/claim`: signed in, holding an anonymous session too."""
    anonymous = TestClient(app, base_url="https://testserver")  # type: ignore[arg-type]
    session_id = start(anonymous)
    anonymous_token = anonymous.cookies.get(ANONYMOUS_COOKIE)
    assert anonymous_token

    sign_up(client)
    client.cookies.set(ANONYMOUS_COOKIE, anonymous_token)

    response = client.post("/v1/research/claim")

    assert response.status_code == 200
    assert response.json() == {"claimed": 1}
    assert session_id in {item["id"] for item in client.get("/v1/me/research").json()}
    # A second claim has nothing left to move.
    client.cookies.set(ANONYMOUS_COOKIE, anonymous_token)
    assert client.post("/v1/research/claim").json() == {"claimed": 0}


def test_claiming_requires_an_account(client: TestClient) -> None:
    start(client)

    response = client.post("/v1/research/claim")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "account_required"


def test_a_wrong_password_and_an_unknown_email_read_the_same(client: TestClient) -> None:
    sign_up(client)
    client.cookies.clear()

    wrong = client.post("/v1/auth/signin", json={"email": EMAIL, "password": "not the password"})
    unknown = client.post(
        "/v1/auth/signin", json={"email": "nobody@example.com", "password": PASSWORD}
    )

    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_a_signed_out_cookie_no_longer_works_even_if_copied(client: TestClient) -> None:
    """Sign-out revokes server-side, not only in the browser."""
    sign_up(client)
    token = client.cookies.get(ACCOUNT_COOKIE)
    assert token

    client.post("/v1/auth/signout")
    client.cookies.set(ACCOUNT_COOKIE, token)

    assert client.get("/v1/auth/session").json() == {"account": None}
    assert client.get("/v1/me/research").status_code == 401


def test_repeated_sign_in_attempts_are_rate_limited(client: TestClient) -> None:
    """`REQ-AUTH-009 AC-3`, `REQ-SEC-010 AC-3`: counted even when they fail."""
    sign_up(client)
    client.cookies.clear()

    statuses = [
        client.post(
            "/v1/auth/signin", json={"email": EMAIL, "password": "a wrong guess!!"}
        ).status_code
        for _ in range(SIGNIN_PER_EMAIL.limit + 1)
    ]

    assert statuses[:-1] == [401] * SIGNIN_PER_EMAIL.limit
    assert statuses[-1] == 429
    limited = client.post("/v1/auth/signin", json={"email": EMAIL, "password": PASSWORD})
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limited"
    assert int(limited.headers["retry-after"]) > 0


# --------------------------------------------------------------------------
# History — `REQ-AUTH-005`
# --------------------------------------------------------------------------


def test_history_lists_subject_objective_and_last_update_newest_first(
    client: TestClient,
) -> None:
    sign_up(client)
    older = start(client, "The first question in this account")
    newer = start(client, "The second question in this account")

    items = client.get("/v1/me/research").json()

    assert [item["id"] for item in items] == [newer, older]
    assert set(items[0]) >= {"objective", "subject", "updated_at", "version_count"}
    assert items[0]["version_count"] == 1


def test_history_is_for_accounts(client: TestClient) -> None:
    start(client)

    response = client.get("/v1/me/research")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "account_required"


# --------------------------------------------------------------------------
# Deletion — `REQ-SEC-008`, `DEC-18`
# --------------------------------------------------------------------------


def test_deleting_research_removes_it_from_every_read(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    sign_up(client)
    session_id = start(client)
    assert client.post(f"/v1/research/{session_id}/start").status_code == 202

    assert client.delete(f"/v1/research/{session_id}").status_code == 204

    assert client.get(f"/v1/research/{session_id}").status_code == 404
    assert client.get(f"/v1/research/{session_id}/versions/1").status_code == 404
    assert client.get("/v1/me/research").json() == []
    with session_factory() as session:
        statuses = set(session.execute(select(RunStep.status)).scalars())
    assert statuses == {StepStatus.DEAD}
    # Idempotent.
    assert client.delete(f"/v1/research/{session_id}").status_code == 204


def test_a_deleted_document_is_still_named_by_the_version_that_cited_it(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    """`REQ-AUTH-008 AC-3`: prior versions record that the document existed."""
    from scrapr_core.db.base import utcnow
    from scrapr_core.db.enums import Accessibility, AuthorityTier, SourceCategory
    from scrapr_core.db.models import Source

    session_id = start(client)
    with session_factory() as session:
        research = session.get(ResearchSession, UUID(session_id))
        assert research is not None and research.current_version_id is not None
        upload = Upload(
            session_id=research.id,
            filename="board-notes.pdf",
            content_type="application/pdf",
            size_bytes=10,
            storage_key="",
            sha256="",
            processing_state=UploadState.READY,
            deleted_at=utcnow(),
        )
        session.add(upload)
        session.flush()
        session.add(
            Source(
                version_id=research.current_version_id,
                name="board-notes.pdf",
                category=SourceCategory.DOCUMENT,
                authority_tier=AuthorityTier.SECONDARY,
                tier_rationale={"rule": "document"},
                retrieved_at=utcnow(),
                accessibility=Accessibility.ACCESSIBLE,
                upload_id=upload.id,
            )
        )
        session.commit()

    sources = client.get(f"/v1/research/{session_id}/versions/1").json()["sources"]

    assert [source["name"] for source in sources] == ["board-notes.pdf"]
    assert sources[0]["document_removed"] is True


def test_a_write_is_committed_before_its_response_is_sent(
    app: FastAPI, session_factory: sessionmaker[Session]
) -> None:
    """A 2xx must mean the write is committed.

    Observed in a browser: signing up and immediately asking who is signed in
    read a database without the new session, because the commit ran after the
    response had gone. The observable proof is a commit that fails: committed
    before the response, the client is told it failed; committed after, the
    client has already been told "created".
    """
    from collections.abc import Iterator

    from scrapr_api import deps

    def failing_commit() -> Iterator[Session]:
        session = session_factory()
        try:
            yield session
            session.rollback()
            raise RuntimeError("the commit failed")
        finally:
            session.close()

    app.dependency_overrides[deps.db_session] = failing_commit
    with TestClient(app, base_url="https://testserver", raise_server_exceptions=False) as client:
        response = client.post("/v1/auth/signup", json={"email": EMAIL, "password": PASSWORD})

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
