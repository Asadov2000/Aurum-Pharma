"""Harden support MFA recovery, key rotation, and database privileges.

Revision ID: 0057
Revises: 0056
Create Date: 2026-07-18
"""

from __future__ import annotations

import importlib.util
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Union

from alembic import op

revision: str = "0057"
down_revision: Union[str, Sequence[str], None] = "0056"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


DECRYPT_MFA_SECRET_SQL = """
CREATE FUNCTION public.decrypt_support_mfa_secret(
  p_ciphertext BYTEA,
  p_key_version SMALLINT,
  p_keyring JSONB
) RETURNS TEXT AS $$
DECLARE
  v_key TEXT;
BEGIN
  IF p_ciphertext IS NULL
    OR p_key_version IS NULL
    OR pg_catalog.jsonb_typeof(p_keyring) IS DISTINCT FROM 'object'
  THEN
    RAISE EXCEPTION 'Invalid MFA decryption payload'
      USING ERRCODE = '22023';
  END IF;

  v_key := p_keyring ->> p_key_version::TEXT;
  IF v_key IS NULL OR v_key !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'MFA encryption key version is unavailable'
      USING ERRCODE = '22023';
  END IF;

  RETURN public.pgp_sym_decrypt(p_ciphertext, v_key);
END;
$$ LANGUAGE plpgsql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog, pg_temp
"""


LOOKUP_MFA_CHALLENGE_SQL = """
CREATE FUNCTION public.lookup_auth_mfa_challenge(
  p_token_hash TEXT,
  p_encryption_keyring JSONB,
  p_include_secret BOOLEAN
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
      WHEN p_include_secret
        AND challenge.purpose = 'verify'
        AND support_mfa.status = 'active'
        AND support_mfa.active_secret_ciphertext IS NOT NULL
        AND support_mfa.active_key_version IS NOT NULL
      THEN public.decrypt_support_mfa_secret(
        support_mfa.active_secret_ciphertext,
        support_mfa.active_key_version,
        p_encryption_keyring
      )
      WHEN p_include_secret
        AND challenge.purpose IN ('enroll', 'recovery_enroll')
        AND support_mfa.pending_secret_ciphertext IS NOT NULL
        AND support_mfa.pending_key_version IS NOT NULL
      THEN public.decrypt_support_mfa_secret(
        support_mfa.pending_secret_ciphertext,
        support_mfa.pending_key_version,
        p_encryption_keyring
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
  p_key_version SMALLINT,
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
    OR p_key_version IS NULL
    OR p_key_version < 1
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
  JOIN public.app_user AS app_user
    ON app_user.id = challenge.user_id
  WHERE challenge.token_hash = p_token_hash
    AND challenge.purpose IN ('enroll', 'recovery_enroll')
    AND challenge.consumed_at IS NULL
    AND challenge.expires_at > pg_catalog.now()
    AND challenge.failed_attempts < 5
    AND app_user.status IN ('invited', 'active')
    AND (app_user.is_developer OR app_user.is_administrator)
  FOR UPDATE OF challenge, app_user;

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

  IF v_purpose = 'recovery_enroll' THEN
    UPDATE public.support_mfa AS support_mfa
    SET
      pending_secret_ciphertext = public.pgp_sym_encrypt(
        p_secret,
        p_encryption_key,
        'cipher-algo=aes256, compress-algo=0'
      ),
      pending_key_version = p_key_version,
      status = 'recovery_pending',
      pending_generation = v_generation,
      updated_at = pg_catalog.now()
    WHERE support_mfa.user_id = v_user_id
      AND support_mfa.status = 'recovery_pending'
      AND support_mfa.active_secret_ciphertext IS NOT NULL
      AND support_mfa.active_key_version IS NOT NULL
      AND support_mfa.active_generation IS NOT NULL;
    IF NOT FOUND THEN
      RETURN false;
    END IF;
  ELSE
    INSERT INTO public.support_mfa (
      user_id,
      pending_secret_ciphertext,
      pending_key_version,
      status,
      pending_generation
    ) VALUES (
      v_user_id,
      public.pgp_sym_encrypt(
        p_secret,
        p_encryption_key,
        'cipher-algo=aes256, compress-algo=0'
      ),
      p_key_version,
      'pending',
      v_generation
    )
    ON CONFLICT (user_id) DO UPDATE
    SET
      pending_secret_ciphertext = EXCLUDED.pending_secret_ciphertext,
      pending_key_version = EXCLUDED.pending_key_version,
      status = EXCLUDED.status,
      pending_generation = EXCLUDED.pending_generation,
      updated_at = pg_catalog.now();
  END IF;

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
  p_encryption_keyring JSONB,
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
  v_active_generation SMALLINT;
  v_generation SMALLINT;
  v_secret TEXT;
  v_recovery_code_id UUID;
  v_session_id UUID;
  v_now TIMESTAMPTZ;
  v_ip INET;
BEGIN
  IF p_counter < 0
    OR p_verified_secret !~ '^[A-Z2-7]{32}$'
    OR pg_catalog.jsonb_typeof(p_encryption_keyring) IS DISTINCT FROM 'object'
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

  SELECT
    challenge.user_id,
    challenge.purpose,
    challenge.recovery_code_id
  INTO v_user_id, v_purpose, v_recovery_code_id
  FROM public.auth_mfa_challenge AS challenge
  JOIN public.app_user AS app_user
    ON app_user.id = challenge.user_id
  WHERE challenge.token_hash = p_token_hash
    AND challenge.purpose IN ('enroll', 'recovery_enroll')
    AND challenge.consumed_at IS NULL
    AND challenge.expires_at > v_now
    AND challenge.failed_attempts < 5
    AND app_user.status IN ('invited', 'active')
    AND (app_user.is_developer OR app_user.is_administrator)
  FOR UPDATE OF challenge, app_user;

  IF v_user_id IS NULL THEN
    RETURN;
  END IF;

  SELECT
    support_mfa.status,
    support_mfa.active_generation,
    support_mfa.pending_generation,
    public.decrypt_support_mfa_secret(
      support_mfa.pending_secret_ciphertext,
      support_mfa.pending_key_version,
      p_encryption_keyring
    )
  INTO v_status, v_active_generation, v_generation, v_secret
  FROM public.support_mfa AS support_mfa
  WHERE support_mfa.user_id = v_user_id
    AND support_mfa.pending_secret_ciphertext IS NOT NULL
    AND support_mfa.pending_key_version IS NOT NULL
    AND support_mfa.pending_generation IS NOT NULL
  FOR UPDATE;

  IF v_secret IS DISTINCT FROM p_verified_secret
    OR v_generation IS NULL
    OR NOT (
      (v_purpose = 'enroll' AND v_status = 'pending')
      OR (
        v_purpose = 'recovery_enroll'
        AND v_status = 'recovery_pending'
      )
    )
  THEN
    RETURN;
  END IF;

  IF v_purpose = 'recovery_enroll' THEN
    IF v_recovery_code_id IS NULL OR v_active_generation IS NULL THEN
      RETURN;
    END IF;
    PERFORM 1
    FROM public.support_mfa_recovery_code AS recovery_code
    WHERE recovery_code.id = v_recovery_code_id
      AND recovery_code.user_id = v_user_id
      AND recovery_code.generation = v_active_generation
      AND recovery_code.activated_at IS NOT NULL
      AND recovery_code.used_at IS NULL
    FOR UPDATE;
    IF NOT FOUND THEN
      RETURN;
    END IF;
  END IF;

  UPDATE public.support_mfa AS support_mfa
  SET
    active_secret_ciphertext = support_mfa.pending_secret_ciphertext,
    active_key_version = support_mfa.pending_key_version,
    pending_secret_ciphertext = NULL,
    pending_key_version = NULL,
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

  IF v_recovery_code_id IS NOT NULL THEN
    UPDATE public.support_mfa_recovery_code AS recovery_code
    SET used_at = v_now
    WHERE recovery_code.id = v_recovery_code_id;
  END IF;

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
  WHERE app_user.id = v_user_id;
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
  p_encryption_keyring JSONB,
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
    OR pg_catalog.jsonb_typeof(p_encryption_keyring) IS DISTINCT FROM 'object'
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
  JOIN public.app_user AS app_user
    ON app_user.id = challenge.user_id
  WHERE challenge.token_hash = p_token_hash
    AND challenge.purpose = 'verify'
    AND challenge.consumed_at IS NULL
    AND challenge.expires_at > v_now
    AND challenge.failed_attempts < 5
    AND app_user.status IN ('invited', 'active')
    AND (app_user.is_developer OR app_user.is_administrator)
  FOR UPDATE OF challenge, app_user;

  IF v_user_id IS NULL THEN
    RETURN;
  END IF;

  SELECT
    support_mfa.last_used_counter,
    public.decrypt_support_mfa_secret(
      support_mfa.active_secret_ciphertext,
      support_mfa.active_key_version,
      p_encryption_keyring
    )
  INTO v_last_used_counter, v_secret
  FROM public.support_mfa AS support_mfa
  WHERE support_mfa.user_id = v_user_id
    AND support_mfa.status = 'active'
    AND support_mfa.active_secret_ciphertext IS NOT NULL
    AND support_mfa.active_key_version IS NOT NULL
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
  WHERE app_user.id = v_user_id;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


RECOVER_MFA_CHALLENGE_SQL = """
CREATE OR REPLACE FUNCTION public.recover_auth_mfa_challenge(
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
    AND app_user.status IN ('invited', 'active')
    AND (app_user.is_developer OR app_user.is_administrator)
    AND support_mfa.status IN ('active', 'recovery_pending')
    AND support_mfa.active_generation IS NOT NULL
  FOR UPDATE OF challenge, app_user, support_mfa;

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

  UPDATE public.support_mfa AS support_mfa
  SET
    status = 'recovery_pending',
    pending_secret_ciphertext = NULL,
    pending_key_version = NULL,
    pending_generation = NULL,
    updated_at = v_now
  WHERE support_mfa.user_id = v_user_id;

  DELETE FROM public.support_mfa_recovery_code AS recovery_code
  WHERE recovery_code.user_id = v_user_id
    AND recovery_code.activated_at IS NULL;

  UPDATE public.auth_mfa_challenge AS challenge
  SET
    purpose = 'recovery_enroll',
    recovery_code_id = v_recovery_code_id,
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
  p_encryption_keyring JSONB
) RETURNS TABLE(
  email TEXT,
  secret TEXT,
  last_used_counter BIGINT
) AS $$
  SELECT
    app_user.email,
    public.decrypt_support_mfa_secret(
      support_mfa.active_secret_ciphertext,
      support_mfa.active_key_version,
      p_encryption_keyring
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
    AND support_mfa.active_key_version IS NOT NULL
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
  p_encryption_keyring JSONB
) RETURNS TIMESTAMPTZ AS $$
DECLARE
  v_secret TEXT;
  v_last_used_counter BIGINT;
  v_session_mfa_verified_at TIMESTAMPTZ;
  v_now TIMESTAMPTZ;
BEGIN
  IF p_user_id IS DISTINCT FROM public.current_app_user_id()
    OR p_counter < 0
    OR p_verified_secret !~ '^[A-Z2-7]{32}$'
    OR pg_catalog.jsonb_typeof(p_encryption_keyring) IS DISTINCT FROM 'object'
  THEN
    RETURN NULL;
  END IF;

  SELECT
    public.decrypt_support_mfa_secret(
      support_mfa.active_secret_ciphertext,
      support_mfa.active_key_version,
      p_encryption_keyring
    ),
    support_mfa.last_used_counter,
    auth_session.mfa_verified_at
  INTO v_secret, v_last_used_counter, v_session_mfa_verified_at
  FROM public.support_mfa AS support_mfa
  JOIN public.app_user AS app_user
    ON app_user.id = support_mfa.user_id
  JOIN public.session AS auth_session
    ON auth_session.user_id = support_mfa.user_id
  WHERE support_mfa.user_id = p_user_id
    AND support_mfa.status = 'active'
    AND support_mfa.active_secret_ciphertext IS NOT NULL
    AND support_mfa.active_key_version IS NOT NULL
    AND app_user.status IN ('invited', 'active')
    AND (app_user.is_developer OR app_user.is_administrator)
    AND auth_session.id = p_session_id
    AND auth_session.revoked_at IS NULL
    AND auth_session.expires_at > pg_catalog.now()
  FOR UPDATE OF support_mfa, app_user, auth_session;

  IF v_secret IS DISTINCT FROM p_verified_secret
    OR p_counter <= COALESCE(v_last_used_counter, (-1)::BIGINT)
    OR v_session_mfa_verified_at IS NULL
  THEN
    RETURN NULL;
  END IF;

  v_now := GREATEST(
    pg_catalog.clock_timestamp(),
    v_session_mfa_verified_at + INTERVAL '1 second'
  );
  UPDATE public.support_mfa AS support_mfa
  SET
    last_used_counter = p_counter,
    updated_at = v_now
  WHERE support_mfa.user_id = p_user_id;

  -- Step-up is deliberately not persisted on the refresh session. Otherwise
  -- a refresh token stolen before this check could inherit elevated assurance.
  UPDATE public.session AS auth_session
  SET last_used_at = v_now
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


ROTATE_MFA_ENCRYPTION_SQL = """
CREATE FUNCTION public.rotate_support_mfa_encryption(
  p_from_version SMALLINT,
  p_to_version SMALLINT,
  p_to_key TEXT,
  p_encryption_keyring JSONB
) RETURNS INTEGER AS $$
DECLARE
  v_user_id UUID;
  v_count INTEGER := 0;
BEGIN
  IF p_from_version IS NULL
    OR p_to_version IS NULL
    OR p_from_version < 1
    OR p_to_version < 1
    OR p_from_version = p_to_version
    OR p_to_key !~ '^[0-9a-f]{64}$'
    OR p_encryption_keyring ->> p_to_version::TEXT IS DISTINCT FROM p_to_key
    OR p_encryption_keyring ->> p_from_version::TEXT !~ '^[0-9a-f]{64}$'
  THEN
    RAISE EXCEPTION 'Invalid MFA key-rotation payload'
      USING ERRCODE = '22023';
  END IF;

  FOR v_user_id IN
    SELECT support_mfa.user_id
    FROM public.support_mfa AS support_mfa
    WHERE support_mfa.active_key_version = p_from_version
       OR support_mfa.pending_key_version = p_from_version
    ORDER BY support_mfa.user_id
    FOR UPDATE
  LOOP
    UPDATE public.support_mfa AS support_mfa
    SET
      active_secret_ciphertext = CASE
        WHEN support_mfa.active_key_version = p_from_version
        THEN public.pgp_sym_encrypt(
          public.decrypt_support_mfa_secret(
            support_mfa.active_secret_ciphertext,
            support_mfa.active_key_version,
            p_encryption_keyring
          ),
          p_to_key,
          'cipher-algo=aes256, compress-algo=0'
        )
        ELSE support_mfa.active_secret_ciphertext
      END,
      active_key_version = CASE
        WHEN support_mfa.active_key_version = p_from_version
        THEN p_to_version
        ELSE support_mfa.active_key_version
      END,
      pending_secret_ciphertext = CASE
        WHEN support_mfa.pending_key_version = p_from_version
        THEN public.pgp_sym_encrypt(
          public.decrypt_support_mfa_secret(
            support_mfa.pending_secret_ciphertext,
            support_mfa.pending_key_version,
            p_encryption_keyring
          ),
          p_to_key,
          'cipher-algo=aes256, compress-algo=0'
        )
        ELSE support_mfa.pending_secret_ciphertext
      END,
      pending_key_version = CASE
        WHEN support_mfa.pending_key_version = p_from_version
        THEN p_to_version
        ELSE support_mfa.pending_key_version
      END,
      updated_at = pg_catalog.now()
    WHERE support_mfa.user_id = v_user_id;

    INSERT INTO public.audit_log (
      user_id,
      action,
      table_name,
      record_id,
      metadata
    ) VALUES (
      v_user_id,
      'UPDATE',
      'support_mfa',
      v_user_id,
      pg_catalog.jsonb_build_object(
        'event', 'mfa_encryption_rotated',
        'from_version', p_from_version,
        'to_version', p_to_version
      )
    );
    v_count := v_count + 1;
  END LOOP;

  RETURN v_count;
END;
$$ LANGUAGE plpgsql
VOLATILE
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


def _secure_function(signature: str, *, app_access: bool = False) -> None:
    op.execute(f"ALTER FUNCTION {signature} OWNER TO aurum_support")
    op.execute(f"REVOKE ALL PRIVILEGES ON FUNCTION {signature} FROM PUBLIC, aurum_app")
    grantees = "aurum_support, aurum_app" if app_access else "aurum_support"
    op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {grantees}")


def upgrade() -> None:
    old_signatures = (
        "public.lookup_auth_mfa_challenge(TEXT, TEXT)",
        "public.stage_auth_mfa_enrollment(TEXT, TEXT, TEXT, TEXT[])",
        "public.complete_auth_mfa_enrollment("
        "TEXT, BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TIMESTAMP WITH TIME ZONE)",
        "public.complete_auth_mfa_verification("
        "TEXT, BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TIMESTAMP WITH TIME ZONE)",
        "public.lookup_support_mfa_for_step_up(UUID, UUID, TEXT)",
        "public.complete_support_mfa_step_up(UUID, UUID, BIGINT, TEXT, TEXT)",
    )
    for signature in old_signatures:
        op.execute(f"DROP FUNCTION {signature}")

    op.execute(
        "ALTER TABLE public.support_mfa "
        "ADD COLUMN active_key_version SMALLINT CHECK (active_key_version > 0), "
        "ADD COLUMN pending_key_version SMALLINT CHECK (pending_key_version > 0)"
    )
    op.execute("""
        UPDATE public.support_mfa
        SET
          active_key_version = CASE
            WHEN active_secret_ciphertext IS NOT NULL THEN key_version
            ELSE NULL
          END,
          pending_key_version = CASE
            WHEN pending_secret_ciphertext IS NOT NULL THEN key_version
            ELSE NULL
          END
        """)
    op.execute("ALTER TABLE public.support_mfa DROP COLUMN key_version")

    op.execute(
        "ALTER TABLE public.auth_mfa_challenge "
        "ADD COLUMN recovery_code_id UUID "
        "REFERENCES public.support_mfa_recovery_code(id) ON DELETE SET NULL"
    )
    op.execute("""
        ALTER TABLE public.support_mfa
        ADD CONSTRAINT ck_support_mfa_state_consistency
        CHECK (
          (
            status = 'active'
            AND active_secret_ciphertext IS NOT NULL
            AND active_key_version IS NOT NULL
            AND active_generation IS NOT NULL
            AND pending_secret_ciphertext IS NULL
            AND pending_key_version IS NULL
            AND pending_generation IS NULL
          )
          OR (
            status = 'pending'
            AND active_secret_ciphertext IS NULL
            AND active_key_version IS NULL
            AND active_generation IS NULL
            AND pending_secret_ciphertext IS NOT NULL
            AND pending_key_version IS NOT NULL
            AND pending_generation IS NOT NULL
          )
          OR (
            status = 'recovery_pending'
            AND active_secret_ciphertext IS NOT NULL
            AND active_key_version IS NOT NULL
            AND active_generation IS NOT NULL
            AND (
              (
                pending_secret_ciphertext IS NULL
                AND pending_key_version IS NULL
                AND pending_generation IS NULL
              )
              OR (
                pending_secret_ciphertext IS NOT NULL
                AND pending_key_version IS NOT NULL
                AND pending_generation IS NOT NULL
              )
            )
          )
        )
        """)

    functions = (
        (
            DECRYPT_MFA_SECRET_SQL,
            "public.decrypt_support_mfa_secret(BYTEA, SMALLINT, JSONB)",
        ),
        (
            LOOKUP_MFA_CHALLENGE_SQL,
            "public.lookup_auth_mfa_challenge(TEXT, JSONB, BOOLEAN)",
        ),
        (
            STAGE_MFA_ENROLLMENT_SQL,
            "public.stage_auth_mfa_enrollment(" "TEXT, TEXT, SMALLINT, TEXT, TEXT[])",
        ),
        (
            COMPLETE_MFA_ENROLLMENT_SQL,
            "public.complete_auth_mfa_enrollment("
            "TEXT, BIGINT, TEXT, JSONB, TEXT, TEXT, TEXT, TIMESTAMP WITH TIME ZONE)",
        ),
        (
            COMPLETE_MFA_VERIFICATION_SQL,
            "public.complete_auth_mfa_verification("
            "TEXT, BIGINT, TEXT, JSONB, TEXT, TEXT, TEXT, TIMESTAMP WITH TIME ZONE)",
        ),
        (
            LOOKUP_STEP_UP_SQL,
            "public.lookup_support_mfa_for_step_up(UUID, UUID, JSONB)",
        ),
        (
            COMPLETE_STEP_UP_SQL,
            "public.complete_support_mfa_step_up(" "UUID, UUID, BIGINT, TEXT, JSONB)",
        ),
        (
            ROTATE_MFA_ENCRYPTION_SQL,
            "public.rotate_support_mfa_encryption(" "SMALLINT, SMALLINT, TEXT, JSONB)",
        ),
    )
    for statement, signature in functions:
        op.execute(statement)
        _secure_function(signature)

    op.execute(RECOVER_MFA_CHALLENGE_SQL)
    _secure_function("public.recover_auth_mfa_challenge(TEXT, TEXT, TEXT, TEXT)")
    _secure_function("public.record_auth_mfa_failure(TEXT, TEXT, TEXT)")


def downgrade() -> None:
    signatures = (
        "public.rotate_support_mfa_encryption(SMALLINT, SMALLINT, TEXT, JSONB)",
        "public.complete_support_mfa_step_up(UUID, UUID, BIGINT, TEXT, JSONB)",
        "public.lookup_support_mfa_for_step_up(UUID, UUID, JSONB)",
        "public.recover_auth_mfa_challenge(TEXT, TEXT, TEXT, TEXT)",
        "public.complete_auth_mfa_verification("
        "TEXT, BIGINT, TEXT, JSONB, TEXT, TEXT, TEXT, TIMESTAMP WITH TIME ZONE)",
        "public.complete_auth_mfa_enrollment("
        "TEXT, BIGINT, TEXT, JSONB, TEXT, TEXT, TEXT, TIMESTAMP WITH TIME ZONE)",
        "public.stage_auth_mfa_enrollment(TEXT, TEXT, SMALLINT, TEXT, TEXT[])",
        "public.lookup_auth_mfa_challenge(TEXT, JSONB, BOOLEAN)",
        "public.decrypt_support_mfa_secret(BYTEA, SMALLINT, JSONB)",
    )
    for signature in signatures:
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")

    op.execute("ALTER TABLE public.support_mfa " "DROP CONSTRAINT ck_support_mfa_state_consistency")
    op.execute("""
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM public.support_mfa
            WHERE active_key_version IS NOT NULL
              AND pending_key_version IS NOT NULL
              AND active_key_version <> pending_key_version
          ) THEN
            RAISE EXCEPTION
              'Cannot downgrade while active and pending MFA keys use different versions';
          END IF;
        END
        $$
        """)
    op.execute("ALTER TABLE public.support_mfa " "ADD COLUMN key_version SMALLINT")
    op.execute("""
        UPDATE public.support_mfa
        SET key_version = COALESCE(active_key_version, pending_key_version, 1)
        """)
    op.execute(
        "ALTER TABLE public.support_mfa "
        "ALTER COLUMN key_version SET DEFAULT 1, "
        "ALTER COLUMN key_version SET NOT NULL, "
        "ADD CONSTRAINT ck_support_mfa_key_version CHECK (key_version > 0)"
    )
    op.execute(
        "ALTER TABLE public.support_mfa "
        "DROP COLUMN active_key_version, "
        "DROP COLUMN pending_key_version"
    )
    op.execute("ALTER TABLE public.auth_mfa_challenge " "DROP COLUMN recovery_code_id")

    source_0056 = _load_revision_module("0056_add_support_mfa.py")
    old_functions = (
        (
            source_0056.LOOKUP_MFA_CHALLENGE_SQL,
            "public.lookup_auth_mfa_challenge(TEXT, TEXT)",
        ),
        (
            source_0056.STAGE_MFA_ENROLLMENT_SQL,
            "public.stage_auth_mfa_enrollment(TEXT, TEXT, TEXT, TEXT[])",
        ),
        (
            source_0056.COMPLETE_MFA_ENROLLMENT_SQL,
            "public.complete_auth_mfa_enrollment("
            "TEXT, BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TIMESTAMP WITH TIME ZONE)",
        ),
        (
            source_0056.COMPLETE_MFA_VERIFICATION_SQL,
            "public.complete_auth_mfa_verification("
            "TEXT, BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TIMESTAMP WITH TIME ZONE)",
        ),
        (
            source_0056.LOOKUP_STEP_UP_SQL,
            "public.lookup_support_mfa_for_step_up(UUID, UUID, TEXT)",
        ),
        (
            source_0056.COMPLETE_STEP_UP_SQL,
            "public.complete_support_mfa_step_up(" "UUID, UUID, BIGINT, TEXT, TEXT)",
        ),
    )
    for statement, signature in old_functions:
        op.execute(statement)
        _secure_function(signature, app_access=True)

    op.execute(source_0056.RECOVER_MFA_CHALLENGE_SQL)
    _secure_function(
        "public.recover_auth_mfa_challenge(TEXT, TEXT, TEXT, TEXT)",
        app_access=True,
    )
    _secure_function(
        "public.record_auth_mfa_failure(TEXT, TEXT, TEXT)",
        app_access=True,
    )
