"""authorization: add protected platform access grant lifecycle

Revision ID: 0066
Revises: 0065
Create Date: 2026-07-26

Developer and Administrator access becomes an explicit, append-only grant
instead of an ordinary mutable account attribute. Existing boolean columns
remain as a compatibility projection until all JWT and auth SQL consumers are
cut over. New grants require another active Developer when one is available.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0066"
down_revision: str | Sequence[str] | None = "0065"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


LEGACY_PLATFORM_ACCESS_PREFLIGHT_SQL = """
DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM public.app_user AS account
    WHERE (account.is_developer OR account.is_administrator)
      AND (
        account.status IS DISTINCT FROM 'active'
        OR (account.is_developer AND account.is_administrator)
        OR account.home_tenant_id IS NOT NULL
        OR EXISTS (
          SELECT 1
          FROM public.tenant_membership AS membership
          WHERE membership.user_id = account.id
            AND membership.status IN ('pending', 'active', 'suspended')
        )
        OR EXISTS (
          SELECT 1
          FROM public.tenant_ownership AS ownership
          JOIN public.tenant_membership AS membership
            ON membership.id = ownership.membership_id
          WHERE membership.user_id = account.id
            AND ownership.is_active
        )
        OR EXISTS (
          SELECT 1
          FROM public.user_assignment AS assignment
          WHERE assignment.user_id = account.id
            AND assignment.is_active
        )
      )
  ) THEN
    RAISE EXCEPTION
      'Legacy platform accounts must be active, single-kind, and outside tenant scope'
      USING ERRCODE = '23514';
  END IF;
END;
$$;
"""


PLATFORM_GRANT_GUARD_SQL = """
CREATE FUNCTION public.trg_guard_platform_access_grant()
RETURNS TRIGGER AS $$
DECLARE
  v_actor_id UUID;
  v_actor_is_developer BOOLEAN;
  v_has_other_developer BOOLEAN;
  v_target_is_available BOOLEAN;
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'Platform access grant history cannot be deleted'
      USING ERRCODE = '42501';
  END IF;

  -- Serialise the control-plane workflow so concurrent revocations cannot
  -- remove every Developer after both transactions observe the other.
  PERFORM pg_catalog.pg_advisory_xact_lock(7148, 1);

  -- Trusted bootstrap only runs while an app_user row with a projected flag is
  -- inserted outside an authenticated request.
  IF TG_OP = 'INSERT'
    AND NEW.requested_by IS NULL
    AND public.current_app_user_id() IS NULL
    AND public.is_support_session()
    AND NEW.status = 'active'
    AND NOT NEW.requires_approval
    AND NEW.approval_expires_at IS NULL
    AND NEW.approved_by IS NULL
    AND NEW.approved_at IS NULL
    AND NEW.approval_reason_code IS NULL
    AND NEW.approval_reason IS NULL
    AND NEW.revoked_by IS NULL
    AND NEW.revoked_at IS NULL
    AND NEW.revoke_reason_code IS NULL
    AND NEW.revoke_reason IS NULL
    AND NEW.request_reason_code = 'bootstrap'
    AND NEW.request_reason = 'Trusted support bootstrap account creation'
    AND NEW.access_kind = 'developer'
    AND NOT EXISTS (
      SELECT 1
      FROM public.platform_access_grant
      WHERE access_kind = 'developer'
    )
    AND EXISTS (
      SELECT 1
      FROM public.app_user AS bootstrap_account
      WHERE bootstrap_account.id = NEW.user_id
        AND (
          (
            NEW.access_kind = 'developer'
            AND bootstrap_account.is_developer
          )
          OR (
            NEW.access_kind = 'administrator'
            AND bootstrap_account.is_administrator
          )
        )
    )
  THEN
    RETURN NEW;
  END IF;

  -- Disabling a platform account is a narrow revocation-only transition
  -- initiated by the app_user status trigger.
  IF TG_OP = 'UPDATE'
    AND OLD.status IN ('pending', 'active')
    AND NEW.status = 'revoked'
    AND NEW.user_id IS NOT DISTINCT FROM OLD.user_id
    AND NEW.access_kind IS NOT DISTINCT FROM OLD.access_kind
    AND NEW.requested_by IS NOT DISTINCT FROM OLD.requested_by
    AND NEW.request_reason_code IS NOT DISTINCT FROM OLD.request_reason_code
    AND NEW.request_reason IS NOT DISTINCT FROM OLD.request_reason
    AND NEW.requested_at IS NOT DISTINCT FROM OLD.requested_at
    AND NEW.requires_approval IS NOT DISTINCT FROM OLD.requires_approval
    AND NEW.approval_expires_at IS NOT DISTINCT FROM OLD.approval_expires_at
    AND NEW.created_at IS NOT DISTINCT FROM OLD.created_at
    AND NEW.version = OLD.version + 1
    AND NEW.revoked_by IS NOT DISTINCT FROM public.current_app_user_id()
    AND NEW.revoked_at IS NOT DISTINCT FROM pg_catalog.statement_timestamp()
    AND NEW.updated_at IS NOT DISTINCT FROM pg_catalog.statement_timestamp()
    AND NEW.revoke_reason_code = 'account_disabled'
    AND NEW.revoke_reason = 'Platform account disabled'
    AND NEW.approved_by IS NOT DISTINCT FROM OLD.approved_by
    AND NEW.approved_at IS NOT DISTINCT FROM OLD.approved_at
    AND NEW.approval_reason_code IS NOT DISTINCT FROM OLD.approval_reason_code
    AND NEW.approval_reason IS NOT DISTINCT FROM OLD.approval_reason
    AND EXISTS (
      SELECT 1
      FROM public.app_user AS deactivated_account
      WHERE deactivated_account.id = NEW.user_id
        AND deactivated_account.status <> 'active'
    )
  THEN
    RETURN NEW;
  END IF;

  v_actor_id := public.current_app_user_id();
  SELECT EXISTS (
    SELECT 1
    FROM public.platform_access_grant AS actor_grant
    JOIN public.app_user AS actor
      ON actor.id = actor_grant.user_id
     AND actor.status = 'active'
    WHERE actor_grant.user_id = v_actor_id
      AND actor_grant.access_kind = 'developer'
      AND actor_grant.status = 'active'
  )
  INTO v_actor_is_developer;

  IF v_actor_id IS NULL
    OR NOT public.is_support_session()
    OR public.current_tenant_id() IS NOT NULL
    OR NOT v_actor_is_developer
  THEN
    RAISE EXCEPTION 'Active Developer platform context required'
      USING ERRCODE = '42501';
  END IF;

  IF TG_OP = 'INSERT' THEN
    IF NEW.requested_by IS DISTINCT FROM v_actor_id
      OR NEW.user_id = v_actor_id
      OR NEW.version <> 1
      OR NEW.requested_at IS DISTINCT FROM pg_catalog.statement_timestamp()
      OR NEW.created_at IS DISTINCT FROM pg_catalog.statement_timestamp()
      OR NEW.updated_at IS DISTINCT FROM pg_catalog.statement_timestamp()
      OR NEW.approved_by IS NOT NULL
      OR NEW.approved_at IS NOT NULL
      OR NEW.approval_reason_code IS NOT NULL
      OR NEW.approval_reason IS NOT NULL
      OR NEW.revoked_by IS NOT NULL
      OR NEW.revoked_at IS NOT NULL
      OR NEW.revoke_reason_code IS NOT NULL
      OR NEW.revoke_reason IS NOT NULL
    THEN
      RAISE EXCEPTION 'Invalid platform access request'
        USING ERRCODE = '42501';
    END IF;

    SELECT EXISTS (
      SELECT 1
      FROM public.app_user AS target
      WHERE target.id = NEW.user_id
        AND target.status = 'active'
        AND target.home_tenant_id IS NULL
        AND NOT EXISTS (
          SELECT 1
          FROM public.tenant_membership AS membership
          WHERE membership.user_id = target.id
            AND membership.status IN ('pending', 'active', 'suspended')
        )
    )
    INTO v_target_is_available;

    IF NOT v_target_is_available THEN
      RAISE EXCEPTION 'Target account is unavailable for platform access'
        USING ERRCODE = '42501';
    END IF;

    IF EXISTS (
      SELECT 1
      FROM public.platform_access_grant AS existing
      WHERE existing.user_id = NEW.user_id
        AND existing.status IN ('pending', 'active')
    ) THEN
      RAISE EXCEPTION 'Target already has pending or active platform access'
        USING ERRCODE = '23505';
    END IF;

    SELECT EXISTS (
      SELECT 1
      FROM public.platform_access_grant AS developer_grant
      JOIN public.app_user AS developer
        ON developer.id = developer_grant.user_id
       AND developer.status = 'active'
      WHERE developer_grant.access_kind = 'developer'
        AND developer_grant.status = 'active'
        AND developer_grant.user_id <> v_actor_id
    )
    INTO v_has_other_developer;

    IF v_has_other_developer THEN
      IF NEW.status <> 'pending'
        OR NOT NEW.requires_approval
        OR NEW.approval_expires_at IS NULL
        OR NEW.approval_expires_at <= pg_catalog.statement_timestamp()
        OR NEW.approval_expires_at
          > pg_catalog.statement_timestamp() + INTERVAL '20 minutes'
      THEN
        RAISE EXCEPTION 'A second Developer approval is required'
          USING ERRCODE = '42501';
      END IF;
    ELSIF NEW.status <> 'active'
      OR NEW.requires_approval
      OR NEW.approval_expires_at IS NOT NULL
    THEN
      RAISE EXCEPTION 'Sole-Developer grant must activate atomically'
        USING ERRCODE = '42501';
    END IF;

    RETURN NEW;
  END IF;

  IF OLD.user_id IS DISTINCT FROM NEW.user_id
    OR OLD.access_kind IS DISTINCT FROM NEW.access_kind
    OR OLD.requested_by IS DISTINCT FROM NEW.requested_by
    OR OLD.request_reason_code IS DISTINCT FROM NEW.request_reason_code
    OR OLD.request_reason IS DISTINCT FROM NEW.request_reason
    OR OLD.requested_at IS DISTINCT FROM NEW.requested_at
    OR OLD.requires_approval IS DISTINCT FROM NEW.requires_approval
    OR OLD.approval_expires_at IS DISTINCT FROM NEW.approval_expires_at
    OR OLD.created_at IS DISTINCT FROM NEW.created_at
  THEN
    RAISE EXCEPTION 'Platform access request fields are immutable'
      USING ERRCODE = '42501';
  END IF;

  IF NEW.version <> OLD.version + 1 THEN
    RAISE EXCEPTION 'Platform access transition requires the next version'
      USING ERRCODE = '40001';
  END IF;

  IF OLD.status = 'pending' AND NEW.status = 'active' THEN
    IF NOT OLD.requires_approval
      OR OLD.approval_expires_at <= pg_catalog.statement_timestamp()
      OR v_actor_id = OLD.requested_by
      OR NEW.approved_by IS DISTINCT FROM v_actor_id
      OR NEW.approved_at IS DISTINCT FROM pg_catalog.statement_timestamp()
      OR NEW.approval_reason_code IS NULL
      OR NEW.approval_reason IS NULL
      OR NEW.updated_at IS DISTINCT FROM pg_catalog.statement_timestamp()
      OR NEW.revoked_by IS NOT NULL
      OR NEW.revoked_at IS NOT NULL
      OR NEW.revoke_reason_code IS NOT NULL
      OR NEW.revoke_reason IS NOT NULL
      OR NOT EXISTS (
        SELECT 1
        FROM public.app_user AS target
        WHERE target.id = NEW.user_id
          AND target.status = 'active'
          AND target.home_tenant_id IS NULL
          AND NOT EXISTS (
            SELECT 1
            FROM public.tenant_membership AS membership
            WHERE membership.user_id = target.id
              AND membership.status IN ('pending', 'active', 'suspended')
          )
      )
    THEN
      RAISE EXCEPTION 'Independent, timely Developer approval required'
        USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
  END IF;

  IF OLD.status = 'pending' AND NEW.status IN ('revoked', 'expired') THEN
    IF NEW.revoked_at IS DISTINCT FROM pg_catalog.statement_timestamp()
      OR NEW.revoke_reason_code IS NULL
      OR NEW.revoke_reason IS NULL
      OR NEW.revoked_by IS DISTINCT FROM v_actor_id
      OR NEW.updated_at IS DISTINCT FROM pg_catalog.statement_timestamp()
      OR NEW.approved_by IS NOT NULL
      OR NEW.approved_at IS NOT NULL
      OR NEW.approval_reason_code IS NOT NULL
      OR NEW.approval_reason IS NOT NULL
    THEN
      RAISE EXCEPTION 'Invalid pending platform access termination'
        USING ERRCODE = '42501';
    END IF;
    IF NEW.status = 'expired'
      AND OLD.approval_expires_at > pg_catalog.statement_timestamp()
    THEN
      RAISE EXCEPTION 'Platform access request has not expired'
        USING ERRCODE = '22023';
    END IF;
    RETURN NEW;
  END IF;

  IF OLD.status = 'active' AND NEW.status = 'revoked' THEN
    IF NEW.user_id = v_actor_id
      OR NEW.revoked_at IS NULL
      OR NEW.revoke_reason_code IS NULL
      OR NEW.revoke_reason IS NULL
      OR NEW.revoked_by IS DISTINCT FROM v_actor_id
      OR NEW.revoked_at IS DISTINCT FROM pg_catalog.statement_timestamp()
      OR NEW.updated_at IS DISTINCT FROM pg_catalog.statement_timestamp()
      OR NEW.approved_by IS DISTINCT FROM OLD.approved_by
      OR NEW.approved_at IS DISTINCT FROM OLD.approved_at
      OR NEW.approval_reason_code IS DISTINCT FROM OLD.approval_reason_code
      OR NEW.approval_reason IS DISTINCT FROM OLD.approval_reason
    THEN
      RAISE EXCEPTION 'Platform access cannot be self-revoked'
        USING ERRCODE = '42501';
    END IF;

    IF OLD.access_kind = 'developer' AND NOT EXISTS (
      SELECT 1
      FROM public.platform_access_grant AS other_grant
      JOIN public.app_user AS other_developer
        ON other_developer.id = other_grant.user_id
       AND other_developer.status = 'active'
      WHERE other_grant.access_kind = 'developer'
        AND other_grant.status = 'active'
        AND other_grant.id <> OLD.id
    ) THEN
      RAISE EXCEPTION 'The last active Developer cannot be revoked'
        USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
  END IF;

  RAISE EXCEPTION 'Unsupported platform access transition'
    USING ERRCODE = '42501';
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


PLATFORM_GRANT_PROJECTION_SQL = """
CREATE FUNCTION public.trg_project_platform_access_grant()
RETURNS TRIGGER AS $$
DECLARE
  v_actor_id UUID;
BEGIN
  v_actor_id := public.current_app_user_id();

  UPDATE public.app_user AS account
  SET
    is_developer = EXISTS (
      SELECT 1
      FROM public.platform_access_grant AS active_grant
      WHERE active_grant.user_id = NEW.user_id
        AND active_grant.access_kind = 'developer'
        AND active_grant.status = 'active'
    ),
    is_administrator = EXISTS (
      SELECT 1
      FROM public.platform_access_grant AS active_grant
      WHERE active_grant.user_id = NEW.user_id
        AND active_grant.access_kind = 'administrator'
        AND active_grant.status = 'active'
    ),
    updated_at = pg_catalog.statement_timestamp()
  WHERE account.id = NEW.user_id;

  IF NEW.status = 'active'
    OR (TG_OP = 'UPDATE' AND OLD.status = 'active' AND NEW.status <> 'active')
  THEN
    UPDATE public.session AS auth_session
    SET
      revoked_at = COALESCE(
        auth_session.revoked_at,
        pg_catalog.statement_timestamp()
      ),
      revoked_reason = COALESCE(
        auth_session.revoked_reason,
        'platform_access_changed'
      ),
      last_used_at = pg_catalog.statement_timestamp()
    WHERE auth_session.user_id = NEW.user_id
      AND auth_session.revoked_at IS NULL;

    UPDATE public.support_access_session AS support_session
    SET
      revoked_at = COALESCE(
        support_session.revoked_at,
        pg_catalog.statement_timestamp()
      ),
      revoked_by_user_id = COALESCE(
        support_session.revoked_by_user_id,
        v_actor_id
      ),
      updated_at = pg_catalog.statement_timestamp(),
      updated_by = COALESCE(support_session.updated_by, v_actor_id)
    WHERE support_session.actor_user_id = NEW.user_id
      AND support_session.revoked_at IS NULL;
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


APP_USER_PLATFORM_FLAG_GUARD_SQL = """
CREATE FUNCTION public.trg_guard_app_user_platform_flags()
RETURNS TRIGGER AS $$
DECLARE
  v_expected_developer BOOLEAN;
  v_expected_administrator BOOLEAN;
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF (NEW.is_developer OR NEW.is_administrator)
      AND NOT (
        NEW.is_developer
        AND NOT NEW.is_administrator
        AND NEW.status = 'active'
        AND NEW.home_tenant_id IS NULL
        AND public.current_app_user_id() IS NULL
        AND public.is_support_session()
        AND NOT EXISTS (
          SELECT 1
          FROM public.platform_access_grant
          WHERE access_kind = 'developer'
        )
      )
    THEN
      RAISE EXCEPTION 'Platform flags require the trusted bootstrap path'
        USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
  END IF;

  IF OLD.is_developer IS DISTINCT FROM NEW.is_developer
    OR OLD.is_administrator IS DISTINCT FROM NEW.is_administrator
  THEN
    SELECT
      EXISTS (
        SELECT 1
        FROM public.platform_access_grant AS developer_grant
        WHERE developer_grant.user_id = NEW.id
          AND developer_grant.access_kind = 'developer'
          AND developer_grant.status = 'active'
      ),
      EXISTS (
        SELECT 1
        FROM public.platform_access_grant AS administrator_grant
        WHERE administrator_grant.user_id = NEW.id
          AND administrator_grant.access_kind = 'administrator'
          AND administrator_grant.status = 'active'
      )
    INTO v_expected_developer, v_expected_administrator;

    IF NEW.is_developer IS DISTINCT FROM v_expected_developer
      OR NEW.is_administrator IS DISTINCT FROM v_expected_administrator
    THEN
      RAISE EXCEPTION 'Platform flags are a read-only grant projection'
        USING ERRCODE = '42501';
    END IF;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


APP_USER_PLATFORM_BOOTSTRAP_SQL = """
CREATE FUNCTION public.trg_capture_bootstrap_platform_access()
RETURNS TRIGGER AS $$
BEGIN
  IF NOT NEW.is_developer THEN
    RETURN NEW;
  END IF;

  INSERT INTO public.platform_access_grant (
    user_id,
    access_kind,
    status,
    requested_by,
    request_reason_code,
    request_reason,
    requested_at,
    requires_approval,
    created_at,
    updated_at
  ) VALUES (
    NEW.id,
    'developer',
    'active',
    NULL,
    'bootstrap',
    'Trusted support bootstrap account creation',
    pg_catalog.statement_timestamp(),
    false,
    pg_catalog.statement_timestamp(),
    pg_catalog.statement_timestamp()
  );

  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


APP_USER_PLATFORM_STATUS_GUARD_SQL = """
CREATE FUNCTION public.trg_guard_platform_account_status()
RETURNS TRIGGER AS $$
DECLARE
  v_actor_id UUID;
BEGIN
  PERFORM pg_catalog.pg_advisory_xact_lock(7148, 1);

  IF OLD.status = 'active'
    AND NEW.status <> 'active'
    AND EXISTS (
      SELECT 1
      FROM public.platform_access_grant AS current_grant
      WHERE current_grant.user_id = OLD.id
        AND current_grant.status IN ('pending', 'active')
    )
  THEN
    v_actor_id := public.current_app_user_id();
    IF v_actor_id IS NULL
      OR v_actor_id = OLD.id
      OR NOT public.is_support_session()
      OR public.current_tenant_id() IS NOT NULL
      OR NOT EXISTS (
        SELECT 1
        FROM public.platform_access_grant AS actor_grant
        JOIN public.app_user AS actor
          ON actor.id = actor_grant.user_id
         AND actor.status = 'active'
        WHERE actor_grant.user_id = v_actor_id
          AND actor_grant.access_kind = 'developer'
          AND actor_grant.status = 'active'
      )
    THEN
      RAISE EXCEPTION 'Active independent Developer required to disable platform account'
        USING ERRCODE = '42501';
    END IF;
  END IF;

  IF OLD.status = 'active'
    AND NEW.status <> 'active'
    AND EXISTS (
      SELECT 1
      FROM public.platform_access_grant AS developer_grant
      WHERE developer_grant.user_id = OLD.id
        AND developer_grant.access_kind = 'developer'
        AND developer_grant.status = 'active'
    )
    AND NOT EXISTS (
      SELECT 1
      FROM public.platform_access_grant AS other_grant
      JOIN public.app_user AS other_account
        ON other_account.id = other_grant.user_id
       AND other_account.status = 'active'
      WHERE other_grant.access_kind = 'developer'
        AND other_grant.status = 'active'
        AND other_grant.user_id <> OLD.id
    )
  THEN
    RAISE EXCEPTION 'The last active Developer account cannot be disabled'
      USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


APP_USER_PLATFORM_STATUS_REVOKE_SQL = """
CREATE FUNCTION public.trg_revoke_platform_access_for_account()
RETURNS TRIGGER AS $$
BEGIN
  IF OLD.status IS NOT DISTINCT FROM NEW.status
    OR OLD.status <> 'active'
    OR NEW.status = 'active'
  THEN
    RETURN NEW;
  END IF;

  UPDATE public.platform_access_grant
  SET
    status = 'revoked',
    revoked_by = public.current_app_user_id(),
    revoked_at = pg_catalog.statement_timestamp(),
    revoke_reason_code = 'account_disabled',
    revoke_reason = 'Platform account disabled',
    updated_at = pg_catalog.statement_timestamp(),
    version = version + 1
  WHERE user_id = NEW.id
    AND status IN ('pending', 'active');

  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


APP_USER_PLATFORM_TENANT_SCOPE_GUARD_SQL = """
CREATE FUNCTION public.trg_guard_platform_account_tenant_scope()
RETURNS TRIGGER AS $$
BEGIN
  IF NEW.home_tenant_id IS NULL THEN
    RETURN NEW;
  END IF;

  PERFORM pg_catalog.pg_advisory_xact_lock(7148, 1);
  IF EXISTS (
    SELECT 1
    FROM public.platform_access_grant AS platform_grant
    WHERE platform_grant.user_id = NEW.id
      AND platform_grant.status IN ('pending', 'active')
  ) THEN
    RAISE EXCEPTION 'Platform account cannot be attached to a tenant'
      USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


PLATFORM_MEMBERSHIP_SCOPE_GUARD_SQL = """
CREATE FUNCTION public.trg_guard_platform_membership_scope()
RETURNS TRIGGER AS $$
BEGIN
  IF NEW.status NOT IN ('pending', 'active', 'suspended') THEN
    RETURN NEW;
  END IF;

  PERFORM pg_catalog.pg_advisory_xact_lock(7148, 1);
  PERFORM 1
  FROM public.app_user AS account
  WHERE account.id = NEW.user_id
  FOR UPDATE;

  IF EXISTS (
    SELECT 1
    FROM public.platform_access_grant AS platform_grant
    WHERE platform_grant.user_id = NEW.user_id
      AND platform_grant.status IN ('pending', 'active')
  ) THEN
    RAISE EXCEPTION 'Platform account cannot receive tenant membership'
      USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


PLATFORM_GRANT_AUDIT_SQL = """
CREATE FUNCTION public.trg_audit_platform_access_grant()
RETURNS TRIGGER AS $$
DECLARE
  v_event TEXT;
  v_request_id TEXT;
BEGIN
  IF TG_OP = 'INSERT' THEN
    v_event := CASE NEW.status
      WHEN 'active' THEN 'platform_access_activated'
      ELSE 'platform_access_requested'
    END;
  ELSIF OLD.status IS DISTINCT FROM NEW.status THEN
    v_event := CASE NEW.status
      WHEN 'active' THEN 'platform_access_approved'
      WHEN 'expired' THEN 'platform_access_expired'
      ELSE 'platform_access_revoked'
    END;
  ELSE
    RETURN NEW;
  END IF;

  v_request_id := NULLIF(
    pg_catalog.current_setting('app.request_id', true),
    ''
  );

  INSERT INTO public.audit_log (
    tenant_id,
    user_id,
    action,
    table_name,
    record_id,
    old_values,
    new_values,
    changed_fields,
    metadata,
    created_at
  ) VALUES (
    NULL,
    public.current_app_user_id(),
    CASE WHEN TG_OP = 'INSERT' THEN 'INSERT' ELSE 'UPDATE' END,
    'platform_access_grant',
    NEW.id,
    CASE
      WHEN TG_OP = 'UPDATE'
      THEN pg_catalog.jsonb_build_object('status', OLD.status)
      ELSE NULL
    END,
    pg_catalog.jsonb_build_object('status', NEW.status),
    CASE
      WHEN TG_OP = 'UPDATE'
      THEN pg_catalog.jsonb_build_object('status', NEW.status)
      ELSE NULL
    END,
    pg_catalog.jsonb_strip_nulls(
      pg_catalog.jsonb_build_object(
        'event', v_event,
        'target_user_id', NEW.user_id,
        'access_kind', NEW.access_kind,
        'requested_by', NEW.requested_by,
        'request_reason_code', NEW.request_reason_code,
        'approved_by', NEW.approved_by,
        'approval_reason_code', NEW.approval_reason_code,
        'revoked_by', NEW.revoked_by,
        'revoke_reason_code', NEW.revoke_reason_code,
        'requires_approval', NEW.requires_approval,
        'request_id', v_request_id,
        'version', NEW.version
      )
    ),
    pg_catalog.statement_timestamp()
  );
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


TRIGGER_FUNCTIONS = (
    "public.trg_guard_platform_access_grant()",
    "public.trg_project_platform_access_grant()",
    "public.trg_guard_app_user_platform_flags()",
    "public.trg_capture_bootstrap_platform_access()",
    "public.trg_guard_platform_account_status()",
    "public.trg_revoke_platform_access_for_account()",
    "public.trg_guard_platform_account_tenant_scope()",
    "public.trg_guard_platform_membership_scope()",
    "public.trg_audit_platform_access_grant()",
)


def _secure_trigger_function(signature: str) -> None:
    op.execute(f"ALTER FUNCTION {signature} OWNER TO aurum_support")


def _revoke_trigger_function_execution(signature: str) -> None:
    op.execute(
        f"REVOKE ALL PRIVILEGES ON FUNCTION {signature} "
        "FROM PUBLIC, aurum_app, aurum_support"
    )


def upgrade() -> None:
    op.execute(LEGACY_PLATFORM_ACCESS_PREFLIGHT_SQL)

    op.execute("""
        CREATE TABLE public.platform_access_grant (
          id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          user_id              UUID NOT NULL
                               REFERENCES public.app_user(id) ON DELETE RESTRICT,
          access_kind          TEXT NOT NULL
                               CHECK (access_kind IN ('developer', 'administrator')),
          status               TEXT NOT NULL
                               CHECK (status IN ('pending', 'active', 'revoked', 'expired')),
          requested_by         UUID
                               REFERENCES public.app_user(id) ON DELETE SET NULL,
          request_reason_code  TEXT NOT NULL
                               CHECK (
                                 request_reason_code IN (
                                   'platform_staff_onboarding',
                                   'responsibility_change',
                                   'security_incident',
                                   'access_review',
                                   'other',
                                   'bootstrap',
                                   'migration'
                                 )
                               ),
          request_reason       TEXT NOT NULL
                               CHECK (
                                 char_length(btrim(request_reason)) BETWEEN 10 AND 500
                               ),
          requested_at         TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          requires_approval    BOOLEAN NOT NULL DEFAULT true,
          approval_expires_at  TIMESTAMPTZ,
          approved_by          UUID
                               REFERENCES public.app_user(id) ON DELETE SET NULL,
          approved_at          TIMESTAMPTZ,
          approval_reason_code TEXT
                               CHECK (
                                 approval_reason_code IS NULL
                                 OR approval_reason_code IN (
                                   'platform_staff_onboarding',
                                   'responsibility_change',
                                   'security_incident',
                                   'access_review',
                                   'other'
                                 )
                               ),
          approval_reason      TEXT
                               CHECK (
                                 approval_reason IS NULL
                                 OR char_length(btrim(approval_reason)) BETWEEN 10 AND 500
                               ),
          revoked_by           UUID
                               REFERENCES public.app_user(id) ON DELETE SET NULL,
          revoked_at           TIMESTAMPTZ,
          revoke_reason_code   TEXT
                               CHECK (
                                 revoke_reason_code IS NULL
                                 OR revoke_reason_code IN (
                                   'platform_staff_onboarding',
                                   'responsibility_change',
                                   'security_incident',
                                   'access_review',
                                   'other',
                                   'approval_window_expired',
                                   'account_disabled'
                                 )
                               ),
          revoke_reason        TEXT
                               CHECK (
                                 revoke_reason IS NULL
                                 OR char_length(btrim(revoke_reason)) BETWEEN 10 AND 500
                               ),
          version              INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
          created_at           TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          updated_at           TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT ck_platform_access_grant_state CHECK (
            (
              status = 'pending'
              AND requires_approval
              AND approval_expires_at IS NOT NULL
              AND approved_by IS NULL
              AND approved_at IS NULL
              AND approval_reason_code IS NULL
              AND approval_reason IS NULL
              AND revoked_by IS NULL
              AND revoked_at IS NULL
              AND revoke_reason_code IS NULL
              AND revoke_reason IS NULL
            )
            OR (
              status = 'active'
              AND (
                (
                  requires_approval
                  AND approval_expires_at IS NOT NULL
                  AND approved_by IS NOT NULL
                  AND approved_at IS NOT NULL
                  AND approval_reason_code IS NOT NULL
                  AND approval_reason IS NOT NULL
                )
                OR (
                  NOT requires_approval
                  AND approval_expires_at IS NULL
                  AND approved_by IS NULL
                  AND approved_at IS NULL
                  AND approval_reason_code IS NULL
                  AND approval_reason IS NULL
                )
              )
              AND revoked_by IS NULL
              AND revoked_at IS NULL
              AND revoke_reason_code IS NULL
              AND revoke_reason IS NULL
            )
            OR (
              status IN ('revoked', 'expired')
              AND revoked_at IS NOT NULL
              AND revoke_reason_code IS NOT NULL
              AND revoke_reason IS NOT NULL
            )
          )
        )
        """)
    op.execute(
        "CREATE UNIQUE INDEX uq_platform_access_active_kind "
        "ON public.platform_access_grant (user_id, access_kind) "
        "WHERE status = 'active'"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_platform_access_pending_target "
        "ON public.platform_access_grant (user_id) "
        "WHERE status = 'pending'"
    )
    op.execute(
        "CREATE INDEX ix_platform_access_target_history "
        "ON public.platform_access_grant (user_id, requested_at DESC)"
    )
    op.execute(
        "CREATE INDEX ix_platform_access_pending_expiry "
        "ON public.platform_access_grant (approval_expires_at) "
        "WHERE status = 'pending'"
    )

    op.execute("""
        INSERT INTO public.platform_access_grant (
          user_id,
          access_kind,
          status,
          requested_by,
          request_reason_code,
          request_reason,
          requested_at,
          requires_approval,
          created_at,
          updated_at
        )
        SELECT
          account.id,
          kinds.access_kind,
          'active',
          NULL,
          'migration',
          'Legacy platform flag migration backfill',
          COALESCE(account.activated_at, account.created_at),
          false,
          account.created_at,
          account.updated_at
        FROM public.app_user AS account
        CROSS JOIN LATERAL (
          VALUES
            ('developer'::TEXT, account.is_developer),
            ('administrator'::TEXT, account.is_administrator)
        ) AS kinds(access_kind, enabled)
        WHERE kinds.enabled
          AND account.status = 'active'
          AND NOT (account.is_developer AND account.is_administrator)
          AND account.home_tenant_id IS NULL
          AND NOT EXISTS (
            SELECT 1
            FROM public.tenant_membership AS membership
            WHERE membership.user_id = account.id
              AND membership.status IN ('pending', 'active', 'suspended')
          )
          AND NOT EXISTS (
            SELECT 1
            FROM public.tenant_ownership AS ownership
            JOIN public.tenant_membership AS membership
              ON membership.id = ownership.membership_id
            WHERE membership.user_id = account.id
              AND ownership.is_active
          )
          AND NOT EXISTS (
            SELECT 1
            FROM public.user_assignment AS assignment
            WHERE assignment.user_id = account.id
              AND assignment.is_active
          )
        """)

    for statement, signature in (
        (PLATFORM_GRANT_GUARD_SQL, TRIGGER_FUNCTIONS[0]),
        (PLATFORM_GRANT_PROJECTION_SQL, TRIGGER_FUNCTIONS[1]),
        (APP_USER_PLATFORM_FLAG_GUARD_SQL, TRIGGER_FUNCTIONS[2]),
        (APP_USER_PLATFORM_BOOTSTRAP_SQL, TRIGGER_FUNCTIONS[3]),
        (APP_USER_PLATFORM_STATUS_GUARD_SQL, TRIGGER_FUNCTIONS[4]),
        (APP_USER_PLATFORM_STATUS_REVOKE_SQL, TRIGGER_FUNCTIONS[5]),
        (APP_USER_PLATFORM_TENANT_SCOPE_GUARD_SQL, TRIGGER_FUNCTIONS[6]),
        (PLATFORM_MEMBERSHIP_SCOPE_GUARD_SQL, TRIGGER_FUNCTIONS[7]),
        (PLATFORM_GRANT_AUDIT_SQL, TRIGGER_FUNCTIONS[8]),
    ):
        op.execute(statement)
        _secure_trigger_function(signature)

    op.execute("""
        CREATE TRIGGER trg_10_guard_platform_access_grant
        BEFORE INSERT OR DELETE OR UPDATE ON public.platform_access_grant
        FOR EACH ROW EXECUTE FUNCTION public.trg_guard_platform_access_grant()
        """)
    op.execute("""
        CREATE TRIGGER trg_20_project_platform_access_grant
        AFTER INSERT OR UPDATE OF status ON public.platform_access_grant
        FOR EACH ROW EXECUTE FUNCTION public.trg_project_platform_access_grant()
        """)
    op.execute("""
        CREATE TRIGGER trg_30_audit_platform_access_grant
        AFTER INSERT OR UPDATE OF status ON public.platform_access_grant
        FOR EACH ROW EXECUTE FUNCTION public.trg_audit_platform_access_grant()
        """)
    op.execute("""
        CREATE TRIGGER trg_10_guard_app_user_platform_insert
        BEFORE INSERT ON public.app_user
        FOR EACH ROW EXECUTE FUNCTION public.trg_guard_app_user_platform_flags()
        """)
    op.execute("""
        CREATE TRIGGER trg_10_guard_app_user_platform_update
        BEFORE UPDATE OF is_developer, is_administrator ON public.app_user
        FOR EACH ROW EXECUTE FUNCTION public.trg_guard_app_user_platform_flags()
        """)
    op.execute("""
        CREATE TRIGGER trg_20_capture_bootstrap_platform_access
        AFTER INSERT ON public.app_user
        FOR EACH ROW EXECUTE FUNCTION public.trg_capture_bootstrap_platform_access()
        """)
    op.execute("""
        CREATE TRIGGER trg_10_guard_platform_account_status
        BEFORE UPDATE OF status ON public.app_user
        FOR EACH ROW EXECUTE FUNCTION public.trg_guard_platform_account_status()
        """)
    op.execute("""
        CREATE TRIGGER trg_20_revoke_platform_account_status
        AFTER UPDATE OF status ON public.app_user
        FOR EACH ROW EXECUTE FUNCTION public.trg_revoke_platform_access_for_account()
        """)
    op.execute("""
        CREATE TRIGGER trg_10_guard_platform_account_tenant_scope
        BEFORE UPDATE OF home_tenant_id ON public.app_user
        FOR EACH ROW EXECUTE FUNCTION public.trg_guard_platform_account_tenant_scope()
        """)
    op.execute(
        "GRANT TRIGGER ON TABLE public.tenant_membership TO aurum_support"
    )
    op.execute("""
        CREATE TRIGGER trg_05_guard_platform_membership_scope
        BEFORE INSERT OR UPDATE OF user_id, status ON public.tenant_membership
        FOR EACH ROW EXECUTE FUNCTION public.trg_guard_platform_membership_scope()
        """)
    op.execute(
        "REVOKE TRIGGER ON TABLE public.tenant_membership FROM aurum_support"
    )

    op.execute("""
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM public.app_user AS account
            WHERE account.is_developer IS DISTINCT FROM EXISTS (
              SELECT 1
              FROM public.platform_access_grant AS active_grant
              WHERE active_grant.user_id = account.id
                AND active_grant.access_kind = 'developer'
                AND active_grant.status = 'active'
            )
            OR account.is_administrator IS DISTINCT FROM EXISTS (
              SELECT 1
              FROM public.platform_access_grant AS active_grant
              WHERE active_grant.user_id = account.id
                AND active_grant.access_kind = 'administrator'
                AND active_grant.status = 'active'
            )
          ) THEN
            RAISE EXCEPTION 'Platform access projection backfill is inconsistent';
          END IF;
        END;
        $$;
        """)

    for signature in TRIGGER_FUNCTIONS:
        _revoke_trigger_function_execution(signature)

    op.execute(
        "REVOKE ALL PRIVILEGES ON TABLE public.platform_access_grant "
        "FROM PUBLIC, aurum_app, aurum_support"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE "
        "ON TABLE public.platform_access_grant TO aurum_support"
    )


def downgrade() -> None:
    for trigger, table in (
        ("trg_05_guard_platform_membership_scope", "tenant_membership"),
        ("trg_10_guard_platform_account_tenant_scope", "app_user"),
        ("trg_20_revoke_platform_account_status", "app_user"),
        ("trg_10_guard_platform_account_status", "app_user"),
        ("trg_20_capture_bootstrap_platform_access", "app_user"),
        ("trg_10_guard_app_user_platform_update", "app_user"),
        ("trg_10_guard_app_user_platform_insert", "app_user"),
        ("trg_30_audit_platform_access_grant", "platform_access_grant"),
        ("trg_20_project_platform_access_grant", "platform_access_grant"),
        ("trg_10_guard_platform_access_grant", "platform_access_grant"),
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON public.{table}")

    for signature in reversed(TRIGGER_FUNCTIONS):
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")

    op.execute("DROP TABLE public.platform_access_grant")
