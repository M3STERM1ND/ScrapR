"""Observability columns (`REQ-OBS-001..006`, `DEC-24`, `DEC-25`).

- `research_runs.failure_stage` and `failure_kind`: a failed run records which
  step ended it and why, as a category a query can group by
  (`REQ-OBS-001 AC-2`).
- `tool_invocations.source_domain`: paywalled, blocked and unreachable outcomes
  counted per domain (`REQ-OBS-006`), and an index on `created_at` for the
  operator report's time windows.
- `exports.render_ms`: generation latency, for `TBD-08` and `TBD-09`.

All nullable additions: no existing row is rewritten.

Revision ID: 0007
Revises: 0006
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("exports", sa.Column("render_ms", sa.Integer(), nullable=True))
    op.add_column("research_runs", sa.Column("failure_stage", sa.Text(), nullable=True))
    op.add_column("research_runs", sa.Column("failure_kind", sa.Text(), nullable=True))
    op.add_column("tool_invocations", sa.Column("source_domain", sa.Text(), nullable=True))
    op.create_index("ix_tool_invocations_created_at", "tool_invocations", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_tool_invocations_created_at", table_name="tool_invocations")
    op.drop_column("tool_invocations", "source_domain")
    op.drop_column("research_runs", "failure_kind")
    op.drop_column("research_runs", "failure_stage")
    op.drop_column("exports", "render_ms")
