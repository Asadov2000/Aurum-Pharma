"""separate auth request throttling from credential lockout

Revision ID: 0085
Revises: 0084
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0085"
down_revision: str | None = "0084"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ISSUE_AUTH_EMAIL_CODE_SQL = """
CREATE OR REPLACE FUNCTION public.issue_auth_email_code(
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
      'rate_limited',
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
      'rate_limited',
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


ENFORCE_AUTH_LOGIN_GUARD_SQL = """
CREATE OR REPLACE FUNCTION public.enforce_auth_login_guard(
  p_email TEXT,
  p_ip_address TEXT
) RETURNS BOOLEAN AS $$
DECLARE
  v_email TEXT;
  v_ip INET;
  v_now TIMESTAMPTZ;
  v_last_success TIMESTAMPTZ;
  v_failures BIGINT;
BEGIN
  v_email := pg_catalog.lower(pg_catalog.btrim(p_email));
  v_ip := p_ip_address::INET;
  v_now := pg_catalog.now();

  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(v_email || '|' || pg_catalog.host(v_ip), 3702)
  );

  SELECT pg_catalog.max(attempt.created_at)
  INTO v_last_success
  FROM public.login_attempt AS attempt
  WHERE attempt.email_lower = v_email
    AND attempt.ip_address = v_ip
    AND attempt.outcome = 'success'
    AND attempt.created_at >= v_now - INTERVAL '15 minutes';

  IF EXISTS (
    SELECT 1
    FROM public.login_attempt AS attempt
    WHERE attempt.email_lower = v_email
      AND attempt.ip_address = v_ip
      AND attempt.outcome = 'blocked'
      AND attempt.metadata_json ->> 'reason' = 'too_many_failures'
      AND attempt.created_at >= v_now - INTERVAL '15 minutes'
      AND (v_last_success IS NULL OR attempt.created_at > v_last_success)
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
    AND attempt.created_at >= v_now - INTERVAL '15 minutes'
    AND (v_last_success IS NULL OR attempt.created_at > v_last_success);

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


PREVIOUS_ISSUE_AUTH_EMAIL_CODE_SQL = ISSUE_AUTH_EMAIL_CODE_SQL.replace(
    "'rate_limited'", "'blocked'"
)


PREVIOUS_ENFORCE_AUTH_LOGIN_GUARD_SQL = """
CREATE OR REPLACE FUNCTION public.enforce_auth_login_guard(
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


def upgrade() -> None:
    op.drop_constraint("login_attempt_outcome_check", "login_attempt", type_="check")
    op.create_check_constraint(
        "ck_login_attempt_outcome",
        "login_attempt",
        "outcome IN ('code_requested','code_failed','code_expired',"
        "'password_failed','totp_failed','success','rate_limited','blocked')",
    )
    op.execute(ISSUE_AUTH_EMAIL_CODE_SQL)
    op.execute(ENFORCE_AUTH_LOGIN_GUARD_SQL)


def downgrade() -> None:
    op.execute(
        "UPDATE public.login_attempt SET outcome = 'blocked' " "WHERE outcome = 'rate_limited'"
    )
    op.drop_constraint("ck_login_attempt_outcome", "login_attempt", type_="check")
    op.create_check_constraint(
        "login_attempt_outcome_check",
        "login_attempt",
        "outcome IN ('code_requested','code_failed','code_expired',"
        "'password_failed','totp_failed','success','blocked')",
    )
    op.execute(PREVIOUS_ISSUE_AUTH_EMAIL_CODE_SQL)
    op.execute(PREVIOUS_ENFORCE_AUTH_LOGIN_GUARD_SQL)
