"""enforce one tenant-scoped namespace for POS operation identifiers

Revision ID: 0119
Revises: 0118
Create Date: 2026-08-29
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0119"
down_revision: str | Sequence[str] | None = "0118"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_FUNCTION = """
CREATE OR REPLACE FUNCTION public.trg_enforce_pos_operation_namespace()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $function$
DECLARE
  v_conflict BOOLEAN;
  v_linked_sale_id UUID;
BEGIN
  IF NEW.operation_id IS NULL THEN
    RETURN NEW;
  END IF;

  IF TG_OP = 'UPDATE'
     AND NEW.id IS NOT DISTINCT FROM OLD.id
     AND NEW.tenant_id IS NOT DISTINCT FROM OLD.tenant_id
     AND NEW.operation_id IS NOT DISTINCT FROM OLD.operation_id THEN
    RETURN NEW;
  END IF;

  v_linked_sale_id := NULLIF(pg_catalog.to_jsonb(NEW) ->> 'sale_id', '')::UUID;
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(
      NEW.tenant_id::TEXT || ':' || NEW.operation_id::TEXT,
      0
    )
  );

  SELECT
    EXISTS (
      SELECT 1
      FROM public.pos_command AS command
      WHERE command.tenant_id = NEW.tenant_id
        AND command.operation_id = NEW.operation_id
        AND NOT (TG_TABLE_NAME = 'pos_command' AND command.id = NEW.id)
    )
    OR EXISTS (
      SELECT 1
      FROM public.sale AS sale_row
      WHERE sale_row.tenant_id = NEW.tenant_id
        AND sale_row.operation_id = NEW.operation_id
        AND NOT (
          (TG_TABLE_NAME = 'sale' AND sale_row.id = NEW.id)
          OR (
            TG_TABLE_NAME = 'edge_cash_command'
            AND sale_row.id = v_linked_sale_id
          )
        )
    )
    OR EXISTS (
      SELECT 1
      FROM public.sale_payment AS payment
      WHERE payment.tenant_id = NEW.tenant_id
        AND payment.operation_id = NEW.operation_id
        AND NOT (TG_TABLE_NAME = 'sale_payment' AND payment.id = NEW.id)
    )
    OR EXISTS (
      SELECT 1
      FROM public.pos_payment_attempt AS attempt
      WHERE attempt.tenant_id = NEW.tenant_id
        AND attempt.operation_id = NEW.operation_id
        AND NOT (TG_TABLE_NAME = 'pos_payment_attempt' AND attempt.id = NEW.id)
    )
    OR EXISTS (
      SELECT 1
      FROM public.pos_refund_attempt AS attempt
      WHERE attempt.tenant_id = NEW.tenant_id
        AND attempt.operation_id = NEW.operation_id
        AND NOT (TG_TABLE_NAME = 'pos_refund_attempt' AND attempt.id = NEW.id)
    )
    OR EXISTS (
      SELECT 1
      FROM public.edge_cash_command AS command
      WHERE command.tenant_id = NEW.tenant_id
        AND command.operation_id = NEW.operation_id
        AND NOT (
          (TG_TABLE_NAME = 'edge_cash_command' AND command.id = NEW.id)
          OR (TG_TABLE_NAME = 'sale' AND command.sale_id = NEW.id)
        )
    )
  INTO v_conflict;

  IF v_conflict THEN
    RAISE EXCEPTION 'POS operation ID is already owned by another operation'
      USING ERRCODE = '23505', CONSTRAINT = 'uq_pos_operation_namespace';
  END IF;

  RETURN NEW;
END
$function$
"""

_TABLES = (
    "pos_command",
    "sale",
    "sale_payment",
    "pos_payment_attempt",
    "pos_refund_attempt",
    "edge_cash_command",
)


def upgrade() -> None:
    op.execute("""
        DO $block$
        BEGIN
          IF EXISTS (
            WITH claims AS (
              SELECT tenant_id, operation_id, 'command:' || id::TEXT AS claim
              FROM public.pos_command
              UNION ALL
              SELECT tenant_id, operation_id, 'sale:' || id::TEXT
              FROM public.sale
              WHERE operation_id IS NOT NULL
              UNION ALL
              SELECT tenant_id, operation_id, 'payment:' || id::TEXT
              FROM public.sale_payment
              WHERE operation_id IS NOT NULL
              UNION ALL
              SELECT tenant_id, operation_id, 'payment_attempt:' || id::TEXT
              FROM public.pos_payment_attempt
              UNION ALL
              SELECT tenant_id, operation_id, 'refund_attempt:' || id::TEXT
              FROM public.pos_refund_attempt
              UNION ALL
              SELECT tenant_id, operation_id, 'sale:' || sale_id::TEXT
              FROM public.edge_cash_command
            )
            SELECT 1
            FROM claims
            GROUP BY tenant_id, operation_id
            HAVING pg_catalog.count(DISTINCT claim) > 1
          ) THEN
            RAISE EXCEPTION
              'Existing POS operation IDs conflict across operation types';
          END IF;
        END
        $block$
        """)
    op.execute(_FUNCTION)
    op.execute(
        "REVOKE ALL PRIVILEGES ON FUNCTION "
        "public.trg_enforce_pos_operation_namespace() FROM PUBLIC"
    )
    for table_name in _TABLES:
        op.execute(f"""
            CREATE TRIGGER trg_enforce_pos_operation_namespace
            BEFORE INSERT OR UPDATE ON public.{table_name}
            FOR EACH ROW
            EXECUTE FUNCTION public.trg_enforce_pos_operation_namespace()
            """)


def downgrade() -> None:
    for table_name in reversed(_TABLES):
        op.execute("DROP TRIGGER trg_enforce_pos_operation_namespace " f"ON public.{table_name}")
    op.execute("DROP FUNCTION public.trg_enforce_pos_operation_namespace()")
