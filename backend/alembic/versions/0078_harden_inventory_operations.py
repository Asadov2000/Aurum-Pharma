"""security: harden inventory dates, scopes, and write-off ledger

Revision ID: 0078
Revises: 0077
Create Date: 2026-08-02
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0078"
down_revision: str | Sequence[str] | None = "0077"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


WRITE_OFF_GUARD_SQL = """
CREATE FUNCTION public.trg_guard_write_off_immutability()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
  RAISE EXCEPTION 'Write-off ledger is immutable'
    USING ERRCODE = 'check_violation';
END;
$$;
"""


def upgrade() -> None:
    for table in ("incoming_item", "batch", "write_off"):
        op.execute(f"LOCK TABLE public.{table} IN SHARE ROW EXCLUSIVE MODE")

    op.execute("""
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM public.incoming_item
            WHERE manufactured_at IS NOT NULL
              AND manufactured_at > expires_at
          ) THEN
            RAISE EXCEPTION 'Cannot add incoming item date constraint: invalid rows exist';
          END IF;

          IF EXISTS (
            SELECT 1
            FROM public.batch
            WHERE manufactured_at IS NOT NULL
              AND manufactured_at > expires_at
          ) THEN
            RAISE EXCEPTION 'Cannot add batch date constraint: invalid rows exist';
          END IF;

          IF EXISTS (SELECT 1 FROM public.write_off WHERE amount < 0) THEN
            RAISE EXCEPTION 'Cannot add write-off amount constraint: invalid rows exist';
          END IF;
        END;
        $$;
        """)
    op.create_check_constraint(
        "ck_ii_manufactured_before_expiry",
        "incoming_item",
        "manufactured_at IS NULL OR manufactured_at <= expires_at",
    )
    op.create_check_constraint(
        "ck_batch_manufactured_before_expiry",
        "batch",
        "manufactured_at IS NULL OR manufactured_at <= expires_at",
    )
    op.create_check_constraint("ck_wo_amount", "write_off", "amount >= 0")

    op.execute(WRITE_OFF_GUARD_SQL)
    op.execute(
        "REVOKE ALL ON FUNCTION public.trg_guard_write_off_immutability() "
        "FROM PUBLIC, aurum_app, aurum_support"
    )
    op.execute("""
        CREATE TRIGGER trg_guard_write_off_immutability
          BEFORE UPDATE OR DELETE ON public.write_off
          FOR EACH ROW
          EXECUTE FUNCTION public.trg_guard_write_off_immutability()
        """)
    op.execute(
        "REVOKE UPDATE, DELETE ON TABLE public.write_off FROM aurum_app, aurum_support"
    )

    op.execute("""
        UPDATE public.permission
        SET scope_type = 'BRANCH_SET'
        WHERE code IN ('batches.view', 'batches.create', 'batches.update')
        """)


def downgrade() -> None:
    op.execute("""
        UPDATE public.permission
        SET scope_type = 'TENANT_ALL'
        WHERE code IN ('batches.view', 'batches.create', 'batches.update')
        """)

    op.execute(
        "GRANT UPDATE, DELETE ON TABLE public.write_off TO aurum_app, aurum_support"
    )
    op.execute("DROP TRIGGER IF EXISTS trg_guard_write_off_immutability ON public.write_off")
    op.execute("DROP FUNCTION IF EXISTS public.trg_guard_write_off_immutability()")

    op.drop_constraint("ck_wo_amount", "write_off", type_="check")
    op.drop_constraint("ck_batch_manufactured_before_expiry", "batch", type_="check")
    op.drop_constraint("ck_ii_manufactured_before_expiry", "incoming_item", type_="check")
