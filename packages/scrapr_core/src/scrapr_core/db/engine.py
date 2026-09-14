"""Engine and session construction.

**Synchronous SQLAlchemy.** The worker is the throughput-critical path and it
scales by process, not by concurrency inside one event loop, so async buys it
nothing while costing every repository an `await`. The API is a thin read layer
over denormalised payloads (implementation plan §6.2). FastAPI runs `def`
endpoints in a threadpool, which covers it.

This is an implementation-level choice, recorded here rather than argued in
every module. Should a route appear that genuinely needs async database access,
the psycopg driver already supports it and the change is scoped to that path.

**No server-side prepared statements.** Production connects through Supabase's
pooler, which hands one Postgres backend to client after client. psycopg 3
prepares any query it has run `prepare_threshold` times (five, by default) and
names it `_pg3_<n>`, counting from zero again on every new connection — so a
client that inherits a backend a previous client prepared on collides with it:
`prepared statement "_pg3_1" already exists`. The statements this application
runs are short and plan in microseconds, so preparing them buys nothing worth a
failure that only appears behind the pooler. It is off everywhere, not only in
production, so the test suite runs the configuration that ships.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Final

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from scrapr_core.config import Settings, get_settings

__all__ = ["PSYCOPG_CONNECT_ARGS", "build_engine", "build_session_factory", "session_scope"]

PSYCOPG_CONNECT_ARGS: Final[dict[str, object]] = {
    # psycopg 3's switch for "never prepare on the server". Passed through
    # SQLAlchemy to `psycopg.Connection.connect` for every pooled connection.
    "prepare_threshold": None,
}


def build_engine(settings: Settings | None = None) -> Engine:
    """Create an engine for the configured database.

    `pool_pre_ping` costs one round trip per checkout and removes the entire
    class of "first query after an idle period fails": managed Postgres and
    connection proxies both close idle connections without telling the client.
    """
    resolved = settings or get_settings()
    return create_engine(
        resolved.database_url,
        pool_pre_ping=True,
        future=True,
        # A copy, so no engine can alter the arguments another one is built with.
        connect_args=dict(PSYCOPG_CONNECT_ARGS),
    )


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a session factory.

    `expire_on_commit=False` because the caller frequently reads attributes off
    an object after the transaction closes — returning a freshly written row
    from a repository, for instance. The default would emit a fresh SELECT per
    attribute, or fail outright once the session is gone.
    """
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """A transaction that commits on success and rolls back on any exception."""
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
