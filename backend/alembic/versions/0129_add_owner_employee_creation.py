"""allow tenant owners to create their own employee accounts

Revision ID: 0129
Revises: 0128
Create Date: 2026-08-30
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0129"
down_revision: str | Sequence[str] | None = "0128"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


CREATE_OWNER_EMPLOYEE_SQL = """
CREATE FUNCTION public.create_tenant_employee_invitation(
  p_tenant_id UUID,
  p_email TEXT,
  p_full_name TEXT,
  p_phone TEXT,
  p_operation_id UUID,
  p_issued_at TIMESTAMPTZ
) RETURNS TABLE(
  employee_user_id UUID,
  employee_membership_id UUID,
  employee_invitation_id UUID,
  invited_at TIMESTAMPTZ,
  invitation_expires_at TIMESTAMPTZ,
  employee_created BOOLEAN
) AS $$
DECLARE
  v_actor_id UUID := public.current_app_user_id();
  v_email TEXT := pg_catalog.lower(pg_catalog.btrim(p_email));
  v_full_name TEXT := pg_catalog.btrim(p_full_name);
  v_phone TEXT := NULLIF(pg_catalog.btrim(p_phone), '');
  v_now TIMESTAMPTZ := COALESCE(p_issued_at, pg_catalog.statement_timestamp());
  v_existing_email TEXT;
BEGIN
  IF p_tenant_id IS NULL OR p_operation_id IS NULL OR v_actor_id IS NULL THEN
    RAISE EXCEPTION 'Employee invitation request is incomplete'
      USING ERRCODE = '22004';
  END IF;
  IF v_email IS NULL OR v_email = '' OR pg_catalog.char_length(v_email) > 320
    OR v_full_name IS NULL OR v_full_name = ''
    OR pg_catalog.char_length(v_full_name) > 200
    OR (v_phone IS NOT NULL AND pg_catalog.char_length(v_phone) > 50)
  THEN
    RAISE EXCEPTION 'Employee invitation details are invalid'
      USING ERRCODE = '22023';
  END IF;
  IF public.is_support_session()
    OR NOT public.tenant_actor_is_owner(p_tenant_id)
    OR NOT public.tenant_actor_has_permission(p_tenant_id, 'users.invite')
    OR NOT public.tenant_actor_has_permission(p_tenant_id, 'roles.assign')
  THEN
    RAISE EXCEPTION 'Only the active tenant owner can create employee accounts'
      USING ERRCODE = '42501';
  END IF;
  IF NOT EXISTS (
    SELECT 1
    FROM public.tenant AS tenant
    WHERE tenant.id = p_tenant_id
      AND tenant.status <> 'archived'
  ) THEN
    RAISE EXCEPTION 'Tenant is unavailable for employee creation'
      USING ERRCODE = '42501';
  END IF;

  SELECT invitation.user_id, invitation.membership_id, invitation.id,
         invitation.issued_at, invitation.expires_at, app_user.email_lower
  INTO employee_user_id, employee_membership_id, employee_invitation_id,
       invited_at, invitation_expires_at, v_existing_email
  FROM public.tenant_invitation AS invitation
  JOIN public.app_user AS app_user ON app_user.id = invitation.user_id
  WHERE invitation.tenant_id = p_tenant_id
    AND invitation.operation_id = p_operation_id;
  IF employee_invitation_id IS NOT NULL THEN
    IF v_existing_email IS DISTINCT FROM v_email THEN
      RAISE EXCEPTION 'Employee invitation operation belongs to another email'
        USING ERRCODE = '22023';
    END IF;
    employee_created := false;
    RETURN NEXT;
    RETURN;
  END IF;

  BEGIN
    INSERT INTO public.app_user (
      email,
      full_name,
      phone,
      home_tenant_id,
      is_developer,
      is_administrator,
      status
    ) VALUES (
      v_email,
      v_full_name,
      v_phone,
      p_tenant_id,
      false,
      false,
      'invited'
    )
    RETURNING app_user.id INTO employee_user_id;
  EXCEPTION WHEN unique_violation THEN
    RAISE EXCEPTION 'Employee account cannot be created with this email'
      USING ERRCODE = '23505';
  END;

  INSERT INTO public.tenant_membership (
    tenant_id,
    user_id,
    full_name,
    phone,
    status,
    invited_at,
    created_by,
    updated_by
  ) VALUES (
    p_tenant_id,
    employee_user_id,
    v_full_name,
    v_phone,
    'pending',
    v_now,
    v_actor_id,
    v_actor_id
  )
  RETURNING tenant_membership.id INTO employee_membership_id;

  invited_at := v_now;
  invitation_expires_at := v_now + INTERVAL '7 days';
  INSERT INTO public.tenant_invitation (
    tenant_id,
    membership_id,
    user_id,
    version,
    status,
    operation_id,
    issued_at,
    expires_at,
    created_by
  ) VALUES (
    p_tenant_id,
    employee_membership_id,
    employee_user_id,
    1,
    'pending',
    p_operation_id,
    invited_at,
    invitation_expires_at,
    v_actor_id
  )
  RETURNING tenant_invitation.id INTO employee_invitation_id;

  employee_created := true;
  RETURN NEXT;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""

REISSUE_WRAPPER_SQL = """
CREATE FUNCTION public.reissue_tenant_invitation(
  p_tenant_id UUID,
  p_user_id UUID,
  p_operation_id UUID,
  p_issued_at TIMESTAMPTZ
) RETURNS TABLE(
  invitation_id UUID,
  invitation_status TEXT,
  invited_at TIMESTAMPTZ,
  invitation_expires_at TIMESTAMPTZ
) AS $$
DECLARE
  v_invitation_id UUID;
  v_invitation_status TEXT;
  v_invited_at TIMESTAMPTZ;
  v_invitation_expires_at TIMESTAMPTZ;
  v_invitation_user_id UUID;
BEGIN
  SELECT result.invitation_id,
         result.invitation_status,
         result.invited_at,
         result.invitation_expires_at
  INTO v_invitation_id,
       v_invitation_status,
       v_invited_at,
       v_invitation_expires_at
  FROM public.reissue_tenant_invitation_0128(
    p_tenant_id, p_user_id, p_operation_id, p_issued_at
  ) AS result;

  SELECT invitation.user_id
  INTO v_invitation_user_id
  FROM public.tenant_invitation AS invitation
  WHERE invitation.id = v_invitation_id
    AND invitation.tenant_id = p_tenant_id;

  IF v_invitation_id IS NULL OR v_invitation_user_id IS DISTINCT FROM p_user_id THEN
    RAISE EXCEPTION 'Invitation operation belongs to another employee'
      USING ERRCODE = '22023';
  END IF;

  invitation_id := v_invitation_id;
  invitation_status := v_invitation_status;
  invited_at := v_invited_at;
  invitation_expires_at := v_invitation_expires_at;
  RETURN NEXT;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""

FUNCTION_SIGNATURE = (
    "public.create_tenant_employee_invitation("
    "UUID, TEXT, TEXT, TEXT, UUID, TIMESTAMP WITH TIME ZONE)"
)
REISSUE_SIGNATURE = "public.reissue_tenant_invitation(UUID, UUID, UUID, TIMESTAMPTZ)"
LEGACY_REISSUE_SIGNATURE = "public.reissue_tenant_invitation_0128(UUID, UUID, UUID, TIMESTAMPTZ)"


def upgrade() -> None:
    op.execute(CREATE_OWNER_EMPLOYEE_SQL)
    op.execute(f"ALTER FUNCTION {FUNCTION_SIGNATURE} OWNER TO aurum_schema_owner")
    op.execute(
        f"REVOKE ALL PRIVILEGES ON FUNCTION {FUNCTION_SIGNATURE} "
        "FROM PUBLIC, aurum_app, aurum_support"
    )
    op.execute(f"GRANT EXECUTE ON FUNCTION {FUNCTION_SIGNATURE} TO aurum_app")

    op.execute(f"ALTER FUNCTION {REISSUE_SIGNATURE} RENAME TO reissue_tenant_invitation_0128")
    op.execute(
        f"REVOKE ALL PRIVILEGES ON FUNCTION {LEGACY_REISSUE_SIGNATURE} "
        "FROM PUBLIC, aurum_app, aurum_support"
    )
    op.execute(REISSUE_WRAPPER_SQL)
    op.execute(f"ALTER FUNCTION {REISSUE_SIGNATURE} OWNER TO aurum_schema_owner")
    op.execute(
        f"REVOKE ALL PRIVILEGES ON FUNCTION {REISSUE_SIGNATURE} "
        "FROM PUBLIC, aurum_app, aurum_support"
    )
    op.execute(f"GRANT EXECUTE ON FUNCTION {REISSUE_SIGNATURE} TO aurum_app, aurum_support")


def downgrade() -> None:
    op.execute(f"DROP FUNCTION {FUNCTION_SIGNATURE}")
    op.execute(f"DROP FUNCTION {REISSUE_SIGNATURE}")
    op.execute(f"ALTER FUNCTION {LEGACY_REISSUE_SIGNATURE} RENAME TO reissue_tenant_invitation")
    op.execute(f"GRANT EXECUTE ON FUNCTION {REISSUE_SIGNATURE} TO aurum_app, aurum_support")
