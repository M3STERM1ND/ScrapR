"""Fixtures shared by every suite that needs a real PostgreSQL database.

**A real database, not SQLite.** The schema is built out of `citext`, native
enums, `jsonb`, `numeric` and `num_nonnulls()`. A SQLite stand-in would either
fail to create the schema or, worse, create a different one and let a test pass
against a database that does not exist in production.

**A scratch database, migrated once per session.** Tests run against
`<database>_test`, created if absent, so a test run cannot touch development
data. Each test then runs inside a transaction that is rolled back, which is
both faster than recreating the schema and a stronger isolation guarantee than
deleting rows afterwards.

If no server is reachable the whole module skips rather than fails: `docker
compose up -d` is a setup step, and a developer who has not run it should see
that, not a wall of connection errors.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from alembic import command
from db_support import alembic_config, ensure_database_exists, scratch_database_url
from sqlalchemy import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from scrapr_core.config import Settings
from scrapr_core.db.engine import build_engine


@pytest.fixture(scope="session")
def migrated_engine() -> Iterator[Engine]:
    """An engine pointed at a scratch database with every migration applied.

    Migrating rather than calling `metadata.create_all` is deliberate: it is the
    migrations that run in production, so they are what the tests must exercise.
    A schema that only `create_all` can build is a schema nobody can deploy.
    """
    url = scratch_database_url()
    try:
        ensure_database_exists(url)
    except OperationalError as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"no PostgreSQL server reachable ({exc.orig}); run `docker compose up -d`")

    command.upgrade(alembic_config(url), "head")

    # The application's own engine, so every database test runs the connection
    # options production runs — prepared statements off included.
    engine = build_engine(
        Settings.model_validate({"DATABASE_URL": url.render_as_string(hide_password=False)})
    )
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(migrated_engine: Engine) -> Iterator[Session]:
    """A session whose work is rolled back when the test ends."""
    connection = migrated_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        # A test that provoked an IntegrityError has already rolled the
        # transaction back through the session, which deassociates it from the
        # connection; rolling back again warns.
        if transaction.is_active:
            transaction.rollback()
        connection.close()
