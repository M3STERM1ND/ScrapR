"""The export row becomes its own job (`DEC-21`).

An export is one unit of work with no stage to resume from, and the research
run machinery closes versions and moves session status — neither of which an
export may touch. So the `exports` row carries its own lease: `attempts`, and
`lease_expires_at` so a worker that dies mid-render loses the export to another.
`size_bytes` records what was stored, and the index serves the claim query.

`attempts` is NOT NULL. Existing rows get 0 through a server default that is
dropped again at once, because the models set defaults application-side and a
lingering server default would be drift `alembic check` reports.

Revision ID: 0006
Revises: 0005
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "exports",
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.alter_column("exports", "attempts", server_default=None)
    op.add_column("exports", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("exports", sa.Column("size_bytes", sa.BigInteger(), nullable=True))
    op.create_index("ix_exports_status_created_at", "exports", ["status", "created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_exports_status_created_at", table_name="exports")
    op.drop_column("exports", "size_bytes")
    op.drop_column("exports", "lease_expires_at")
    op.drop_column("exports", "attempts")
