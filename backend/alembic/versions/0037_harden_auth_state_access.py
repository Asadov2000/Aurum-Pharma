"""security: harden authentication state access

Revision ID: 0037
Revises: 0036
Create Date: 2026-07-14

The runtime role loses direct access to email codes, login attempts, and
refresh sessions. Narrow security-definer functions keep code consumption and
refresh rotation atomic and bind new sessions to an existing credential.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0037"
down_revision: str | Sequence[str] | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


APP_FUNCTIONS = (
    "public.issue_auth_email_code(TEXT, TEXT, TEXT, TEXT, TEXT)",
    "public.find_auth_email_code_challenge(TEXT)",
    "public.auth_email_code_matches(UUID, TEXT, TEXT)",
    "public.consume_auth_email_code(UUID, TEXT, TEXT)",
    "public.lookup_login_user_by_email(TEXT, UUID, TEXT)",
    "public.create_auth_session_from_email_code("
    "UUID, TEXT, TEXT, TEXT, TEXT, TEXT, TIMESTAMP WITH TIME ZONE)",
    "public.enforce_auth_login_guard(TEXT, TEXT)",
    "public.record_auth_login_attempt(TEXT, UUID, TEXT, TEXT, TEXT, TEXT)",
    "public.lookup_auth_session_by_hash(TEXT)",
    "public.rotate_auth_session(TEXT, TEXT, TEXT, TEXT, TIMESTAMP WITH TIME ZONE)",
    "public.revoke_auth_session_by_hash(TEXT, TEXT)",
    "public.lookup_auth_user_by_id(UUID, UUID)",
    "public.touch_auth_user_last_login(UUID, UUID)",
)

REMOVED_0036_FUNCTIONS = (
    "public.lookup_auth_user_by_email(TEXT)",
    "public.touch_auth_user_last_login(UUID, TIMESTAMP WITH TIME ZONE)",
)


ISSUE_AUTH_EMAIL_CODE_SQL = """
CREATE FUNCTION public.issue_auth_email_code(
  p_email TEXT,
  p_code_hash TEXT,
  p_code_salt TEXT,
  p_ip_address TEXT,
  p_user_agent TEXT
) RETURNS TEXT AS $$
DECLARE
  v_email TEXT;
  v_ip INET;
  v_now TIMESTAMPTZ;
BEGIN
  v_email := pg_catalog.lower(pg_catalog.btrim(p_email));
  v_ip := p_ip_address::INET;
  v_now := pg_catalog.now();

  IF NULLIF(v_email, '') IS NULL
    OR pg_catalog.char_length(v_email) > 320
    OR p_code_hash !~ '^[0-9a-f]{64}$'
    OR p_code_salt !~ '^[0-9a-f]{32}$'
  THEN
    RAISE EXCEPTION 'Invalid email-code payload'
      USING ERRCODE = '22023';
  END IF;

  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(v_email, 3701)
  );

  IF EXISTS (
    SELECT 1
    FROM public.email_code AS email_code
    WHERE email_code.email_lower = v_email
      AND email_code.created_at >= v_now - INTERVAL '1 minute'
  ) THEN
    INSERT INTO public.login_attempt (
      email_lower, ip_address, user_agent, outcome, metadata_json
    ) VALUES (
      v_email,
      v_ip,
      pg_catalog.left(p_user_agent, 1024),
      'blocked',
      pg_catalog.jsonb_build_object('reason', 'code_rate_limit_minute')
    );
    RETURN 'rate_limit_minute';
  END IF;

  IF (
    SELECT pg_catalog.count(*)
    FROM public.email_code AS email_code
    WHERE email_code.email_lower = v_email
      AND email_code.created_at >= v_now - INTERVAL '1 hour'
  ) >= 10 THEN
    INSERT INTO public.login_attempt (
      email_lower, ip_address, user_agent, outcome, metadata_json
    ) VALUES (
      v_email,
      v_ip,
      pg_catalog.left(p_user_agent, 1024),
      'blocked',
      pg_catalog.jsonb_build_object('reason', 'code_rate_limit_hour')
    );
    RETURN 'rate_limit_hour';
  END IF;

  INSERT INTO public.email_code (
    email_lower,
    code_hash,
    code_salt,
    purpose,
    ip_address,
    expires_at
  ) VALUES (
    v_email,
    p_code_hash,
    p_code_salt,
    'login',
    v_ip,
    v_now + INTERVAL '10 minutes'
  );

  INSERT INTO public.login_attempt (
    email_lower, ip_address, user_agent, outcome
  ) VALUES (
    v_email,
    v_ip,
    pg_catalog.left(p_user_agent, 1024),
    'code_requested'
  );

  RETURN 'created';
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


FIND_AUTH_EMAIL_CODE_CHALLENGE_SQL = """
CREATE FUNCTION public.find_auth_email_code_challenge(
  p_email TEXT
) RETURNS TABLE(id UUID, code_salt TEXT) AS $$
  SELECT email_code.id, email_code.code_salt
  FROM public.email_code AS email_code
  WHERE email_code.email_lower = pg_catalog.lower(pg_catalog.btrim(p_email))
    AND email_code.purpose = 'login'
    AND email_code.used_at IS NULL
    AND email_code.expires_at > pg_catalog.now()
  ORDER BY email_code.created_at DESC
  LIMIT 1
$$ LANGUAGE SQL
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


AUTH_EMAIL_CODE_MATCHES_SQL = """
CREATE FUNCTION public.auth_email_code_matches(
  p_code_id UUID,
  p_email TEXT,
  p_candidate_hash TEXT
) RETURNS BOOLEAN AS $$
  SELECT EXISTS (
    SELECT 1
    FROM public.email_code AS email_code
    WHERE email_code.id = p_code_id
      AND email_code.email_lower = pg_catalog.lower(pg_catalog.btrim(p_email))
      AND email_code.purpose = 'login'
      AND email_code.code_hash = p_candidate_hash
      AND email_code.used_at IS NULL
      AND email_code.expires_at > pg_catalog.now()
  )
$$ LANGUAGE SQL
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


CONSUME_AUTH_EMAIL_CODE_SQL = """
CREATE FUNCTION public.consume_auth_email_code(
  p_code_id UUID,
  p_email TEXT,
  p_candidate_hash TEXT
) RETURNS BOOLEAN AS $$
DECLARE
  v_updated INTEGER;
BEGIN
  UPDATE public.email_code AS email_code
  SET used_at = pg_catalog.now()
  WHERE email_code.id = p_code_id
    AND email_code.email_lower = pg_catalog.lower(pg_catalog.btrim(p_email))
    AND email_code.purpose = 'login'
    AND email_code.code_hash = p_candidate_hash
    AND email_code.used_at IS NULL
    AND email_code.expires_at > pg_catalog.now();

  GET DIAGNOSTICS v_updated = ROW_COUNT;
  RETURN v_updated = 1;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


LOOKUP_LOGIN_USER_BY_EMAIL_SQL = """
CREATE FUNCTION public.lookup_login_user_by_email(
  p_email TEXT,
  p_code_id UUID,
  p_candidate_hash TEXT
) RETURNS TABLE(
  id UUID,
  email TEXT,
  full_name TEXT,
  password_hash TEXT,
  is_developer BOOLEAN,
  is_administrator BOOLEAN,
  home_tenant_id UUID,
  status TEXT,
  last_login_at TIMESTAMPTZ,
  password_required BOOLEAN
) AS $$
  SELECT
    app_user.id,
    app_user.email,
    app_user.full_name,
    app_user.password_hash,
    app_user.is_developer,
    app_user.is_administrator,
    app_user.home_tenant_id,
    app_user.status,
    app_user.last_login_at,
    EXISTS (
      SELECT 1
      FROM public.user_assignment AS assignment
      WHERE assignment.user_id = app_user.id
        AND assignment.tenant_id = app_user.home_tenant_id
        AND assignment.is_active
        AND assignment.password_required
    ) AS password_required
  FROM public.app_user AS app_user
  WHERE app_user.email_lower = pg_catalog.lower(pg_catalog.btrim(p_email))
    AND public.auth_email_code_matches(
      p_code_id,
      p_email,
      p_candidate_hash
    )
  LIMIT 1
$$ LANGUAGE SQL
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


CREATE_AUTH_SESSION_FROM_EMAIL_CODE_SQL = """
CREATE FUNCTION public.create_auth_session_from_email_code(
  p_code_id UUID,
  p_email TEXT,
  p_candidate_hash TEXT,
  p_refresh_token_hash TEXT,
  p_user_agent TEXT,
  p_ip_address TEXT,
  p_expires_at TIMESTAMPTZ
) RETURNS UUID AS $$
DECLARE
  v_code_email TEXT;
  v_ip INET;
  v_session_id UUID;
  v_user_id UUID;
BEGIN
  IF p_refresh_token_hash !~ '^[0-9a-f]{64}$'
    OR p_expires_at IS NULL
    OR p_expires_at <= pg_catalog.now()
    OR p_expires_at > pg_catalog.now() + INTERVAL '31 days'
  THEN
    RAISE EXCEPTION 'Invalid refresh-session payload'
      USING ERRCODE = '22023';
  END IF;

  v_ip := NULLIF(pg_catalog.btrim(p_ip_address), '')::INET;

  SELECT email_code.email_lower
  INTO v_code_email
  FROM public.email_code AS email_code
  WHERE email_code.id = p_code_id
    AND email_code.email_lower = pg_catalog.lower(pg_catalog.btrim(p_email))
    AND email_code.purpose = 'login'
    AND email_code.code_hash = p_candidate_hash
    AND email_code.used_at IS NULL
    AND email_code.expires_at > pg_catalog.now()
  FOR UPDATE;

  IF v_code_email IS NULL THEN
    RETURN NULL;
  END IF;

  UPDATE public.email_code AS email_code
  SET used_at = pg_catalog.now()
  WHERE email_code.id = p_code_id;

  SELECT app_user.id
  INTO v_user_id
  FROM public.app_user AS app_user
  WHERE app_user.email_lower = v_code_email
    AND app_user.status IN ('invited', 'active');

  IF v_user_id IS NULL THEN
    RETURN NULL;
  END IF;

  INSERT INTO public.session (
    user_id,
    refresh_token_hash,
    user_agent,
    ip_address,
    expires_at
  ) VALUES (
    v_user_id,
    p_refresh_token_hash,
    pg_catalog.left(p_user_agent, 1024),
    v_ip,
    p_expires_at
  )
  RETURNING session.id INTO v_session_id;

  RETURN v_session_id;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


ENFORCE_AUTH_LOGIN_GUARD_SQL = """
CREATE FUNCTION public.enforce_auth_login_guard(
  p_email TEXT,
  p_ip_address TEXT
) RETURNS BOOLEAN AS $$
DECLARE
  v_email TEXT;
  v_ip INET;
  v_now TIMESTAMPTZ;
  v_failures BIGINT;
BEGIN
  v_email := pg_catalog.lower(pg_catalog.btrim(p_email));
  v_ip := p_ip_address::INET;
  v_now := pg_catalog.now();

  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(v_email || '|' || pg_catalog.host(v_ip), 3702)
  );

  IF EXISTS (
    SELECT 1
    FROM public.login_attempt AS attempt
    WHERE attempt.email_lower = v_email
      AND attempt.ip_address = v_ip
      AND attempt.outcome = 'blocked'
      AND attempt.created_at >= v_now - INTERVAL '15 minutes'
  ) THEN
    RETURN true;
  END IF;

  SELECT pg_catalog.count(*)
  INTO v_failures
  FROM public.login_attempt AS attempt
  WHERE attempt.email_lower = v_email
    AND attempt.ip_address = v_ip
    AND attempt.outcome IN (
      'code_failed', 'code_expired', 'password_failed', 'totp_failed'
    )
    AND attempt.created_at >= v_now - INTERVAL '15 minutes';

  IF v_failures >= 5 THEN
    INSERT INTO public.login_attempt (
      email_lower, ip_address, outcome, metadata_json
    ) VALUES (
      v_email,
      v_ip,
      'blocked',
      pg_catalog.jsonb_build_object('reason', 'too_many_failures')
    );
    RETURN true;
  END IF;

  RETURN false;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


RECORD_AUTH_LOGIN_ATTEMPT_SQL = """
CREATE FUNCTION public.record_auth_login_attempt(
  p_email TEXT,
  p_user_id UUID,
  p_ip_address TEXT,
  p_user_agent TEXT,
  p_outcome TEXT,
  p_reason TEXT
) RETURNS VOID AS $$
DECLARE
  v_email TEXT;
  v_ip INET;
BEGIN
  v_email := pg_catalog.lower(pg_catalog.btrim(p_email));
  v_ip := p_ip_address::INET;

  IF p_outcome NOT IN (
    'code_failed', 'code_expired', 'password_failed', 'totp_failed', 'success'
  ) OR (
    p_reason IS NOT NULL
    AND NOT (
      p_outcome = 'code_failed'
      AND p_reason = 'user_missing_or_inactive'
    )
  ) THEN
    RAISE EXCEPTION 'Invalid login-attempt payload'
      USING ERRCODE = '22023';
  END IF;

  IF p_user_id IS NOT NULL AND NOT EXISTS (
    SELECT 1
    FROM public.app_user AS app_user
    WHERE app_user.id = p_user_id
      AND app_user.email_lower = v_email
  ) THEN
    RAISE EXCEPTION 'Login-attempt identity mismatch'
      USING ERRCODE = '42501';
  END IF;

  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(v_email || '|' || pg_catalog.host(v_ip), 3702)
  );

  INSERT INTO public.login_attempt (
    email_lower,
    user_id,
    ip_address,
    user_agent,
    outcome,
    metadata_json
  ) VALUES (
    v_email,
    p_user_id,
    v_ip,
    pg_catalog.left(p_user_agent, 1024),
    p_outcome,
    CASE
      WHEN p_reason IS NULL THEN NULL
      ELSE pg_catalog.jsonb_build_object('reason', p_reason)
    END
  );
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


LOOKUP_AUTH_SESSION_BY_HASH_SQL = """
CREATE FUNCTION public.lookup_auth_session_by_hash(
  p_token_hash TEXT
) RETURNS TABLE(id UUID, user_id UUID, expires_at TIMESTAMPTZ) AS $$
  SELECT auth_session.id, auth_session.user_id, auth_session.expires_at
  FROM public.session AS auth_session
  WHERE auth_session.refresh_token_hash = p_token_hash
    AND auth_session.revoked_at IS NULL
    AND auth_session.expires_at > pg_catalog.now()
  LIMIT 1
$$ LANGUAGE SQL
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


ROTATE_AUTH_SESSION_SQL = """
CREATE FUNCTION public.rotate_auth_session(
  p_old_token_hash TEXT,
  p_new_token_hash TEXT,
  p_user_agent TEXT,
  p_ip_address TEXT,
  p_expires_at TIMESTAMPTZ
) RETURNS TABLE(id UUID, user_id UUID, expires_at TIMESTAMPTZ) AS $$
DECLARE
  v_old_session_id UUID;
  v_user_id UUID;
  v_new_session_id UUID;
  v_ip INET;
BEGIN
  IF p_old_token_hash !~ '^[0-9a-f]{64}$'
    OR p_new_token_hash !~ '^[0-9a-f]{64}$'
    OR p_old_token_hash = p_new_token_hash
    OR p_expires_at IS NULL
    OR p_expires_at <= pg_catalog.now()
    OR p_expires_at > pg_catalog.now() + INTERVAL '31 days'
  THEN
    RAISE EXCEPTION 'Invalid refresh-rotation payload'
      USING ERRCODE = '22023';
  END IF;

  v_ip := NULLIF(pg_catalog.btrim(p_ip_address), '')::INET;

  SELECT auth_session.id, auth_session.user_id
  INTO v_old_session_id, v_user_id
  FROM public.session AS auth_session
  WHERE auth_session.refresh_token_hash = p_old_token_hash
    AND auth_session.revoked_at IS NULL
    AND auth_session.expires_at > pg_catalog.now()
  FOR UPDATE;

  IF v_old_session_id IS NULL THEN
    RETURN;
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM public.app_user AS app_user
    WHERE app_user.id = v_user_id
      AND app_user.status IN ('invited', 'active')
  ) THEN
    UPDATE public.session AS auth_session
    SET
      revoked_at = pg_catalog.now(),
      revoked_reason = 'user_inactive'
    WHERE auth_session.id = v_old_session_id;
    RETURN;
  END IF;

  UPDATE public.session AS auth_session
  SET
    revoked_at = pg_catalog.now(),
    revoked_reason = 'rotated',
    last_used_at = pg_catalog.now()
  WHERE auth_session.id = v_old_session_id;

  INSERT INTO public.session (
    user_id,
    refresh_token_hash,
    user_agent,
    ip_address,
    expires_at
  ) VALUES (
    v_user_id,
    p_new_token_hash,
    pg_catalog.left(p_user_agent, 1024),
    v_ip,
    p_expires_at
  )
  RETURNING session.id INTO v_new_session_id;

  RETURN QUERY
  SELECT v_new_session_id, v_user_id, p_expires_at;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


REVOKE_AUTH_SESSION_BY_HASH_SQL = """
CREATE FUNCTION public.revoke_auth_session_by_hash(
  p_token_hash TEXT,
  p_reason TEXT
) RETURNS UUID AS $$
DECLARE
  v_user_id UUID;
BEGIN
  IF p_reason NOT IN ('logout', 'user_inactive') THEN
    RAISE EXCEPTION 'Invalid session-revocation reason'
      USING ERRCODE = '22023';
  END IF;

  UPDATE public.session AS auth_session
  SET
    revoked_at = pg_catalog.now(),
    revoked_reason = p_reason,
    last_used_at = pg_catalog.now()
  WHERE auth_session.refresh_token_hash = p_token_hash
    AND auth_session.revoked_at IS NULL
  RETURNING auth_session.user_id INTO v_user_id;

  RETURN v_user_id;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


LOOKUP_AUTH_USER_BY_ID_SQL = """
CREATE OR REPLACE FUNCTION public.lookup_auth_user_by_id(
  p_user_id UUID,
  p_session_id UUID
) RETURNS TABLE(
  id UUID,
  email TEXT,
  full_name TEXT,
  password_hash TEXT,
  is_developer BOOLEAN,
  is_administrator BOOLEAN,
  home_tenant_id UUID,
  status TEXT,
  last_login_at TIMESTAMPTZ,
  password_required BOOLEAN
) AS $$
BEGIN
  IF session_user <> 'aurum_support'
    AND NOT (
      (
        p_session_id IS NULL
        AND p_user_id IS NOT DISTINCT FROM public.current_app_user_id()
      )
      OR EXISTS (
        SELECT 1
        FROM public.session AS auth_session
        WHERE auth_session.id = p_session_id
          AND auth_session.user_id = p_user_id
          AND auth_session.revoked_at IS NULL
          AND auth_session.expires_at > pg_catalog.now()
      )
    )
  THEN
    RAISE EXCEPTION 'Authentication user is unavailable'
      USING ERRCODE = '42501';
  END IF;

  RETURN QUERY
  SELECT
    app_user.id,
    app_user.email,
    app_user.full_name,
    NULL::TEXT AS password_hash,
    app_user.is_developer,
    app_user.is_administrator,
    app_user.home_tenant_id,
    app_user.status,
    app_user.last_login_at,
    EXISTS (
      SELECT 1
      FROM public.user_assignment AS assignment
      WHERE assignment.user_id = app_user.id
        AND assignment.tenant_id = app_user.home_tenant_id
        AND assignment.is_active
        AND assignment.password_required
    ) AS password_required
  FROM public.app_user AS app_user
  WHERE app_user.id = p_user_id;
END;
$$ LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


TOUCH_AUTH_USER_LAST_LOGIN_SQL = """
CREATE FUNCTION public.touch_auth_user_last_login(
  p_user_id UUID,
  p_session_id UUID
) RETURNS VOID AS $$
BEGIN
  IF session_user <> 'aurum_support' AND NOT EXISTS (
    SELECT 1
    FROM public.session AS auth_session
    WHERE auth_session.id = p_session_id
      AND auth_session.user_id = p_user_id
      AND auth_session.revoked_at IS NULL
      AND auth_session.expires_at > pg_catalog.now()
  ) THEN
    RAISE EXCEPTION 'Authentication user is unavailable'
      USING ERRCODE = '42501';
  END IF;

  UPDATE public.app_user AS app_user
  SET last_login_at = GREATEST(
    COALESCE(app_user.last_login_at, pg_catalog.now()),
    pg_catalog.now()
  )
  WHERE app_user.id = p_user_id;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


RESTORE_LOOKUP_AUTH_USER_BY_EMAIL_SQL = """
CREATE FUNCTION public.lookup_auth_user_by_email(
  p_email TEXT
) RETURNS TABLE(
  id UUID,
  email TEXT,
  full_name TEXT,
  password_hash TEXT,
  is_developer BOOLEAN,
  is_administrator BOOLEAN,
  home_tenant_id UUID,
  status TEXT,
  last_login_at TIMESTAMPTZ,
  password_required BOOLEAN
) AS $$
  SELECT
    app_user.id,
    app_user.email,
    app_user.full_name,
    app_user.password_hash,
    app_user.is_developer,
    app_user.is_administrator,
    app_user.home_tenant_id,
    app_user.status,
    app_user.last_login_at,
    EXISTS (
      SELECT 1
      FROM public.user_assignment AS assignment
      WHERE assignment.user_id = app_user.id
        AND assignment.tenant_id = app_user.home_tenant_id
        AND assignment.is_active
        AND assignment.password_required
    ) AS password_required
  FROM public.app_user AS app_user
  WHERE app_user.email_lower = pg_catalog.lower(pg_catalog.btrim(p_email))
  LIMIT 1
$$ LANGUAGE SQL
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


RESTORE_LOOKUP_AUTH_USER_BY_ID_SQL = """
CREATE OR REPLACE FUNCTION public.lookup_auth_user_by_id(
  p_user_id UUID,
  p_session_id UUID
) RETURNS TABLE(
  id UUID,
  email TEXT,
  full_name TEXT,
  password_hash TEXT,
  is_developer BOOLEAN,
  is_administrator BOOLEAN,
  home_tenant_id UUID,
  status TEXT,
  last_login_at TIMESTAMPTZ,
  password_required BOOLEAN
) AS $$
BEGIN
  IF session_user <> 'aurum_support'
    AND p_user_id IS DISTINCT FROM public.current_app_user_id()
    AND NOT EXISTS (
      SELECT 1
      FROM public.session AS auth_session
      WHERE auth_session.id = p_session_id
        AND auth_session.user_id = p_user_id
        AND auth_session.revoked_at IS NULL
        AND auth_session.expires_at > pg_catalog.now()
    )
  THEN
    RAISE EXCEPTION 'Authentication user is unavailable'
      USING ERRCODE = '42501';
  END IF;

  RETURN QUERY
  SELECT
    app_user.id,
    app_user.email,
    app_user.full_name,
    app_user.password_hash,
    app_user.is_developer,
    app_user.is_administrator,
    app_user.home_tenant_id,
    app_user.status,
    app_user.last_login_at,
    EXISTS (
      SELECT 1
      FROM public.user_assignment AS assignment
      WHERE assignment.user_id = app_user.id
        AND assignment.tenant_id = app_user.home_tenant_id
        AND assignment.is_active
        AND assignment.password_required
    ) AS password_required
  FROM public.app_user AS app_user
  WHERE app_user.id = p_user_id;
END;
$$ LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


RESTORE_TOUCH_AUTH_USER_LAST_LOGIN_SQL = """
CREATE FUNCTION public.touch_auth_user_last_login(
  p_user_id UUID,
  p_when TIMESTAMPTZ
) RETURNS VOID AS $$
DECLARE
  v_when TIMESTAMPTZ;
BEGIN
  IF p_when IS NULL THEN
    RAISE EXCEPTION 'Login timestamp is required'
      USING ERRCODE = '22004';
  END IF;

  IF session_user <> 'aurum_support'
    AND p_user_id IS DISTINCT FROM public.current_app_user_id()
    AND NOT EXISTS (
      SELECT 1
      FROM public.session AS auth_session
      WHERE auth_session.user_id = p_user_id
        AND auth_session.revoked_at IS NULL
        AND auth_session.expires_at > pg_catalog.now()
    )
  THEN
    RAISE EXCEPTION 'Authentication user is unavailable'
      USING ERRCODE = '42501';
  END IF;

  v_when := LEAST(p_when, pg_catalog.now());
  UPDATE public.app_user AS app_user
  SET last_login_at = GREATEST(
    COALESCE(app_user.last_login_at, v_when),
    v_when
  )
  WHERE app_user.id = p_user_id;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


def _function_guard_sql() -> str:
    functions = ",\n        ".join(f"'{function}'::REGPROCEDURE" for function in APP_FUNCTIONS)
    return f"""
    DO $$
    DECLARE
      v_function REGPROCEDURE;
      v_owner TEXT;
      v_security_definer BOOLEAN;
      v_settings TEXT[];
    BEGIN
      IF EXISTS (
        SELECT 1
        FROM pg_catalog.unnest(ARRAY[
          'public.email_code'::REGCLASS,
          'public.login_attempt'::REGCLASS,
          'public.session'::REGCLASS
        ]) AS relations(relation_oid)
        CROSS JOIN pg_catalog.unnest(ARRAY[
          'SELECT', 'INSERT', 'UPDATE', 'DELETE',
          'TRUNCATE', 'REFERENCES', 'TRIGGER'
        ]) AS checks(privilege)
        WHERE pg_catalog.has_table_privilege(
          'aurum_app', relations.relation_oid, checks.privilege
        ) OR pg_catalog.has_table_privilege(
          'aurum_app',
          relations.relation_oid,
          checks.privilege || ' WITH GRANT OPTION'
        )
      ) THEN
        RAISE EXCEPTION 'aurum_app must not access authentication state tables';
      END IF;

      FOREACH v_function IN ARRAY ARRAY[
        {functions}
      ] LOOP
        SELECT
          pg_catalog.pg_get_userbyid(routines.proowner),
          routines.prosecdef,
          routines.proconfig
        INTO v_owner, v_security_definer, v_settings
        FROM pg_catalog.pg_proc AS routines
        WHERE routines.oid = v_function;

        IF v_owner IS DISTINCT FROM 'aurum_support'
          OR NOT v_security_definer
          OR NOT COALESCE(
            v_settings @> ARRAY['search_path=pg_catalog, pg_temp'],
            false
          )
          OR NOT pg_catalog.has_function_privilege(
            'aurum_app', v_function, 'EXECUTE'
          )
          OR pg_catalog.has_function_privilege(
            'aurum_app', v_function, 'EXECUTE WITH GRANT OPTION'
          )
          OR EXISTS (
            SELECT 1
            FROM pg_catalog.pg_proc AS routines
            CROSS JOIN LATERAL pg_catalog.aclexplode(
              COALESCE(
                routines.proacl,
                pg_catalog.acldefault('f'::"char", routines.proowner)
              )
            ) AS privileges
            LEFT JOIN pg_catalog.pg_roles AS grantees
              ON grantees.oid = privileges.grantee
            WHERE routines.oid = v_function
              AND privileges.privilege_type = 'EXECUTE'
              AND (
                privileges.grantee = 0
                OR grantees.rolname NOT IN ('aurum_app', 'aurum_support')
              )
          )
        THEN
          RAISE EXCEPTION 'Unsafe authentication function %', v_function;
        END IF;
      END LOOP;
    END
    $$
    """


def _configure_function_acl(function: str) -> None:
    op.execute(f"ALTER FUNCTION {function} OWNER TO aurum_support")
    op.execute(f"REVOKE ALL PRIVILEGES ON FUNCTION {function} FROM PUBLIC, aurum_app")
    op.execute(f"GRANT EXECUTE ON FUNCTION {function} TO aurum_support, aurum_app")


def upgrade() -> None:
    op.execute(
        "ALTER TABLE public.email_code ADD CONSTRAINT "
        "ck_email_code_hash_format CHECK ("
        "code_hash ~ '^[0-9a-f]{64}$' AND code_salt ~ '^[0-9a-f]{32}$'"
        ") NOT VALID"
    )
    op.execute(
        "ALTER TABLE public.session ADD CONSTRAINT "
        "ck_session_refresh_hash_format CHECK ("
        "refresh_token_hash ~ '^[0-9a-f]{64}$'"
        ") NOT VALID"
    )
    op.execute("ALTER TABLE public.email_code VALIDATE CONSTRAINT ck_email_code_hash_format")
    op.execute("ALTER TABLE public.session VALIDATE CONSTRAINT ck_session_refresh_hash_format")
    op.execute(
        "CREATE INDEX ix_email_code_rate_limit "
        "ON public.email_code (email_lower, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX ix_login_attempt_email_ip_time "
        "ON public.login_attempt (email_lower, ip_address, created_at DESC)"
    )
    # Existing ten-minute SHA-256 codes cannot be verified by the new keyed
    # HMAC scheme. Mark them consumed explicitly instead of leaving ambiguous
    # challenges visible until expiry.
    op.execute("UPDATE public.email_code SET used_at = pg_catalog.now() " "WHERE used_at IS NULL")

    for function in REMOVED_0036_FUNCTIONS:
        op.execute(f"DROP FUNCTION {function}")

    op.execute(ISSUE_AUTH_EMAIL_CODE_SQL)
    op.execute(FIND_AUTH_EMAIL_CODE_CHALLENGE_SQL)
    op.execute(AUTH_EMAIL_CODE_MATCHES_SQL)
    op.execute(CONSUME_AUTH_EMAIL_CODE_SQL)
    op.execute(LOOKUP_LOGIN_USER_BY_EMAIL_SQL)
    op.execute(CREATE_AUTH_SESSION_FROM_EMAIL_CODE_SQL)
    op.execute(ENFORCE_AUTH_LOGIN_GUARD_SQL)
    op.execute(RECORD_AUTH_LOGIN_ATTEMPT_SQL)
    op.execute(LOOKUP_AUTH_SESSION_BY_HASH_SQL)
    op.execute(ROTATE_AUTH_SESSION_SQL)
    op.execute(REVOKE_AUTH_SESSION_BY_HASH_SQL)
    op.execute(LOOKUP_AUTH_USER_BY_ID_SQL)
    op.execute(TOUCH_AUTH_USER_LAST_LOGIN_SQL)

    op.execute(
        "REVOKE ALL PRIVILEGES ON TABLE "
        "public.email_code, public.login_attempt, public.session FROM aurum_app"
    )

    for function in APP_FUNCTIONS:
        _configure_function_acl(function)

    op.execute(_function_guard_sql())


def downgrade() -> None:
    drop_functions = tuple(
        function
        for function in APP_FUNCTIONS
        if function != "public.lookup_auth_user_by_id(UUID, UUID)"
    )
    for function in reversed(drop_functions):
        op.execute(f"DROP FUNCTION {function}")

    op.execute(RESTORE_LOOKUP_AUTH_USER_BY_EMAIL_SQL)
    op.execute(RESTORE_LOOKUP_AUTH_USER_BY_ID_SQL)
    op.execute(RESTORE_TOUCH_AUTH_USER_LAST_LOGIN_SQL)

    for function in (
        "public.lookup_auth_user_by_email(TEXT)",
        "public.lookup_auth_user_by_id(UUID, UUID)",
        "public.touch_auth_user_last_login(UUID, TIMESTAMP WITH TIME ZONE)",
    ):
        _configure_function_acl(function)

    op.execute("DROP INDEX public.ix_login_attempt_email_ip_time")
    op.execute("DROP INDEX public.ix_email_code_rate_limit")
    op.execute("ALTER TABLE public.session DROP CONSTRAINT ck_session_refresh_hash_format")
    op.execute("ALTER TABLE public.email_code DROP CONSTRAINT ck_email_code_hash_format")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE "
        "public.email_code, public.login_attempt, public.session TO aurum_app"
    )
