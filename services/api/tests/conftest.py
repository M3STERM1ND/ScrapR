"""Fixtures for the HTTP suite.

The API is tested against a **real database and real repositories**, with only
the tool and model providers faked. Replacing the repository layer with a mock
here would test the mock: ownership scoping is the property most worth checking
at this boundary, and it lives in the repositories.

The database session is swapped through FastAPI's own dependency override, so
every line of route code that runs in production runs here too.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from scrapr_api import deps
from scrapr_api.main import create_app
from scrapr_core.db.models import Base


@pytest.fixture
def session_factory(migrated_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=migrated_engine, expire_on_commit=False)


@pytest.fixture
def app(session_factory: sessionmaker[Session]) -> FastAPI:
    """The real application, with only its database session swapped."""

    def override() -> Iterator[Session]:
        session = session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    application = create_app()
    application.dependency_overrides[deps.db_session] = override
    return application


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    """A client that surfaces an unhandled server error as a raised exception.

    That is the right default for a test suite: a 500 that a test quietly
    asserts against is a bug nobody reads the traceback for. The one test that
    *is* about the 500 envelope builds its own client.

    `https`, because the session cookie is `Secure`: over http the client would
    accept it and then never send it back, and every read would 401 for a reason
    that has nothing to do with the code under test.
    """
    with TestClient(app, base_url="https://testserver") as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def _empty_afterwards(session_factory: sessionmaker[Session]) -> Iterator[None]:
    """Truncate between tests.

    The rollback fixture the core suite uses cannot serve here: a request
    commits, which is the behaviour under test.
    """
    yield
    tables = ", ".join(sorted(Base.metadata.tables))
    with session_factory() as session:
        session.execute(text(f"TRUNCATE {tables} CASCADE"))
        session.commit()
