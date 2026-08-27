"""add immutable refund fields to the sale sync projection

Revision ID: 0114
Revises: 0113
Create Date: 2026-08-27
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0114"
down_revision: str | Sequence[str] | None = "0113"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE public.sync_sale_projection
          ADD COLUMN sale_type TEXT NOT NULL DEFAULT 'sale',
          ADD COLUMN parent_sale_id UUID,
          ADD COLUMN parent_fully_refunded BOOLEAN,
          ADD CONSTRAINT ck_sync_sale_projection_lifecycle
            CHECK (
              (
                sale_type = 'sale'
                AND parent_sale_id IS NULL
                AND parent_fully_refunded IS NULL
              )
              OR
              (
                sale_type = 'return'
                AND parent_sale_id IS NOT NULL
                AND parent_sale_id <> sale_id
                AND parent_fully_refunded IS NOT NULL
              )
            )
        """)
    op.execute("""
        CREATE INDEX ix_sync_sale_projection_parent
        ON public.sync_sale_projection (tenant_id, parent_sale_id, sequence)
        WHERE parent_sale_id IS NOT NULL
        """)
    op.execute("""
        GRANT INSERT (sale_type, parent_sale_id, parent_fully_refunded)
        ON TABLE public.sync_sale_projection TO aurum_app
        """)


def downgrade() -> None:
    op.execute("DROP INDEX public.ix_sync_sale_projection_parent")
    op.execute("""
        ALTER TABLE public.sync_sale_projection
          DROP CONSTRAINT ck_sync_sale_projection_lifecycle,
          DROP COLUMN parent_fully_refunded,
          DROP COLUMN parent_sale_id,
          DROP COLUMN sale_type
        """)
