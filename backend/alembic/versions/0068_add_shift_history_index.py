"""pos: index the tenant shift history

Revision ID: 0068
Revises: 0067
Create Date: 2026-07-26
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0068"
down_revision: str | Sequence[str] | None = "0067"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX ix_shift_tenant_status_opened "
        "ON public.shift (tenant_id, status, opened_at DESC, id DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX public.ix_shift_tenant_status_opened")
