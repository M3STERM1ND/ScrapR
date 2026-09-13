"""Cross-account isolation, route by route (`REQ-SEC-002 AC-3`, `REQ-SEC-009`, `REQ-SEC-016`).

**Every route that takes a research identifier is exercised by every kind of
stranger**: another account, another anonymous visitor, and someone with no
session at all. None of them may read it, change it, or learn that it exists.

**The route list is derived from the application, not typed out.** A test that
enumerates routes by hand passes happily while a new route leaks, because
nobody added it to the list. So the suite reads `app.routes`, and it fails if a
route with a path parameter has no entry below saying how to call it — adding a
route without adding it here is a red build, not a quiet gap.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from scrapr_core.db.base import utcnow
from scrapr_core.db.enums import (
    ExportFormat,
    ExportStatus,
    ExportTheme,
    UploadState,
    VersionStatus,
)
from scrapr_core.db.models import Export, ResearchSession, ResearchVersion, Upload

pytestmark = pytest.mark.integration

PASSWORD = "a long enough password"


@dataclass(frozen=True)
class Probe:
    """How to call one route against the victim's research."""

    body: dict[str, object] | None = None
    ok_statuses: frozenset[int] = frozenset({404})
    """What a stranger may be told. 404, except where the route is idempotent
    and answers every caller alike."""


PROBES: dict[tuple[str, str], Probe] = {
    ("GET", "/v1/research/{session_id}"): Probe(),
    ("DELETE", "/v1/research/{session_id}"): Probe(ok_statuses=frozenset({204})),
    ("POST", "/v1/research/{session_id}/start"): Probe(),
    ("POST", "/v1/research/{session_id}/update"): Probe(),
    ("GET", "/v1/research/{session_id}/versions/{version_number}"): Probe(),
    ("GET", "/v1/research/{session_id}/activity"): Probe(),
    ("GET", "/v1/research/{session_id}/messages"): Probe(),
    ("POST", "/v1/research/{session_id}/messages"): Probe(body={"question": "What changed?"}),
    ("POST", "/v1/research/{session_id}/uploads"): Probe(
        body={"filename": "a.txt", "content_type": "text/plain", "size_bytes": 10}
    ),
    ("GET", "/v1/research/{session_id}/uploads"): Probe(),
    ("POST", "/v1/research/{session_id}/uploads/{upload_id}/complete"): Probe(),
    ("DELETE", "/v1/research/{session_id}/uploads/{upload_id}"): Probe(),
    ("POST", "/v1/research/{session_id}/versions/{version_number}/exports"): Probe(
        body={"format": "pdf", "theme": "minimal"}
    ),
    ("GET", "/v1/research/{session_id}/versions/{version_number}/exports"): Probe(),
    ("GET", "/v1/exports/{export_id}"): Probe(),
    ("POST", "/v1/exports/{export_id}/retry"): Probe(),
}


def parameterised_routes(app: FastAPI) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for route in app.routes:
        if isinstance(route, APIRoute) and "{" in route.path:
            for method in route.methods or ():
                found.add((method, route.path))
    return found


@dataclass
class Victim:
    session_id: str
    upload_id: str
    export_id: str


@pytest.fixture
def victim(app: FastAPI, session_factory: sessionmaker[Session]) -> Victim:
    """Research owned by an account, with a version and an upload."""
    owner = TestClient(app, base_url="https://testserver")
    assert owner.post(
        "/v1/auth/signup", json={"email": "victim@example.com", "password": PASSWORD}
    ).status_code == 201
    created = owner.post(
        "/v1/research", json={"objective": "Private research about Acme Corp", "defer_start": True}
    )
    session_id = created.json()["session_id"]

    with session_factory() as session:
        upload = Upload(
            session_id=UUID(session_id),
            filename="private.txt",
            content_type="text/plain",
            size_bytes=10,
            storage_key=f"uploads/{session_id}/private.txt",
            sha256="",
            processing_state=UploadState.PENDING,
        )
        session.add(upload)
        research = session.get(ResearchSession, UUID(session_id))
        assert research is not None and research.current_version_id is not None
        version = session.get(ResearchVersion, research.current_version_id)
        assert version is not None
        # A finished version with a failed export: every export route has
        # something real to refuse, including retry.
        version.status = VersionStatus.COMPLETE
        version.closed_at = utcnow()
        export = Export(
            version_id=version.id,
            format=ExportFormat.PDF,
            theme=ExportTheme.MINIMAL,
            status=ExportStatus.FAILED,
            storage_key=f"exports/{session_id}/private.pdf",
            error="failed",
        )
        session.add(export)
        session.commit()
        upload_id, export_id = str(upload.id), str(export.id)

    return Victim(session_id=session_id, upload_id=upload_id, export_id=export_id)


def stranger_account(app: FastAPI) -> TestClient:
    client = TestClient(app, base_url="https://testserver")
    assert client.post(
        "/v1/auth/signup", json={"email": "stranger@example.com", "password": PASSWORD}
    ).status_code == 201
    return client


def stranger_anonymous(app: FastAPI) -> TestClient:
    client = TestClient(app, base_url="https://testserver")
    assert client.post(
        "/v1/research", json={"objective": "The stranger's own research", "defer_start": True}
    ).status_code == 202
    return client


def nobody(app: FastAPI) -> TestClient:
    return TestClient(app, base_url="https://testserver")


STRANGERS: dict[str, Callable[[FastAPI], TestClient]] = {
    "another account": stranger_account,
    "another anonymous visitor": stranger_anonymous,
    "no session": nobody,
}


def test_every_parameterised_route_is_probed(app: FastAPI) -> None:
    """A new route with an identifier fails here until someone says how to probe it."""
    missing = parameterised_routes(app) - set(PROBES)
    assert not missing, f"routes with no isolation probe: {sorted(missing)}"


@pytest.mark.parametrize("stranger", sorted(STRANGERS))
@pytest.mark.parametrize(("method", "path"), sorted(PROBES))
def test_a_stranger_cannot_reach_someone_elses_research(
    app: FastAPI,
    session_factory: sessionmaker[Session],
    victim: Victim,
    stranger: str,
    method: str,
    path: str,
) -> None:
    client = STRANGERS[stranger](app)
    probe = PROBES[(method, path)]
    url = path.format(
        session_id=victim.session_id,
        version_number=1,
        upload_id=victim.upload_id,
        export_id=victim.export_id,
    )

    response = client.request(method, url, json=probe.body)

    allowed = probe.ok_statuses | ({401} if stranger == "no session" else set())
    assert response.status_code in allowed, (stranger, method, path, response.text)
    # Nothing the stranger received names the research or its contents.
    assert "Private research about Acme Corp" not in response.text
    assert "private.txt" not in response.text

    # And nothing changed for the owner.
    with session_factory() as session:
        research = session.get(ResearchSession, UUID(victim.session_id))
        assert research is not None and research.deleted_at is None
        upload = session.get(Upload, UUID(victim.upload_id))
        assert upload is not None and upload.deleted_at is None
        assert upload.processing_state is UploadState.PENDING
        export = session.get(Export, UUID(victim.export_id))
        assert export is not None and export.status is ExportStatus.FAILED
        assert session.execute(select(func.count(Export.id))).scalar_one() == 1


def test_history_shows_only_the_requesting_accounts_research(
    app: FastAPI, victim: Victim
) -> None:
    """`REQ-AUTH-005 AC-2`."""
    client = stranger_account(app)
    own = client.post(
        "/v1/research", json={"objective": "The stranger's own research", "defer_start": True}
    ).json()["session_id"]

    ids = {item["id"] for item in client.get("/v1/me/research").json()}

    assert ids == {own}
    assert victim.session_id not in ids


def test_a_guessed_identifier_reads_exactly_like_a_missing_one(
    app: FastAPI, victim: Victim
) -> None:
    """`REQ-SEC-009 AC-2`: knowing the id is not enough, and 404 never becomes 403."""
    client = stranger_account(app)

    real = client.get(f"/v1/research/{victim.session_id}")
    invented = client.get("/v1/research/01890000-0000-7000-8000-000000000000")

    assert real.status_code == invented.status_code == 404
    assert real.json() == invented.json()


def test_no_route_serves_research_without_an_owner(app: FastAPI) -> None:
    """`REQ-SEC-016`: there is no sharing surface to find."""
    paths = {route.path for route in app.routes if isinstance(route, APIRoute)}

    assert not any("share" in path or "public" in path for path in paths)
