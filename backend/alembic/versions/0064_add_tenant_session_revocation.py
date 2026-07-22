"""security: add tenant-scoped administrative session revocation

Revision ID: 0064
Revises: 0063
Create Date: 2026-07-22

An owner or an explicitly scoped support operator may end every active session
of a tenant employee without suspending the membership.  The database function
rechecks the tenant, capability and protected-account boundaries and appends an
immutable audit event in the same transaction.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0064"
down_revision: str | Sequence[str] | None = "0063"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


REVOKE_TENANT_USER_AUTH_SESSIONS_SQL = """
CREATE FUNCTION public.revoke_tenant_user_auth_sessions(
  p_tenant_id UUID,
  p_target_user_id UUID
) RETURNS TABLE(result TEXT, revoked_count INTEGER) AS $$
DECLARE
  v_actor_is_developer BOOLEAN;
  v_actor_user_id UUID;
  v_revoked_count INTEGER;
  v_target_is_administrator BOOLEAN;
  v_target_is_developer BOOLEAN;
  v_target_is_owner BOOLEAN;
BEGIN
  v_actor_user_id := public.current_app_user_id();

  IF p_tenant_id IS NULL
    OR p_target_user_id IS NULL
    OR v_actor_user_id IS NULL
    OR p_tenant_id IS DISTINCT FROM public.current_tenant_id()
    OR NOT public.tenant_actor_has_permission(p_tenant_id, 'users.block')
  THEN
    RAISE EXCEPTION 'Administrative session revocation is unavailable'
      USING ERRCODE = '42501';
  END IF;

  IF v_actor_user_id = p_target_user_id THEN
    RETURN QUERY SELECT 'self'::TEXT, 0;
    RETURN;
  END IF;

  SELECT
    target.is_developer,
    target.is_administrator,
    EXISTS (
      SELECT 1
      FROM public.tenant_ownership AS ownership
      WHERE ownership.tenant_id = membership.tenant_id
        AND ownership.membership_id = membership.id
        AND ownership.is_active
    )
  INTO
    v_target_is_developer,
    v_target_is_administrator,
    v_target_is_owner
  FROM public.tenant_membership AS membership
  JOIN public.app_user AS target ON target.id = membership.user_id
  WHERE membership.tenant_id = p_tenant_id
    AND membership.user_id = p_target_user_id
    AND membership.status IN ('active', 'suspended')
  LIMIT 1;

  IF NOT FOUND THEN
    RETURN QUERY SELECT 'not_found'::TEXT, 0;
    RETURN;
  END IF;

  IF v_target_is_developer OR v_target_is_administrator THEN
    RETURN QUERY SELECT 'protected'::TEXT, 0;
    RETURN;
  END IF;

  SELECT actor.is_developer
  INTO v_actor_is_developer
  FROM public.app_user AS actor
  WHERE actor.id = v_actor_user_id
    AND actor.status = 'active';

  IF v_target_is_owner AND COALESCE(v_actor_is_developer, false) IS NOT TRUE THEN
    RETURN QUERY SELECT 'protected'::TEXT, 0;
    RETURN;
  END IF;

  UPDATE public.session AS auth_session
  SET
    revoked_at = pg_catalog.statement_timestamp(),
    revoked_reason = 'tenant_admin_revoked',
    last_used_at = pg_catalog.statement_timestamp()
  WHERE auth_session.user_id = p_target_user_id
    AND auth_session.revoked_at IS NULL
    AND auth_session.expires_at > pg_catalog.statement_timestamp();

  GET DIAGNOSTICS v_revoked_count = ROW_COUNT;

  INSERT INTO public.audit_log (
    tenant_id,
    user_id,
    action,
    table_name,
    record_id,
    metadata,
    created_at
  ) VALUES (
    p_tenant_id,
    v_actor_user_id,
    'UPDATE',
    'session',
    p_target_user_id,
    public.audit_redact_jsonb(
      pg_catalog.jsonb_build_object(
        'event', 'tenant_user_sessions_revoked',
        'target_user_id', p_target_user_id,
        'revoked_count', v_revoked_count
      )
    ),
    pg_catalog.statement_timestamp()
  );

  RETURN QUERY SELECT 'revoked'::TEXT, v_revoked_count;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


FUNCTION_SIGNATURE = "public.revoke_tenant_user_auth_sessions(UUID, UUID)"


def upgrade() -> None:
    # `users.block` is already the protected account-security capability.  It
    # remains non-delegable to ordinary tenant roles, but support sessions may
    # now request it explicitly for this workflow.
    op.execute("""
        UPDATE public.permission
        SET
          developer_grantable = true,
          administrator_grantable = true
        WHERE code = 'users.block'
        """)
    op.execute(REVOKE_TENANT_USER_AUTH_SESSIONS_SQL)
    op.execute(f"ALTER FUNCTION {FUNCTION_SIGNATURE} OWNER TO aurum_support")
    op.execute(
        f"REVOKE ALL PRIVILEGES ON FUNCTION {FUNCTION_SIGNATURE} FROM PUBLIC, aurum_app"
    )
    op.execute(f"GRANT EXECUTE ON FUNCTION {FUNCTION_SIGNATURE} TO aurum_support, aurum_app")


def downgrade() -> None:
    op.execute(f"DROP FUNCTION IF EXISTS {FUNCTION_SIGNATURE}")
    op.execute("""
        UPDATE public.permission
        SET
          developer_grantable = false,
          administrator_grantable = false
        WHERE code = 'users.block'
        """)
