"""store an immutable receipt snapshot when a sale is completed

Revision ID: 0075
Revises: 0074
Create Date: 2026-07-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0075"
down_revision: str | Sequence[str] | None = "0074"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


RECEIPT_SNAPSHOT_GUARD_SQL = """
CREATE FUNCTION public.trg_require_sale_receipt_snapshot()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
  IF OLD.status = 'draft'
     AND NEW.status = 'completed'
     AND NEW.receipt_snapshot IS NULL
  THEN
    RAISE EXCEPTION 'Completed sale requires an immutable receipt snapshot'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END;
$$;
"""


def upgrade() -> None:
    op.add_column(
        "sale",
        sa.Column("receipt_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_check_constraint(
        "ck_sale_receipt_snapshot_object",
        "sale",
        "receipt_snapshot IS NULL OR jsonb_typeof(receipt_snapshot) = 'object'",
    )
    op.execute(RECEIPT_SNAPSHOT_GUARD_SQL)
    op.execute(
        "REVOKE ALL ON FUNCTION public.trg_require_sale_receipt_snapshot() "
        "FROM PUBLIC, aurum_app, aurum_support"
    )
    op.execute("""
        CREATE TRIGGER trg_require_sale_receipt_snapshot
          BEFORE UPDATE ON public.sale
          FOR EACH ROW EXECUTE FUNCTION public.trg_require_sale_receipt_snapshot()
        """)


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_require_sale_receipt_snapshot ON public.sale"
    )
    op.execute("DROP FUNCTION public.trg_require_sale_receipt_snapshot()")
    op.drop_constraint("ck_sale_receipt_snapshot_object", "sale", type_="check")
    op.drop_column("sale", "receipt_snapshot")
