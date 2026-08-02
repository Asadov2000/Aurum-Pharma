"""security: harden supplier directory and return ledger

Revision ID: 0079
Revises: 0078
Create Date: 2026-08-02
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0079"
down_revision: str | Sequence[str] | None = "0078"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


RETURN_GUARD_SQL = """
CREATE FUNCTION public.trg_guard_supplier_return_immutability()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
  RAISE EXCEPTION 'Supplier return ledger is immutable'
    USING ERRCODE = 'check_violation';
END;
$$;
"""


def upgrade() -> None:
    for table in ("supplier", "incoming_document", "batch", "supplier_return"):
        op.execute(f"LOCK TABLE public.{table} IN SHARE ROW EXCLUSIVE MODE")

    op.execute("""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM public.supplier_return WHERE amount < 0) THEN
            RAISE EXCEPTION 'Cannot harden supplier returns: negative amounts exist';
          END IF;

          IF EXISTS (
            SELECT 1
            FROM public.supplier_return supplier_return
            JOIN public.incoming_document document
              ON document.id = supplier_return.source_document_id
            WHERE supplier_return.source_document_id IS NOT NULL
              AND (
                document.tenant_id IS DISTINCT FROM supplier_return.tenant_id
                OR document.supplier_id IS DISTINCT FROM supplier_return.supplier_id
              )
          ) THEN
            RAISE EXCEPTION 'Cannot harden supplier returns: source document mismatch exists';
          END IF;
        END;
        $$;
        """)

    op.execute("""
        UPDATE public.supplier_return
        SET reason = CASE
          WHEN lower(reason) IN (
            'damaged', 'expired', 'incorrect_delivery', 'quality_issue', 'other'
          ) THEN lower(reason)
          WHEN lower(reason) LIKE '%повреж%' THEN 'damaged'
          WHEN lower(reason) LIKE '%срок%' OR lower(reason) LIKE '%просроч%' THEN 'expired'
          WHEN lower(reason) LIKE '%несоответ%' THEN 'incorrect_delivery'
          WHEN lower(reason) LIKE '%качеств%' THEN 'quality_issue'
          ELSE 'other'
        END
        """)

    op.create_check_constraint("ck_sr_amount", "supplier_return", "amount >= 0")
    op.create_check_constraint(
        "ck_sr_reason",
        "supplier_return",
        "reason IN ('damaged','expired','incorrect_delivery','quality_issue','other')",
    )

    op.create_unique_constraint("uq_supplier_tenant_id", "supplier", ["tenant_id", "id"])
    op.create_unique_constraint(
        "uq_incoming_document_tenant_supplier_id",
        "incoming_document",
        ["tenant_id", "supplier_id", "id"],
    )
    op.create_unique_constraint("uq_batch_tenant_id", "batch", ["tenant_id", "id"])
    op.create_foreign_key(
        "fk_sr_tenant_supplier",
        "supplier_return",
        "supplier",
        ["tenant_id", "supplier_id"],
        ["tenant_id", "id"],
    )
    op.create_foreign_key(
        "fk_sr_tenant_supplier_document",
        "supplier_return",
        "incoming_document",
        ["tenant_id", "supplier_id", "source_document_id"],
        ["tenant_id", "supplier_id", "id"],
    )
    op.create_foreign_key(
        "fk_sr_tenant_batch",
        "supplier_return",
        "batch",
        ["tenant_id", "batch_id"],
        ["tenant_id", "id"],
    )

    op.execute(
        "CREATE INDEX ix_supplier_tenant_name_lower " "ON public.supplier (tenant_id, lower(name))"
    )
    op.create_index(
        "ix_sr_tenant_created",
        "supplier_return",
        ["tenant_id", "created_at"],
    )

    op.execute(RETURN_GUARD_SQL)
    op.execute(
        "REVOKE ALL ON FUNCTION public.trg_guard_supplier_return_immutability() "
        "FROM PUBLIC, aurum_app, aurum_support"
    )
    op.execute("""
        CREATE TRIGGER trg_guard_supplier_return_immutability
          BEFORE UPDATE OR DELETE ON public.supplier_return
          FOR EACH ROW
          EXECUTE FUNCTION public.trg_guard_supplier_return_immutability()
        """)
    op.execute(
        "REVOKE UPDATE, DELETE ON TABLE public.supplier_return FROM aurum_app, aurum_support"
    )


def downgrade() -> None:
    op.execute("GRANT UPDATE, DELETE ON TABLE public.supplier_return TO aurum_app, aurum_support")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_guard_supplier_return_immutability " "ON public.supplier_return"
    )
    op.execute("DROP FUNCTION IF EXISTS public.trg_guard_supplier_return_immutability()")

    op.drop_index("ix_sr_tenant_created", table_name="supplier_return")
    op.drop_index("ix_supplier_tenant_name_lower", table_name="supplier")
    op.drop_constraint("fk_sr_tenant_batch", "supplier_return", type_="foreignkey")
    op.drop_constraint(
        "fk_sr_tenant_supplier_document",
        "supplier_return",
        type_="foreignkey",
    )
    op.drop_constraint("fk_sr_tenant_supplier", "supplier_return", type_="foreignkey")
    op.drop_constraint("uq_batch_tenant_id", "batch", type_="unique")
    op.drop_constraint(
        "uq_incoming_document_tenant_supplier_id",
        "incoming_document",
        type_="unique",
    )
    op.drop_constraint("uq_supplier_tenant_id", "supplier", type_="unique")
    op.drop_constraint("ck_sr_reason", "supplier_return", type_="check")
    op.drop_constraint("ck_sr_amount", "supplier_return", type_="check")
