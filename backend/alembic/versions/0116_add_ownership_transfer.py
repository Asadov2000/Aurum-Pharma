"""Add protected tenant ownership transfer workflow.

Revision ID: 0116
Revises: 0115
Create Date: 2026-08-28
"""

from __future__ import annotations

import importlib.util
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Union

from alembic import op

revision: str = "0116"
down_revision: Union[str, Sequence[str], None] = "0115"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

REFERENCE_TABLES = ("tenant", "tenant_membership", "app_user")


def _grant_missing_reference_privileges() -> None:
    op.execute("""
        CREATE TEMPORARY TABLE aurum_0116_missing_reference_privilege (
          table_name TEXT PRIMARY KEY
        ) ON COMMIT DROP
        """)
    for table_name in REFERENCE_TABLES:
        op.execute(f"""
            DO $$
            BEGIN
              IF NOT pg_catalog.has_table_privilege(
                'aurum_schema_owner',
                'public.{table_name}',
                'REFERENCES'
              ) THEN
                INSERT INTO pg_temp.aurum_0116_missing_reference_privilege (
                  table_name
                ) VALUES ('{table_name}');
                GRANT REFERENCES ON TABLE public.{table_name}
                  TO aurum_schema_owner;
              END IF;
            END
            $$
            """)


def _restore_reference_privileges() -> None:
    for table_name in REFERENCE_TABLES:
        op.execute(f"""
            DO $$
            BEGIN
              IF EXISTS (
                SELECT 1
                FROM pg_temp.aurum_0116_missing_reference_privilege
                WHERE table_name = '{table_name}'
              ) THEN
                REVOKE REFERENCES ON TABLE public.{table_name}
                  FROM aurum_schema_owner;
              END IF;
            END
            $$
            """)
    op.execute("DROP TABLE pg_temp.aurum_0116_missing_reference_privilege")


def _load_revision_module(filename: str) -> ModuleType:
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(f"aurum_migration_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load migration source: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _secure_function(signature: str, *, app_access: bool = False) -> None:
    op.execute(f"ALTER FUNCTION {signature} OWNER TO aurum_schema_owner")
    op.execute(
        f"REVOKE ALL PRIVILEGES ON FUNCTION {signature} "
        "FROM PUBLIC, aurum_app, aurum_support"
    )
    if app_access:
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO aurum_app")


ACCOUNT_REQUIRES_MFA_SQL = """
CREATE OR REPLACE FUNCTION public.auth_account_requires_mfa(p_user_id UUID)
RETURNS BOOLEAN AS $$
  SELECT EXISTS (
    SELECT 1
    FROM public.app_user AS app_user
    WHERE app_user.id = p_user_id
      AND app_user.status IN ('invited', 'active')
      AND (
        app_user.is_developer
        OR app_user.is_administrator
        OR EXISTS (
          SELECT 1
          FROM public.tenant_membership AS membership
          JOIN public.tenant_ownership AS ownership
            ON ownership.tenant_id = membership.tenant_id
           AND ownership.membership_id = membership.id
           AND ownership.is_active
          WHERE membership.user_id = app_user.id
            AND membership.tenant_id = app_user.home_tenant_id
            AND membership.status = 'active'
        )
        OR EXISTS (
          SELECT 1
          FROM public.tenant_membership AS membership
          JOIN public.tenant_ownership_transfer AS transfer
            ON transfer.tenant_id = membership.tenant_id
           AND transfer.target_membership_id = membership.id
           AND transfer.status = 'pending'
           AND transfer.expires_at > pg_catalog.statement_timestamp()
          WHERE membership.user_id = app_user.id
            AND membership.tenant_id = app_user.home_tenant_id
            AND membership.status = 'active'
        )
      )
  )
$$ LANGUAGE SQL
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


LEGACY_ACCOUNT_REQUIRES_MFA_SQL = """
CREATE OR REPLACE FUNCTION public.auth_account_requires_mfa(p_user_id UUID)
RETURNS BOOLEAN AS $$
  SELECT EXISTS (
    SELECT 1
    FROM public.app_user AS app_user
    WHERE app_user.id = p_user_id
      AND app_user.status IN ('invited', 'active')
      AND (
        app_user.is_developer
        OR app_user.is_administrator
        OR EXISTS (
          SELECT 1
          FROM public.tenant_membership AS membership
          JOIN public.tenant_ownership AS ownership
            ON ownership.tenant_id = membership.tenant_id
           AND ownership.membership_id = membership.id
           AND ownership.is_active
          WHERE membership.user_id = app_user.id
            AND membership.tenant_id = app_user.home_tenant_id
            AND membership.status = 'active'
        )
      )
  )
$$ LANGUAGE SQL
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


MFA_RECENT_SQL = """
CREATE FUNCTION public.ownership_transfer_mfa_is_recent()
RETURNS BOOLEAN AS $$
  SELECT CASE
    WHEN NULLIF(
      pg_catalog.current_setting('app.mfa_verified_at', true),
      ''
    ) IS NULL THEN false
    ELSE NULLIF(
      pg_catalog.current_setting('app.mfa_verified_at', true),
      ''
    )::BIGINT BETWEEN
      EXTRACT(epoch FROM pg_catalog.statement_timestamp())::BIGINT - 900
      AND EXTRACT(epoch FROM pg_catalog.statement_timestamp())::BIGINT + 60
  END
$$ LANGUAGE SQL
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


ASSIGNMENT_ALLOWED_SQL = """
CREATE FUNCTION public.ownership_transfer_assignment_allowed(
  p_tenant_id UUID,
  p_membership_id UUID,
  p_role_id UUID,
  p_operation TEXT,
  p_old_active BOOLEAN,
  p_new_active BOOLEAN
) RETURNS BOOLEAN AS $$
  SELECT EXISTS (
    SELECT 1
    FROM public.tenant_ownership_transfer AS transfer
    JOIN public.role AS assigned_role
      ON assigned_role.id = p_role_id
     AND assigned_role.tenant_id = transfer.tenant_id
    WHERE transfer.tenant_id = p_tenant_id
      AND transfer.status = 'completed'
      AND transfer.completion_xid = pg_catalog.txid_current()
      AND transfer.target_membership_id = (
        SELECT membership.id
        FROM public.tenant_membership AS membership
        WHERE membership.tenant_id = transfer.tenant_id
          AND membership.user_id = public.current_app_user_id()
      )
      AND (
        (
          p_membership_id = transfer.target_membership_id
          AND (
            (p_operation = 'UPDATE' AND p_old_active AND NOT p_new_active)
            OR (
              p_operation IN ('INSERT', 'UPDATE')
              AND p_new_active
              AND (p_operation = 'INSERT' OR NOT p_old_active)
              AND assigned_role.is_protected
              AND assigned_role.protected_kind = 'tenant_owner'
            )
          )
        )
        OR (
          p_membership_id = transfer.initiator_membership_id
          AND p_operation = 'UPDATE'
          AND p_old_active
          AND NOT p_new_active
          AND assigned_role.is_protected
          AND assigned_role.protected_kind = 'tenant_owner'
        )
      )
  )
$$ LANGUAGE SQL
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


OWNERSHIP_ALLOWED_SQL = """
CREATE FUNCTION public.ownership_transfer_ownership_allowed(
  p_tenant_id UUID,
  p_membership_id UUID,
  p_operation TEXT,
  p_old_active BOOLEAN,
  p_new_active BOOLEAN
) RETURNS BOOLEAN AS $$
  SELECT EXISTS (
    SELECT 1
    FROM public.tenant_ownership_transfer AS transfer
    WHERE transfer.tenant_id = p_tenant_id
      AND transfer.status = 'completed'
      AND transfer.completion_xid = pg_catalog.txid_current()
      AND transfer.target_membership_id = (
        SELECT membership.id
        FROM public.tenant_membership AS membership
        WHERE membership.tenant_id = transfer.tenant_id
          AND membership.user_id = public.current_app_user_id()
      )
      AND (
        (
          p_membership_id = transfer.target_membership_id
          AND p_operation = 'INSERT'
          AND p_new_active
        )
        OR (
          p_membership_id = transfer.initiator_membership_id
          AND p_operation = 'UPDATE'
          AND p_old_active
          AND NOT p_new_active
        )
      )
  )
$$ LANGUAGE SQL
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


CREATE_TRANSFER_SQL = """
CREATE FUNCTION public.create_tenant_ownership_transfer(
  p_operation_id UUID,
  p_target_membership_id UUID,
  p_expires_at TIMESTAMPTZ
) RETURNS UUID AS $$
DECLARE
  v_actor_id UUID := public.current_app_user_id();
  v_existing public.tenant_ownership_transfer%ROWTYPE;
  v_initiator_membership_id UUID;
  v_tenant_id UUID := public.current_tenant_id();
BEGIN
  IF p_operation_id IS NULL
    OR p_target_membership_id IS NULL
    OR p_expires_at IS NULL
    OR p_expires_at <= pg_catalog.statement_timestamp()
    OR p_expires_at > pg_catalog.statement_timestamp() + INTERVAL '7 days'
    OR v_actor_id IS NULL
    OR v_tenant_id IS NULL
    OR NOT public.ownership_transfer_mfa_is_recent()
  THEN
    RAISE EXCEPTION 'Ownership transfer request is invalid'
      USING ERRCODE = '22023';
  END IF;

  PERFORM tenant.id
  FROM public.tenant AS tenant
  WHERE tenant.id = v_tenant_id
  FOR UPDATE;

  SELECT membership.id
  INTO v_initiator_membership_id
  FROM public.tenant_membership AS membership
  JOIN public.tenant_ownership AS ownership
    ON ownership.tenant_id = membership.tenant_id
   AND ownership.membership_id = membership.id
   AND ownership.is_active
  WHERE membership.tenant_id = v_tenant_id
    AND membership.user_id = v_actor_id
    AND membership.status = 'active';

  IF v_initiator_membership_id IS NULL THEN
    RAISE EXCEPTION 'Active owner is required'
      USING ERRCODE = '42501';
  END IF;

  SELECT transfer.*
  INTO v_existing
  FROM public.tenant_ownership_transfer AS transfer
  WHERE transfer.id = p_operation_id;

  IF FOUND THEN
    IF v_existing.tenant_id = v_tenant_id
      AND v_existing.initiator_membership_id = v_initiator_membership_id
      AND v_existing.target_membership_id = p_target_membership_id
    THEN
      RETURN v_existing.id;
    END IF;
    RAISE EXCEPTION 'Ownership transfer operation conflicts with an existing request'
      USING ERRCODE = '23505';
  END IF;

  UPDATE public.tenant_ownership_transfer AS transfer
  SET
    status = 'expired',
    updated_at = pg_catalog.statement_timestamp(),
    updated_by = v_actor_id
  WHERE transfer.tenant_id = v_tenant_id
    AND transfer.status = 'pending'
    AND transfer.expires_at <= pg_catalog.statement_timestamp();

  IF p_target_membership_id = v_initiator_membership_id
    OR NOT EXISTS (
      SELECT 1
      FROM public.tenant_membership AS membership
      JOIN public.app_user AS target ON target.id = membership.user_id
      WHERE membership.id = p_target_membership_id
        AND membership.tenant_id = v_tenant_id
        AND membership.status = 'active'
        AND target.status IN ('invited', 'active')
        AND NOT target.is_developer
        AND NOT target.is_administrator
    )
    OR EXISTS (
      SELECT 1
      FROM public.tenant_ownership AS ownership
      WHERE ownership.tenant_id = v_tenant_id
        AND ownership.membership_id = p_target_membership_id
        AND ownership.is_active
    )
  THEN
    RAISE EXCEPTION 'Ownership transfer target is unavailable'
      USING ERRCODE = '42501';
  END IF;

  INSERT INTO public.tenant_ownership_transfer (
    id,
    tenant_id,
    initiator_membership_id,
    target_membership_id,
    status,
    expires_at,
    created_by,
    updated_by
  ) VALUES (
    p_operation_id,
    v_tenant_id,
    v_initiator_membership_id,
    p_target_membership_id,
    'pending',
    p_expires_at,
    v_actor_id,
    v_actor_id
  );

  UPDATE public.session AS auth_session
  SET
    revoked_at = pg_catalog.statement_timestamp(),
    revoked_reason = 'ownership_transfer_requested',
    last_used_at = pg_catalog.statement_timestamp()
  WHERE auth_session.user_id = (
      SELECT membership.user_id
      FROM public.tenant_membership AS membership
      WHERE membership.id = p_target_membership_id
    )
    AND auth_session.revoked_at IS NULL;

  RETURN p_operation_id;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


CANCEL_TRANSFER_SQL = """
CREATE FUNCTION public.cancel_tenant_ownership_transfer(p_request_id UUID)
RETURNS UUID AS $$
DECLARE
  v_actor_id UUID := public.current_app_user_id();
  v_request public.tenant_ownership_transfer%ROWTYPE;
BEGIN
  IF p_request_id IS NULL OR NOT public.ownership_transfer_mfa_is_recent() THEN
    RAISE EXCEPTION 'Ownership transfer cancellation is invalid'
      USING ERRCODE = '22023';
  END IF;

  SELECT transfer.*
  INTO v_request
  FROM public.tenant_ownership_transfer AS transfer
  WHERE transfer.id = p_request_id
  FOR UPDATE;

  IF NOT FOUND
    OR v_request.tenant_id IS DISTINCT FROM public.current_tenant_id()
    OR v_request.status <> 'pending'
    OR NOT EXISTS (
      SELECT 1
      FROM public.tenant_membership AS membership
      JOIN public.tenant_ownership AS ownership
        ON ownership.tenant_id = membership.tenant_id
       AND ownership.membership_id = membership.id
       AND ownership.is_active
      WHERE membership.id = v_request.initiator_membership_id
        AND membership.user_id = v_actor_id
        AND membership.status = 'active'
    )
  THEN
    RAISE EXCEPTION 'Ownership transfer cannot be cancelled'
      USING ERRCODE = '42501';
  END IF;

  UPDATE public.tenant_ownership_transfer
  SET
    status = 'cancelled',
    cancelled_at = pg_catalog.statement_timestamp(),
    updated_at = pg_catalog.statement_timestamp(),
    updated_by = v_actor_id
  WHERE id = p_request_id;

  RETURN p_request_id;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


ACCEPT_TRANSFER_SQL = """
CREATE FUNCTION public.accept_tenant_ownership_transfer(p_request_id UUID)
RETURNS UUID AS $$
DECLARE
  v_actor_id UUID := public.current_app_user_id();
  v_current_ownership_id UUID;
  v_owner_role_id UUID;
  v_request public.tenant_ownership_transfer%ROWTYPE;
  v_target_user_id UUID;
BEGIN
  IF p_request_id IS NULL OR NOT public.ownership_transfer_mfa_is_recent() THEN
    RAISE EXCEPTION 'Ownership transfer acceptance is invalid'
      USING ERRCODE = '22023';
  END IF;

  SELECT transfer.*
  INTO v_request
  FROM public.tenant_ownership_transfer AS transfer
  WHERE transfer.id = p_request_id
  FOR UPDATE;

  IF NOT FOUND
    OR v_request.tenant_id IS DISTINCT FROM public.current_tenant_id()
    OR v_request.status <> 'pending'
  THEN
    RAISE EXCEPTION 'Ownership transfer request is unavailable'
      USING ERRCODE = '42501';
  END IF;

  PERFORM tenant.id
  FROM public.tenant AS tenant
  WHERE tenant.id = v_request.tenant_id
  FOR UPDATE;

  IF v_request.expires_at <= pg_catalog.statement_timestamp() THEN
    UPDATE public.tenant_ownership_transfer
    SET
      status = 'expired',
      updated_at = pg_catalog.statement_timestamp(),
      updated_by = v_actor_id
    WHERE id = p_request_id;
    RAISE EXCEPTION 'Ownership transfer request expired'
      USING ERRCODE = 'P0001';
  END IF;

  SELECT membership.user_id
  INTO v_target_user_id
  FROM public.tenant_membership AS membership
  JOIN public.app_user AS target ON target.id = membership.user_id
  WHERE membership.id = v_request.target_membership_id
    AND membership.tenant_id = v_request.tenant_id
    AND membership.user_id = v_actor_id
    AND membership.status = 'active'
    AND target.status IN ('invited', 'active')
    AND public.auth_account_requires_mfa(target.id)
    AND EXISTS (
      SELECT 1
      FROM public.support_mfa AS account_mfa
      WHERE account_mfa.user_id = target.id
        AND account_mfa.status = 'active'
    );

  SELECT ownership.id
  INTO v_current_ownership_id
  FROM public.tenant_ownership AS ownership
  JOIN public.tenant_membership AS membership
    ON membership.id = ownership.membership_id
   AND membership.tenant_id = ownership.tenant_id
  WHERE ownership.tenant_id = v_request.tenant_id
    AND ownership.membership_id = v_request.initiator_membership_id
    AND ownership.is_active
    AND membership.status = 'active'
  FOR UPDATE OF ownership;

  SELECT role.id
  INTO v_owner_role_id
  FROM public.role AS role
  WHERE role.tenant_id = v_request.tenant_id
    AND role.is_active
    AND role.is_protected
    AND role.protected_kind = 'tenant_owner'
  LIMIT 1;

  IF v_target_user_id IS NULL THEN
    RAISE EXCEPTION 'Ownership transfer target is unavailable'
      USING ERRCODE = '42501';
  END IF;

  IF v_current_ownership_id IS NULL OR v_owner_role_id IS NULL THEN
    RAISE EXCEPTION 'Ownership transfer preconditions changed'
      USING ERRCODE = '40001';
  END IF;

  UPDATE public.tenant_ownership_transfer
  SET
    status = 'completed',
    completed_at = pg_catalog.statement_timestamp(),
    completion_xid = pg_catalog.txid_current(),
    updated_at = pg_catalog.statement_timestamp(),
    updated_by = v_actor_id
  WHERE id = p_request_id;

  UPDATE public.user_assignment AS assignment
  SET
    is_active = false,
    updated_at = pg_catalog.statement_timestamp(),
    updated_by = v_actor_id
  WHERE assignment.tenant_id = v_request.tenant_id
    AND assignment.membership_id = v_request.target_membership_id
    AND assignment.is_active;

  INSERT INTO public.tenant_ownership (
    tenant_id,
    membership_id,
    is_active,
    granted_at,
    created_by,
    updated_by
  ) VALUES (
    v_request.tenant_id,
    v_request.target_membership_id,
    true,
    pg_catalog.statement_timestamp(),
    v_actor_id,
    v_actor_id
  )
  ON CONFLICT (tenant_id, membership_id) DO UPDATE
  SET
    is_active = true,
    granted_at = pg_catalog.statement_timestamp(),
    revoked_at = NULL,
    updated_at = pg_catalog.statement_timestamp(),
    updated_by = v_actor_id;

  INSERT INTO public.user_assignment (
    user_id,
    tenant_id,
    membership_id,
    branch_id,
    role_id,
    password_required,
    is_active,
    created_by,
    updated_by
  ) VALUES (
    v_target_user_id,
    v_request.tenant_id,
    v_request.target_membership_id,
    NULL,
    v_owner_role_id,
    false,
    true,
    v_actor_id,
    v_actor_id
  )
  ON CONFLICT (user_id, tenant_id, branch_id) DO UPDATE
  SET
    membership_id = EXCLUDED.membership_id,
    role_id = EXCLUDED.role_id,
    password_required = false,
    is_active = true,
    updated_at = pg_catalog.statement_timestamp(),
    updated_by = v_actor_id;

  UPDATE public.user_assignment AS assignment
  SET
    is_active = false,
    updated_at = pg_catalog.statement_timestamp(),
    updated_by = v_actor_id
  WHERE assignment.tenant_id = v_request.tenant_id
    AND assignment.membership_id = v_request.initiator_membership_id
    AND assignment.is_active;

  UPDATE public.tenant_ownership
  SET
    is_active = false,
    revoked_at = pg_catalog.statement_timestamp(),
    updated_at = pg_catalog.statement_timestamp(),
    updated_by = v_actor_id
  WHERE id = v_current_ownership_id;

  UPDATE public.session AS auth_session
  SET
    revoked_at = pg_catalog.statement_timestamp(),
    revoked_reason = 'ownership_transferred',
    last_used_at = pg_catalog.statement_timestamp()
  WHERE auth_session.user_id IN (
      v_actor_id,
      (
        SELECT membership.user_id
        FROM public.tenant_membership AS membership
        WHERE membership.id = v_request.initiator_membership_id
      )
    )
    AND (
      auth_session.revoked_at IS NULL
      OR auth_session.revoked_reason = 'ownership_transfer_requested'
    );

  INSERT INTO public.audit_log (
    tenant_id,
    user_id,
    action,
    table_name,
    record_id,
    metadata,
    created_at
  ) VALUES (
    v_request.tenant_id,
    v_actor_id,
    'UPDATE',
    'tenant_ownership_transfer',
    p_request_id,
    public.audit_redact_jsonb(
      pg_catalog.jsonb_build_object(
        'event', 'tenant_ownership_transferred',
        'from_membership_id', v_request.initiator_membership_id,
        'to_membership_id', v_request.target_membership_id
      )
    ),
    pg_catalog.statement_timestamp()
  );

  RETURN p_request_id;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


def _transfer_aware_assignment_trigger(source_0077: ModuleType) -> str:
    statement = source_0077._assignment_scope_trigger_sql(protect_owner_accounts=True)
    marker = "  -- Ownership changes have their own protected workflow."
    guard = """
  IF public.ownership_transfer_assignment_allowed(
    NEW.tenant_id,
    NEW.membership_id,
    NEW.role_id,
    TG_OP,
    CASE WHEN TG_OP = 'UPDATE' THEN OLD.is_active ELSE NULL END,
    NEW.is_active
  ) THEN
    RETURN NEW;
  END IF;

"""
    if marker not in statement:
        raise RuntimeError("Owner assignment trigger contract changed")
    return statement.replace(marker, guard + marker, 1)


def _transfer_aware_ownership_trigger(source_0077: ModuleType) -> str:
    statement = source_0077._ownership_guard_trigger_sql(protect_owner_accounts=True)
    marker = "  IF NOT EXISTS (\n    SELECT 1\n    FROM public.tenant_membership AS membership"
    guard = """
  IF public.ownership_transfer_ownership_allowed(
    NEW.tenant_id,
    NEW.membership_id,
    TG_OP,
    CASE WHEN TG_OP = 'UPDATE' THEN OLD.is_active ELSE NULL END,
    NEW.is_active
  ) THEN
    RETURN NEW;
  END IF;

"""
    if marker not in statement:
        raise RuntimeError("Owner ownership trigger contract changed")
    return statement.replace(marker, guard + marker, 1)


def upgrade() -> None:
    source_0077 = _load_revision_module("0077_protect_owner_assignments.py")

    _grant_missing_reference_privileges()
    op.execute("""
        CREATE TABLE public.tenant_ownership_transfer (
          id UUID PRIMARY KEY,
          tenant_id UUID NOT NULL
            REFERENCES public.tenant(id) ON DELETE RESTRICT,
          initiator_membership_id UUID NOT NULL
            REFERENCES public.tenant_membership(id) ON DELETE RESTRICT,
          target_membership_id UUID NOT NULL
            REFERENCES public.tenant_membership(id) ON DELETE RESTRICT,
          status TEXT NOT NULL DEFAULT 'pending',
          expires_at TIMESTAMPTZ NOT NULL,
          completed_at TIMESTAMPTZ,
          cancelled_at TIMESTAMPTZ,
          completion_xid BIGINT,
          created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          created_by UUID REFERENCES public.app_user(id) ON DELETE RESTRICT,
          updated_by UUID REFERENCES public.app_user(id) ON DELETE RESTRICT,
          CONSTRAINT ck_tenant_ownership_transfer_distinct_memberships
            CHECK (initiator_membership_id <> target_membership_id),
          CONSTRAINT ck_tenant_ownership_transfer_status
            CHECK (status IN ('pending', 'completed', 'cancelled', 'expired')),
          CONSTRAINT ck_tenant_ownership_transfer_timestamps CHECK (
            (status = 'pending'
              AND completed_at IS NULL
              AND cancelled_at IS NULL
              AND completion_xid IS NULL)
            OR (status = 'completed'
              AND completed_at IS NOT NULL
              AND cancelled_at IS NULL
              AND completion_xid IS NOT NULL)
            OR (status = 'cancelled'
              AND completed_at IS NULL
              AND cancelled_at IS NOT NULL
              AND completion_xid IS NULL)
            OR (status = 'expired'
              AND completed_at IS NULL
              AND cancelled_at IS NULL
              AND completion_xid IS NULL)
          ),
          CONSTRAINT ck_tenant_ownership_transfer_expiry
            CHECK (expires_at > created_at)
        )
        """)
    _restore_reference_privileges()
    op.execute("""
        CREATE UNIQUE INDEX uq_tenant_ownership_transfer_pending
        ON public.tenant_ownership_transfer (tenant_id)
        WHERE status = 'pending'
        """)
    op.execute("""
        CREATE INDEX ix_tenant_ownership_transfer_target_status
        ON public.tenant_ownership_transfer (
          target_membership_id,
          status,
          expires_at DESC
        )
        """)

    op.execute("ALTER TABLE public.tenant_ownership_transfer ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.tenant_ownership_transfer FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_ownership_transfer_owner_access
        ON public.tenant_ownership_transfer
        TO aurum_schema_owner
        USING (true)
        WITH CHECK (true)
        """)
    op.execute("""
        CREATE POLICY tenant_ownership_transfer_participant_read
        ON public.tenant_ownership_transfer
        FOR SELECT TO aurum_app
        USING (
          tenant_id = public.current_tenant_id()
          AND EXISTS (
            SELECT 1
            FROM public.tenant_membership AS participant
            WHERE participant.tenant_id = tenant_ownership_transfer.tenant_id
              AND participant.user_id = public.current_app_user_id()
              AND participant.id IN (
                tenant_ownership_transfer.initiator_membership_id,
                tenant_ownership_transfer.target_membership_id
              )
          )
        )
        """)
    op.execute(
        "REVOKE ALL PRIVILEGES ON TABLE public.tenant_ownership_transfer "
        "FROM PUBLIC, aurum_app, aurum_support"
    )
    op.execute(
        "GRANT SELECT ON TABLE public.tenant_ownership_transfer TO aurum_app, aurum_support"
    )

    op.execute("""
        CREATE TRIGGER trg_audit_tenant_ownership_transfer
        AFTER INSERT OR UPDATE ON public.tenant_ownership_transfer
        FOR EACH ROW EXECUTE FUNCTION public.trg_audit_log()
        """)

    op.execute(MFA_RECENT_SQL)
    _secure_function("public.ownership_transfer_mfa_is_recent()")
    op.execute(ASSIGNMENT_ALLOWED_SQL)
    _secure_function(
        "public.ownership_transfer_assignment_allowed(UUID, UUID, UUID, TEXT, BOOLEAN, BOOLEAN)"
    )
    op.execute(OWNERSHIP_ALLOWED_SQL)
    _secure_function(
        "public.ownership_transfer_ownership_allowed(UUID, UUID, TEXT, BOOLEAN, BOOLEAN)"
    )
    op.execute(ACCOUNT_REQUIRES_MFA_SQL)
    _secure_function("public.auth_account_requires_mfa(UUID)")

    op.execute(_transfer_aware_assignment_trigger(source_0077))
    op.execute(_transfer_aware_ownership_trigger(source_0077))

    op.execute(CREATE_TRANSFER_SQL)
    _secure_function(
        "public.create_tenant_ownership_transfer(UUID, UUID, TIMESTAMP WITH TIME ZONE)",
        app_access=True,
    )
    op.execute(CANCEL_TRANSFER_SQL)
    _secure_function(
        "public.cancel_tenant_ownership_transfer(UUID)",
        app_access=True,
    )
    op.execute(ACCEPT_TRANSFER_SQL)
    _secure_function(
        "public.accept_tenant_ownership_transfer(UUID)",
        app_access=True,
    )


def downgrade() -> None:
    source_0077 = _load_revision_module("0077_protect_owner_assignments.py")

    op.execute("DROP FUNCTION public.accept_tenant_ownership_transfer(UUID)")
    op.execute("DROP FUNCTION public.cancel_tenant_ownership_transfer(UUID)")
    op.execute(
        "DROP FUNCTION public.create_tenant_ownership_transfer("
        "UUID, UUID, TIMESTAMP WITH TIME ZONE)"
    )
    op.execute(source_0077._ownership_guard_trigger_sql(protect_owner_accounts=True))
    op.execute(source_0077._assignment_scope_trigger_sql(protect_owner_accounts=True))
    op.execute(LEGACY_ACCOUNT_REQUIRES_MFA_SQL)
    _secure_function("public.auth_account_requires_mfa(UUID)")
    op.execute(
        "DROP FUNCTION public.ownership_transfer_ownership_allowed("
        "UUID, UUID, TEXT, BOOLEAN, BOOLEAN)"
    )
    op.execute(
        "DROP FUNCTION public.ownership_transfer_assignment_allowed("
        "UUID, UUID, UUID, TEXT, BOOLEAN, BOOLEAN)"
    )
    op.execute("DROP FUNCTION public.ownership_transfer_mfa_is_recent()")
    op.execute("DROP TABLE public.tenant_ownership_transfer")
