"""Retrieval diagnostics on tool invocations.

- `tool_invocations.result_items`: how many items a successful call returned,
  so an empty success is distinguishable from evidence lost after retrieval.
- `tool_invocations.error_detail`: the provider's clipped, redacted reason for
  a failure, so a `blocked` row says whether it was a bad key, a retired
  endpoint or a plan limit.

Both nullable: no existing row is rewritten.

Revision ID: 0008
Revises: 0007
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tool_invocations", sa.Column("result_items", sa.Integer(), nullable=True))
    op.add_column("tool_invocations", sa.Column("error_detail", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("tool_invocations", "error_detail")
    op.drop_column("tool_invocations", "result_items")
