"""Evidence records whether a figure was reported or estimated.

`REQ-TOOL-004 AC-3` requires estimates be distinguishable from reported values,
and `DEC-10 §4.2` is what needs the distinction: an analyst estimate of $1.3bn
against a filed $1.2bn is not a source being wrong, so it must not raise a
conflict. Without somewhere to persist the basis, that exclusion cannot fire —
the providers were already reporting it in their structured payload and the
column to keep it in did not exist.

The reporting-period columns needed no migration; `0001` already had them. They
were simply never written, which is the other half of the same defect.

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "evidence",
        # No column comment: the model carries the explanation, and a comment
        # here that the model does not declare shows up as schema drift on
        # every `alembic check`.
        sa.Column("value_basis", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("evidence", "value_basis")
