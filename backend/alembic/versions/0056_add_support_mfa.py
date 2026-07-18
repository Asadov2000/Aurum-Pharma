"""Add encrypted TOTP MFA and recovery for platform support accounts.

Revision ID: 0056
Revises: 0055
Create Date: 2026-07-18
"""

from __future__ import annotations

import importlib.util
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Union

from alembic import op

revision: str = "0056"
down_revision: Union[str, Sequence[str], None] = "0055"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


LOOKUP_LOGIN_USER_SQL = """
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
  password_required BOOLEAN,
  membership_status TEXT,
  mfa_status TEXT
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
    ) AS password_required,
    membership.status AS membership_status,
    support_mfa.status AS mfa_status
  FROM public.app_user AS app_user
  LEFT JOIN public.tenant_membership AS membership
    ON membership.tenant_id = app_user.home_tenant_id
   AND membership.user_id = app_user.id
  LEFT JOIN public.support_mfa AS support_mfa
    ON support_mfa.user_id = app_user.id
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


LOOKUP_AUTH_USER_SQL = """
CREATE FUNCTION public.lookup_auth_user_by_id(
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
  password_required BOOLEAN,
  membership_status TEXT,
  mfa_status TEXT
) AS $$
BEGIN
  IF NOT public.is_support_session()
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
    ) AS password_required,
    membership.status AS membership_status,
    support_mfa.status AS mfa_status
  FROM public.app_user AS app_user
  LEFT JOIN public.tenant_membership AS membership
    ON membership.tenant_id = app_user.home_tenant_id
   AND membership.user_id = app_user.id
  LEFT JOIN public.support_mfa AS support_mfa
    ON support_mfa.user_id = app_user.id
  WHERE app_user.id = p_user_id;
END;
$$ LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


CREATE_STANDARD_SESSION_SQL = """
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

  SELECT app_user.id
  INTO v_user_id
  FROM public.app_user AS app_user
  WHERE app_user.email_lower = v_code_email
    AND app_user.status IN ('invited', 'active')
    AND NOT app_user.is_developer
    AND NOT app_user.is_administrator;

  IF v_user_id IS NULL THEN
    RETURN NULL;
  END IF;

  UPDATE public.email_code AS email_code
  SET used_at = pg_catalog.now()
  WHERE email_code.id = p_code_id;

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


CREATE_MFA_CHALLENGE_SQL = """
CREATE FUNCTION public.create_auth_mfa_challenge_from_email_code(
  p_email TEXT,
  p_code_id UUID,
  p_candidate_hash TEXT,
  p_token_hash TEXT,
  p_ip_address TEXT,
  p_user_agent TEXT,
  p_expires_at TIMESTAMPTZ
) RETURNS TABLE(challenge_id UUID, purpose TEXT) AS $$
DECLARE
  v_user_id UUID;
  v_mfa_status TEXT;
  v_purpose TEXT;
  v_ip INET;
  v_challenge_id UUID;
BEGIN
  IF p_token_hash !~ '^[0-9a-f]{64}$'
    OR p_expires_at IS NULL
    OR p_expires_at <= pg_catalog.now()
    OR p_expires_at > pg_catalog.now() + INTERVAL '10 minutes'
  THEN
    RAISE EXCEPTION 'Invalid MFA challenge payload'
      USING ERRCODE = '22023';
  END IF;

  v_ip := p_ip_address::INET;

  SELECT app_user.id, support_mfa.status
  INTO v_user_id, v_mfa_status
  FROM public.email_code AS email_code
  JOIN public.app_user AS app_user
    ON app_user.email_lower = email_code.email_lower
  LEFT JOIN public.support_mfa AS support_mfa
    ON support_mfa.user_id = app_user.id
  WHERE email_code.id = p_code_id
    AND email_code.email_lower = pg_catalog.lower(pg_catalog.btrim(p_email))
    AND email_code.purpose = 'login'
    AND email_code.code_hash = p_candidate_hash
    AND email_code.used_at IS NULL
    AND email_code.expires_at > pg_catalog.now()
    AND app_user.status IN ('invited', 'active')
    AND (app_user.is_developer OR app_user.is_administrator)
    AND app_user.password_hash IS NOT NULL
  FOR UPDATE OF email_code;

  IF v_user_id IS NULL THEN
    RETURN;
  END IF;

  v_purpose := CASE
    WHEN v_mfa_status = 'active' THEN 'verify'
    WHEN v_mfa_status = 'recovery_pending' THEN 'recover'
    ELSE 'enroll'
  END;

  UPDATE public.email_code AS email_code
  SET used_at = pg_catalog.now()
  WHERE email_code.id = p_code_id;

  UPDATE public.auth_mfa_challenge AS challenge
  SET consumed_at = pg_catalog.now()
  WHERE challenge.user_id = v_user_id
    AND challenge.consumed_at IS NULL;

  INSERT INTO public.auth_mfa_challenge (
    token_hash,
    user_id,
    purpose,
    ip_address,
    user_agent,
    expires_at
  ) VALUES (
    p_token_hash,
    v_user_id,
    v_purpose,
    v_ip,
    pg_catalog.left(p_user_agent, 1024),
    p_expires_at
  )
  RETURNING auth_mfa_challenge.id INTO v_challenge_id;

  RETURN QUERY SELECT v_challenge_id, v_purpose;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


LOOKUP_MFA_CHALLENGE_SQL = """
CREATE FUNCTION public.lookup_auth_mfa_challenge(
  p_token_hash TEXT,
  p_encryption_key TEXT
) RETURNS TABLE(
  user_id UUID,
  email TEXT,
  is_developer BOOLEAN,
  is_administrator BOOLEAN,
  purpose TEXT,
  mfa_status TEXT,
  secret TEXT,
  last_used_counter BIGINT,
  failed_attempts SMALLINT,
  expires_at TIMESTAMPTZ
) AS $$
  SELECT
    app_user.id,
    app_user.email,
    app_user.is_developer,
    app_user.is_administrator,
    challenge.purpose,
    support_mfa.status,
    CASE
      WHEN challenge.purpose = 'verify'
        AND support_mfa.status = 'active'
        AND support_mfa.active_secret_ciphertext IS NOT NULL
      THEN public.pgp_sym_decrypt(
        support_mfa.active_secret_ciphertext,
        p_encryption_key
      )
      WHEN challenge.purpose IN ('enroll', 'recovery_enroll')
        AND support_mfa.pending_secret_ciphertext IS NOT NULL
      THEN public.pgp_sym_decrypt(
        support_mfa.pending_secret_ciphertext,
        p_encryption_key
      )
      ELSE NULL
    END AS secret,
    support_mfa.last_used_counter,
    challenge.failed_attempts,
    challenge.expires_at
  FROM public.auth_mfa_challenge AS challenge
  JOIN public.app_user AS app_user
    ON app_user.id = challenge.user_id
  LEFT JOIN public.support_mfa AS support_mfa
    ON support_mfa.user_id = challenge.user_id
  WHERE challenge.token_hash = p_token_hash
    AND challenge.consumed_at IS NULL
    AND challenge.expires_at > pg_catalog.now()
    AND challenge.failed_attempts < 5
    AND app_user.status IN ('invited', 'active')
    AND (app_user.is_developer OR app_user.is_administrator)
  LIMIT 1
$$ LANGUAGE SQL
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


STAGE_MFA_ENROLLMENT_SQL = """
CREATE FUNCTION public.stage_auth_mfa_enrollment(
  p_token_hash TEXT,
  p_secret TEXT,
  p_encryption_key TEXT,
  p_code_hashes TEXT[]
) RETURNS BOOLEAN AS $$
DECLARE
  v_user_id UUID;
  v_purpose TEXT;
  v_generation SMALLINT;
  v_distinct_codes BIGINT;
BEGIN
  IF p_secret !~ '^[A-Z2-7]{32}$'
    OR p_encryption_key !~ '^[0-9a-f]{64}$'
    OR pg_catalog.cardinality(p_code_hashes) <> 10
    OR EXISTS (
      SELECT 1
      FROM pg_catalog.unnest(p_code_hashes) AS code_hash
      WHERE code_hash !~ '^[0-9a-f]{64}$'
    )
  THEN
    RAISE EXCEPTION 'Invalid MFA enrollment payload'
      USING ERRCODE = '22023';
  END IF;

  SELECT pg_catalog.count(DISTINCT code_hash)
  INTO v_distinct_codes
  FROM pg_catalog.unnest(p_code_hashes) AS code_hash;
  IF v_distinct_codes <> 10 THEN
    RAISE EXCEPTION 'Recovery codes must be unique'
      USING ERRCODE = '22023';
  END IF;

  SELECT challenge.user_id, challenge.purpose
  INTO v_user_id, v_purpose
  FROM public.auth_mfa_challenge AS challenge
  WHERE challenge.token_hash = p_token_hash
    AND challenge.purpose IN ('enroll', 'recovery_enroll')
    AND challenge.consumed_at IS NULL
    AND challenge.expires_at > pg_catalog.now()
    AND challenge.failed_attempts < 5
  FOR UPDATE;

  IF v_user_id IS NULL THEN
    RETURN false;
  END IF;

  SELECT (
    GREATEST(
      COALESCE(support_mfa.active_generation, 0::SMALLINT),
      COALESCE(support_mfa.pending_generation, 0::SMALLINT)
    ) + 1
  )::SMALLINT
  INTO v_generation
  FROM public.support_mfa AS support_mfa
  WHERE support_mfa.user_id = v_user_id
  FOR UPDATE;
  v_generation := COALESCE(v_generation, 1::SMALLINT);

  INSERT INTO public.support_mfa (
    user_id,
    pending_secret_ciphertext,
    key_version,
    status,
    pending_generation
  ) VALUES (
    v_user_id,
    public.pgp_sym_encrypt(
      p_secret,
      p_encryption_key,
      'cipher-algo=aes256, compress-algo=0'
    ),
    1,
    CASE WHEN v_purpose = 'enroll' THEN 'pending' ELSE 'recovery_pending' END,
    v_generation
  )
  ON CONFLICT (user_id) DO UPDATE
  SET
    pending_secret_ciphertext = EXCLUDED.pending_secret_ciphertext,
    key_version = EXCLUDED.key_version,
    status = EXCLUDED.status,
    pending_generation = EXCLUDED.pending_generation,
    updated_at = pg_catalog.now();

  DELETE FROM public.support_mfa_recovery_code AS recovery_code
  WHERE recovery_code.user_id = v_user_id
    AND recovery_code.activated_at IS NULL;

  INSERT INTO public.support_mfa_recovery_code (
    user_id,
    generation,
    code_hash
  )
  SELECT v_user_id, v_generation, code_hash
  FROM pg_catalog.unnest(p_code_hashes) AS code_hash;

  INSERT INTO public.audit_log (
    user_id,
    action,
    table_name,
    record_id,
    new_values,
    metadata
  ) VALUES (
    v_user_id,
    'UPDATE',
    'support_mfa',
    v_user_id,
    pg_catalog.jsonb_build_object(
      'status',
      CASE WHEN v_purpose = 'enroll' THEN 'pending' ELSE 'recovery_pending' END
    ),
    pg_catalog.jsonb_build_object('event', 'mfa_enrollment_staged')
  );

  RETURN true;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


COMPLETE_MFA_ENROLLMENT_SQL = """
CREATE FUNCTION public.complete_auth_mfa_enrollment(
  p_token_hash TEXT,
  p_counter BIGINT,
  p_verified_secret TEXT,
  p_encryption_key TEXT,
  p_refresh_token_hash TEXT,
  p_user_agent TEXT,
  p_ip_address TEXT,
  p_expires_at TIMESTAMPTZ
) RETURNS TABLE(
  session_id UUID,
  user_id UUID,
  mfa_verified_at TIMESTAMPTZ,
  is_developer BOOLEAN,
  is_administrator BOOLEAN
) AS $$
DECLARE
  v_user_id UUID;
  v_purpose TEXT;
  v_status TEXT;
  v_generation SMALLINT;
  v_secret TEXT;
  v_session_id UUID;
  v_now TIMESTAMPTZ;
  v_ip INET;
BEGIN
  IF p_counter < 0
    OR p_verified_secret !~ '^[A-Z2-7]{32}$'
    OR p_encryption_key !~ '^[0-9a-f]{64}$'
    OR p_refresh_token_hash !~ '^[0-9a-f]{64}$'
    OR p_expires_at IS NULL
    OR p_expires_at <= pg_catalog.now()
    OR p_expires_at > pg_catalog.now() + INTERVAL '31 days'
  THEN
    RAISE EXCEPTION 'Invalid MFA completion payload'
      USING ERRCODE = '22023';
  END IF;

  v_now := pg_catalog.now();
  v_ip := p_ip_address::INET;

  SELECT challenge.user_id, challenge.purpose
  INTO v_user_id, v_purpose
  FROM public.auth_mfa_challenge AS challenge
  WHERE challenge.token_hash = p_token_hash
    AND challenge.purpose IN ('enroll', 'recovery_enroll')
    AND challenge.consumed_at IS NULL
    AND challenge.expires_at > v_now
    AND challenge.failed_attempts < 5
  FOR UPDATE;

  IF v_user_id IS NULL THEN
    RETURN;
  END IF;

  SELECT
    support_mfa.status,
    support_mfa.pending_generation,
    public.pgp_sym_decrypt(
      support_mfa.pending_secret_ciphertext,
      p_encryption_key
    )
  INTO v_status, v_generation, v_secret
  FROM public.support_mfa AS support_mfa
  WHERE support_mfa.user_id = v_user_id
    AND support_mfa.pending_secret_ciphertext IS NOT NULL
    AND support_mfa.pending_generation IS NOT NULL
  FOR UPDATE;

  IF v_secret IS DISTINCT FROM p_verified_secret
    OR v_generation IS NULL
    OR v_status NOT IN ('pending', 'recovery_pending')
  THEN
    RETURN;
  END IF;

  UPDATE public.support_mfa AS support_mfa
  SET
    active_secret_ciphertext = support_mfa.pending_secret_ciphertext,
    pending_secret_ciphertext = NULL,
    status = 'active',
    active_generation = support_mfa.pending_generation,
    pending_generation = NULL,
    last_used_counter = p_counter,
    confirmed_at = v_now,
    updated_at = v_now
  WHERE support_mfa.user_id = v_user_id;

  UPDATE public.support_mfa_recovery_code AS recovery_code
  SET activated_at = v_now
  WHERE recovery_code.user_id = v_user_id
    AND recovery_code.generation = v_generation
    AND recovery_code.activated_at IS NULL;

  UPDATE public.auth_mfa_challenge AS challenge
  SET consumed_at = v_now
  WHERE challenge.token_hash = p_token_hash;

  UPDATE public.session AS auth_session
  SET
    revoked_at = v_now,
    revoked_reason = CASE
      WHEN v_purpose = 'recovery_enroll' THEN 'mfa_recovered'
      ELSE 'mfa_enrolled'
    END,
    last_used_at = v_now
  WHERE auth_session.user_id = v_user_id
    AND auth_session.revoked_at IS NULL;

  INSERT INTO public.session (
    user_id,
    refresh_token_hash,
    user_agent,
    ip_address,
    expires_at,
    mfa_verified_at
  ) VALUES (
    v_user_id,
    p_refresh_token_hash,
    pg_catalog.left(p_user_agent, 1024),
    v_ip,
    p_expires_at,
    v_now
  )
  RETURNING session.id INTO v_session_id;

  INSERT INTO public.audit_log (
    user_id,
    action,
    table_name,
    record_id,
    new_values,
    ip_address,
    user_agent,
    metadata
  ) VALUES (
    v_user_id,
    'UPDATE',
    'support_mfa',
    v_user_id,
    pg_catalog.jsonb_build_object('status', 'active'),
    v_ip,
    pg_catalog.left(p_user_agent, 1024),
    pg_catalog.jsonb_build_object(
      'event',
      CASE
        WHEN v_purpose = 'recovery_enroll' THEN 'mfa_recovered'
        ELSE 'mfa_enrolled'
      END
    )
  );

  RETURN QUERY
  SELECT
    v_session_id,
    app_user.id,
    v_now,
    app_user.is_developer,
    app_user.is_administrator
  FROM public.app_user AS app_user
  WHERE app_user.id = v_user_id
    AND app_user.status IN ('invited', 'active')
    AND (app_user.is_developer OR app_user.is_administrator);
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


COMPLETE_MFA_VERIFICATION_SQL = """
CREATE FUNCTION public.complete_auth_mfa_verification(
  p_token_hash TEXT,
  p_counter BIGINT,
  p_verified_secret TEXT,
  p_encryption_key TEXT,
  p_refresh_token_hash TEXT,
  p_user_agent TEXT,
  p_ip_address TEXT,
  p_expires_at TIMESTAMPTZ
) RETURNS TABLE(
  session_id UUID,
  user_id UUID,
  mfa_verified_at TIMESTAMPTZ,
  is_developer BOOLEAN,
  is_administrator BOOLEAN
) AS $$
DECLARE
  v_user_id UUID;
  v_last_used_counter BIGINT;
  v_secret TEXT;
  v_session_id UUID;
  v_now TIMESTAMPTZ;
  v_ip INET;
BEGIN
  IF p_counter < 0
    OR p_verified_secret !~ '^[A-Z2-7]{32}$'
    OR p_encryption_key !~ '^[0-9a-f]{64}$'
    OR p_refresh_token_hash !~ '^[0-9a-f]{64}$'
    OR p_expires_at IS NULL
    OR p_expires_at <= pg_catalog.now()
    OR p_expires_at > pg_catalog.now() + INTERVAL '31 days'
  THEN
    RAISE EXCEPTION 'Invalid MFA verification payload'
      USING ERRCODE = '22023';
  END IF;

  v_now := pg_catalog.now();
  v_ip := p_ip_address::INET;

  SELECT challenge.user_id
  INTO v_user_id
  FROM public.auth_mfa_challenge AS challenge
  WHERE challenge.token_hash = p_token_hash
    AND challenge.purpose = 'verify'
    AND challenge.consumed_at IS NULL
    AND challenge.expires_at > v_now
    AND challenge.failed_attempts < 5
  FOR UPDATE;

  IF v_user_id IS NULL THEN
    RETURN;
  END IF;

  SELECT
    support_mfa.last_used_counter,
    public.pgp_sym_decrypt(
      support_mfa.active_secret_ciphertext,
      p_encryption_key
    )
  INTO v_last_used_counter, v_secret
  FROM public.support_mfa AS support_mfa
  WHERE support_mfa.user_id = v_user_id
    AND support_mfa.status = 'active'
    AND support_mfa.active_secret_ciphertext IS NOT NULL
  FOR UPDATE;

  IF v_secret IS DISTINCT FROM p_verified_secret
    OR p_counter <= COALESCE(v_last_used_counter, (-1)::BIGINT)
  THEN
    RETURN;
  END IF;

  UPDATE public.support_mfa AS support_mfa
  SET
    last_used_counter = p_counter,
    updated_at = v_now
  WHERE support_mfa.user_id = v_user_id;

  UPDATE public.auth_mfa_challenge AS challenge
  SET consumed_at = v_now
  WHERE challenge.token_hash = p_token_hash;

  INSERT INTO public.session (
    user_id,
    refresh_token_hash,
    user_agent,
    ip_address,
    expires_at,
    mfa_verified_at
  ) VALUES (
    v_user_id,
    p_refresh_token_hash,
    pg_catalog.left(p_user_agent, 1024),
    v_ip,
    p_expires_at,
    v_now
  )
  RETURNING session.id INTO v_session_id;

  RETURN QUERY
  SELECT
    v_session_id,
    app_user.id,
    v_now,
    app_user.is_developer,
    app_user.is_administrator
  FROM public.app_user AS app_user
  WHERE app_user.id = v_user_id
    AND app_user.status IN ('invited', 'active')
    AND (app_user.is_developer OR app_user.is_administrator);
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


RECORD_MFA_FAILURE_SQL = """
CREATE FUNCTION public.record_auth_mfa_failure(
  p_token_hash TEXT,
  p_ip_address TEXT,
  p_user_agent TEXT
) RETURNS BOOLEAN AS $$
DECLARE
  v_user_id UUID;
  v_email TEXT;
  v_failed_attempts SMALLINT;
  v_ip INET;
BEGIN
  v_ip := p_ip_address::INET;

  SELECT challenge.user_id, app_user.email_lower, challenge.failed_attempts
  INTO v_user_id, v_email, v_failed_attempts
  FROM public.auth_mfa_challenge AS challenge
  JOIN public.app_user AS app_user
    ON app_user.id = challenge.user_id
  WHERE challenge.token_hash = p_token_hash
    AND challenge.consumed_at IS NULL
    AND challenge.expires_at > pg_catalog.now()
    AND challenge.failed_attempts < 5
  FOR UPDATE OF challenge;

  IF v_user_id IS NULL THEN
    RETURN false;
  END IF;

  v_failed_attempts := v_failed_attempts + 1;
  UPDATE public.auth_mfa_challenge AS challenge
  SET
    failed_attempts = v_failed_attempts,
    consumed_at = CASE WHEN v_failed_attempts >= 5 THEN pg_catalog.now() ELSE NULL END
  WHERE challenge.token_hash = p_token_hash;

  INSERT INTO public.login_attempt (
    email_lower,
    user_id,
    ip_address,
    user_agent,
    outcome
  ) VALUES (
    v_email,
    v_user_id,
    v_ip,
    pg_catalog.left(p_user_agent, 1024),
    'totp_failed'
  );

  RETURN true;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


RECOVER_MFA_CHALLENGE_SQL = """
CREATE FUNCTION public.recover_auth_mfa_challenge(
  p_token_hash TEXT,
  p_code_hash TEXT,
  p_ip_address TEXT,
  p_user_agent TEXT
) RETURNS BOOLEAN AS $$
DECLARE
  v_user_id UUID;
  v_email TEXT;
  v_generation SMALLINT;
  v_recovery_code_id UUID;
  v_failed_attempts SMALLINT;
  v_ip INET;
  v_now TIMESTAMPTZ;
BEGIN
  IF p_code_hash !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'Invalid recovery payload'
      USING ERRCODE = '22023';
  END IF;

  v_ip := p_ip_address::INET;
  v_now := pg_catalog.now();

  SELECT
    challenge.user_id,
    app_user.email_lower,
    challenge.failed_attempts,
    support_mfa.active_generation
  INTO v_user_id, v_email, v_failed_attempts, v_generation
  FROM public.auth_mfa_challenge AS challenge
  JOIN public.app_user AS app_user
    ON app_user.id = challenge.user_id
  JOIN public.support_mfa AS support_mfa
    ON support_mfa.user_id = challenge.user_id
  WHERE challenge.token_hash = p_token_hash
    AND challenge.purpose IN ('verify', 'recover')
    AND challenge.consumed_at IS NULL
    AND challenge.expires_at > v_now
    AND challenge.failed_attempts < 5
    AND support_mfa.status IN ('active', 'recovery_pending')
    AND support_mfa.active_generation IS NOT NULL
  FOR UPDATE OF challenge, support_mfa;

  IF v_user_id IS NULL THEN
    RETURN false;
  END IF;

  SELECT recovery_code.id
  INTO v_recovery_code_id
  FROM public.support_mfa_recovery_code AS recovery_code
  WHERE recovery_code.user_id = v_user_id
    AND recovery_code.generation = v_generation
    AND recovery_code.code_hash = p_code_hash
    AND recovery_code.activated_at IS NOT NULL
    AND recovery_code.used_at IS NULL
  FOR UPDATE;

  IF v_recovery_code_id IS NULL THEN
    v_failed_attempts := v_failed_attempts + 1;
    UPDATE public.auth_mfa_challenge AS challenge
    SET
      failed_attempts = v_failed_attempts,
      consumed_at = CASE WHEN v_failed_attempts >= 5 THEN v_now ELSE NULL END
    WHERE challenge.token_hash = p_token_hash;

    INSERT INTO public.login_attempt (
      email_lower,
      user_id,
      ip_address,
      user_agent,
      outcome,
      metadata_json
    ) VALUES (
      v_email,
      v_user_id,
      v_ip,
      pg_catalog.left(p_user_agent, 1024),
      'totp_failed',
      pg_catalog.jsonb_build_object('factor', 'recovery_code')
    );
    RETURN false;
  END IF;

  UPDATE public.support_mfa_recovery_code AS recovery_code
  SET used_at = v_now
  WHERE recovery_code.id = v_recovery_code_id;

  UPDATE public.support_mfa AS support_mfa
  SET
    status = 'recovery_pending',
    pending_secret_ciphertext = NULL,
    pending_generation = NULL,
    updated_at = v_now
  WHERE support_mfa.user_id = v_user_id;

  DELETE FROM public.support_mfa_recovery_code AS recovery_code
  WHERE recovery_code.user_id = v_user_id
    AND recovery_code.activated_at IS NULL;

  UPDATE public.auth_mfa_challenge AS challenge
  SET
    purpose = 'recovery_enroll',
    failed_attempts = 0,
    expires_at = v_now + INTERVAL '10 minutes'
  WHERE challenge.token_hash = p_token_hash;

  UPDATE public.session AS auth_session
  SET
    revoked_at = v_now,
    revoked_reason = 'mfa_recovery_started',
    last_used_at = v_now
  WHERE auth_session.user_id = v_user_id
    AND auth_session.revoked_at IS NULL;

  INSERT INTO public.audit_log (
    user_id,
    action,
    table_name,
    record_id,
    new_values,
    ip_address,
    user_agent,
    metadata
  ) VALUES (
    v_user_id,
    'UPDATE',
    'support_mfa',
    v_user_id,
    pg_catalog.jsonb_build_object('status', 'recovery_pending'),
    v_ip,
    pg_catalog.left(p_user_agent, 1024),
    pg_catalog.jsonb_build_object('event', 'mfa_recovery_started')
  );

  RETURN true;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


LOOKUP_STEP_UP_SQL = """
CREATE FUNCTION public.lookup_support_mfa_for_step_up(
  p_user_id UUID,
  p_session_id UUID,
  p_encryption_key TEXT
) RETURNS TABLE(
  email TEXT,
  secret TEXT,
  last_used_counter BIGINT
) AS $$
  SELECT
    app_user.email,
    public.pgp_sym_decrypt(
      support_mfa.active_secret_ciphertext,
      p_encryption_key
    ),
    support_mfa.last_used_counter
  FROM public.app_user AS app_user
  JOIN public.support_mfa AS support_mfa
    ON support_mfa.user_id = app_user.id
  JOIN public.session AS auth_session
    ON auth_session.user_id = app_user.id
  WHERE app_user.id = p_user_id
    AND app_user.id = public.current_app_user_id()
    AND (app_user.is_developer OR app_user.is_administrator)
    AND app_user.status IN ('invited', 'active')
    AND support_mfa.status = 'active'
    AND support_mfa.active_secret_ciphertext IS NOT NULL
    AND auth_session.id = p_session_id
    AND auth_session.revoked_at IS NULL
    AND auth_session.expires_at > pg_catalog.now()
  LIMIT 1
$$ LANGUAGE SQL
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


COMPLETE_STEP_UP_SQL = """
CREATE FUNCTION public.complete_support_mfa_step_up(
  p_user_id UUID,
  p_session_id UUID,
  p_counter BIGINT,
  p_verified_secret TEXT,
  p_encryption_key TEXT
) RETURNS TIMESTAMPTZ AS $$
DECLARE
  v_secret TEXT;
  v_last_used_counter BIGINT;
  v_now TIMESTAMPTZ;
BEGIN
  IF p_user_id IS DISTINCT FROM public.current_app_user_id()
    OR p_counter < 0
    OR p_verified_secret !~ '^[A-Z2-7]{32}$'
    OR p_encryption_key !~ '^[0-9a-f]{64}$'
  THEN
    RETURN NULL;
  END IF;

  SELECT
    public.pgp_sym_decrypt(
      support_mfa.active_secret_ciphertext,
      p_encryption_key
    ),
    support_mfa.last_used_counter
  INTO v_secret, v_last_used_counter
  FROM public.support_mfa AS support_mfa
  JOIN public.app_user AS app_user
    ON app_user.id = support_mfa.user_id
  WHERE support_mfa.user_id = p_user_id
    AND support_mfa.status = 'active'
    AND support_mfa.active_secret_ciphertext IS NOT NULL
    AND app_user.status IN ('invited', 'active')
    AND (app_user.is_developer OR app_user.is_administrator)
  FOR UPDATE OF support_mfa;

  IF v_secret IS DISTINCT FROM p_verified_secret
    OR p_counter <= COALESCE(v_last_used_counter, (-1)::BIGINT)
    OR NOT EXISTS (
      SELECT 1
      FROM public.session AS auth_session
      WHERE auth_session.id = p_session_id
        AND auth_session.user_id = p_user_id
        AND auth_session.revoked_at IS NULL
        AND auth_session.expires_at > pg_catalog.now()
    )
  THEN
    RETURN NULL;
  END IF;

  v_now := pg_catalog.now();
  UPDATE public.support_mfa AS support_mfa
  SET
    last_used_counter = p_counter,
    updated_at = v_now
  WHERE support_mfa.user_id = p_user_id;

  UPDATE public.session AS auth_session
  SET
    mfa_verified_at = v_now,
    last_used_at = v_now
  WHERE auth_session.id = p_session_id;

  INSERT INTO public.audit_log (
    user_id,
    action,
    table_name,
    record_id,
    metadata
  ) VALUES (
    p_user_id,
    'VIEW',
    'support_mfa',
    p_user_id,
    pg_catalog.jsonb_build_object('event', 'mfa_step_up')
  );

  RETURN v_now;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


LOOKUP_SESSION_MFA_SQL = """
CREATE FUNCTION public.lookup_auth_session_mfa(
  p_session_id UUID,
  p_user_id UUID
) RETURNS TIMESTAMPTZ AS $$
  SELECT auth_session.mfa_verified_at
  FROM public.session AS auth_session
  WHERE auth_session.id = p_session_id
    AND auth_session.user_id = p_user_id
    AND auth_session.revoked_at IS NULL
    AND auth_session.expires_at > pg_catalog.now()
  LIMIT 1
$$ LANGUAGE SQL
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


COPY_SESSION_MFA_SQL = """
CREATE FUNCTION public.trg_copy_session_mfa_verification()
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


def _load_revision_module(filename: str) -> ModuleType:
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(f"aurum_migration_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load migration source: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _secure_function(signature: str, *, app_access: bool = True) -> None:
    op.execute(f"ALTER FUNCTION {signature} OWNER TO aurum_support")
    op.execute(f"REVOKE ALL PRIVILEGES ON FUNCTION {signature} FROM PUBLIC, aurum_app")
    grantees = "aurum_support, aurum_app" if app_access else "aurum_support"
    op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {grantees}")


def _replace_function(signature: str, statement: str) -> None:
    op.execute(f"DROP FUNCTION {signature}")
    op.execute(statement)
    _secure_function(signature)


def upgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM public.app_user
            WHERE totp_secret IS NOT NULL
          ) THEN
            RAISE EXCEPTION
              'Refusing to drop plaintext TOTP values; migrate them explicitly first';
          END IF;
        END
        $$
        """)
    op.execute("ALTER TABLE public.app_user DROP COLUMN totp_secret")
    op.execute("ALTER TABLE public.session " "ADD COLUMN mfa_verified_at TIMESTAMP WITH TIME ZONE")

    op.execute("""
        CREATE TABLE public.support_mfa (
          user_id UUID PRIMARY KEY
            REFERENCES public.app_user(id) ON DELETE CASCADE,
          active_secret_ciphertext BYTEA,
          pending_secret_ciphertext BYTEA,
          key_version SMALLINT NOT NULL DEFAULT 1
            CHECK (key_version > 0),
          status TEXT NOT NULL
            CHECK (status IN ('pending', 'active', 'recovery_pending')),
          active_generation SMALLINT,
          pending_generation SMALLINT,
          last_used_counter BIGINT,
          confirmed_at TIMESTAMPTZ,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CHECK (
            (status = 'active'
              AND active_secret_ciphertext IS NOT NULL
              AND active_generation IS NOT NULL)
            OR status IN ('pending', 'recovery_pending')
          )
        )
        """)
    op.execute("""
        CREATE TABLE public.support_mfa_recovery_code (
          id UUID PRIMARY KEY DEFAULT public.gen_random_uuid(),
          user_id UUID NOT NULL
            REFERENCES public.support_mfa(user_id) ON DELETE CASCADE,
          generation SMALLINT NOT NULL CHECK (generation > 0),
          code_hash TEXT NOT NULL CHECK (code_hash ~ '^[0-9a-f]{64}$'),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          activated_at TIMESTAMPTZ,
          used_at TIMESTAMPTZ,
          CONSTRAINT uq_support_mfa_recovery_code
            UNIQUE (user_id, generation, code_hash)
        )
        """)
    op.execute("""
        CREATE TABLE public.auth_mfa_challenge (
          id UUID PRIMARY KEY DEFAULT public.gen_random_uuid(),
          token_hash TEXT NOT NULL UNIQUE
            CHECK (token_hash ~ '^[0-9a-f]{64}$'),
          user_id UUID NOT NULL
            REFERENCES public.app_user(id) ON DELETE CASCADE,
          purpose TEXT NOT NULL
            CHECK (purpose IN ('verify', 'enroll', 'recover', 'recovery_enroll')),
          failed_attempts SMALLINT NOT NULL DEFAULT 0
            CHECK (failed_attempts BETWEEN 0 AND 5),
          ip_address INET NOT NULL,
          user_agent TEXT,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          expires_at TIMESTAMPTZ NOT NULL,
          consumed_at TIMESTAMPTZ
        )
        """)
    op.execute(
        "CREATE INDEX ix_auth_mfa_challenge_user_active "
        "ON public.auth_mfa_challenge (user_id, expires_at DESC) "
        "WHERE consumed_at IS NULL"
    )
    op.execute(
        "CREATE INDEX ix_support_mfa_recovery_active "
        "ON public.support_mfa_recovery_code (user_id, generation) "
        "WHERE activated_at IS NOT NULL AND used_at IS NULL"
    )

    for table in (
        "support_mfa",
        "support_mfa_recovery_code",
        "auth_mfa_challenge",
    ):
        op.execute(f"ALTER TABLE public.{table} OWNER TO aurum_support")
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL PRIVILEGES ON TABLE public.{table} " "FROM PUBLIC, aurum_app")

    _replace_function(
        "public.lookup_login_user_by_email(TEXT, UUID, TEXT)",
        LOOKUP_LOGIN_USER_SQL,
    )
    _replace_function(
        "public.lookup_auth_user_by_id(UUID, UUID)",
        LOOKUP_AUTH_USER_SQL,
    )
    _replace_function(
        "public.create_auth_session_from_email_code("
        "UUID, TEXT, TEXT, TEXT, TEXT, TEXT, TIMESTAMP WITH TIME ZONE)",
        CREATE_STANDARD_SESSION_SQL,
    )

    functions = (
        (
            CREATE_MFA_CHALLENGE_SQL,
            "public.create_auth_mfa_challenge_from_email_code("
            "TEXT, UUID, TEXT, TEXT, TEXT, TEXT, TIMESTAMP WITH TIME ZONE)",
        ),
        (
            LOOKUP_MFA_CHALLENGE_SQL,
            "public.lookup_auth_mfa_challenge(TEXT, TEXT)",
        ),
        (
            STAGE_MFA_ENROLLMENT_SQL,
            "public.stage_auth_mfa_enrollment(TEXT, TEXT, TEXT, TEXT[])",
        ),
        (
            COMPLETE_MFA_ENROLLMENT_SQL,
            "public.complete_auth_mfa_enrollment("
            "TEXT, BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TIMESTAMP WITH TIME ZONE)",
        ),
        (
            COMPLETE_MFA_VERIFICATION_SQL,
            "public.complete_auth_mfa_verification("
            "TEXT, BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TIMESTAMP WITH TIME ZONE)",
        ),
        (
            RECORD_MFA_FAILURE_SQL,
            "public.record_auth_mfa_failure(TEXT, TEXT, TEXT)",
        ),
        (
            RECOVER_MFA_CHALLENGE_SQL,
            "public.recover_auth_mfa_challenge(TEXT, TEXT, TEXT, TEXT)",
        ),
        (
            LOOKUP_STEP_UP_SQL,
            "public.lookup_support_mfa_for_step_up(UUID, UUID, TEXT)",
        ),
        (
            COMPLETE_STEP_UP_SQL,
            "public.complete_support_mfa_step_up(" "UUID, UUID, BIGINT, TEXT, TEXT)",
        ),
        (
            LOOKUP_SESSION_MFA_SQL,
            "public.lookup_auth_session_mfa(UUID, UUID)",
        ),
    )
    for statement, signature in functions:
        op.execute(statement)
        _secure_function(signature)

    op.execute(COPY_SESSION_MFA_SQL)
    _secure_function(
        "public.trg_copy_session_mfa_verification()",
        app_access=False,
    )

    op.execute("""
        CREATE TRIGGER trg_copy_session_mfa_verification
        BEFORE INSERT ON public.session
        FOR EACH ROW
        EXECUTE FUNCTION public.trg_copy_session_mfa_verification()
        """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_copy_session_mfa_verification " "ON public.session")

    signatures = (
        "public.trg_copy_session_mfa_verification()",
        "public.lookup_auth_session_mfa(UUID, UUID)",
        "public.complete_support_mfa_step_up(UUID, UUID, BIGINT, TEXT, TEXT)",
        "public.lookup_support_mfa_for_step_up(UUID, UUID, TEXT)",
        "public.recover_auth_mfa_challenge(TEXT, TEXT, TEXT, TEXT)",
        "public.record_auth_mfa_failure(TEXT, TEXT, TEXT)",
        "public.complete_auth_mfa_verification("
        "TEXT, BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TIMESTAMP WITH TIME ZONE)",
        "public.complete_auth_mfa_enrollment("
        "TEXT, BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TIMESTAMP WITH TIME ZONE)",
        "public.stage_auth_mfa_enrollment(TEXT, TEXT, TEXT, TEXT[])",
        "public.lookup_auth_mfa_challenge(TEXT, TEXT)",
        "public.create_auth_mfa_challenge_from_email_code("
        "TEXT, UUID, TEXT, TEXT, TEXT, TEXT, TIMESTAMP WITH TIME ZONE)",
    )
    for signature in signatures:
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")

    source_0053 = _load_revision_module("0053_add_scoped_delegated_authorization.py")
    source_0037 = _load_revision_module("0037_harden_auth_state_access.py")
    _replace_function(
        "public.lookup_login_user_by_email(TEXT, UUID, TEXT)",
        source_0053.ACTIVE_LOOKUP_LOGIN_USER_BY_EMAIL_SQL,
    )
    _replace_function(
        "public.lookup_auth_user_by_id(UUID, UUID)",
        source_0053.ACTIVE_LOOKUP_AUTH_USER_BY_ID_SQL,
    )
    _replace_function(
        "public.create_auth_session_from_email_code("
        "UUID, TEXT, TEXT, TEXT, TEXT, TEXT, TIMESTAMP WITH TIME ZONE)",
        source_0037.CREATE_AUTH_SESSION_FROM_EMAIL_CODE_SQL,
    )

    op.execute("DROP TABLE public.auth_mfa_challenge")
    op.execute("DROP TABLE public.support_mfa_recovery_code")
    op.execute("DROP TABLE public.support_mfa")
    op.execute("ALTER TABLE public.session DROP COLUMN mfa_verified_at")
    op.execute("ALTER TABLE public.app_user ADD COLUMN totp_secret TEXT")
