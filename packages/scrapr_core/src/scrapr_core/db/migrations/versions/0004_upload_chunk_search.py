"""Full-text search over uploaded document chunks (`DEC-15`, closing `OPEN-12`).

`OPEN-12` asked whether document retrieval needs vector search. `DEC-15` says
no: the corpus is one user's ten files, which is exactly the size where keyword
recall is high, and pgvector is a real infrastructure commitment that a baseline
migration must not make by accident.

**A GIN index on an expression, not a stored column.** A `tsvector` column would
have to be kept in step with `text` by a trigger or by application code, and the
day those disagree the index silently stops finding rows that are in the table.
The expression index cannot drift, because there is nothing to drift from.

The expression here is `to_tsvector('english', text)` and it must stay
character-identical to the one `tools/impl/documents.py` builds, or Postgres
will plan a sequential scan and the index will look present while doing nothing.

Revision ID: 0004
Revises: 0003
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "ix_upload_chunks_fts"


def upgrade() -> None:
    op.execute(
        f"CREATE INDEX {_INDEX} ON upload_chunks "
        "USING GIN (to_tsvector('english', text))"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_INDEX}")
