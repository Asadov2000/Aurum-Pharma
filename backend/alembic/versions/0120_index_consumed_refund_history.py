"""index consumed refund history validation

Revision ID: 0120
Revises: 0119
Create Date: 2026-08-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0120"
down_revision: str | Sequence[str] | None = "0119"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_pos_refund_attempt_consumed_sale",
        "pos_refund_attempt",
        ["tenant_id", "parent_sale_id"],
        unique=False,
        postgresql_where=sa.text("status = 'consumed'"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_pos_refund_attempt_consumed_sale",
        table_name="pos_refund_attempt",
    )
