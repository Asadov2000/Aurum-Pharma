"""harden catalog import concurrency and FEFO lookup

Revision ID: 0133
Revises: 0132
Create Date: 2026-09-04
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0133"
down_revision: str | Sequence[str] | None = "0132"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


BATCH_QTY_GUARD_SQL = """
CREATE FUNCTION public.trg_guard_batch_qty_ledger()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
  IF SESSION_USER <> 'aurum_support' THEN
    IF TG_OP = 'INSERT' AND NEW.qty_remaining <> 0 THEN
      RAISE EXCEPTION 'Initial batch quantity must be recorded through batch_movement'
        USING ERRCODE = 'check_violation';
    END IF;
    IF TG_OP = 'UPDATE'
      AND NEW.qty_remaining IS DISTINCT FROM OLD.qty_remaining
      AND pg_catalog.pg_trigger_depth() <= 1
    THEN
      RAISE EXCEPTION 'Batch quantity can only change through batch_movement'
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  RETURN NEW;
END;
$$;
"""


def _add_batch_cost_permission() -> None:
    op.execute("""
        UPDATE public.permission
        SET description = 'Просмотр партий на складе: остатки и сроки годности.'
        WHERE code = 'batches.view'
        """)
    op.execute("""
        INSERT INTO public.permission (
          code, group_code, name, description, min_level_required,
          is_dangerous, is_active, scope_type, target_role_type, risk_level,
          developer_grantable, administrator_grantable, owner_grantable,
          developer_delegable, administrator_delegable, owner_delegable,
          requires_step_up, requires_confirmation
        ) VALUES (
          'batches.view_costs', 'batches', 'Просмотр себестоимости партий',
          'Закупочные цены, стоимость остатка и расчётная маржа.', 3,
          false, true, 'BRANCH_SET', 'tenant', 'sensitive',
          true, true, true, true, true, true, false, false
        )
        ON CONFLICT (code) DO NOTHING
        """)
    op.execute(
        "ALTER TABLE public.role_permission "
        "DISABLE TRIGGER trg_guard_role_permission_mutation"
    )
    op.execute("""
        INSERT INTO public.role_permission (role_id, permission_code)
        SELECT role.id, 'batches.view_costs'
        FROM public.role AS role
        WHERE role.is_active
          AND (
            (role.is_system AND role.level <= 3)
            OR (role.is_protected AND role.protected_kind = 'tenant_owner')
          )
        ON CONFLICT (role_id, permission_code) DO NOTHING
        """)
    op.execute(
        "ALTER TABLE public.role_permission "
        "ENABLE TRIGGER trg_guard_role_permission_mutation"
    )
    op.execute("""
        INSERT INTO public.role_template_permission (template_id, permission_code)
        SELECT template.id, 'batches.view_costs'
        FROM public.role_template AS template
        WHERE template.slug = 'owner' AND template.is_active
        ON CONFLICT (template_id, permission_code) DO NOTHING
        """)
    op.execute("""
        INSERT INTO public.access_role_version_permission (
          role_version_id, permission_code, created_at
        )
        SELECT version.id, 'batches.view_costs', pg_catalog.statement_timestamp()
        FROM public.access_role_version AS version
        JOIN public.role AS role ON role.id = version.role_id
        WHERE version.status = 'published'
          AND role.is_active
          AND (
            (role.is_system AND role.level <= 3)
            OR (role.is_protected AND role.protected_kind = 'tenant_owner')
          )
        ON CONFLICT (role_version_id, permission_code) DO NOTHING
        """)
    op.execute("SELECT public.bump_all_authorization_policy_revisions()")


def upgrade() -> None:
    _add_batch_cost_permission()
    op.execute(BATCH_QTY_GUARD_SQL)
    op.execute(
        "REVOKE ALL ON FUNCTION public.trg_guard_batch_qty_ledger() "
        "FROM PUBLIC, aurum_app, aurum_support"
    )
    op.execute("""
        CREATE TRIGGER trg_guard_batch_qty_ledger
          BEFORE INSERT OR UPDATE ON public.batch
          FOR EACH ROW
          EXECUTE FUNCTION public.trg_guard_batch_qty_ledger()
        """)
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM public.catalog_import_job
            WHERE status = 'importing'
            GROUP BY tenant_id
            HAVING count(*) > 1
          ) THEN
            RAISE EXCEPTION
              'Cannot enforce catalog import concurrency: a tenant has multiple active jobs';
          END IF;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_catalog_import_job_tenant_active
        ON public.catalog_import_job (tenant_id)
        WHERE status = 'importing'
        """
    )
    op.execute(
        """
        CREATE INDEX ix_batch_fefo_sellable
        ON public.batch (
          tenant_id,
          branch_id,
          catalog_id,
          expires_at,
          created_at,
          id
        )
        WHERE qty_remaining > 0 AND is_blocked = false
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS public.ix_batch_fefo_sellable")
    op.execute("DROP INDEX IF EXISTS public.uq_catalog_import_job_tenant_active")
    op.execute("DROP TRIGGER IF EXISTS trg_guard_batch_qty_ledger ON public.batch")
    op.execute("DROP FUNCTION IF EXISTS public.trg_guard_batch_qty_ledger()")
    op.execute("""
        DELETE FROM public.access_role_version_permission
        WHERE permission_code = 'batches.view_costs'
        """)
    op.execute(
        "ALTER TABLE public.role_permission "
        "DISABLE TRIGGER trg_guard_role_permission_mutation"
    )
    op.execute("""
        DELETE FROM public.role_permission
        WHERE permission_code = 'batches.view_costs'
        """)
    op.execute(
        "ALTER TABLE public.role_permission "
        "ENABLE TRIGGER trg_guard_role_permission_mutation"
    )
    op.execute("""
        DELETE FROM public.role_template_permission
        WHERE permission_code = 'batches.view_costs'
        """)
    op.execute("SELECT public.bump_all_authorization_policy_revisions()")
    op.execute("DELETE FROM public.permission WHERE code = 'batches.view_costs'")
    op.execute("""
        UPDATE public.permission
        SET description = 'Просмотр партий на складе: остатки, сроки годности, цены закупки.'
        WHERE code = 'batches.view'
        """)
