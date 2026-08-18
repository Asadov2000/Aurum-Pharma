"""harden billing payment verification

Revision ID: 0102
Revises: 0101
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0102"
down_revision: str | None = "0101"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


READ_PAYMENT_REVIEW_DETAIL_SQL = r"""
CREATE FUNCTION public.read_platform_billing_payment_review(
  p_actor_user_id UUID,
  p_actor_session_id UUID,
  p_tenant_id UUID,
  p_review_id UUID
)
RETURNS JSONB AS $$
DECLARE
  v_review public.billing_payment_review%ROWTYPE;
  v_invoice_number TEXT;
  v_tenant_name TEXT;
BEGIN
  PERFORM public.assert_and_lock_platform_recent_capability(
    p_actor_user_id,
    p_actor_session_id,
    'platform.billing.payment.approve'
  );
  IF p_tenant_id IS NULL OR p_review_id IS NULL THEN
    RAISE EXCEPTION 'Invalid billing payment review detail request'
      USING ERRCODE = '22023';
  END IF;

  SELECT review.*
  INTO v_review
  FROM public.billing_payment_review AS review
  WHERE review.tenant_id = p_tenant_id
    AND review.id = p_review_id
    AND review.status = 'pending_approval';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Billing payment review was not found' USING ERRCODE = 'P0002';
  END IF;
  IF v_review.reviewed_by = p_actor_user_id THEN
    RAISE EXCEPTION 'Independent payment approval is required' USING ERRCODE = '42501';
  END IF;

  SELECT invoice.invoice_number, tenant.name
  INTO v_invoice_number, v_tenant_name
  FROM public.billing_invoice AS invoice
  JOIN public.tenant AS tenant ON tenant.id = invoice.tenant_id
  WHERE invoice.tenant_id = v_review.tenant_id
    AND invoice.id = v_review.target_invoice_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Billing invoice was not found' USING ERRCODE = 'P0002';
  END IF;

  INSERT INTO public.audit_log (
    tenant_id, user_id, action, table_name, record_id, metadata, created_at
  ) VALUES (
    p_tenant_id,
    p_actor_user_id,
    'VIEW',
    'billing_payment_review_sensitive_detail',
    p_review_id,
    pg_catalog.jsonb_strip_nulls(pg_catalog.jsonb_build_object(
      'request_id', NULLIF(
        pg_catalog.current_setting('app.request_id', true), ''
      ),
      'status', v_review.status,
      'row_version', v_review.row_version
    )),
    pg_catalog.statement_timestamp()
  );

  RETURN pg_catalog.jsonb_build_object(
    'review_id', v_review.id,
    'tenant_id', v_review.tenant_id,
    'tenant_name', v_tenant_name,
    'target_invoice_id', v_review.target_invoice_id,
    'invoice_number', v_invoice_number,
    'amount', v_review.amount::TEXT,
    'currency', v_review.currency,
    'paid_at', v_review.paid_at,
    'recipient_account_key', v_review.recipient_account_key,
    'external_reference', v_review.external_reference,
    'status', v_review.status,
    'row_version', v_review.row_version,
    'created_at', v_review.created_at,
    'is_own_review', false
  );
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""

READ_PAYMENT_REVIEW_DETAIL_SIGNATURE = (
    "public.read_platform_billing_payment_review(UUID, UUID, UUID, UUID)"
)


def _secure_payment_review_detail_function() -> None:
    op.execute(f"ALTER FUNCTION {READ_PAYMENT_REVIEW_DETAIL_SIGNATURE} OWNER TO aurum_schema_owner")
    op.execute(
        f"REVOKE ALL PRIVILEGES ON FUNCTION {READ_PAYMENT_REVIEW_DETAIL_SIGNATURE} "
        "FROM PUBLIC, aurum_app, aurum_support, aurum_mailer, "
        "aurum_edge_cash_executor, aurum_edge_cash_owner"
    )
    op.execute(f"GRANT EXECUTE ON FUNCTION {READ_PAYMENT_REVIEW_DETAIL_SIGNATURE} TO aurum_support")


def upgrade() -> None:
    op.execute("""
        ALTER TABLE public.billing_payment_submission
          DROP CONSTRAINT ck_billing_submission_reference,
          DROP CONSTRAINT ck_billing_submission_account_key,
          ADD CONSTRAINT ck_billing_submission_reference CHECK (
            external_reference ~ '^[A-Z0-9]{8,128}$'
          ),
          ADD CONSTRAINT ck_billing_submission_account_key CHECK (
            recipient_account_key IS NULL
            OR recipient_account_key = 'aurum_tjs_primary'
          )
        """)
    op.execute("""
        ALTER TABLE public.billing_payment_review
          DROP CONSTRAINT ck_billing_review_reference,
          DROP CONSTRAINT ck_billing_review_account_key,
          ADD CONSTRAINT ck_billing_review_reference CHECK (
            external_reference ~ '^[A-Z0-9]{8,128}$'
          ),
          ADD CONSTRAINT ck_billing_review_account_key CHECK (
            recipient_account_key = 'aurum_tjs_primary'
          )
        """)
    op.execute("""
        ALTER TABLE public.billing_payment
          ADD CONSTRAINT ck_billing_payment_reference CHECK (
            external_reference ~ '^[A-Z0-9]{8,128}$'
          ),
          ADD CONSTRAINT ck_billing_payment_account_key CHECK (
            recipient_account_key = 'aurum_tjs_primary'
          )
        """)
    op.execute(READ_PAYMENT_REVIEW_DETAIL_SQL)
    _secure_payment_review_detail_function()


def downgrade() -> None:
    op.execute(f"DROP FUNCTION IF EXISTS {READ_PAYMENT_REVIEW_DETAIL_SIGNATURE}")
    op.execute("""
        ALTER TABLE public.billing_payment
          DROP CONSTRAINT ck_billing_payment_reference,
          DROP CONSTRAINT ck_billing_payment_account_key
        """)
    op.execute("""
        ALTER TABLE public.billing_payment_review
          DROP CONSTRAINT ck_billing_review_reference,
          DROP CONSTRAINT ck_billing_review_account_key,
          ADD CONSTRAINT ck_billing_review_reference CHECK (
            external_reference ~ '^[A-Z0-9]{4,128}$'
          ),
          ADD CONSTRAINT ck_billing_review_account_key CHECK (
            recipient_account_key ~ '^[a-z0-9][a-z0-9_.:-]{2,63}$'
          )
        """)
    op.execute("""
        ALTER TABLE public.billing_payment_submission
          DROP CONSTRAINT ck_billing_submission_reference,
          DROP CONSTRAINT ck_billing_submission_account_key,
          ADD CONSTRAINT ck_billing_submission_reference CHECK (
            external_reference ~ '^[A-Z0-9]{4,128}$'
          ),
          ADD CONSTRAINT ck_billing_submission_account_key CHECK (
            recipient_account_key IS NULL
            OR recipient_account_key ~ '^[a-z0-9][a-z0-9_.:-]{2,63}$'
          )
        """)
