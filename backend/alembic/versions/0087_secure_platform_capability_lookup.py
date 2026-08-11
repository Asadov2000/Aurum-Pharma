"""secure platform capability lookup

Revision ID: 0087
Revises: 0086
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0087"
down_revision: str | None = "0086"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


LOOKUP_ACTIVE_PLATFORM_CAPABILITIES_SQL = """
CREATE OR REPLACE FUNCTION public.lookup_active_platform_capabilities(
  p_user_id UUID,
  p_session_id UUID
)
RETURNS TABLE(code TEXT) AS $$
DECLARE
  v_context_user_id UUID;
BEGIN
  v_context_user_id := public.current_app_user_id();

  IF (v_context_user_id IS NOT NULL AND v_context_user_id <> p_user_id)
    OR public.current_tenant_id() IS NOT NULL
    OR NOT EXISTS (
      SELECT 1
      FROM public.session AS auth_session
      WHERE auth_session.id = p_session_id
        AND auth_session.user_id = p_user_id
        AND auth_session.revoked_at IS NULL
        AND auth_session.expires_at > pg_catalog.now()
    )
  THEN
    RAISE EXCEPTION 'Platform capability lookup is outside the request identity'
      USING ERRCODE = '42501';
  END IF;

  RETURN QUERY
  SELECT permission.code
  FROM public.platform_access_grant AS platform_grant
  JOIN public.platform_access_grant_permission AS assignment
    ON assignment.grant_id = platform_grant.id
  JOIN public.permission AS permission
    ON permission.code = assignment.permission_code
   AND permission.is_active
   AND permission.target_role_type = 'platform'
   AND permission.scope_type = 'PLATFORM'
  JOIN public.app_user AS platform_user
    ON platform_user.id = platform_grant.user_id
   AND platform_user.status = 'active'
  WHERE platform_grant.user_id = p_user_id
    AND platform_grant.status = 'active'
  ORDER BY permission.code;
END;
$$ LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


READ_WITHOUT_STEP_UP_CODES = (
    "platform.tenants.view",
)


def upgrade() -> None:
    op.execute(LOOKUP_ACTIVE_PLATFORM_CAPABILITIES_SQL)
    op.execute(
        "REVOKE ALL PRIVILEGES ON FUNCTION "
        "public.lookup_active_platform_capabilities(UUID, UUID) "
        "FROM PUBLIC, aurum_app, aurum_support"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION "
        "public.lookup_active_platform_capabilities(UUID, UUID) "
        "TO aurum_app, aurum_support"
    )
    codes = ", ".join(f"'{code}'" for code in READ_WITHOUT_STEP_UP_CODES)
    op.execute(
        f"UPDATE public.permission SET requires_step_up = false "
        f"WHERE code IN ({codes})"
    )


def downgrade() -> None:
    codes = ", ".join(f"'{code}'" for code in READ_WITHOUT_STEP_UP_CODES)
    op.execute(
        f"UPDATE public.permission SET requires_step_up = true "
        f"WHERE code IN ({codes})"
    )
    op.execute(
        "DROP FUNCTION public.lookup_active_platform_capabilities(UUID, UUID)"
    )
