"""security: bind RLS support context to the database role

Revision ID: 0027
Revises: 0026
Create Date: 2026-07-12
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0027"
down_revision: str | Sequence[str] | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TENANT_POLICY_TABLES = (
    "audit_log",
    "barcode",
    "batch",
    "batch_movement",
    "branch",
    "catalog_import_job",
    "incoming_document",
    "incoming_item",
    "invoice",
    "notification",
    "onboarding_checklist",
    "payment",
    "prescription_log",
    "register",
    "sale",
    "sale_item",
    "sale_payment",
    "shift",
    "supplier",
    "supplier_return",
    "tenant_catalog",
    "tenant_settings",
    "tenant_subscription",
    "user_assignment",
    "wizard_state",
    "write_off",
)


DATABASE_ROLE_GUARD_SQL = """
DO $$
DECLARE
  v_app_bypasses_rls BOOLEAN;
  v_support_bypasses_rls BOOLEAN;
BEGIN
  SELECT rolbypassrls INTO v_app_bypasses_rls
  FROM pg_catalog.pg_roles WHERE rolname = 'aurum_app';

  SELECT rolbypassrls INTO v_support_bypasses_rls
  FROM pg_catalog.pg_roles WHERE rolname = 'aurum_support';

  IF v_app_bypasses_rls IS DISTINCT FROM false THEN
    RAISE EXCEPTION 'aurum_app must exist without BYPASSRLS';
  END IF;
  IF v_support_bypasses_rls IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'aurum_support must exist with BYPASSRLS';
  END IF;
  IF pg_catalog.pg_has_role('aurum_app', 'aurum_support', 'MEMBER') THEN
    RAISE EXCEPTION 'aurum_app must not be a member of aurum_support';
  END IF;
END
$$
"""


HARDENED_SUPPORT_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION public.is_support_session()
RETURNS BOOLEAN AS $$
  SELECT session_user = 'aurum_support'
    AND COALESCE(current_setting('app.support_session', true), '') = 'true'
$$ LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog, pg_temp
"""


LEGACY_SUPPORT_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION public.is_support_session() RETURNS BOOLEAN AS $$
BEGIN
  RETURN current_setting('app.support_session', true) = 'true';
EXCEPTION WHEN OTHERS THEN
  RETURN false;
END;
$$ LANGUAGE plpgsql STABLE SECURITY INVOKER
"""


def _set_tenant_policies(*, trust_support_flag: bool) -> None:
    predicate = "tenant_id = public.current_tenant_id()"
    if trust_support_flag:
        predicate += " OR public.is_support_session()"

    for table in TENANT_POLICY_TABLES:
        op.execute(f"ALTER POLICY tenant_isolation ON public.{table} USING ({predicate})")

    role_predicate = "tenant_id IS NULL OR tenant_id = public.current_tenant_id()"
    if trust_support_flag:
        role_predicate += " OR public.is_support_session()"
    op.execute(f"ALTER POLICY tenant_isolation ON public.role USING ({role_predicate})")


def upgrade() -> None:
    op.execute(DATABASE_ROLE_GUARD_SQL)
    op.execute(HARDENED_SUPPORT_FUNCTION_SQL)
    op.execute("ALTER FUNCTION public.is_support_session() OWNER TO aurum_support")
    op.execute(
        "COMMENT ON FUNCTION public.is_support_session() IS "
        "'True only for an explicit context on the aurum_support login role'"
    )
    _set_tenant_policies(trust_support_flag=False)


def downgrade() -> None:
    _set_tenant_policies(trust_support_flag=True)
    op.execute(LEGACY_SUPPORT_FUNCTION_SQL)
    op.execute("ALTER FUNCTION public.is_support_session() RESET search_path")
    op.execute("COMMENT ON FUNCTION public.is_support_session() IS NULL")
