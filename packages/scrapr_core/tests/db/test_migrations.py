"""The migrations are what ship, so the migrations are what is tested.

Two properties matter and neither is obvious from reading the file:

1. **Applying them produces exactly the models.** Drift between `Base.metadata`
   and the migration history is how a schema ends up correct in development and
   wrong in production.
2. **They reverse cleanly.** A downgrade that leaves an enum type behind makes
   the next upgrade fail with "type already exists" — on a preview database that
   is recycled, which is precisely where nobody is watching.

Both need a live server, so both are marked `integration`.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from db_support import (
    alembic_config,
    drop_database,
    ensure_database_exists,
    scratch_database_url,
)
from sqlalchemy import Engine, create_engine, text

from scrapr_core.db.models import Base

pytestmark = pytest.mark.integration


@pytest.fixture
def roundtrip_engine() -> Iterator[Engine]:
    """A disposable database of its own.

    The upgrade/downgrade test destroys the schema it runs against, so it cannot
    share the session-scoped one every other test reads.
    """
    url = scratch_database_url(suffix="_roundtrip")
    ensure_database_exists(url)
    engine = create_engine(url)
    try:
        yield engine
    finally:
        engine.dispose()
        drop_database(url)


def test_migrations_reproduce_the_models_exactly(migrated_engine: Engine) -> None:
    """Autogenerate against the migrated database must find nothing to do."""
    with migrated_engine.connect() as connection:
        context = MigrationContext.configure(
            connection,
            opts={"compare_type": True, "compare_server_default": True},
        )
        diff = compare_metadata(context, Base.metadata)

    assert diff == [], f"schema has drifted from the models: {diff}"


def test_upgrade_downgrade_upgrade_is_clean(roundtrip_engine: Engine) -> None:
    """A full reversal leaves nothing behind, and re-applying still works.

    The enum types are the part Alembic does not generate: it tracks tables, and
    a native enum outlives every table that used it.
    """
    config = alembic_config(roundtrip_engine.url)

    command.upgrade(config, "head")
    command.downgrade(config, "base")

    with roundtrip_engine.connect() as connection:
        tables = connection.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public'"
            )
        ).scalars().all()
        enum_types = connection.execute(
            text(
                "SELECT t.typname FROM pg_type t "
                "JOIN pg_namespace n ON n.oid = t.typnamespace "
                "WHERE n.nspname = 'public' AND t.typtype = 'e'"
            )
        ).scalars().all()

    # Alembic's own bookkeeping table is expected to survive a downgrade.
    assert set(tables) == {"alembic_version"}
    assert enum_types == []

    # And the whole thing applies again on top of what the downgrade left.
    command.upgrade(config, "head")


def test_the_cyclic_foreign_key_actually_exists(migrated_engine: Engine) -> None:
    """`research_sessions.current_version_id` -> `research_versions.id`.

    It is declared `use_alter=True` because the two tables reference each other.
    `op.create_table` does not honour that — it silently omits the constraint —
    so the migration adds it explicitly, and this test is what stops that edit
    being lost the next time the migration is regenerated.
    """
    with migrated_engine.connect() as connection:
        constraints = connection.execute(
            text(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = 'research_sessions'::regclass AND contype = 'f'"
            )
        ).scalars().all()

    assert "fk_research_sessions_current_version_id_research_versions" in constraints
