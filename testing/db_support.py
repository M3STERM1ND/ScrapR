"""Helpers for tests that need a real PostgreSQL database.

Shared by the core suite and the API suite, so both migrate one scratch database
and cannot disagree about the schema. Reachable from either because
`pytest.ini_options.pythonpath` puts this directory on the path — a conftest
would not be importable, which is the other half of why it lives here.
"""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url

from scrapr_core.config import get_settings

TEST_DATABASE_SUFFIX = "_test"

# testing/db_support.py -> the repository root, where alembic.ini lives.
REPO_ROOT = Path(__file__).resolve().parents[1]


def scratch_database_url(suffix: str = TEST_DATABASE_SUFFIX) -> URL:
    """The configured database name plus a suffix. Never the development one."""
    url = make_url(get_settings().database_url)
    return url.set(database=f"{url.database}{suffix}")


def drop_database(url: URL) -> None:
    """Drop a scratch database, terminating anything still connected to it."""
    maintenance = create_engine(url.set(database="postgres"))
    with maintenance.connect().execution_options(
        isolation_level="AUTOCOMMIT"
    ) as connection:
        connection.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :name AND pid <> pg_backend_pid()"
            ),
            {"name": url.database},
        )
        connection.execute(text(f'DROP DATABASE IF EXISTS "{url.database}"'))
    maintenance.dispose()


def ensure_database_exists(url: URL) -> None:
    """Create the scratch database if it is not there yet.

    `CREATE DATABASE` cannot run inside a transaction, hence the autocommit
    isolation level, and it is issued against the maintenance database because
    the target may not exist to connect to.
    """
    maintenance = create_engine(url.set(database="postgres"))
    with maintenance.connect().execution_options(
        isolation_level="AUTOCOMMIT"
    ) as connection:
        exists = connection.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :name"),
            {"name": url.database},
        ).scalar()
        if not exists:
            # The database name is derived from configuration, never from test
            # input, so interpolation here is not an injection surface — and
            # CREATE DATABASE takes no parameters.
            connection.execute(text(f'CREATE DATABASE "{url.database}"'))
    maintenance.dispose()


def alembic_config(url: URL) -> Config:
    """Alembic configuration pointed at the scratch database.

    `script_location` is made absolute because `alembic.ini` states it relative
    to the repository root, and pytest is run from wherever the developer
    happens to be.
    """
    config = Config(REPO_ROOT / "alembic.ini")
    config.set_main_option(
        "script_location",
        str(REPO_ROOT / "packages/scrapr_core/src/scrapr_core/db/migrations"),
    )
    config.set_main_option("sqlalchemy.url", url.render_as_string(hide_password=False))
    return config
