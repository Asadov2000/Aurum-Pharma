"""harden employee invitation idempotency and support containment

Revision ID: 0136
Revises: 0135
Create Date: 2026-09-04
"""

from __future__ import annotations

from collections.abc import Sequence
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType

from alembic import op

revision: str = "0136"
down_revision: str | Sequence[str] | None = "0135"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _load_revision(filename: str, module_name: str) -> ModuleType:
    path = Path(__file__).with_name(filename)
    spec = spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load migration dependency: {filename}")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


OLD_SIGNATURE = (
    "public.create_tenant_employee_invitation("
    "UUID, TEXT, TEXT, TEXT, UUID, TIMESTAMP WITH TIME ZONE)"
)
NEW_SIGNATURE = (
    "public.create_tenant_employee_invitation("
    "UUID, TEXT, TEXT, TEXT, UUID, TEXT, TIMESTAMP WITH TIME ZONE)"
)


CREATE_OWNER_EMPLOYEE_SQL = """
CREATE FUNCTION public.create_tenant_employee_invitation(
  p_tenant_id UUID,
  p_email TEXT,
  p_full_name TEXT,
  p_phone TEXT,
  p_operation_id UUID,
  p_request_fingerprint TEXT,
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
  v_existing_fingerprint TEXT;
BEGIN
  IF p_tenant_id IS NULL OR p_operation_id IS NULL OR v_actor_id IS NULL
    OR p_request_fingerprint IS NULL
    OR p_request_fingerprint !~ '^[0-9a-f]{64}$'
  THEN
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
      AND tenant.status NOT IN ('readonly', 'archived')
  ) THEN
    RAISE EXCEPTION 'Tenant is unavailable for employee creation'
      USING ERRCODE = '42501';
  END IF;

  SELECT invitation.user_id, invitation.membership_id, invitation.id,
         invitation.issued_at, invitation.expires_at, app_user.email_lower,
         invitation.request_fingerprint
  INTO employee_user_id, employee_membership_id, employee_invitation_id,
       invited_at, invitation_expires_at, v_existing_email,
       v_existing_fingerprint
  FROM public.tenant_invitation AS invitation
  JOIN public.app_user AS app_user ON app_user.id = invitation.user_id
  WHERE invitation.tenant_id = p_tenant_id
    AND invitation.operation_id = p_operation_id;
  IF employee_invitation_id IS NOT NULL THEN
    IF v_existing_email IS DISTINCT FROM v_email
      OR v_existing_fingerprint IS DISTINCT FROM p_request_fingerprint
    THEN
      RAISE EXCEPTION 'Employee invitation operation belongs to another request'
        USING ERRCODE = 'P2002';
    END IF;
    employee_created := false;
    RETURN NEXT;
    RETURN;
  END IF;

  BEGIN
    INSERT INTO public.app_user (
      email, full_name, phone, home_tenant_id,
      is_developer, is_administrator, status
    ) VALUES (
      v_email, v_full_name, v_phone, p_tenant_id,
      false, false, 'invited'
    )
    RETURNING app_user.id INTO employee_user_id;
  EXCEPTION WHEN unique_violation THEN
    RAISE EXCEPTION 'Employee account cannot be created with this email'
      USING ERRCODE = '23505';
  END;

  INSERT INTO public.tenant_membership (
    tenant_id, user_id, full_name, phone, status,
    invited_at, created_by, updated_by
  ) VALUES (
    p_tenant_id, employee_user_id, v_full_name, v_phone, 'pending',
    v_now, v_actor_id, v_actor_id
  )
  RETURNING tenant_membership.id INTO employee_membership_id;

  invited_at := v_now;
  invitation_expires_at := v_now + INTERVAL '7 days';
  INSERT INTO public.tenant_invitation (
    tenant_id, membership_id, user_id, version, status,
    operation_id, request_fingerprint, issued_at, expires_at, created_by
  ) VALUES (
    p_tenant_id, employee_membership_id, employee_user_id, 1, 'pending',
    p_operation_id, p_request_fingerprint, invited_at,
    invitation_expires_at, v_actor_id
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


SET_MEMBERSHIP_STATUS_SQL = """
CREATE OR REPLACE FUNCTION public.set_tenant_membership_status(
  p_tenant_id UUID,
  p_user_id UUID,
  p_status TEXT,
  p_changed_at TIMESTAMPTZ
) RETURNS BOOLEAN AS $$
DECLARE
  v_actor_id UUID := public.current_app_user_id();
  v_current_status TEXT;
  v_required_permission TEXT;
  v_updated INTEGER;
BEGIN
  IF p_changed_at IS NULL THEN
    RAISE EXCEPTION 'Status timestamp is required' USING ERRCODE = '22004';
  END IF;
  IF p_status = 'active' THEN v_required_permission := 'users.update';
  ELSIF p_status = 'suspended' THEN v_required_permission := 'users.block';
  ELSIF p_status = 'offboarded' THEN v_required_permission := 'users.delete';
  ELSE RAISE EXCEPTION 'Unsupported membership status transition' USING ERRCODE = '22023';
  END IF;
  IF NOT (
      public.tenant_actor_is_owner(p_tenant_id)
      OR public.is_tenant_support_session()
    )
    OR NOT public.tenant_actor_has_permission(p_tenant_id, v_required_permission)
  THEN RAISE EXCEPTION 'Membership status update is not allowed' USING ERRCODE = '42501';
  END IF;

  SELECT status INTO v_current_status
  FROM public.tenant_membership
  WHERE tenant_id = p_tenant_id AND user_id = p_user_id
  FOR UPDATE;
  IF v_current_status IS NULL OR p_user_id = v_actor_id OR v_current_status = 'offboarded'
    OR (p_status = 'active' AND v_current_status <> 'suspended')
    OR (p_status = 'suspended' AND v_current_status <> 'active')
  THEN RAISE EXCEPTION 'Membership status transition is not allowed' USING ERRCODE = '42501';
  END IF;
  IF NOT public.is_support_session() AND EXISTS (
    SELECT 1 FROM public.tenant_ownership AS ownership
    JOIN public.tenant_membership AS membership
      ON membership.id = ownership.membership_id AND membership.tenant_id = ownership.tenant_id
    WHERE ownership.tenant_id = p_tenant_id AND membership.user_id = p_user_id
      AND ownership.is_active
  ) THEN
    RAISE EXCEPTION 'An owner membership cannot be changed through user lifecycle'
      USING ERRCODE = '42501';
  END IF;

  UPDATE public.tenant_membership
  SET status = p_status,
      activated_at = CASE WHEN p_status = 'active' THEN COALESCE(activated_at, p_changed_at)
                          ELSE activated_at END,
      suspended_at = CASE WHEN p_status = 'suspended' THEN p_changed_at
                          WHEN p_status = 'active' THEN NULL ELSE suspended_at END,
      offboarded_at = CASE WHEN p_status = 'offboarded' THEN p_changed_at ELSE offboarded_at END,
      updated_by = v_actor_id
  WHERE tenant_id = p_tenant_id AND user_id = p_user_id;
  GET DIAGNOSTICS v_updated = ROW_COUNT;

  IF v_updated = 1 AND p_status = 'offboarded' THEN
    UPDATE public.tenant_invitation
    SET status = 'revoked', revoked_at = p_changed_at
    WHERE tenant_id = p_tenant_id AND user_id = p_user_id AND status = 'pending';
    UPDATE public.email_code AS code
    SET used_at = COALESCE(code.used_at, p_changed_at)
    WHERE code.tenant_invitation_id IN (
      SELECT id FROM public.tenant_invitation
      WHERE tenant_id = p_tenant_id AND user_id = p_user_id AND status = 'revoked'
    ) AND code.used_at IS NULL;
    UPDATE public.user_assignment
    SET is_active = false, updated_by = v_actor_id
    WHERE tenant_id = p_tenant_id AND user_id = p_user_id AND is_active;
  END IF;
  IF v_updated = 1 AND p_status IN ('suspended', 'offboarded') THEN
    UPDATE public.session
    SET revoked_at = COALESCE(revoked_at, p_changed_at),
        revoked_reason = COALESCE(revoked_reason, 'membership_' || p_status)
    WHERE user_id = p_user_id AND revoked_at IS NULL;
  END IF;
  RETURN v_updated = 1;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


def upgrade() -> None:
    op.execute("ALTER TABLE public.tenant_invitation " "ADD COLUMN request_fingerprint VARCHAR(64)")
    op.execute(
        "ALTER TABLE public.tenant_invitation "
        "ADD CONSTRAINT ck_tenant_invitation_request_fingerprint "
        "CHECK (request_fingerprint IS NULL OR request_fingerprint ~ '^[0-9a-f]{64}$')"
    )
    op.execute(f"DROP FUNCTION {OLD_SIGNATURE}")
    op.execute(CREATE_OWNER_EMPLOYEE_SQL)
    op.execute(f"ALTER FUNCTION {NEW_SIGNATURE} OWNER TO aurum_schema_owner")
    op.execute(
        f"REVOKE ALL PRIVILEGES ON FUNCTION {NEW_SIGNATURE} "
        "FROM PUBLIC, aurum_app, aurum_support"
    )
    op.execute(f"GRANT EXECUTE ON FUNCTION {NEW_SIGNATURE} TO aurum_app")
    op.execute(SET_MEMBERSHIP_STATUS_SQL)


def downgrade() -> None:
    previous_invite = _load_revision("0129_add_owner_employee_creation.py", "aurum_migration_0129")
    previous_membership = _load_revision(
        "0128_add_tenant_invitation_lifecycle.py", "aurum_migration_0128"
    )
    op.execute(previous_membership.SET_MEMBERSHIP_STATUS_SQL)
    op.execute(f"DROP FUNCTION {NEW_SIGNATURE}")
    op.execute(
        "ALTER TABLE public.tenant_invitation "
        "DROP CONSTRAINT ck_tenant_invitation_request_fingerprint"
    )
    op.execute("ALTER TABLE public.tenant_invitation DROP COLUMN request_fingerprint")
    op.execute(previous_invite.CREATE_OWNER_EMPLOYEE_SQL)
    op.execute(f"ALTER FUNCTION {OLD_SIGNATURE} OWNER TO aurum_schema_owner")
    op.execute(
        f"REVOKE ALL PRIVILEGES ON FUNCTION {OLD_SIGNATURE} "
        "FROM PUBLIC, aurum_app, aurum_support"
    )
    op.execute(f"GRANT EXECUTE ON FUNCTION {OLD_SIGNATURE} TO aurum_app")
