"""security: require active permissions in database authorization gates

Revision ID: 0049
Revises: 0048
Create Date: 2026-07-15

The security-definer assignment functions delegate to
tenant_actor_has_permission. The gate already ignored inactive assignments and
roles, but an inactive global permission still authorized the caller.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0049"
down_revision: str | Sequence[str] | None = "0048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ACTIVE_PERMISSION_GATE_SQL = """
CREATE OR REPLACE FUNCTION public.tenant_actor_has_permission(
  p_tenant_id UUID,
  p_permission_code TEXT
) RETURNS BOOLEAN AS $$
  SELECT session_user = 'aurum_support'
    OR (
      p_tenant_id IS NOT NULL
      AND public.current_app_user_id() IS NOT NULL
      AND p_tenant_id IS NOT DISTINCT FROM public.current_tenant_id()
      AND EXISTS (
        SELECT 1
        FROM public.user_assignment AS assignment
        JOIN public.role AS assigned_role
          ON assigned_role.id = assignment.role_id
         AND assigned_role.is_active
        JOIN public.role_permission AS role_permission
          ON role_permission.role_id = assigned_role.id
        JOIN public.permission AS granted_permission
          ON granted_permission.code = role_permission.permission_code
         AND granted_permission.is_active
        WHERE assignment.tenant_id = p_tenant_id
          AND assignment.user_id = public.current_app_user_id()
          AND assignment.is_active
          AND granted_permission.code = p_permission_code
      )
    )
$$ LANGUAGE SQL
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


LEGACY_PERMISSION_GATE_SQL = """
CREATE OR REPLACE FUNCTION public.tenant_actor_has_permission(
  p_tenant_id UUID,
  p_permission_code TEXT
) RETURNS BOOLEAN AS $$
  SELECT session_user = 'aurum_support'
    OR (
      p_tenant_id IS NOT NULL
      AND public.current_app_user_id() IS NOT NULL
      AND p_tenant_id IS NOT DISTINCT FROM public.current_tenant_id()
      AND EXISTS (
        SELECT 1
        FROM public.user_assignment AS assignment
        JOIN public.role AS assigned_role
          ON assigned_role.id = assignment.role_id
         AND assigned_role.is_active
        JOIN public.role_permission AS role_permission
          ON role_permission.role_id = assigned_role.id
        WHERE assignment.tenant_id = p_tenant_id
          AND assignment.user_id = public.current_app_user_id()
          AND assignment.is_active
          AND role_permission.permission_code = p_permission_code
      )
    )
$$ LANGUAGE SQL
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


def _secure_internal_function() -> None:
    function = "public.tenant_actor_has_permission(UUID, TEXT)"
    op.execute(f"ALTER FUNCTION {function} OWNER TO aurum_support")
    op.execute(f"REVOKE ALL PRIVILEGES ON FUNCTION {function} FROM PUBLIC, aurum_app")
    op.execute(f"GRANT EXECUTE ON FUNCTION {function} TO aurum_support")


def upgrade() -> None:
    op.execute(ACTIVE_PERMISSION_GATE_SQL)
    _secure_internal_function()


def downgrade() -> None:
    op.execute(LEGACY_PERMISSION_GATE_SQL)
    _secure_internal_function()
