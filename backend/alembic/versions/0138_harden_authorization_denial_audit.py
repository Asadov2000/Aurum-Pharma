"""Harden critical permissions and audit authorization denials.

Revision ID: 0138
Revises: 0137
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0138"
down_revision: str | Sequence[str] | None = "0137"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


AUDIT_ACTIONS = (
    "INSERT",
    "UPDATE",
    "DELETE",
    "VIEW",
    "EXPORT",
    "IMPERSONATE",
    "MEMBERSHIP_CREATED",
    "MEMBERSHIP_UPDATED",
    "MEMBERSHIP_ACTIVATED",
    "MEMBERSHIP_SUSPENDED",
    "MEMBERSHIP_OFFBOARDED",
    "OWNERSHIP_GRANTED",
    "OWNERSHIP_REVOKED",
    "ROLE_PERMISSIONS_CHANGED",
    "ROLE_VERSION_PUBLISHED",
    "ROLE_ARCHIVED_WITH_REPLACEMENT",
    "AUTHORIZATION_DENIED",
)


def _set_audit_actions(actions: Sequence[str]) -> None:
    values = ", ".join(f"'{action}'" for action in actions)
    op.execute("ALTER TABLE public.audit_log DROP CONSTRAINT audit_log_action_check")
    op.execute(
        "ALTER TABLE public.audit_log "
        f"ADD CONSTRAINT audit_log_action_check CHECK (action IN ({values}))"
    )


def _append_audit_event_sql(actions: Sequence[str]) -> str:
    explicit_actions = ", ".join(
        f"'{action}'"
        for action in actions
        if action in {"VIEW", "EXPORT", "IMPERSONATE", "AUTHORIZATION_DENIED"}
    )
    return f"""
CREATE OR REPLACE FUNCTION public.append_audit_event(
  p_tenant_id UUID,
  p_user_id UUID,
  p_action TEXT,
  p_table_name TEXT,
  p_record_id UUID,
  p_metadata JSONB
) RETURNS UUID AS $$
DECLARE
  v_id UUID;
BEGIN
  IF p_action NOT IN ({explicit_actions}) THEN
    RAISE EXCEPTION 'Only explicit audit actions may be appended'
      USING ERRCODE = '22023';
  END IF;

  IF NULLIF(pg_catalog.btrim(p_table_name), '') IS NULL THEN
    RAISE EXCEPTION 'Audit table name must not be empty'
      USING ERRCODE = '22023';
  END IF;

  IF SESSION_USER <> 'aurum_support' THEN
    IF p_tenant_id IS NULL
      OR p_tenant_id IS DISTINCT FROM public.current_tenant_id()
    THEN
      RAISE EXCEPTION 'Audit tenant does not match the active context'
        USING ERRCODE = '42501';
    END IF;

    IF p_user_id IS DISTINCT FROM public.current_app_user_id() THEN
      RAISE EXCEPTION 'Audit user does not match the active context'
        USING ERRCODE = '42501';
    END IF;
  END IF;

  IF p_action = 'IMPERSONATE' AND SESSION_USER <> 'aurum_support' THEN
    RAISE EXCEPTION 'Only the support role may log impersonation'
      USING ERRCODE = '42501';
  END IF;

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
    p_user_id,
    p_action,
    p_table_name,
    p_record_id,
    public.audit_redact_jsonb(p_metadata),
    pg_catalog.now()
  )
  RETURNING id INTO v_id;

  RETURN v_id;
END;
$$ LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
"""


def upgrade() -> None:
    op.execute("""
        UPDATE public.permission
        SET requires_step_up = true
        WHERE code IN ('incoming.finalize', 'pos.refund_external_confirm')
        """)
    op.execute("""
        ALTER TABLE public.permission
          ADD CONSTRAINT ck_permission_critical_requires_step_up
            CHECK (risk_level <> 'critical' OR requires_step_up) NOT VALID,
          ADD CONSTRAINT ck_permission_dangerous_requires_confirmation
            CHECK (NOT is_dangerous OR requires_confirmation) NOT VALID
        """)
    op.execute(
        "ALTER TABLE public.permission "
        "VALIDATE CONSTRAINT ck_permission_critical_requires_step_up"
    )
    op.execute(
        "ALTER TABLE public.permission "
        "VALIDATE CONSTRAINT ck_permission_dangerous_requires_confirmation"
    )
    _set_audit_actions(AUDIT_ACTIONS)
    op.execute(_append_audit_event_sql(AUDIT_ACTIONS))


def downgrade() -> None:
    op.execute(_append_audit_event_sql(("VIEW", "EXPORT", "IMPERSONATE")))
    op.execute("""
        ALTER TABLE public.permission
          DROP CONSTRAINT ck_permission_dangerous_requires_confirmation,
          DROP CONSTRAINT ck_permission_critical_requires_step_up
        """)
    op.execute("""
        UPDATE public.permission
        SET requires_step_up = false
        WHERE code IN ('incoming.finalize', 'pos.refund_external_confirm')
        """)
    # Existing append-only denial events remain valid after downgrade.
    _set_audit_actions(AUDIT_ACTIONS)
