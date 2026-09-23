"""optimize Edge inventory page reads

Revision ID: 0150
Revises: 0149
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0150"
down_revision: str | Sequence[str] | None = "0149"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
      DROP INDEX public.ix_sync_read_inventory_projection_available
    """)
    op.execute("""
      CREATE INDEX ix_sync_read_inventory_projection_available
      ON public.sync_read_inventory_projection (
        tenant_id, branch_id, node_id, snapshot_id, batch_id
      ) WHERE qty_remaining > 0
    """)


def downgrade() -> None:
    op.execute("DROP INDEX public.ix_sync_read_inventory_projection_available")
    op.execute("""
      CREATE INDEX ix_sync_read_inventory_projection_available
      ON public.sync_read_inventory_projection (
        tenant_id, branch_id, node_id, snapshot_id
      ) WHERE qty_remaining > 0
    """)
