"""Rate limits, load shedding and request guards (`REQ-SEC-006`, `REQ-SEC-010`, `DEC-23`, `DEC-24`).

Each limit is exercised to the request that breaks it, because a limit tested
only below its threshold is a limit nobody has seen work.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from scrapr_api.main import create_app
from scrapr_core.config import Settings, get_settings
from scrapr_core.db.models import User
from scrapr_core.security.limits import EXPORT, RESEARCH

pytestmark = pytest.mark.integration

OBJECTIVE = {"objective": "How is Acme Corp positioned against its competitors?", "defer_start": True}


@pytest.fixture
def settings_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


def fresh(app: FastAPI) -> TestClient:
    return TestClient(app, base_url="https://testserver")


def test_an_anonymous_session_is_limited_on_starting_research(client: TestClient) -> None:
    """`REQ-SEC-010 AC-1`, per anonymous session."""
    allowed = RESEARCH.anonymous[0].limit
    statuses = [client.post("/v1/research", json=OBJECTIVE).status_code for _ in range(allowed + 1)]

    assert statuses[:allowed] == [202] * allowed
    assert statuses[-1] == 429
    refused = client.post("/v1/research", json=OBJECTIVE)
    assert refused.json()["error"]["code"] == "rate_limited"
    assert int(refused.headers["retry-after"]) > 0
    # `AC-5`: nothing about counters, windows or limits in the message.
    assert "limit" not in refused.json()["error"]["message"].lower()


def test_clearing_cookies_does_not_escape_the_address_limit(app: FastAPI) -> None:
    """The anonymous flow's hole (`OPEN-18`): a new session per request."""
    allowed = RESEARCH.address[0].limit
    statuses = [fresh(app).post("/v1/research", json=OBJECTIVE).status_code for _ in range(allowed + 1)]

    assert statuses.count(202) == allowed
    assert statuses[-1] == 429


def test_an_account_has_its_own_larger_allowance_and_usage_is_recorded(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    """`REQ-SEC-010 AC-1` per account, and `REQ-OBS-008` usage metadata."""
    client.post("/v1/auth/signup", json={"email": "reader@example.com", "password": "a long enough password"})
    anonymous_allowance = RESEARCH.anonymous[0].limit

    statuses = [client.post("/v1/research", json=OBJECTIVE).status_code for _ in range(anonymous_allowance + 1)]

    assert statuses == [202] * (anonymous_allowance + 1)
    with session_factory() as session:
        user = session.execute(select(User)).scalar_one()
    assert user.usage_meta["research_started"] == anonymous_allowance + 1
    assert "last_active_at" in user.usage_meta


def test_new_research_is_shed_when_the_queue_is_full(
    app: FastAPI, settings_env: pytest.MonkeyPatch
) -> None:
    """`NFR-SCALE-002`, `TBD-13`: refused at the door with a plain 503."""
    settings_env.setenv("QUEUE_SHED_THRESHOLD", "2")
    get_settings.cache_clear()
    queued = {"objective": OBJECTIVE["objective"]}

    first, second = fresh(app), fresh(app)
    assert first.post("/v1/research", json=queued).status_code == 202
    assert second.post("/v1/research", json=queued).status_code == 202

    shed = fresh(app).post("/v1/research", json=queued)
    assert shed.status_code == 503
    assert shed.json()["error"]["code"] == "busy"
    assert shed.headers["retry-after"]


def test_exports_are_rate_limited(client: TestClient) -> None:
    """`REQ-SEC-010 AC-2`. Counted even when the version is not ready to export."""
    session_id = client.post("/v1/research", json=OBJECTIVE).json()["session_id"]
    path = f"/v1/research/{session_id}/versions/1/exports"
    body = {"format": "pdf", "theme": "minimal"}

    statuses = [client.post(path, json=body).status_code for _ in range(EXPORT.anonymous[0].limit + 1)]

    assert set(statuses[:-1]) == {409}  # not ready, but counted
    assert statuses[-1] == 429


def test_a_follow_up_needs_a_finished_report(client: TestClient) -> None:
    session_id = client.post("/v1/research", json=OBJECTIVE).json()["session_id"]

    response = client.post(f"/v1/research/{session_id}/messages", json={"question": "What changed?"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "research_not_ready"


# --------------------------------------------------------------------------
# Request guards
# --------------------------------------------------------------------------


def test_an_oversized_body_is_refused_before_parsing(client: TestClient) -> None:
    response = client.post(
        "/v1/research",
        content=b"{" + b" " * (1024 * 1024 + 10) + b"}",
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"


def test_a_write_from_another_origin_is_refused(client: TestClient) -> None:
    refused = client.post("/v1/research", json=OBJECTIVE, headers={"origin": "https://evil.example"})
    allowed = client.post("/v1/research", json=OBJECTIVE, headers={"origin": "http://localhost:3000"})

    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "forbidden_origin"
    assert allowed.status_code == 202


def test_reads_from_another_origin_are_left_to_cors(client: TestClient) -> None:
    """Only state-changing methods are refused here; CORS governs reads."""
    response = client.get("/health", headers={"origin": "https://evil.example"})
    assert response.status_code == 200


# --------------------------------------------------------------------------
# Least privilege — `REQ-SEC-006`
# --------------------------------------------------------------------------


PRODUCTION = {
    "SCRAPR_ENV": "production",
    "DATABASE_URL": "postgresql+psycopg://app:secret@db.example.com/scrapr?sslmode=require",
    "STORAGE_ENDPOINT_URL": "https://account.r2.cloudflarestorage.com",
    "STORAGE_SECRET_KEY": "a-real-secret",
    "WEB_ORIGINS": "https://scrapr.example.com",
}


@pytest.mark.parametrize("key", ["TAVILY_API_KEY", "FMP_API_KEY", "ADZUNA_APP_KEY", "SEC_EDGAR_USER_AGENT"])
def test_the_api_refuses_to_hold_a_workers_credentials(key: str) -> None:
    settings = Settings.model_validate({**PRODUCTION, key: "present"})
    assert any(key in problem for problem in settings.api_privilege_problems())


def test_an_api_with_only_its_own_credentials_starts(settings_env: pytest.MonkeyPatch) -> None:
    for name, value in PRODUCTION.items():
        settings_env.setenv(name, value)
    for name in ("TAVILY_API_KEY", "FMP_API_KEY", "ADZUNA_APP_ID", "ADZUNA_APP_KEY", "SEC_EDGAR_USER_AGENT"):
        settings_env.setenv(name, "")
    get_settings.cache_clear()

    assert create_app() is not None

    settings_env.setenv("TAVILY_API_KEY", "leaked-into-the-api")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="TAVILY_API_KEY"):
        create_app()
