"""catalog: index manufacturer search

Revision ID: 0070
Revises: 0069
Create Date: 2026-07-28
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0070"
down_revision: str | Sequence[str] | None = "0069"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE INDEX ix_tc_manufacturer_trgm
        ON tenant_catalog USING gin (manufacturer gin_trgm_ops)
        WHERE deleted_at IS NULL AND manufacturer IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_tc_manufacturer_trgm")
