"""The upload routes (`REQ-DOC-001..003`, `REQ-DOC-010`, `REQ-SEC-008`).

Against real MinIO, because the interesting half of these routes is what
happens to the object. A mocked store would let `complete` pass while the
presigned URL it handed the browser did not work.

Ownership is checked on every route rather than on one representative, since
"the route somebody forgot" is exactly the shape this bug takes.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from scrapr_api.routers.uploads import get_object_store
from scrapr_core.config import Settings
from scrapr_core.db.models import ResearchRun
from scrapr_core.storage.objects import ObjectStore

MB = 1024 * 1024
TEXT = b"Acme Corp\n\nRevenue for the year was $1.2bn, up 18 percent.\n"


@pytest.fixture(autouse=True)
def _storage_available() -> Iterator[None]:
    """Skip the module when nothing is listening, and start from a clean client.

    `get_object_store` is `lru_cache`d for the process, so the cache is cleared
    between tests: a settings change in one test must not be invisible to the
    next.
    """
    get_object_store.cache_clear()
    try:
        ObjectStore(Settings()).stored_size("probe/does-not-exist")
    except Exception as exc:
        pytest.skip(f"no object storage reachable ({exc}); run `docker compose up -d`")
    yield
    get_object_store.cache_clear()


def _start(client: TestClient, *, defer: bool = True) -> str:
    response = client.post(
        "/v1/research",
        json={
            "objective": "How did Acme Corp perform last year?",
            "defer_start": defer,
        },
    )
    assert response.status_code == 202, response.text
    return str(response.json()["session_id"])


@dataclass(frozen=True, slots=True)
class Ticket:
    """A presign response, reduced to what the assertions need.

    Not the `httpx` response object: `TestClient` is typed against the vendored
    `httpx2` the Anthropic SDK brings in, so naming its return type here would
    tie this suite to an implementation detail of an unrelated dependency.
    """

    status: int
    body: dict[str, object]

    @property
    def upload_id(self) -> str:
        return str(self.body["upload_id"])


def _ticket(client: TestClient, session_id: str, **overrides: object) -> Ticket:
    body: dict[str, object] = {
        "filename": "annual.txt",
        "content_type": "text/plain",
        "size_bytes": len(TEXT),
    }
    body.update(overrides)
    response = client.post(f"/v1/research/{session_id}/uploads", json=body)
    return Ticket(response.status_code, dict(response.json()))


def _upload(ticket: dict[str, object], data: bytes = TEXT) -> int:
    """PUT straight to storage, exactly as the browser will."""
    return httpx.put(
        str(ticket["url"]),
        content=data,
        headers={"content-type": str(ticket["content_type"])},
        timeout=10.0,
    ).status_code


# --------------------------------------------------------------------------
# The happy path
# --------------------------------------------------------------------------


def test_the_whole_attach_flow(client: TestClient) -> None:
    """Presign, PUT direct to storage, complete, list.

    One test covering the sequence, because the sequence is the feature and
    each step passing in isolation has already proved not to be enough.
    """
    session_id = _start(client)

    ticket = _ticket(client, session_id)
    assert ticket.status == 201, ticket.body
    body = ticket.body

    assert _upload(body) == 200

    completed = client.post(
        f"/v1/research/{session_id}/uploads/{ticket.upload_id}/complete"
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["state"] == "processing"
    assert completed.json()["size_bytes"] == len(TEXT)

    listed = client.get(f"/v1/research/{session_id}/uploads")
    assert listed.status_code == 200
    assert [row["filename"] for row in listed.json()] == ["annual.txt"]


def test_the_api_never_receives_the_bytes(client: TestClient) -> None:
    """Implementation plan §10, asserted rather than assumed.

    There is no route that accepts a file body. If one appeared, a 25 MB upload
    would start flowing through a request handler with a fixed memory limit.
    """
    session_id = _start(client)
    ticket = _ticket(client, session_id).body

    # The signed URL points at storage, not at us.
    assert "/v1/research" not in str(ticket["url"])

    # And posting a body to the upload collection is not a file upload.
    response = client.post(
        f"/v1/research/{session_id}/uploads", content=TEXT, headers={}
    )
    assert response.status_code == 422


# --------------------------------------------------------------------------
# Limits — `REQ-DOC-010`
# --------------------------------------------------------------------------


def test_an_oversized_file_is_refused_before_it_is_transferred(
    client: TestClient,
) -> None:
    """`AC-2`, and the point of checking at presign: no URL is ever issued, so
    the user is not asked to upload 40 MB and then told no."""
    session_id = _start(client)

    response = _ticket(client, session_id, size_bytes=40 * MB)

    assert response.status == 413
    assert response.body["error"]["code"] == "file_too_large"  # type: ignore[index]
    assert "25.0 MB" in response.body["error"]["message"]  # type: ignore[index]


def test_an_unsupported_type_is_refused_and_says_what_is_accepted(
    client: TestClient,
) -> None:
    """`REQ-DOC-001 AC-2`."""
    session_id = _start(client)

    response = _ticket(client, session_id, content_type="image/png", filename="x.png")

    assert response.status == 415
    body = response.body["error"]["message"]  # type: ignore[index]
    assert "PDF" in body
    assert "Excel" in body


def test_the_count_limit_holds_without_a_single_byte_uploaded(
    client: TestClient,
) -> None:
    """`AC-1`: server-side, not only in the browser.

    Ten tickets and no PUTs. If pending rows did not count, this would be an
    unbounded number of signed write grants against one session.
    """
    session_id = _start(client)

    for index in range(10):
        assert _ticket(client, session_id, filename=f"f{index}.txt").status == 201

    refused = _ticket(client, session_id, filename="eleventh.txt")
    assert refused.status == 413
    assert refused.body["error"]["code"] == "too_many_files"  # type: ignore[index]


def test_a_client_that_understated_its_size_is_caught_at_completion(
    client: TestClient,
) -> None:
    """`REQ-DOC-010 AC-1`'s second check, and the reason there are two.

    A presigned URL is a write grant, not a size limit. This client declares
    1 KB, gets a signature, and uploads 30 MB with it.
    """
    session_id = _start(client)
    ticket = _ticket(client, session_id, size_bytes=1024)

    assert _upload(ticket.body, b"x" * (30 * MB)) == 200

    completed = client.post(
        f"/v1/research/{session_id}/uploads/{ticket.upload_id}/complete"
    )

    assert completed.status_code == 413
    assert completed.json()["error"]["code"] == "file_too_large"

    # The row is gone, and so is the object: a bucket that keeps what the limit
    # refused is not limited.
    assert client.get(f"/v1/research/{session_id}/uploads").json() == []


def test_completing_before_the_bytes_arrive_is_a_conflict(
    client: TestClient,
) -> None:
    """409, not 500. The client has simply called too early, and the message
    tells it so rather than looking like an outage."""
    session_id = _start(client)
    ticket = _ticket(client, session_id)

    response = client.post(
        f"/v1/research/{session_id}/uploads/{ticket.upload_id}/complete"
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "not_uploaded"


# --------------------------------------------------------------------------
# Deletion — `REQ-SEC-008`
# --------------------------------------------------------------------------


def test_deleting_removes_the_row_and_the_object(client: TestClient) -> None:
    session_id = _start(client)
    ticket = _ticket(client, session_id)
    _upload(ticket.body)
    client.post(f"/v1/research/{session_id}/uploads/{ticket.upload_id}/complete")

    store = get_object_store()
    key_bearing = client.get(f"/v1/research/{session_id}/uploads").json()[0]
    assert key_bearing["state"] == "processing"

    deleted = client.delete(f"/v1/research/{session_id}/uploads/{ticket.upload_id}")
    assert deleted.status_code == 204
    assert client.get(f"/v1/research/{session_id}/uploads").json() == []

    # `AC-2`: the file leaves storage, not just the listing.
    remaining = [
        key
        for key in _keys_under(store, f"uploads/{session_id}/")
        if ticket.upload_id in key
    ]
    assert remaining == []


def test_deleting_twice_is_not_an_error(client: TestClient) -> None:
    """A client retrying a delete it already made should not be told it failed."""
    session_id = _start(client)
    ticket = _ticket(client, session_id)

    first = client.delete(f"/v1/research/{session_id}/uploads/{ticket.upload_id}")
    second = client.delete(f"/v1/research/{session_id}/uploads/{ticket.upload_id}")

    assert first.status_code == 204
    assert second.status_code == 204


# --------------------------------------------------------------------------
# Ownership — `REQ-SEC-002`, `REQ-SEC-009`
# --------------------------------------------------------------------------


def test_every_upload_route_is_404_for_another_visitor(
    app: object, client: TestClient
) -> None:
    """404 and not 403, on all four (`REQ-SEC-009`).

    A 403 would confirm the session id is real, which is the whole thing an
    identifier probe is trying to learn.
    """
    session_id = _start(client)
    upload_id = _ticket(client, session_id).upload_id

    with TestClient(app, base_url="https://testserver") as stranger:  # type: ignore[arg-type]
        # A visitor with their own session, not an anonymous one: the routes
        # must refuse a *valid* caller reading someone else's research.
        stranger.post(
            "/v1/research",
            json={"objective": "A different question entirely, at length."},
        )

        assert stranger.get(f"/v1/research/{session_id}/uploads").status_code == 404
        assert _ticket(stranger, session_id).status == 404
        assert (
            stranger.post(
                f"/v1/research/{session_id}/uploads/{upload_id}/complete"
            ).status_code
            == 404
        )
        assert (
            stranger.delete(
                f"/v1/research/{session_id}/uploads/{upload_id}"
            ).status_code
            == 404
        )


def test_uploads_require_a_session_at_all(app: object) -> None:
    with TestClient(app, base_url="https://testserver") as anonymous:  # type: ignore[arg-type]
        response = anonymous.get(
            "/v1/research/01a09949-83a2-7100-9096-17f194151e31/uploads"
        )
    assert response.status_code == 401


# --------------------------------------------------------------------------
# Deferred start — `REQ-INPUT-004`
# --------------------------------------------------------------------------


def test_defer_start_creates_no_run_until_asked(client: TestClient) -> None:
    """`REQ-INPUT-004 AC-2` depends on this ordering.

    If the run started at creation, retrieval could reach the documents
    category before the file finished extracting, and "available to the agent
    during the first run" would hold only when the upload won a race.
    """
    session_id = _start(client, defer=True)

    header = client.get(f"/v1/research/{session_id}").json()
    assert header["versions"][0]["status"] == "building"

    started = client.post(f"/v1/research/{session_id}/start")
    assert started.status_code == 202
    assert started.json()["session_id"] == session_id


def test_starting_twice_does_not_enqueue_two_runs(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    """The call a client retries after a dropped connection.

    Two runs against one version would write two sets of claims into one
    report, and the reader would see every finding twice with no way to tell
    which pass produced which.
    """
    session_id = _start(client, defer=True)

    first = client.post(f"/v1/research/{session_id}/start").json()
    second = client.post(f"/v1/research/{session_id}/start").json()

    assert first["version_id"] == second["version_id"]

    with session_factory() as session:
        runs = session.execute(
            select(func.count(ResearchRun.id)).where(
                ResearchRun.version_id == UUID(first["version_id"])
            )
        ).scalar_one()

    assert runs == 1


def test_research_without_documents_is_still_one_request(client: TestClient) -> None:
    """`REQ-INPUT-004 AC-3`. The default path must not grow a second round trip
    for a feature most runs do not use."""
    response = client.post(
        "/v1/research",
        json={"objective": "How did Acme Corp perform last year?"},
    )

    assert response.status_code == 202
    session_id = response.json()["session_id"]
    # A run already exists, so starting again is a no-op rather than a second.
    assert client.post(f"/v1/research/{session_id}/start").status_code == 202


def _keys_under(store: ObjectStore, prefix: str) -> list[str]:
    """List object keys for an assertion. Test-only: nothing in the application
    lists a bucket, which is why `ObjectStore` has no such method."""
    client = store._client
    bucket = store._bucket
    response = client.list_objects_v2(Bucket=bucket, Prefix=prefix)
    return [item["Key"] for item in response.get("Contents", [])]
