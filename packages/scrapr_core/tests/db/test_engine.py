"""The engine every process connects through (`db/engine.py`).

**Behind Supabase's pooler, a server connection outlives the client that used
it.** Supavisor hands the same Postgres backend to one client after another,
and psycopg 3 names the statements it prepares `_pg3_0`, `_pg3_1`, … counting
from zero on every new client connection. Left at its default, psycopg prepares
any query it has run five times — so the next client to inherit that backend
prepares `_pg3_0` again and Postgres refuses: `prepared statement "_pg3_1"
already exists`. That is the production failure on `anonymous_sessions`.

A plain local Postgres never shares a backend, so the failure cannot happen here
on its own. These tests recreate the pooler's leftover state directly — a
backend that already holds `_pg3_0` and `_pg3_1` — which is exactly what a
pooled client inherits, and then run the query production failed on.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import Connection, Engine, create_engine, select, text
from sqlalchemy.exc import ProgrammingError

from scrapr_core.config import Settings
from scrapr_core.db.engine import build_engine
from scrapr_core.db.models import AnonymousSession

pytestmark = pytest.mark.integration

# Comfortably past psycopg's default `prepare_threshold` of five executions.
REPEATS = 12


@pytest.fixture
def app_engine(migrated_engine: Engine) -> Iterator[Engine]:
    """The application's own engine, pointed at the migrated scratch database."""
    url = migrated_engine.url.render_as_string(hide_password=False)
    engine = build_engine(Settings.model_validate({"DATABASE_URL": url}))
    try:
        yield engine
    finally:
        engine.dispose()


def _inherit_a_pooled_backend(connection: Connection) -> None:
    """Leave the statements a previous pooled client would have left behind."""
    connection.exec_driver_sql("PREPARE _pg3_0 AS SELECT 1")
    connection.exec_driver_sql("PREPARE _pg3_1 AS SELECT 2")


def _resolve_anonymous_session_repeatedly(connection: Connection) -> None:
    """The statement `AnonymousSessionRepository` runs on every request."""
    query = select(AnonymousSession).where(AnonymousSession.token_hash == "no-such-token")
    for _ in range(REPEATS):
        connection.execute(query).first()


def _server_prepared_statements(connection: Connection) -> set[str]:
    return set(connection.execute(text("SELECT name FROM pg_prepared_statements")).scalars())


def test_the_engine_keeps_the_psycopg3_driver(app_engine: Engine) -> None:
    """The fix is a driver option, not a driver change: still psycopg 3."""
    assert app_engine.dialect.driver == "psycopg"
    assert app_engine.url.drivername == "postgresql+psycopg"


def test_driver_connections_never_prepare_statements(app_engine: Engine) -> None:
    """`prepare_threshold=None` is psycopg's switch for "never prepare"."""
    with app_engine.connect() as connection:
        driver_connection = connection.connection.dbapi_connection
        assert driver_connection is not None
        assert getattr(driver_connection, "prepare_threshold", "missing") is None


def test_a_repeated_query_leaves_nothing_prepared_on_the_server(app_engine: Engine) -> None:
    """Nothing prepared means nothing for the next pooled client to collide with."""
    with app_engine.connect() as connection:
        _resolve_anonymous_session_repeatedly(connection)

        assert _server_prepared_statements(connection) == set()


def test_a_backend_inherited_from_the_pooler_does_not_collide(app_engine: Engine) -> None:
    """The production failure, reproduced against the application's engine."""
    with app_engine.connect() as connection:
        _inherit_a_pooled_backend(connection)

        _resolve_anonymous_session_repeatedly(connection)

        # Only what was inherited; the application added nothing of its own.
        assert _server_prepared_statements(connection) == {"_pg3_0", "_pg3_1"}


def test_psycopg_defaults_do_collide_on_an_inherited_backend(migrated_engine: Engine) -> None:
    """The control. If this stops failing, psycopg changed how it names or
    prepares statements, and the test above no longer proves anything."""
    engine = create_engine(migrated_engine.url)
    try:
        with engine.connect() as connection:
            _inherit_a_pooled_backend(connection)

            with pytest.raises(ProgrammingError, match="already exists"):
                _resolve_anonymous_session_repeatedly(connection)
    finally:
        engine.dispose()


def test_ordinary_sessions_still_work(app_engine: Engine) -> None:
    """Parameters, transactions and pre-ping behave as they did."""
    with app_engine.begin() as connection:
        assert connection.execute(text("SELECT :value"), {"value": 42}).scalar() == 42
        _resolve_anonymous_session_repeatedly(connection)
    assert app_engine.pool._pre_ping
