"""Exports over HTTP: Flow D (`REQ-EXP-001..010`).

The routes the export panel uses, against a real finished version. Storage is
swapped for a stand-in that signs fake URLs, because what is under test here is
who may ask for a download and when — the rendering itself is covered by the
core export suite.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pipeline_support import fixture_registry, run_pipeline, scripted_provider
from sqlalchemy.orm import Session, sessionmaker

from scrapr_api.routers import exports as exports_router
from scrapr_core.export import ExportProcessor

pytestmark = pytest.mark.integration

AREAS = (("Financial performance", ("What is Acme's revenue?",), ("web_search",)),)


class FakeStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put(self, storage_key: str, data: bytes, content_type: str) -> None:
        self.objects[storage_key] = data

    def delete(self, storage_key: str) -> None:
        self.objects.pop(storage_key, None)

    def presign_get(self, storage_key: str, *, download_name: str | None = None) -> str:
        return f"https://storage.example/{storage_key}?signed=1&name={download_name}"


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeStore]:
    fake = FakeStore()
    monkeypatch.setattr(exports_router, "get_object_store", lambda: fake)
    yield fake


async def finished(client: TestClient, session_factory: sessionmaker[Session]) -> str:
    response = client.post("/v1/research", json={"objective": "How is Acme Corp performing?"})
    assert response.status_code == 202
    await run_pipeline(
        session_factory, fixture_registry(), scripted_provider("Acme Corp", ("What is Acme's revenue?",), AREAS)
    )
    return str(response.json()["session_id"])


async def test_an_anonymous_visitor_can_export_and_download(
    client: TestClient, session_factory: sessionmaker[Session], store: FakeStore
) -> None:
    """Flow D, D-1 to D-6, with no account anywhere (`REQ-EXP-010`)."""
    session_id = await finished(client, session_factory)

    requested = client.post(
        f"/v1/research/{session_id}/versions/1/exports", json={"format": "pdf", "theme": "investor"}
    )
    # `REQ-EXP-007 AC-1`: returns at once, not when the file exists.
    assert requested.status_code == 202
    body = requested.json()
    assert body["status"] == "pending" and body["version_number"] == 1
    assert body["download_url"] is None

    # The same request again is the same export, not a second render.
    again = client.post(
        f"/v1/research/{session_id}/versions/1/exports", json={"format": "pdf", "theme": "investor"}
    )
    assert again.json()["id"] == body["id"]

    assert ExportProcessor(session_factory, store).run_one() is not None

    ready = client.get(f"/v1/exports/{body['id']}").json()
    assert ready["status"] == "ready" and ready["size_bytes"] > 0
    assert ready["download_url"].startswith("https://storage.example/exports/")
    assert "scrapr-research-v1-investor.pdf" in ready["download_url"]

    listed = client.get(f"/v1/research/{session_id}/versions/1/exports").json()
    assert [item["id"] for item in listed] == [body["id"]]
    # A signed link is only ever handed out one export at a time.
    assert listed[0]["download_url"] is None


async def test_a_failed_export_offers_a_retry(
    client: TestClient, session_factory: sessionmaker[Session], store: FakeStore
) -> None:
    session_id = await finished(client, session_factory)
    export_id = client.post(
        f"/v1/research/{session_id}/versions/1/exports", json={"format": "pptx", "theme": "dark"}
    ).json()["id"]

    assert client.post(f"/v1/exports/{export_id}/retry").status_code == 409

    from scrapr_core.db.enums import ExportStatus
    from scrapr_core.db.models import Export

    with session_factory() as session:
        row = session.get(Export, export_id)
        assert row is not None
        row.status = ExportStatus.FAILED
        row.error = "This export could not be generated."
        session.commit()

    failed = client.get(f"/v1/exports/{export_id}").json()
    assert failed["status"] == "failed" and failed["error"]

    retried = client.post(f"/v1/exports/{export_id}/retry")
    assert retried.status_code == 202
    assert retried.json()["status"] == "pending" and retried.json()["error"] is None


def test_an_unfinished_version_cannot_be_exported(client: TestClient) -> None:
    session_id = client.post(
        "/v1/research", json={"objective": "How is Acme Corp performing?", "defer_start": True}
    ).json()["session_id"]

    response = client.post(
        f"/v1/research/{session_id}/versions/1/exports", json={"format": "pdf", "theme": "minimal"}
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "version_not_ready"


@pytest.mark.parametrize(
    "payload", [{"format": "docx", "theme": "minimal"}, {"format": "pdf", "theme": "neon"}, {}]
)
def test_only_the_offered_formats_and_themes_are_accepted(
    client: TestClient, payload: dict[str, str]
) -> None:
    """`REQ-EXP-003`: the six themes and two formats, nothing invented."""
    session_id = client.post(
        "/v1/research", json={"objective": "How is Acme Corp performing?", "defer_start": True}
    ).json()["session_id"]

    response = client.post(f"/v1/research/{session_id}/versions/1/exports", json=payload)

    assert response.status_code == 422
