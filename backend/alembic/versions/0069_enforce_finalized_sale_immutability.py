"""security: enforce finalized sale immutability

Revision ID: 0069
Revises: 0068
Create Date: 2026-07-27
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0069"
down_revision: str | Sequence[str] | None = "0068"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


PREFLIGHT_SQL = """
DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM public.sale
    WHERE status IN ('completed', 'voided')
      AND (
        completed_at IS NULL
        OR NULLIF(BTRIM(receipt_number), '') IS NULL
        OR receipt_seq IS NULL
      )
  ) THEN
    RAISE EXCEPTION 'Finalized sale history is missing receipt identity';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM public.sale finalized_sale
    WHERE finalized_sale.status IN ('completed', 'voided')
      AND (
        NOT EXISTS (
          SELECT 1
          FROM public.sale_item item
          WHERE item.sale_id = finalized_sale.id
        )
        OR (
          SELECT COALESCE(SUM(item.total_price), 0)
          FROM public.sale_item item
          WHERE item.sale_id = finalized_sale.id
        ) IS DISTINCT FROM finalized_sale.total_amount
        OR (
          SELECT COALESCE(SUM(payment.amount), 0)
          FROM public.sale_payment payment
          WHERE payment.sale_id = finalized_sale.id
        ) IS DISTINCT FROM finalized_sale.total_amount
      )
  ) THEN
    RAISE EXCEPTION 'Finalized sale financial totals are inconsistent';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM public.sale finalized_sale
    WHERE finalized_sale.status IN ('completed', 'voided')
      AND (
        (
          finalized_sale.status = 'completed'
          AND (
            finalized_sale.voided_at IS NOT NULL
            OR finalized_sale.voided_by_sale_id IS NOT NULL
          )
        )
        OR (
          finalized_sale.sale_type = 'sale'
          AND finalized_sale.parent_sale_id IS NOT NULL
        )
        OR (
          finalized_sale.sale_type = 'return'
          AND finalized_sale.status <> 'completed'
        )
      )
  ) THEN
    RAISE EXCEPTION 'Finalized sale lifecycle is inconsistent';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM (
      SELECT child.tenant_id AS child_tenant_id, parent.tenant_id AS parent_tenant_id
      FROM public.sale_item child
      JOIN public.sale parent ON parent.id = child.sale_id
      UNION ALL
      SELECT child.tenant_id, parent.tenant_id
      FROM public.sale_payment child
      JOIN public.sale parent ON parent.id = child.sale_id
      UNION ALL
      SELECT child.tenant_id, parent.tenant_id
      FROM public.prescription_log child
      JOIN public.sale parent ON parent.id = child.sale_id
    ) scoped_components
    WHERE child_tenant_id IS DISTINCT FROM parent_tenant_id
  ) THEN
    RAISE EXCEPTION 'Sale component tenant scope is inconsistent';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM public.sale return_sale
    LEFT JOIN public.sale parent_sale ON parent_sale.id = return_sale.parent_sale_id
    WHERE return_sale.sale_type = 'return'
      AND (
        parent_sale.id IS NULL
        OR parent_sale.sale_type <> 'sale'
        OR parent_sale.tenant_id IS DISTINCT FROM return_sale.tenant_id
        OR parent_sale.branch_id IS DISTINCT FROM return_sale.branch_id
        OR parent_sale.register_id IS DISTINCT FROM return_sale.register_id
        OR (
          return_sale.status = 'completed'
          AND parent_sale.status NOT IN ('completed', 'voided')
        )
      )
  ) THEN
    RAISE EXCEPTION 'Historical return linkage is inconsistent';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM public.sale return_sale
    JOIN public.sale_item return_item ON return_item.sale_id = return_sale.id
    LEFT JOIN public.sale_item parent_item
      ON parent_item.id = return_item.parent_sale_item_id
     AND parent_item.sale_id = return_sale.parent_sale_id
    WHERE return_sale.sale_type = 'return'
      AND return_sale.status = 'completed'
      AND (
        parent_item.id IS NULL
        OR parent_item.tenant_id IS DISTINCT FROM return_sale.tenant_id
        OR parent_item.catalog_id IS DISTINCT FROM return_item.catalog_id
        OR parent_item.batch_id IS DISTINCT FROM return_item.batch_id
      )
  ) THEN
    RAISE EXCEPTION 'Historical return item linkage is inconsistent';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM public.sale return_sale
    JOIN public.sale_item return_item ON return_item.sale_id = return_sale.id
    JOIN public.sale_item parent_item
      ON parent_item.id = return_item.parent_sale_item_id
     AND parent_item.sale_id = return_sale.parent_sale_id
    WHERE return_sale.sale_type = 'return'
      AND return_sale.status = 'completed'
    GROUP BY return_sale.parent_sale_id, parent_item.id, parent_item.qty
    HAVING SUM(return_item.qty) > parent_item.qty
  ) THEN
    RAISE EXCEPTION 'Historical return quantity exceeds the original sale item';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM public.sale original_sale
    LEFT JOIN public.sale return_sale
      ON return_sale.id = original_sale.voided_by_sale_id
     AND return_sale.parent_sale_id = original_sale.id
     AND return_sale.sale_type = 'return'
     AND return_sale.status = 'completed'
    WHERE original_sale.status = 'voided'
      AND (
        original_sale.voided_at IS NULL
        OR original_sale.voided_by_sale_id IS NULL
        OR return_sale.id IS NULL
      )
  ) THEN
    RAISE EXCEPTION 'Historical voided sale linkage is inconsistent';
  END IF;
END;
$$;
"""


SALE_GUARD_SQL = """
CREATE FUNCTION public.trg_guard_sale_immutability()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  v_parent_tenant_id UUID;
  v_parent_branch_id UUID;
  v_parent_register_id UUID;
  v_parent_sale_type TEXT;
  v_parent_status TEXT;
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF NEW.status <> 'draft' THEN
      RAISE EXCEPTION 'Sale must be created as a draft'
        USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
  END IF;

  IF TG_OP = 'DELETE' THEN
    IF OLD.status <> 'draft' THEN
      RAISE EXCEPTION 'Finalized sale cannot be deleted'
        USING ERRCODE = 'check_violation';
    END IF;
    RETURN OLD;
  END IF;

  IF OLD.status <> 'draft' THEN
    RAISE EXCEPTION 'Finalized sale cannot be changed'
      USING ERRCODE = 'check_violation';
  END IF;

  IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id THEN
    RAISE EXCEPTION 'Sale tenant ownership cannot be changed'
      USING ERRCODE = 'check_violation';
  END IF;

  IF NEW.status NOT IN ('draft', 'completed') THEN
    RAISE EXCEPTION 'Unsupported sale status transition'
      USING ERRCODE = 'check_violation';
  END IF;

  IF NEW.status = 'completed'
     AND (
       NEW.completed_at IS NULL
       OR NULLIF(BTRIM(NEW.receipt_number), '') IS NULL
       OR NEW.receipt_seq IS NULL
       OR NEW.voided_at IS NOT NULL
       OR NEW.voided_by_sale_id IS NOT NULL
     ) THEN
    RAISE EXCEPTION 'Completed sale requires immutable receipt identity'
      USING ERRCODE = 'check_violation';
  END IF;

  IF NEW.status = 'completed' THEN
    IF NOT EXISTS (
      SELECT 1
      FROM public.sale_item
      WHERE sale_id = NEW.id
    ) THEN
      RAISE EXCEPTION 'Completed sale requires at least one item'
        USING ERRCODE = 'check_violation';
    END IF;

    IF EXISTS (
      SELECT 1
      FROM (
        SELECT item.tenant_id
        FROM public.sale_item item
        WHERE item.sale_id = NEW.id
        UNION ALL
        SELECT payment.tenant_id
        FROM public.sale_payment payment
        WHERE payment.sale_id = NEW.id
        UNION ALL
        SELECT prescription.tenant_id
        FROM public.prescription_log prescription
        WHERE prescription.sale_id = NEW.id
      ) component
      WHERE component.tenant_id IS DISTINCT FROM NEW.tenant_id
    ) THEN
      RAISE EXCEPTION 'Sale component tenant scope is inconsistent'
        USING ERRCODE = 'check_violation';
    END IF;

    IF (
      SELECT COALESCE(SUM(total_price), 0)
      FROM public.sale_item
      WHERE sale_id = NEW.id
    ) IS DISTINCT FROM NEW.total_amount THEN
      RAISE EXCEPTION 'Completed sale item total is inconsistent'
        USING ERRCODE = 'check_violation';
    END IF;

    IF (
      SELECT COALESCE(SUM(amount), 0)
      FROM public.sale_payment
      WHERE sale_id = NEW.id
    ) IS DISTINCT FROM NEW.total_amount THEN
      RAISE EXCEPTION 'Completed sale payment total is inconsistent'
        USING ERRCODE = 'check_violation';
    END IF;

    IF NEW.sale_type = 'sale' AND NEW.parent_sale_id IS NOT NULL THEN
      RAISE EXCEPTION 'Forward sale cannot reference a parent sale'
        USING ERRCODE = 'check_violation';
    END IF;

    IF NEW.sale_type = 'return' THEN
      IF NEW.parent_sale_id IS NULL THEN
        RAISE EXCEPTION 'Return requires a parent sale'
          USING ERRCODE = 'check_violation';
      END IF;

      SELECT
        parent_sale.tenant_id,
        parent_sale.branch_id,
        parent_sale.register_id,
        parent_sale.sale_type,
        parent_sale.status
      INTO
        v_parent_tenant_id,
        v_parent_branch_id,
        v_parent_register_id,
        v_parent_sale_type,
        v_parent_status
      FROM public.sale parent_sale
      WHERE parent_sale.id = NEW.parent_sale_id
      FOR UPDATE;

      IF NOT FOUND
         OR v_parent_sale_type <> 'sale'
         OR v_parent_status NOT IN ('completed', 'voided')
         OR v_parent_tenant_id IS DISTINCT FROM NEW.tenant_id
         OR v_parent_branch_id IS DISTINCT FROM NEW.branch_id
         OR v_parent_register_id IS DISTINCT FROM NEW.register_id THEN
        RAISE EXCEPTION 'Return parent sale is invalid'
          USING ERRCODE = 'check_violation';
      END IF;

      IF EXISTS (
        SELECT 1
        FROM public.sale_item return_item
        LEFT JOIN public.sale_item parent_item
          ON parent_item.id = return_item.parent_sale_item_id
         AND parent_item.sale_id = NEW.parent_sale_id
        WHERE return_item.sale_id = NEW.id
          AND (
            parent_item.id IS NULL
            OR parent_item.tenant_id IS DISTINCT FROM NEW.tenant_id
            OR parent_item.catalog_id IS DISTINCT FROM return_item.catalog_id
            OR parent_item.batch_id IS DISTINCT FROM return_item.batch_id
          )
      ) THEN
        RAISE EXCEPTION 'Return item does not match its parent sale item'
          USING ERRCODE = 'check_violation';
      END IF;

      IF EXISTS (
        SELECT 1
        FROM public.sale_item return_item
        JOIN public.sale_item parent_item
          ON parent_item.id = return_item.parent_sale_item_id
         AND parent_item.sale_id = NEW.parent_sale_id
        WHERE return_item.sale_id = NEW.id
          AND return_item.qty + COALESCE(
            (
              SELECT SUM(previous_return_item.qty)
              FROM public.sale_item previous_return_item
              JOIN public.sale previous_return
                ON previous_return.id = previous_return_item.sale_id
               AND previous_return.parent_sale_id = NEW.parent_sale_id
               AND previous_return.sale_type = 'return'
               AND previous_return.status = 'completed'
              WHERE previous_return_item.parent_sale_item_id = parent_item.id
            ),
            0
          ) > parent_item.qty
      ) THEN
        RAISE EXCEPTION 'Return quantity exceeds the original sale item'
          USING ERRCODE = 'check_violation';
      END IF;
    END IF;
  END IF;

  RETURN NEW;
END;
$$;
"""


SALE_CHILD_GUARD_SQL = """
CREATE FUNCTION public.trg_guard_sale_child_immutability()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  v_old_status TEXT;
  v_old_tenant_id UUID;
  v_new_status TEXT;
  v_new_tenant_id UUID;
BEGIN
  IF TG_OP IN ('UPDATE', 'DELETE') THEN
    SELECT sale.status, sale.tenant_id
      INTO v_old_status, v_old_tenant_id
    FROM public.sale
    WHERE sale.id = OLD.sale_id
    FOR SHARE;

    IF TG_OP = 'DELETE' AND NOT FOUND AND pg_trigger_depth() > 1 THEN
      RETURN OLD;
    END IF;

    IF NOT FOUND
       OR v_old_status <> 'draft'
       OR v_old_tenant_id IS DISTINCT FROM OLD.tenant_id THEN
      RAISE EXCEPTION 'Finalized sale components cannot be changed'
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;

  IF TG_OP IN ('INSERT', 'UPDATE') THEN
    IF TG_OP = 'UPDATE'
       AND (
         NEW.sale_id IS DISTINCT FROM OLD.sale_id
         OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
       ) THEN
      RAISE EXCEPTION 'Sale component ownership cannot be changed'
        USING ERRCODE = 'check_violation';
    END IF;

    SELECT sale.status, sale.tenant_id
      INTO v_new_status, v_new_tenant_id
    FROM public.sale
    WHERE sale.id = NEW.sale_id
    FOR SHARE;

    IF NOT FOUND
       OR v_new_status <> 'draft'
       OR v_new_tenant_id IS DISTINCT FROM NEW.tenant_id THEN
      RAISE EXCEPTION 'Sale components require a matching draft sale'
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;

  IF TG_OP = 'DELETE' THEN
    RETURN OLD;
  END IF;
  RETURN NEW;
END;
$$;
"""


def upgrade() -> None:
    for table in ("sale", "sale_item", "sale_payment", "prescription_log"):
        op.execute(f"LOCK TABLE public.{table} IN SHARE ROW EXCLUSIVE MODE")
    op.execute(PREFLIGHT_SQL)

    op.execute(SALE_GUARD_SQL)
    op.execute(SALE_CHILD_GUARD_SQL)

    for function_name in (
        "trg_guard_sale_immutability",
        "trg_guard_sale_child_immutability",
    ):
        op.execute(
            f"REVOKE ALL ON FUNCTION public.{function_name}() "
            "FROM PUBLIC, aurum_app, aurum_support"
        )

    op.execute("""
        CREATE TRIGGER trg_guard_sale_immutability
          BEFORE INSERT OR UPDATE OR DELETE ON public.sale
          FOR EACH ROW EXECUTE FUNCTION public.trg_guard_sale_immutability()
        """)
    for table in ("sale_item", "sale_payment", "prescription_log"):
        op.execute(f"""
            CREATE TRIGGER trg_guard_{table}_immutability
              BEFORE INSERT OR UPDATE OR DELETE ON public.{table}
              FOR EACH ROW
              EXECUTE FUNCTION public.trg_guard_sale_child_immutability()
            """)

    op.execute("""
        DO $$
        DECLARE
          v_bad_function TEXT;
        BEGIN
          SELECT routines.proname
            INTO v_bad_function
          FROM pg_catalog.pg_proc routines
          JOIN pg_catalog.pg_namespace schemas ON schemas.oid = routines.pronamespace
          JOIN pg_catalog.pg_roles owners ON owners.oid = routines.proowner
          WHERE schemas.nspname = 'public'
            AND routines.proname IN (
              'trg_guard_sale_immutability',
              'trg_guard_sale_child_immutability'
            )
            AND (
              owners.rolname <> 'aurum_schema_owner'
              OR routines.prosecdef
              OR pg_catalog.has_function_privilege(
                'aurum_app', routines.oid, 'EXECUTE'
              )
              OR pg_catalog.has_function_privilege(
                'aurum_support', routines.oid, 'EXECUTE'
              )
            )
          LIMIT 1;

          IF v_bad_function IS NOT NULL THEN
            RAISE EXCEPTION 'Unsafe finalized-sale guard function: %', v_bad_function;
          END IF;
        END;
        $$;
        """)


def downgrade() -> None:
    for table in ("prescription_log", "sale_payment", "sale_item"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_guard_{table}_immutability ON public.{table}")
    op.execute("DROP TRIGGER IF EXISTS trg_guard_sale_immutability ON public.sale")
    op.execute("DROP FUNCTION public.trg_guard_sale_child_immutability()")
    op.execute("DROP FUNCTION public.trg_guard_sale_immutability()")
