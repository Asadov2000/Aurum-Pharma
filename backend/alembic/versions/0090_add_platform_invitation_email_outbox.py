"""add encrypted platform invitation email outbox

Revision ID: 0090
Revises: 0089
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0090"
down_revision: str | None = "0089"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ENQUEUE_SQL = """
CREATE FUNCTION public.enqueue_platform_invitation_email(
  p_actor_user_id UUID,
  p_actor_session_id UUID,
  p_target_user_id UUID,
  p_expected_version INTEGER,
  p_activation_token TEXT,
  p_token_hash TEXT,
  p_key_version SMALLINT,
  p_encryption_key TEXT
)
RETURNS UUID AS $$
DECLARE
  v_id UUID;
  v_status TEXT;
  v_version INTEGER;
  v_token_hash TEXT;
  v_now TIMESTAMPTZ := pg_catalog.statement_timestamp();
BEGIN
  IF NOT public.platform_actor_has_recent_capability(
    p_actor_user_id,
    p_actor_session_id,
    'platform.accounts.manage'
  ) THEN
    RAISE EXCEPTION 'Recent platform account management capability required'
      USING ERRCODE = '42501';
  END IF;

  IF p_expected_version < 1
    OR p_key_version < 1
    OR pg_catalog.char_length(p_activation_token) < 32
    OR p_token_hash !~ '^[0-9a-f]{64}$'
    OR pg_catalog.char_length(p_encryption_key) < 32
  THEN
    RAISE EXCEPTION 'Invalid platform invitation delivery data'
      USING ERRCODE = '22023';
  END IF;

  SELECT profile.status, profile.version, profile.invitation_token_hash
  INTO v_status, v_version, v_token_hash
  FROM public.platform_staff_account AS profile
  JOIN public.app_user AS account ON account.id = profile.user_id
  WHERE profile.user_id = p_target_user_id
    AND account.status = 'invited';

  IF NOT FOUND THEN
    RAISE EXCEPTION 'Platform staff account not found'
      USING ERRCODE = 'P0002';
  END IF;
  IF v_status <> 'invited'
    OR v_version <> p_expected_version
    OR v_token_hash IS DISTINCT FROM p_token_hash
  THEN
    RAISE EXCEPTION 'Platform invitation changed before delivery was queued'
      USING ERRCODE = '55000';
  END IF;

  UPDATE public.platform_email_outbox
  SET status = 'cancelled', payload_ciphertext = NULL,
      claim_token = NULL, claimed_at = NULL, updated_at = v_now
  WHERE account_user_id = p_target_user_id
    AND status IN ('pending', 'processing');

  INSERT INTO public.platform_email_outbox (
    account_user_id, account_version, payload_ciphertext,
    encryption_key_version, available_at, created_at, updated_at
  ) VALUES (
    p_target_user_id,
    p_expected_version,
    public.pgp_sym_encrypt(
      p_activation_token,
      p_encryption_key,
      'cipher-algo=aes256,compress-algo=0'
    ),
    p_key_version,
    v_now,
    v_now,
    v_now
  )
  RETURNING id INTO v_id;

  RETURN v_id;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY INVOKER
SET search_path = pg_catalog, public, pg_temp
"""


CLAIM_SQL = """
CREATE FUNCTION public.claim_platform_invitation_email(
  p_encryption_keyring JSONB,
  p_lease_seconds INTEGER
)
RETURNS TABLE(
  outbox_id UUID,
  claim_token UUID,
  recipient_email TEXT,
  activation_token TEXT,
  invitation_expires_at TIMESTAMPTZ,
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
    RAISE EXCEPTION 'Invalid platform email claim request'
      USING ERRCODE = '22023';
  END IF;

  UPDATE public.platform_email_outbox AS delivery
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

  UPDATE public.platform_email_outbox AS delivery
  SET status = 'cancelled', payload_ciphertext = NULL,
      claim_token = NULL, claimed_at = NULL, updated_at = v_now
  WHERE delivery.status IN ('pending', 'processing')
    AND NOT EXISTS (
      SELECT 1
      FROM public.platform_staff_account AS profile
      JOIN public.app_user AS account ON account.id = profile.user_id
      WHERE profile.user_id = delivery.account_user_id
        AND profile.status = 'invited'
        AND account.status = 'invited'
        AND profile.version = delivery.account_version
        AND profile.invitation_expires_at > v_now
    );

  IF EXISTS (
    SELECT 1
    FROM public.platform_email_outbox AS delivery
    JOIN public.platform_staff_account AS profile
      ON profile.user_id = delivery.account_user_id
     AND profile.version = delivery.account_version
     AND profile.status = 'invited'
    JOIN public.app_user AS account
      ON account.id = profile.user_id
     AND account.status = 'invited'
    WHERE delivery.status = 'pending'
      AND delivery.available_at <= v_now
      AND delivery.attempt_count < 5
      AND NOT (p_encryption_keyring ? delivery.encryption_key_version::TEXT)
  ) THEN
    RAISE EXCEPTION 'Platform email encryption key version is unavailable'
      USING ERRCODE = '22023';
  END IF;

  SELECT
    delivery.id,
    account.email,
    delivery.payload_ciphertext,
    delivery.encryption_key_version,
    profile.invitation_expires_at,
    (delivery.attempt_count + 1)::SMALLINT
  INTO
    v_id, v_recipient, v_ciphertext, v_key_version, v_expires_at, v_attempt_count
  FROM public.platform_email_outbox AS delivery
  JOIN public.platform_staff_account AS profile
    ON profile.user_id = delivery.account_user_id
   AND profile.version = delivery.account_version
   AND profile.status = 'invited'
  JOIN public.app_user AS account
    ON account.id = profile.user_id
   AND account.status = 'invited'
  WHERE delivery.status = 'pending'
    AND delivery.available_at <= v_now
    AND delivery.attempt_count < 5
    AND delivery.payload_ciphertext IS NOT NULL
    AND p_encryption_keyring ? delivery.encryption_key_version::TEXT
  ORDER BY delivery.available_at, delivery.created_at, delivery.id
  FOR UPDATE OF delivery SKIP LOCKED
  LIMIT 1;

  IF v_id IS NULL THEN
    RETURN;
  END IF;

  v_claim_token := public.gen_random_uuid();
  UPDATE public.platform_email_outbox
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
SECURITY INVOKER
SET search_path = pg_catalog, public, pg_temp
"""


COMPLETE_SQL = """
CREATE FUNCTION public.complete_platform_invitation_email(
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
    OR (
      p_outcome = 'sent' AND p_error_code IS NOT NULL
    )
    OR (
      p_outcome <> 'sent'
      AND COALESCE(p_error_code, '') !~ '^[a-z0-9_]{1,64}$'
    )
  THEN
    RAISE EXCEPTION 'Invalid platform email completion request'
      USING ERRCODE = '22023';
  END IF;

  SELECT delivery.attempt_count
  INTO v_attempt_count
  FROM public.platform_email_outbox AS delivery
  WHERE delivery.id = p_outbox_id
    AND delivery.status = 'processing'
    AND delivery.claim_token = p_claim_token
  FOR UPDATE;

  IF NOT FOUND THEN
    RETURN NULL;
  END IF;

  IF p_outcome = 'sent' THEN
    v_status := 'sent';
    UPDATE public.platform_email_outbox
    SET status = v_status, payload_ciphertext = NULL,
        sent_at = v_now, claimed_at = NULL, claim_token = NULL,
        last_error_code = NULL, updated_at = v_now
    WHERE id = p_outbox_id;
  ELSIF p_outcome = 'permanent_failure' OR v_attempt_count >= 5 THEN
    v_status := 'failed';
    UPDATE public.platform_email_outbox
    SET status = v_status, payload_ciphertext = NULL,
        claimed_at = NULL, claim_token = NULL,
        last_error_code = p_error_code, updated_at = v_now
    WHERE id = p_outbox_id;
  ELSE
    v_status := 'pending';
    v_jitter_seconds := pg_catalog.get_byte(pg_catalog.uuid_send(p_outbox_id), 0) % 30;
    UPDATE public.platform_email_outbox
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
SECURITY INVOKER
SET search_path = pg_catalog, public, pg_temp
"""


def _secure_function(signature: str, *, grant_to: str | None = None) -> None:
    op.execute(
        f"REVOKE ALL PRIVILEGES ON FUNCTION {signature} "
        "FROM PUBLIC, aurum_app, aurum_support"
    )
    if grant_to is not None:
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {grant_to}")


def upgrade() -> None:
    op.execute("""
        CREATE TABLE public.platform_email_outbox (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          account_user_id UUID NOT NULL
            REFERENCES public.platform_staff_account(user_id) ON DELETE RESTRICT,
          account_version INTEGER NOT NULL CHECK (account_version >= 1),
          message_type TEXT NOT NULL DEFAULT 'invitation'
            CHECK (message_type = 'invitation'),
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
          CONSTRAINT uq_platform_email_outbox_version
            UNIQUE (account_user_id, account_version, message_type),
          CONSTRAINT ck_platform_email_outbox_claim CHECK (
            (status = 'processing' AND claimed_at IS NOT NULL AND claim_token IS NOT NULL)
            OR
            (status <> 'processing' AND claimed_at IS NULL AND claim_token IS NULL)
          ),
          CONSTRAINT ck_platform_email_outbox_payload CHECK (
            (status IN ('pending', 'processing') AND payload_ciphertext IS NOT NULL)
            OR
            (status IN ('sent', 'failed', 'cancelled') AND payload_ciphertext IS NULL)
          ),
          CONSTRAINT ck_platform_email_outbox_sent CHECK (
            (status = 'sent' AND sent_at IS NOT NULL)
            OR
            (status <> 'sent' AND sent_at IS NULL)
          )
        )
        """)
    op.execute(
        "CREATE INDEX ix_platform_email_outbox_claim "
        "ON public.platform_email_outbox(status, available_at, created_at, id) "
        "WHERE status IN ('pending', 'processing')"
    )
    op.execute(
        "REVOKE ALL PRIVILEGES ON TABLE public.platform_email_outbox "
        "FROM PUBLIC, aurum_app, aurum_support"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON TABLE public.platform_email_outbox "
        "TO aurum_support"
    )

    op.execute(ENQUEUE_SQL)
    _secure_function(
        "public.enqueue_platform_invitation_email("
        "UUID, UUID, UUID, INTEGER, TEXT, TEXT, SMALLINT, TEXT)",
        grant_to="aurum_support",
    )
    op.execute(CLAIM_SQL)
    _secure_function(
        "public.claim_platform_invitation_email(JSONB, INTEGER)",
        grant_to="aurum_support",
    )
    op.execute(COMPLETE_SQL)
    _secure_function(
        "public.complete_platform_invitation_email(UUID, UUID, TEXT, TEXT)",
        grant_to="aurum_support",
    )


def downgrade() -> None:
    op.execute(
        "DROP FUNCTION public.complete_platform_invitation_email(UUID, UUID, TEXT, TEXT)"
    )
    op.execute("DROP FUNCTION public.claim_platform_invitation_email(JSONB, INTEGER)")
    op.execute(
        "DROP FUNCTION IF EXISTS public.enqueue_platform_invitation_email("
        "UUID, UUID, UUID, INTEGER, TEXT, TEXT, SMALLINT, TEXT)"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS public.enqueue_platform_invitation_email("
        "UUID, UUID, UUID, INTEGER, TEXT, SMALLINT, TEXT)"
    )
    op.execute("DROP TABLE public.platform_email_outbox")
