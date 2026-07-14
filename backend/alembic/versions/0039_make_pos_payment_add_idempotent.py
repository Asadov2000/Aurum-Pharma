"""pos: make payment addition retry-safe

Revision ID: 0039
Revises: 0038
Create Date: 2026-07-14
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0039"
down_revision: str | Sequence[str] | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE sale_payment ADD COLUMN operation_id UUID")
    op.execute("ALTER TABLE sale_payment ADD COLUMN operation_hash TEXT")
    op.execute(
        "ALTER TABLE sale_payment ADD CONSTRAINT ck_sp_operation_pair "
        "CHECK ((operation_id IS NULL) = (operation_hash IS NULL))"
    )
    op.execute(
        "ALTER TABLE sale_payment ADD CONSTRAINT ck_sp_operation_hash "
        "CHECK (operation_hash IS NULL OR operation_hash ~ '^[0-9a-f]{64}$')"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_sale_payment_tenant_operation "
        "ON sale_payment (tenant_id, operation_id) WHERE operation_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_sale_payment_tenant_operation")
    op.execute("ALTER TABLE sale_payment DROP CONSTRAINT IF EXISTS ck_sp_operation_hash")
    op.execute("ALTER TABLE sale_payment DROP CONSTRAINT IF EXISTS ck_sp_operation_pair")
    op.execute("ALTER TABLE sale_payment DROP COLUMN operation_hash")
    op.execute("ALTER TABLE sale_payment DROP COLUMN operation_id")
