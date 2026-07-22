"""security: notify tenant users about a login from a new device

Revision ID: 0065
Revises: 0064
Create Date: 2026-07-23

The browser keeps a random device identifier in an HttpOnly cookie.  Only its
SHA-256 digest is stored with the authentication session.  A narrow
SECURITY DEFINER function proves ownership of the freshly-created session with
its refresh-token digest, serializes device registration per user and creates a
mandatory in-app warning when the device has not been seen before.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0065"
down_revision: str | Sequence[str] | None = "0064"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


REGISTER_AUTH_SESSION_DEVICE_SQL = """
CREATE FUNCTION public.register_auth_session_device(
  p_session_id UUID,
  p_refresh_token_hash TEXT,
  p_device_id_hash TEXT
) RETURNS TEXT AS $$
DECLARE
  v_current_device_hash TEXT;
  v_has_known_device BOOLEAN;
  v_has_matching_device BOOLEAN;
  v_tenant_id UUID;
  v_user_id UUID;
BEGIN
  IF p_session_id IS NULL
    OR p_refresh_token_hash !~ '^[0-9a-f]{64}$'
    OR p_device_id_hash !~ '^[0-9a-f]{64}$'
  THEN
    RAISE EXCEPTION 'Invalid authentication device proof'
      USING ERRCODE = '22023';
  END IF;

  SELECT auth_session.user_id, auth_session.device_id_hash
  INTO v_user_id, v_current_device_hash
  FROM public.session AS auth_session
  WHERE auth_session.id = p_session_id
    AND auth_session.refresh_token_hash = p_refresh_token_hash
    AND auth_session.revoked_at IS NULL
    AND auth_session.expires_at > pg_catalog.statement_timestamp()
    AND auth_session.created_at
      >= pg_catalog.statement_timestamp() - INTERVAL '5 minutes'
  FOR UPDATE;

  IF NOT FOUND THEN
    RETURN 'unavailable';
  END IF;

  IF v_current_device_hash IS NOT NULL THEN
    IF v_current_device_hash = p_device_id_hash THEN
      RETURN 'known_device';
    END IF;
    RETURN 'unavailable';
  END IF;

  SELECT app_user.home_tenant_id
  INTO v_tenant_id
  FROM public.app_user AS app_user
  WHERE app_user.id = v_user_id
    AND app_user.status IN ('invited', 'active')
  FOR UPDATE;

  IF NOT FOUND THEN
    RETURN 'unavailable';
  END IF;

  SELECT
    EXISTS (
      SELECT 1
      FROM public.session AS previous_session
      WHERE previous_session.user_id = v_user_id
        AND previous_session.id <> p_session_id
        AND previous_session.device_id_hash IS NOT NULL
    ),
    EXISTS (
      SELECT 1
      FROM public.session AS previous_session
      WHERE previous_session.user_id = v_user_id
        AND previous_session.id <> p_session_id
        AND previous_session.device_id_hash = p_device_id_hash
    )
  INTO v_has_known_device, v_has_matching_device;

  UPDATE public.session AS auth_session
  SET device_id_hash = p_device_id_hash
  WHERE auth_session.id = p_session_id
    AND auth_session.device_id_hash IS NULL;

  IF v_has_matching_device THEN
    RETURN 'known_device';
  END IF;

  IF NOT v_has_known_device THEN
    RETURN 'baseline';
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
    v_tenant_id,
    v_user_id,
    'INSERT',
    'session',
    p_session_id,
    public.audit_redact_jsonb(
      pg_catalog.jsonb_build_object('event', 'new_device_login')
    ),
    pg_catalog.statement_timestamp()
  );

  IF v_tenant_id IS NOT NULL AND EXISTS (
    SELECT 1
    FROM public.tenant_membership AS membership
    WHERE membership.tenant_id = v_tenant_id
      AND membership.user_id = v_user_id
      AND membership.status = 'active'
  ) THEN
    INSERT INTO public.notification (
      tenant_id,
      user_id,
      event_type,
      title,
      body,
      data,
      severity,
      created_at
    ) VALUES (
      v_tenant_id,
      v_user_id,
      'security.new_device_login',
      'Вход с нового устройства',
      'Выполнен вход из нового браузера или приложения. Если это были не вы, '
        'откройте раздел «Безопасность» и завершите другие сеансы.',
      pg_catalog.jsonb_build_object(
        'reason', 'new_device',
        'action', 'review_sessions'
      ),
      'warning',
      pg_catalog.statement_timestamp()
    );
  END IF;

  RETURN 'new_device';
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


COPY_SESSION_SECURITY_CONTEXT_SQL = """
CREATE OR REPLACE FUNCTION public.trg_copy_session_mfa_verification()
RETURNS TRIGGER AS $$
BEGIN
  IF NEW.rotated_from_session_id IS NOT NULL THEN
    SELECT parent.mfa_verified_at, parent.device_id_hash
    INTO NEW.mfa_verified_at, NEW.device_id_hash
    FROM public.session AS parent
    WHERE parent.id = NEW.rotated_from_session_id
      AND parent.user_id = NEW.user_id;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


RESTORE_SESSION_MFA_TRIGGER_SQL = """
CREATE OR REPLACE FUNCTION public.trg_copy_session_mfa_verification()
RETURNS TRIGGER AS $$
BEGIN
  IF NEW.rotated_from_session_id IS NOT NULL THEN
    SELECT parent.mfa_verified_at
    INTO NEW.mfa_verified_at
    FROM public.session AS parent
    WHERE parent.id = NEW.rotated_from_session_id
      AND parent.user_id = NEW.user_id;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


FUNCTION_SIGNATURE = "public.register_auth_session_device(UUID, TEXT, TEXT)"


def upgrade() -> None:
    op.execute("ALTER TABLE public.session ADD COLUMN device_id_hash TEXT")
    op.execute("""
        ALTER TABLE public.session
        ADD CONSTRAINT ck_session_device_id_hash
        CHECK (
          device_id_hash IS NULL
          OR device_id_hash ~ '^[0-9a-f]{64}$'
        )
        """)
    op.execute(
        "CREATE INDEX ix_session_user_device "
        "ON public.session (user_id, device_id_hash) "
        "WHERE device_id_hash IS NOT NULL"
    )
    op.execute(COPY_SESSION_SECURITY_CONTEXT_SQL)
    op.execute("""
        UPDATE public.notification_subscription
        SET
          channels = CASE
            WHEN channels @> '["in_app"]'::JSONB THEN channels
            ELSE channels || '["in_app"]'::JSONB
          END,
          is_enabled = true,
          updated_at = pg_catalog.statement_timestamp()
        WHERE event_type = 'security.new_device_login'
        """)
    op.execute("""
        ALTER TABLE public.notification_subscription
        ADD CONSTRAINT ck_notification_subscription_mandatory_security
        CHECK (
          event_type <> 'security.new_device_login'
          OR (
            is_enabled
            AND pg_catalog.jsonb_typeof(channels) = 'array'
            AND channels @> '["in_app"]'::JSONB
          )
        )
        """)
    op.execute(REGISTER_AUTH_SESSION_DEVICE_SQL)
    op.execute(f"ALTER FUNCTION {FUNCTION_SIGNATURE} OWNER TO aurum_support")
    op.execute(f"REVOKE ALL PRIVILEGES ON FUNCTION {FUNCTION_SIGNATURE} FROM PUBLIC, aurum_app")
    op.execute(f"GRANT EXECUTE ON FUNCTION {FUNCTION_SIGNATURE} TO aurum_support, aurum_app")


def downgrade() -> None:
    op.execute(f"DROP FUNCTION IF EXISTS {FUNCTION_SIGNATURE}")
    op.execute("""
        ALTER TABLE public.notification_subscription
        DROP CONSTRAINT ck_notification_subscription_mandatory_security
        """)
    op.execute(RESTORE_SESSION_MFA_TRIGGER_SQL)
    op.execute("DROP INDEX IF EXISTS public.ix_session_user_device")
    op.execute("ALTER TABLE public.session DROP CONSTRAINT ck_session_device_id_hash")
    op.execute("ALTER TABLE public.session DROP COLUMN device_id_hash")
