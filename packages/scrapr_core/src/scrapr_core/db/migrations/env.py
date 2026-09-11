"""Alembic environment.

The database URL comes from `scrapr_core.config`, not from `alembic.ini`, so
there is exactly one place a connection string is configured and a migration
cannot be applied to a database the application is not using.

`compare_type` and `compare_server_default` are on because the default
autogenerate ignores both, which is how a `text` column quietly stays a
`varchar(255)` forever.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

from scrapr_core.config import get_settings
from scrapr_core.db.models import Base

config = context.config

# A caller that already set a URL wins — that is how the integration tests point
# Alembic at a scratch database. Otherwise the application configuration decides,
# so a migration cannot be applied to a database the application is not using.
if not config.get_main_option("sqlalchemy.url", None):
    config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a live connection (`alembic upgrade --sql`).

    This is what a gated production deployment reviews before applying.
    """
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations against a live connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
