"""Declarative base, naming conventions and the column shapes every table reuses.

Two decisions live here and are worth stating once rather than repeating in
eleven model files.

**Constraint names are generated from a convention.** Postgres names anonymous
constraints itself, and those names differ between a database built by
`create_all` and one built by a migration. A convention makes every index,
unique constraint, check and foreign key deterministic, which is what lets
`alembic revision --autogenerate` produce a diff that is about the schema rather
than about naming noise.

**Defaults are application-side, not `server_default`.** Identifiers are UUIDv7
generated in Python (`domain.ids`), and timestamps follow them for the same
reason: one source of truth, no dependency on a database function, and values
that exist before the row reaches the database so a test can assert on them
without a connection.
"""

from __future__ import annotations

import datetime as dt
import decimal
from typing import Annotated, Any, ClassVar
from uuid import UUID

from sqlalchemy import DateTime, MetaData, Numeric, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, mapped_column
from sqlalchemy.types import TypeEngine

from scrapr_core.domain.ids import new_id

__all__ = [
    "NAMING_CONVENTION",
    "Base",
    "CreatedAt",
    "Json",
    "UuidPk",
    "utcnow",
]

Json = dict[str, Any]
"""A `jsonb` document. Mapped by `Base.type_annotation_map`."""

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> dt.datetime:
    """Timezone-aware current time. Every timestamp column stores UTC."""
    return dt.datetime.now(dt.UTC)


class Base(DeclarativeBase):
    """Declarative base for every ScrapR table.

    `type_annotation_map` is what keeps the model files readable: a column is
    declared as `Mapped[dt.datetime]` or `Mapped[Json]` and the storage type
    follows from the annotation, including through `| None`. Getting these three
    mappings right once removes the most common schema mistakes from every
    model file at a stroke.
    """

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    type_annotation_map: ClassVar[dict[Any, TypeEngine[Any]]] = {
        # `timestamptz`, never a naive `timestamp`. Retrieval and publication
        # times are compared across sources and time zones (`REQ-EVID-004`,
        # `REQ-TOOL-007 AC-1`), and a naive column silently discards the only
        # thing that makes that comparison valid.
        dt.datetime: DateTime(timezone=True),
        # `text`, not `varchar(n)`. Postgres stores them identically and an
        # arbitrary length cap on extracted content is a future outage.
        str: Text(),
        # `jsonb`, never `json`: indexable, and normalised on write.
        Json: JSONB(),
        # Unconstrained `numeric`. Financial values arrive at whatever precision
        # the source published, and rounding them at the storage layer would
        # destroy the raw value that `REQ-EVID-008` requires be retained.
        decimal.Decimal: Numeric(),
    }


UuidPk = Annotated[UUID, mapped_column(primary_key=True, default=new_id)]
"""Primary key: UUIDv7, generated in Python.

Never ``default uuidv7()`` in the database. That function only exists in
PostgreSQL 18 and later, so generating here removes the version dependency
entirely.
"""

CreatedAt = Annotated[dt.datetime, mapped_column(default=utcnow)]
"""A ``timestamptz`` that defaults to now, application-side."""
