"""add encrypted authentication email outbox

Revision ID: 0111
Revises: 0110
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0111"
down_revision: str | None = "0110"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ISSUE = (
    "public.issue_auth_email_code("
    "TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, SMALLINT, TEXT)"
)
PREVIOUS_ISSUE = "public.issue_auth_email_code(TEXT, TEXT, TEXT, TEXT, TEXT)"
CLAIM = "public.claim_auth_login_email(JSONB, INTEGER)"
COMPLETE = "public.complete_auth_login_email(UUID, UUID, TEXT, TEXT)"


ISSUE_SQL = """
CREATE FUNCTION public.issue_auth_email_code(
  p_email TEXT,
  p_code_hash TEXT,
  p_code_salt TEXT,
  p_ip_address TEXT,
  p_user_agent TEXT,
  p_plaintext_code TEXT,
  p_key_version SMALLINT,
  p_encryption_key TEXT
) RETURNS TEXT AS $$
DECLARE
  v_email TEXT;
  v_ip INET;
  v_now TIMESTAMPTZ;
  v_code_id UUID;
BEGIN
  v_email := pg_catalog.lower(pg_catalog.btrim(p_email));
  v_ip := p_ip_address::INET;
  v_now := pg_catalog.now();

  IF NULLIF(v_email, '') IS NULL
    OR pg_catalog.char_length(v_email) > 320
    OR p_code_hash !~ '^[0-9a-f]{64}$'
    OR p_code_salt !~ '^[0-9a-f]{32}$'
    OR p_plaintext_code !~ '^[0-9]{6}$'
    OR p_key_version < 1
    OR pg_catalog.char_length(p_encryption_key) < 32
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
  ) RETURNING id INTO v_code_id;

  IF EXISTS (
    SELECT 1
    FROM public.app_user AS account
    WHERE account.email_lower = v_email
      AND account.status IN ('invited', 'active')
  ) THEN
    UPDATE public.auth_email_outbox AS delivery
    SET status = 'cancelled', payload_ciphertext = NULL,
        claim_token = NULL, claimed_at = NULL, updated_at = v_now
    FROM public.email_code AS previous_code
    WHERE delivery.email_code_id = previous_code.id
      AND previous_code.email_lower = v_email
      AND delivery.status IN ('pending', 'processing');

    INSERT INTO public.auth_email_outbox (
      email_code_id,
      payload_ciphertext,
      encryption_key_version,
      available_at,
      created_at,
      updated_at
    ) VALUES (
      v_code_id,
      public.pgp_sym_encrypt(
        p_plaintext_code,
        p_encryption_key,
        'cipher-algo=aes256,compress-algo=0'
      ),
      p_key_version,
      v_now,
      v_now,
      v_now
    );
  END IF;

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
SET search_path = pg_catalog, public, pg_temp
"""


CLAIM_SQL = """
CREATE FUNCTION public.claim_auth_login_email(
  p_encryption_keyring JSONB,
  p_lease_seconds INTEGER
)
RETURNS TABLE(
  outbox_id UUID,
  claim_token UUID,
  recipient_email TEXT,
  login_code TEXT,
  code_expires_at TIMESTAMPTZ,
  attempt_count SMALLINT
) AS $$
DECLARE
  v_id UUID;
  v_claim_token UUID;
  v_recipient TEXT;
  v_ciphertext BYTEA;
  v_key_version SMALLINT;
  v_expires_at TIMESTAMPTZ;
  v_attempt_count SMALLINT;
  v_now TIMESTAMPTZ := pg_catalog.statement_timestamp();
BEGIN
  IF p_lease_seconds NOT BETWEEN 60 AND 1800
    OR pg_catalog.jsonb_typeof(p_encryption_keyring) <> 'object'
  THEN
    RAISE EXCEPTION 'Invalid authentication email claim request'
      USING ERRCODE = '22023';
  END IF;

  UPDATE public.auth_email_outbox AS delivery
  SET status = CASE WHEN delivery.attempt_count >= 5 THEN 'failed' ELSE 'pending' END,
      payload_ciphertext = CASE
        WHEN delivery.attempt_count >= 5 THEN NULL ELSE delivery.payload_ciphertext END,
      available_at = v_now,
      claim_token = NULL,
      claimed_at = NULL,
      last_error_code = 'worker_lease_expired',
      updated_at = v_now
  WHERE delivery.status = 'processing'
    AND delivery.claimed_at < v_now - pg_catalog.make_interval(secs => p_lease_seconds);

  UPDATE public.auth_email_outbox AS delivery
  SET status = 'cancelled', payload_ciphertext = NULL,
      claim_token = NULL, claimed_at = NULL, updated_at = v_now
  WHERE delivery.status IN ('pending', 'processing')
    AND NOT EXISTS (
      SELECT 1
      FROM public.email_code AS code
      WHERE code.id = delivery.email_code_id
        AND code.purpose = 'login'
        AND code.used_at IS NULL
        AND code.expires_at > v_now
    );

  IF EXISTS (
    SELECT 1
    FROM public.auth_email_outbox AS delivery
    JOIN public.email_code AS code ON code.id = delivery.email_code_id
    WHERE delivery.status = 'pending'
      AND delivery.available_at <= v_now
      AND delivery.attempt_count < 5
      AND code.used_at IS NULL
      AND code.expires_at > v_now
      AND NOT (p_encryption_keyring ? delivery.encryption_key_version::TEXT)
  ) THEN
    RAISE EXCEPTION 'Authentication email encryption key version is unavailable'
      USING ERRCODE = '22023';
  END IF;

  SELECT
    delivery.id,
    code.email_lower,
    delivery.payload_ciphertext,
    delivery.encryption_key_version,
    code.expires_at,
    (delivery.attempt_count + 1)::SMALLINT
  INTO
    v_id, v_recipient, v_ciphertext, v_key_version, v_expires_at, v_attempt_count
  FROM public.auth_email_outbox AS delivery
  JOIN public.email_code AS code ON code.id = delivery.email_code_id
  WHERE delivery.status = 'pending'
    AND delivery.available_at <= v_now
    AND delivery.attempt_count < 5
    AND delivery.payload_ciphertext IS NOT NULL
    AND code.purpose = 'login'
    AND code.used_at IS NULL
    AND code.expires_at > v_now
    AND p_encryption_keyring ? delivery.encryption_key_version::TEXT
  ORDER BY delivery.available_at, delivery.created_at, delivery.id
  FOR UPDATE OF delivery SKIP LOCKED
  LIMIT 1;

  IF v_id IS NULL THEN
    RETURN;
  END IF;

  v_claim_token := public.gen_random_uuid();
  UPDATE public.auth_email_outbox
  SET status = 'processing', attempt_count = v_attempt_count,
      claimed_at = v_now, claim_token = v_claim_token,
      last_error_code = NULL, updated_at = v_now
  WHERE id = v_id;

  RETURN QUERY SELECT
    v_id,
    v_claim_token,
    v_recipient,
    public.pgp_sym_decrypt(v_ciphertext, p_encryption_keyring ->> v_key_version::TEXT),
    v_expires_at,
    v_attempt_count;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
"""


COMPLETE_SQL = """
CREATE FUNCTION public.complete_auth_login_email(
  p_outbox_id UUID,
  p_claim_token UUID,
  p_outcome TEXT,
  p_error_code TEXT
)
RETURNS TEXT AS $$
DECLARE
  v_attempt_count SMALLINT;
  v_status TEXT;
  v_now TIMESTAMPTZ := pg_catalog.statement_timestamp();
  v_jitter_seconds INTEGER;
BEGIN
  IF p_outcome NOT IN ('sent', 'transient_failure', 'permanent_failure')
    OR (p_outcome = 'sent' AND p_error_code IS NOT NULL)
    OR (
      p_outcome <> 'sent'
      AND COALESCE(p_error_code, '') !~ '^[a-z0-9_]{1,64}$'
    )
  THEN
    RAISE EXCEPTION 'Invalid authentication email completion request'
      USING ERRCODE = '22023';
  END IF;

  SELECT delivery.attempt_count
  INTO v_attempt_count
  FROM public.auth_email_outbox AS delivery
  WHERE delivery.id = p_outbox_id
    AND delivery.status = 'processing'
    AND delivery.claim_token = p_claim_token
  FOR UPDATE;

  IF NOT FOUND THEN
    RETURN NULL;
  END IF;

  IF p_outcome = 'sent' THEN
    v_status := 'sent';
    UPDATE public.auth_email_outbox
    SET status = v_status, payload_ciphertext = NULL,
        sent_at = v_now, claimed_at = NULL, claim_token = NULL,
        last_error_code = NULL, updated_at = v_now
    WHERE id = p_outbox_id;
  ELSIF p_outcome = 'permanent_failure' OR v_attempt_count >= 5 THEN
    v_status := 'failed';
    UPDATE public.auth_email_outbox
    SET status = v_status, payload_ciphertext = NULL,
        claimed_at = NULL, claim_token = NULL,
        last_error_code = p_error_code, updated_at = v_now
    WHERE id = p_outbox_id;
  ELSE
    v_status := 'pending';
    v_jitter_seconds := pg_catalog.get_byte(pg_catalog.uuid_send(p_outbox_id), 0) % 30;
    UPDATE public.auth_email_outbox
    SET status = v_status,
        available_at = v_now
          + CASE v_attempt_count
              WHEN 1 THEN INTERVAL '1 minute'
              WHEN 2 THEN INTERVAL '2 minutes'
              WHEN 3 THEN INTERVAL '4 minutes'
              ELSE INTERVAL '8 minutes'
            END
          + pg_catalog.make_interval(secs => v_jitter_seconds),
        claimed_at = NULL, claim_token = NULL,
        last_error_code = p_error_code, updated_at = v_now
    WHERE id = p_outbox_id;
  END IF;

  RETURN v_status;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
"""


PREVIOUS_ISSUE_SQL = """
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
    email_lower, code_hash, code_salt, purpose, ip_address, expires_at
  ) VALUES (
    v_email, p_code_hash, p_code_salt, 'login', v_ip, v_now + INTERVAL '10 minutes'
  );

  INSERT INTO public.login_attempt (
    email_lower, ip_address, user_agent, outcome
  ) VALUES (
    v_email, v_ip, pg_catalog.left(p_user_agent, 1024), 'code_requested'
  );

  RETURN 'created';
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


def _secure_function(signature: str, grantee: str) -> None:
    op.execute(f"ALTER FUNCTION {signature} OWNER TO aurum_schema_owner")
    op.execute(f"ALTER FUNCTION {signature} SECURITY DEFINER")
    op.execute(
        f"REVOKE ALL PRIVILEGES ON FUNCTION {signature} "
        "FROM PUBLIC, aurum_app, aurum_support, aurum_mailer"
    )
    op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {grantee}")


def upgrade() -> None:
    op.execute("""
        CREATE TABLE public.auth_email_outbox (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          email_code_id UUID NOT NULL UNIQUE
            REFERENCES public.email_code(id) ON DELETE CASCADE,
          message_type TEXT NOT NULL DEFAULT 'login_code'
            CHECK (message_type = 'login_code'),
          payload_ciphertext BYTEA,
          encryption_key_version SMALLINT NOT NULL CHECK (encryption_key_version >= 1),
          status TEXT NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending', 'processing', 'sent', 'failed', 'cancelled')),
          attempt_count SMALLINT NOT NULL DEFAULT 0 CHECK (attempt_count BETWEEN 0 AND 5),
          available_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          claimed_at TIMESTAMPTZ,
          claim_token UUID,
          sent_at TIMESTAMPTZ,
          last_error_code TEXT CHECK (
            last_error_code IS NULL OR last_error_code ~ '^[a-z0-9_]{1,64}$'
          ),
          created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT ck_auth_email_outbox_claim CHECK (
            (status = 'processing' AND claimed_at IS NOT NULL AND claim_token IS NOT NULL)
            OR
            (status <> 'processing' AND claimed_at IS NULL AND claim_token IS NULL)
          ),
          CONSTRAINT ck_auth_email_outbox_payload CHECK (
            (status IN ('pending', 'processing') AND payload_ciphertext IS NOT NULL)
            OR
            (status IN ('sent', 'failed', 'cancelled') AND payload_ciphertext IS NULL)
          ),
          CONSTRAINT ck_auth_email_outbox_sent CHECK (
            (status = 'sent' AND sent_at IS NOT NULL)
            OR
            (status <> 'sent' AND sent_at IS NULL)
          )
        )
        """)
    op.execute(
        "CREATE INDEX ix_auth_email_outbox_claim "
        "ON public.auth_email_outbox(status, available_at, created_at, id) "
        "WHERE status IN ('pending', 'processing')"
    )
    op.execute(
        "REVOKE ALL PRIVILEGES ON TABLE public.auth_email_outbox "
        "FROM PUBLIC, aurum_app, aurum_support, aurum_mailer"
    )

    op.execute(f"REVOKE ALL PRIVILEGES ON FUNCTION {PREVIOUS_ISSUE} FROM PUBLIC")
    op.execute(f"DROP FUNCTION {PREVIOUS_ISSUE}")
    op.execute(ISSUE_SQL)
    _secure_function(ISSUE, "aurum_app, aurum_support")
    op.execute(CLAIM_SQL)
    _secure_function(CLAIM, "aurum_mailer")
    op.execute(COMPLETE_SQL)
    _secure_function(COMPLETE, "aurum_mailer")


def downgrade() -> None:
    op.execute(f"DROP FUNCTION {COMPLETE}")
    op.execute(f"DROP FUNCTION {CLAIM}")
    op.execute(f"DROP FUNCTION {ISSUE}")
    op.execute("DROP TABLE public.auth_email_outbox")
    op.execute(PREVIOUS_ISSUE_SQL)
    _secure_function(PREVIOUS_ISSUE, "aurum_app, aurum_support")
