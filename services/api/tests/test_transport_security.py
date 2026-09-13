"""Transport and caching guarantees (`REQ-SEC-003`, `REQ-SEC-004`, `REQ-SEC-007 AC-3`).

TLS itself is terminated by the platform, so what is testable here is what the
application insists on: HSTS outside development, no caching of private
responses, and a production configuration that refuses to start when it would
send data in the clear.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from scrapr_api.main import create_app
from scrapr_core.config import Settings, get_settings

PRODUCTION_SAFE = {
    "SCRAPR_ENV": "production",
    "DATABASE_URL": "postgresql+psycopg://app:secret@db.example.com:5432/scrapr?sslmode=require",
    "STORAGE_ENDPOINT_URL": "https://account.r2.cloudflarestorage.com",
    "STORAGE_SECRET_KEY": "a-real-secret",
    "WEB_ORIGINS": "https://scrapr.example.com",
}


def test_every_response_is_private_and_unsniffable(client: TestClient) -> None:
    response = client.get("/health")

    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_errors_carry_the_same_headers(client: TestClient) -> None:
    response = client.get("/v1/research/01890000-0000-7000-8000-000000000000")

    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store"


def test_hsts_is_not_sent_in_local_development(client: TestClient) -> None:
    assert "strict-transport-security" not in client.get("/health").headers


def test_hsts_is_sent_outside_local_development(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in PRODUCTION_SAFE.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("SCRAPR_ENV", "preview")
    get_settings.cache_clear()
    try:
        with TestClient(create_app(), base_url="https://testserver") as client:
            header = client.get("/health").headers["strict-transport-security"]
    finally:
        get_settings.cache_clear()

    assert "max-age=63072000" in header


@pytest.mark.parametrize(
    ("override", "fragment"),
    [
        ({"STORAGE_ENDPOINT_URL": "http://storage.example.com"}, "STORAGE_ENDPOINT_URL"),
        ({"WEB_ORIGINS": "http://scrapr.example.com"}, "WEB_ORIGINS"),
        (
            {"DATABASE_URL": "postgresql+psycopg://app:secret@db.example.com/scrapr"},
            "sslmode",
        ),
        ({"STORAGE_SECRET_KEY": "scrapr_local_dev_only"}, "development credentials"),
    ],
)
def test_production_refuses_a_configuration_that_would_leak(
    override: dict[str, str], fragment: str
) -> None:
    settings = Settings.model_validate({**PRODUCTION_SAFE, **override})

    problems = settings.production_problems()

    assert any(fragment in problem for problem in problems), problems


def test_a_safe_production_configuration_has_no_problems() -> None:
    assert Settings.model_validate(PRODUCTION_SAFE).production_problems() == []


def test_the_api_will_not_start_on_an_unsafe_production_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in PRODUCTION_SAFE.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("STORAGE_ENDPOINT_URL", "http://storage.example.com")
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="refusing to start"):
            create_app()
    finally:
        get_settings.cache_clear()


def test_development_is_not_held_to_production_rules() -> None:
    assert Settings.model_validate({"SCRAPR_ENV": "local"}).production_problems() == []
