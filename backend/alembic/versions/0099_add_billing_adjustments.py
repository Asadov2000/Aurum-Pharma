"""add protected billing adjustments

Revision ID: 0099
Revises: 0098
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0099"
down_revision: str | None = "0098"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


AUDIT_FINANCIAL_ROW_SQL = r"""
CREATE OR REPLACE FUNCTION public.trg_audit_billing_financial_row()
RETURNS TRIGGER AS $$
DECLARE
  v_row RECORD;
  v_row_json JSONB;
  v_tenant_id UUID;
  v_record_id UUID;
  v_actor_user_id UUID;
  v_operation_id UUID;
BEGIN
  v_row := CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
  v_row_json := pg_catalog.to_jsonb(v_row);
  v_tenant_id := v_row.tenant_id;
  v_record_id := v_row.id;
  v_actor_user_id := NULLIF(current_setting('app.user_id', true), '')::UUID;
  IF TG_OP = 'UPDATE' THEN
    v_operation_id := COALESCE(
      NULLIF(v_row_json ->> 'decision_operation_id', '')::UUID,
      NULLIF(v_row_json ->> 'rejected_operation_id', '')::UUID,
      NULLIF(v_row_json ->> 'approved_operation_id', '')::UUID,
      NULLIF(v_row_json ->> 'operation_id', '')::UUID
    );
  ELSE
    v_operation_id := COALESCE(
      NULLIF(v_row_json ->> 'operation_id', '')::UUID,
      NULLIF(v_row_json ->> 'review_operation_id', '')::UUID
    );
  END IF;

  INSERT INTO public.audit_log (
    tenant_id, user_id, action, table_name, record_id, metadata, created_at
  ) VALUES (
    v_tenant_id,
    v_actor_user_id,
    TG_OP,
    TG_TABLE_NAME,
    v_record_id,
    pg_catalog.jsonb_strip_nulls(pg_catalog.jsonb_build_object(
      'operation_id', v_operation_id
    )),
    pg_catalog.statement_timestamp()
  );
  RETURN v_row;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


RESTORE_AUDIT_FINANCIAL_ROW_SQL = r"""
CREATE OR REPLACE FUNCTION public.trg_audit_billing_financial_row()
RETURNS TRIGGER AS $$
DECLARE
  v_row RECORD;
  v_tenant_id UUID;
  v_record_id UUID;
  v_actor_user_id UUID;
  v_operation_id UUID;
BEGIN
  v_row := CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
  v_tenant_id := v_row.tenant_id;
  v_record_id := v_row.id;
  v_actor_user_id := NULLIF(current_setting('app.user_id', true), '')::UUID;
  BEGIN
    v_operation_id := v_row.operation_id;
  EXCEPTION WHEN undefined_column THEN
    v_operation_id := NULL;
  END;

  INSERT INTO public.audit_log (
    tenant_id, user_id, action, table_name, record_id, metadata, created_at
  ) VALUES (
    v_tenant_id,
    v_actor_user_id,
    TG_OP,
    TG_TABLE_NAME,
    v_record_id,
    pg_catalog.jsonb_strip_nulls(pg_catalog.jsonb_build_object(
      'operation_id', v_operation_id
    )),
    pg_catalog.statement_timestamp()
  );
  RETURN v_row;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


REJECT_PAYMENT_REVIEW_SQL = r"""
CREATE FUNCTION public.reject_billing_bank_payment_review(
  p_actor_user_id UUID,
  p_actor_session_id UUID,
  p_operation_id UUID,
  p_request_hash TEXT,
  p_tenant_id UUID,
  p_review_id UUID,
  p_expected_row_version INTEGER,
  p_reason_code TEXT,
  p_reason_note TEXT
)
RETURNS TABLE(result JSONB, applied BOOLEAN) AS $$
DECLARE
  v_mfa_at TIMESTAMPTZ;
  v_payload JSONB;
  v_existing public.billing_financial_operation%ROWTYPE;
  v_review public.billing_payment_review%ROWTYPE;
  v_status TEXT;
  v_created_at TIMESTAMPTZ;
  v_result JSONB;
BEGIN
  v_mfa_at := public.assert_and_lock_platform_recent_capability(
    p_actor_user_id, p_actor_session_id, 'platform.billing.payment.approve'
  );
  p_reason_note := NULLIF(pg_catalog.btrim(p_reason_note), '');
  IF p_operation_id IS NULL
    OR p_request_hash !~ '^[0-9a-f]{64}$'
    OR p_tenant_id IS NULL OR p_review_id IS NULL
    OR p_expected_row_version IS NULL OR p_expected_row_version < 1
    OR p_reason_code IS NULL OR p_reason_code NOT IN (
      'bank_payment_not_found','amount_mismatch','date_mismatch','duplicate',
      'wrong_tenant_or_invoice','other'
    )
    OR (p_reason_note IS NOT NULL AND pg_catalog.length(p_reason_note) > 500)
    OR (p_reason_code = 'other' AND COALESCE(pg_catalog.length(p_reason_note), 0) < 10)
  THEN
    RAISE EXCEPTION 'Invalid bank payment rejection request' USING ERRCODE = '22023';
  END IF;
  v_payload := pg_catalog.jsonb_strip_nulls(pg_catalog.jsonb_build_object(
    'tenant_id', p_tenant_id,
    'review_id', p_review_id,
    'expected_row_version', p_expected_row_version,
    'reason_code', p_reason_code,
    'reason_note', p_reason_note
  ));
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(p_operation_id::TEXT, 9501)
  );
  SELECT * INTO v_existing
  FROM public.billing_financial_operation AS financial_operation
  WHERE financial_operation.operation_id = p_operation_id;
  IF FOUND THEN
    IF v_existing.operation_type <> 'payment_review_rejected'
      OR v_existing.actor_user_id <> p_actor_user_id
      OR v_existing.request_hash <> p_request_hash
      OR v_existing.request_payload <> v_payload
    THEN
      RAISE EXCEPTION 'Billing operation id was reused' USING ERRCODE = '23505';
    END IF;
    RETURN QUERY SELECT v_existing.result_snapshot, false;
    RETURN;
  END IF;
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(p_tenant_id::TEXT, 9701)
  );
  SELECT * INTO v_review
  FROM public.billing_payment_review AS review
  WHERE review.tenant_id = p_tenant_id AND review.id = p_review_id
  FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Bank payment review was not found' USING ERRCODE = 'P0002';
  END IF;
  IF v_review.status <> 'pending_approval'
    OR v_review.row_version <> p_expected_row_version
  THEN
    RAISE EXCEPTION 'Bank payment review changed concurrently' USING ERRCODE = '40001';
  END IF;
  IF v_review.reviewed_by = p_actor_user_id THEN
    RAISE EXCEPTION 'Bank payment requires an independent decision'
      USING ERRCODE = '22023';
  END IF;

  v_created_at := pg_catalog.statement_timestamp();
  v_status := CASE WHEN p_reason_code = 'duplicate' THEN 'duplicate' ELSE 'rejected' END;
  v_result := pg_catalog.jsonb_build_object(
    'review_id', v_review.id,
    'tenant_id', p_tenant_id,
    'target_invoice_id', v_review.target_invoice_id,
    'amount', v_review.amount::TEXT,
    'currency', v_review.currency,
    'paid_at', v_review.paid_at,
    'status', v_status,
    'row_version', v_review.row_version + 1,
    'created_at', v_review.created_at,
    'decided_at', v_created_at,
    'reason_code', p_reason_code
  );
  INSERT INTO public.billing_financial_operation (
    operation_id, operation_type, tenant_id, actor_user_id, actor_session_id,
    mfa_verified_at, request_hash, request_payload, result_snapshot, created_at
  ) VALUES (
    p_operation_id, 'payment_review_rejected', p_tenant_id, p_actor_user_id,
    p_actor_session_id, v_mfa_at, p_request_hash, v_payload, v_result, v_created_at
  );
  UPDATE public.billing_payment_review
  SET status = v_status, row_version = row_version + 1,
      rejected_by = p_actor_user_id, rejected_session_id = p_actor_session_id,
      rejected_operation_id = p_operation_id, rejected_at = v_created_at,
      rejection_reason_code = p_reason_code, rejection_note = p_reason_note,
      updated_at = v_created_at
  WHERE tenant_id = p_tenant_id AND id = v_review.id;
  INSERT INTO public.billing_outbox_event (
    tenant_id, operation_id, event_type, aggregate_type, aggregate_id,
    payload, created_at
  ) VALUES (
    p_tenant_id, p_operation_id, 'billing.payment.review_rejected',
    'billing_payment_review', v_review.id,
    pg_catalog.jsonb_build_object(
      'review_id', v_review.id, 'tenant_id', p_tenant_id, 'status', v_status
    ),
    v_created_at
  );
  RETURN QUERY SELECT v_result, true;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


CREATE_ADJUSTMENT_REQUEST_SQL = r"""
CREATE FUNCTION public.create_billing_payment_adjustment_request(
  p_actor_user_id UUID,
  p_actor_session_id UUID,
  p_operation_id UUID,
  p_request_hash TEXT,
  p_tenant_id UUID,
  p_payment_id UUID,
  p_adjustment_kind TEXT,
  p_amount NUMERIC,
  p_reason_code TEXT,
  p_reason_note TEXT,
  p_refunded_at TIMESTAMPTZ,
  p_refund_reference TEXT
)
RETURNS TABLE(result JSONB, applied BOOLEAN) AS $$
DECLARE
  v_mfa_at TIMESTAMPTZ;
  v_payload JSONB;
  v_existing public.billing_financial_operation%ROWTYPE;
  v_payment public.billing_payment%ROWTYPE;
  v_adjustment_id UUID;
  v_reversible NUMERIC(14,2);
  v_created_at TIMESTAMPTZ;
  v_result JSONB;
BEGIN
  v_mfa_at := public.assert_and_lock_platform_recent_capability(
    p_actor_user_id, p_actor_session_id, 'platform.billing.adjustment.create'
  );
  p_reason_note := NULLIF(pg_catalog.btrim(p_reason_note), '');
  p_refund_reference := NULLIF(pg_catalog.btrim(p_refund_reference), '');
  IF p_operation_id IS NULL
    OR p_request_hash !~ '^[0-9a-f]{64}$'
    OR p_tenant_id IS NULL OR p_payment_id IS NULL
    OR p_adjustment_kind IS NULL
    OR p_adjustment_kind NOT IN ('correction','bank_refund')
    OR p_amount IS NULL OR p_amount <= 0 OR p_amount > 999999999999.99
    OR p_amount <> pg_catalog.round(p_amount, 2)
    OR p_reason_code IS NULL OR p_reason_code NOT IN (
      'payment_entered_in_error','amount_correction','bank_refund_completed',
      'contract_resolution','other'
    )
    OR COALESCE(pg_catalog.length(p_reason_note), 0) < 10
    OR pg_catalog.length(p_reason_note) > 500
    OR (
      p_adjustment_kind = 'correction'
      AND (
        p_reason_code NOT IN ('payment_entered_in_error','amount_correction','other')
        OR p_refunded_at IS NOT NULL OR p_refund_reference IS NOT NULL
      )
    )
    OR (
      p_adjustment_kind = 'bank_refund'
      AND (
        p_reason_code NOT IN ('bank_refund_completed','contract_resolution','other')
        OR p_refunded_at IS NULL
        OR p_refunded_at > pg_catalog.statement_timestamp() + INTERVAL '5 minutes'
        OR p_refunded_at < pg_catalog.statement_timestamp() - INTERVAL '366 days'
        OR p_refund_reference !~ '^[A-Z0-9]{4,128}$'
      )
    )
  THEN
    RAISE EXCEPTION 'Invalid billing payment adjustment request' USING ERRCODE = '22023';
  END IF;
  v_payload := pg_catalog.jsonb_strip_nulls(pg_catalog.jsonb_build_object(
    'tenant_id', p_tenant_id,
    'payment_id', p_payment_id,
    'adjustment_kind', p_adjustment_kind,
    'amount', pg_catalog.round(p_amount, 2)::TEXT,
    'reason_code', p_reason_code,
    'reason_note', p_reason_note,
    'refunded_at', p_refunded_at
  ));
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(p_operation_id::TEXT, 9501)
  );
  SELECT * INTO v_existing
  FROM public.billing_financial_operation AS financial_operation
  WHERE financial_operation.operation_id = p_operation_id;
  IF FOUND THEN
    IF v_existing.operation_type <> 'payment_adjustment_requested'
      OR v_existing.actor_user_id <> p_actor_user_id
      OR v_existing.request_hash <> p_request_hash
      OR v_existing.request_payload <> v_payload
    THEN
      RAISE EXCEPTION 'Billing operation id was reused' USING ERRCODE = '23505';
    END IF;
    RETURN QUERY SELECT v_existing.result_snapshot, false;
    RETURN;
  END IF;
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(p_tenant_id::TEXT, 9701)
  );
  SELECT * INTO v_payment
  FROM public.billing_payment AS payment
  WHERE payment.tenant_id = p_tenant_id AND payment.id = p_payment_id
  FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Billing payment was not found' USING ERRCODE = 'P0002';
  END IF;
  IF EXISTS (
    SELECT 1
    FROM public.billing_payment_adjustment_request AS adjustment_request
    WHERE adjustment_request.tenant_id = p_tenant_id
      AND adjustment_request.payment_id = p_payment_id
      AND adjustment_request.status = 'pending_approval'
  ) THEN
    RAISE EXCEPTION 'Billing payment already has a pending adjustment'
      USING ERRCODE = '23505';
  END IF;
  SELECT v_payment.amount - COALESCE((
    SELECT sum(adjustment.amount)
    FROM public.billing_payment_adjustment AS adjustment
    WHERE adjustment.tenant_id = p_tenant_id
      AND adjustment.payment_id = p_payment_id
  ), 0)
  INTO v_reversible;
  IF p_amount > v_reversible
    OR (p_adjustment_kind = 'correction' AND p_amount <> v_reversible)
  THEN
    RAISE EXCEPTION 'Billing adjustment exceeds reversible payment amount'
      USING ERRCODE = '22023';
  END IF;
  IF p_adjustment_kind = 'correction' AND EXISTS (
    SELECT 1
    FROM public.billing_payment_adjustment AS adjustment
    WHERE adjustment.tenant_id = p_tenant_id
      AND adjustment.payment_id = p_payment_id
      AND adjustment.adjustment_kind = 'bank_refund'
  ) THEN
    RAISE EXCEPTION 'Billing payment correction is not allowed after a bank refund'
      USING ERRCODE = '22023';
  END IF;
  IF p_adjustment_kind = 'bank_refund' AND EXISTS (
    SELECT 1
    FROM public.billing_payment_adjustment_request AS adjustment_request
    WHERE adjustment_request.refund_recipient_account_key = v_payment.recipient_account_key
      AND adjustment_request.refund_reference = p_refund_reference
  ) THEN
    RAISE EXCEPTION 'Bank refund reference was already registered'
      USING ERRCODE = '23505';
  END IF;

  v_adjustment_id := public.gen_random_uuid();
  v_created_at := pg_catalog.statement_timestamp();
  v_result := pg_catalog.jsonb_build_object(
    'adjustment_id', v_adjustment_id,
    'tenant_id', p_tenant_id,
    'payment_id', p_payment_id,
    'adjustment_kind', p_adjustment_kind,
    'amount', pg_catalog.round(p_amount, 2)::TEXT,
    'currency', 'TJS',
    'reason_code', p_reason_code,
    'reason_note', p_reason_note,
    'refunded_at', p_refunded_at,
    'status', 'pending_approval',
    'row_version', 1,
    'created_at', v_created_at
  );
  INSERT INTO public.billing_financial_operation (
    operation_id, operation_type, tenant_id, actor_user_id, actor_session_id,
    mfa_verified_at, request_hash, request_payload, result_snapshot, created_at
  ) VALUES (
    p_operation_id, 'payment_adjustment_requested', p_tenant_id, p_actor_user_id,
    p_actor_session_id, v_mfa_at, p_request_hash, v_payload, v_result, v_created_at
  );
  INSERT INTO public.billing_payment_adjustment_request (
    id, tenant_id, payment_id, operation_id, adjustment_kind, amount, currency,
    reason_code, reason_note, refunded_at, refund_recipient_account_key,
    refund_reference, status, row_version, created_by, created_session_id,
    create_mfa_verified_at, created_at, updated_at
  ) VALUES (
    v_adjustment_id, p_tenant_id, p_payment_id, p_operation_id,
    p_adjustment_kind, pg_catalog.round(p_amount, 2), 'TJS', p_reason_code,
    p_reason_note, p_refunded_at,
    CASE WHEN p_adjustment_kind = 'bank_refund'
      THEN v_payment.recipient_account_key ELSE NULL END,
    p_refund_reference, 'pending_approval', 1, p_actor_user_id,
    p_actor_session_id, v_mfa_at, v_created_at, v_created_at
  );
  INSERT INTO public.billing_outbox_event (
    tenant_id, operation_id, event_type, aggregate_type, aggregate_id,
    payload, created_at
  ) VALUES (
    p_tenant_id, p_operation_id, 'billing.payment.adjustment_requested',
    'billing_payment_adjustment_request', v_adjustment_id,
    pg_catalog.jsonb_build_object(
      'adjustment_id', v_adjustment_id, 'tenant_id', p_tenant_id,
      'payment_id', p_payment_id, 'adjustment_kind', p_adjustment_kind
    ),
    v_created_at
  );
  RETURN QUERY SELECT v_result, true;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


LIST_ADJUSTMENT_QUEUE_SQL = r"""
CREATE FUNCTION public.list_platform_billing_payment_adjustments(
  p_actor_user_id UUID,
  p_actor_session_id UUID,
  p_tenant_id UUID,
  p_limit INTEGER,
  p_offset INTEGER
)
RETURNS JSONB AS $$
DECLARE
  v_result JSONB;
BEGIN
  PERFORM public.assert_and_lock_platform_recent_capability(
    p_actor_user_id, p_actor_session_id, 'platform.billing.adjustment.approve'
  );
  IF p_tenant_id IS NULL
    OR p_limit < 1 OR p_limit > 100 OR p_offset < 0
    OR NOT EXISTS (SELECT 1 FROM public.tenant AS tenant WHERE tenant.id = p_tenant_id)
  THEN
    RAISE EXCEPTION 'Invalid billing adjustment queue request' USING ERRCODE = '22023';
  END IF;
  SELECT pg_catalog.jsonb_build_object(
    'items', COALESCE((
      SELECT pg_catalog.jsonb_agg(queue.item ORDER BY queue.created_at, queue.adjustment_id)
      FROM (
        SELECT
          adjustment_request.created_at,
          adjustment_request.id AS adjustment_id,
          pg_catalog.jsonb_build_object(
            'adjustment_id', adjustment_request.id,
            'tenant_id', adjustment_request.tenant_id,
            'tenant_name', tenant.name,
            'payment_id', adjustment_request.payment_id,
            'payment_amount', payment.amount::TEXT,
            'payment_paid_at', payment.paid_at,
            'adjustment_kind', adjustment_request.adjustment_kind,
            'amount', adjustment_request.amount::TEXT,
            'currency', adjustment_request.currency,
            'reason_code', adjustment_request.reason_code,
            'reason_note', adjustment_request.reason_note,
            'refunded_at', adjustment_request.refunded_at,
            'status', adjustment_request.status,
            'row_version', adjustment_request.row_version,
            'created_at', adjustment_request.created_at,
            'is_own_request', adjustment_request.created_by = p_actor_user_id
          ) AS item
        FROM public.billing_payment_adjustment_request AS adjustment_request
        JOIN public.tenant AS tenant ON tenant.id = adjustment_request.tenant_id
        JOIN public.billing_payment AS payment
          ON payment.tenant_id = adjustment_request.tenant_id
         AND payment.id = adjustment_request.payment_id
        WHERE adjustment_request.tenant_id = p_tenant_id
          AND adjustment_request.status = 'pending_approval'
        ORDER BY adjustment_request.created_at, adjustment_request.id
        LIMIT p_limit OFFSET p_offset
      ) AS queue
    ), '[]'::JSONB),
    'total', (
      SELECT pg_catalog.count(*)
      FROM public.billing_payment_adjustment_request AS adjustment_request
      WHERE adjustment_request.tenant_id = p_tenant_id
        AND adjustment_request.status = 'pending_approval'
    )
  ) INTO v_result;
  RETURN v_result;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


APPROVE_ADJUSTMENT_SQL = r"""
CREATE FUNCTION public.approve_billing_payment_adjustment(
  p_actor_user_id UUID,
  p_actor_session_id UUID,
  p_operation_id UUID,
  p_request_hash TEXT,
  p_tenant_id UUID,
  p_adjustment_id UUID,
  p_expected_row_version INTEGER
)
RETURNS TABLE(result JSONB, applied BOOLEAN) AS $$
DECLARE
  v_mfa_at TIMESTAMPTZ;
  v_payload JSONB;
  v_existing public.billing_financial_operation%ROWTYPE;
  v_request public.billing_payment_adjustment_request%ROWTYPE;
  v_payment public.billing_payment%ROWTYPE;
  v_credit public.billing_tenant_credit%ROWTYPE;
  v_candidate RECORD;
  v_remaining NUMERIC(14,2);
  v_available NUMERIC(14,2);
  v_reverse NUMERIC(14,2);
  v_credit_reversed NUMERIC(14,2) := 0;
  v_allocation_reversed NUMERIC(14,2) := 0;
  v_total_adjusted NUMERIC(14,2);
  v_reversible NUMERIC(14,2);
  v_blocking_outstanding NUMERIC(14,2);
  v_adjustment_record_id UUID;
  v_entry_id UUID;
  v_entry_sequence INTEGER := 1;
  v_allocation_order INTEGER := 1;
  v_created_at TIMESTAMPTZ;
  v_result JSONB;
BEGIN
  v_mfa_at := public.assert_and_lock_platform_recent_capability(
    p_actor_user_id, p_actor_session_id, 'platform.billing.adjustment.approve'
  );
  IF p_operation_id IS NULL
    OR p_request_hash !~ '^[0-9a-f]{64}$'
    OR p_tenant_id IS NULL OR p_adjustment_id IS NULL
    OR p_expected_row_version IS NULL OR p_expected_row_version < 1
  THEN
    RAISE EXCEPTION 'Invalid billing adjustment approval request' USING ERRCODE = '22023';
  END IF;
  v_payload := pg_catalog.jsonb_build_object(
    'tenant_id', p_tenant_id,
    'adjustment_id', p_adjustment_id,
    'expected_row_version', p_expected_row_version
  );
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(p_operation_id::TEXT, 9501)
  );
  SELECT * INTO v_existing
  FROM public.billing_financial_operation AS financial_operation
  WHERE financial_operation.operation_id = p_operation_id;
  IF FOUND THEN
    IF v_existing.operation_type <> 'payment_adjustment_approved'
      OR v_existing.actor_user_id <> p_actor_user_id
      OR v_existing.request_hash <> p_request_hash
      OR v_existing.request_payload <> v_payload
    THEN
      RAISE EXCEPTION 'Billing operation id was reused' USING ERRCODE = '23505';
    END IF;
    RETURN QUERY SELECT v_existing.result_snapshot, false;
    RETURN;
  END IF;
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(p_tenant_id::TEXT, 9701)
  );
  SELECT * INTO v_request
  FROM public.billing_payment_adjustment_request AS adjustment_request
  WHERE adjustment_request.tenant_id = p_tenant_id
    AND adjustment_request.id = p_adjustment_id
  FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Billing payment adjustment was not found' USING ERRCODE = 'P0002';
  END IF;
  IF v_request.status <> 'pending_approval'
    OR v_request.row_version <> p_expected_row_version
  THEN
    RAISE EXCEPTION 'Billing payment adjustment changed concurrently'
      USING ERRCODE = '40001';
  END IF;
  IF v_request.created_by = p_actor_user_id THEN
    RAISE EXCEPTION 'Billing adjustment requires an independent approver'
      USING ERRCODE = '22023';
  END IF;
  SELECT * INTO v_payment
  FROM public.billing_payment AS payment
  WHERE payment.tenant_id = p_tenant_id AND payment.id = v_request.payment_id
  FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Billing payment was not found' USING ERRCODE = 'P0002';
  END IF;
  SELECT v_payment.amount - COALESCE((
    SELECT sum(adjustment.amount)
    FROM public.billing_payment_adjustment AS adjustment
    WHERE adjustment.tenant_id = p_tenant_id
      AND adjustment.payment_id = v_payment.id
  ), 0)
  INTO v_available;
  IF v_request.amount > v_available
    OR (v_request.adjustment_kind = 'correction' AND v_request.amount <> v_available)
  THEN
    RAISE EXCEPTION 'Billing adjustment exceeds reversible payment amount'
      USING ERRCODE = '40001';
  END IF;
  IF v_request.adjustment_kind = 'correction' AND EXISTS (
    SELECT 1
    FROM public.billing_payment_adjustment AS adjustment
    WHERE adjustment.tenant_id = p_tenant_id
      AND adjustment.payment_id = v_payment.id
      AND adjustment.adjustment_kind = 'bank_refund'
  ) THEN
    RAISE EXCEPTION 'Billing payment correction is not allowed after a bank refund'
      USING ERRCODE = '40001';
  END IF;

  v_created_at := pg_catalog.statement_timestamp();
  v_adjustment_record_id := public.gen_random_uuid();
  v_remaining := v_request.amount;
  SELECT * INTO v_credit
  FROM public.billing_tenant_credit AS credit
  WHERE credit.tenant_id = p_tenant_id AND credit.payment_id = v_payment.id;
  IF FOUND THEN
    SELECT v_credit.amount - COALESCE((
      SELECT sum(adjustment.credit_amount)
      FROM public.billing_payment_adjustment AS adjustment
      WHERE adjustment.tenant_id = p_tenant_id
        AND adjustment.payment_id = v_payment.id
    ), 0)
    INTO v_available;
    v_available := GREATEST(v_available, 0);
    v_credit_reversed := LEAST(v_remaining, v_available);
    v_remaining := v_remaining - v_credit_reversed;
  END IF;

  INSERT INTO public.billing_payment_adjustment (
    id, tenant_id, request_id, payment_id, operation_id, adjustment_kind,
    amount, credit_amount, currency, approved_by, approved_session_id,
    mfa_verified_at, approved_at
  ) VALUES (
    v_adjustment_record_id, p_tenant_id, v_request.id, v_payment.id,
    p_operation_id, v_request.adjustment_kind, v_request.amount,
    v_credit_reversed, 'TJS', p_actor_user_id, p_actor_session_id,
    v_mfa_at, v_created_at
  );

  IF v_credit_reversed > 0 THEN
    v_entry_id := public.gen_random_uuid();
    INSERT INTO public.billing_journal_entry (
      id, tenant_id, operation_id, entry_sequence, entry_type, currency,
      actor_user_id, actor_session_id, posted_at
    ) VALUES (
      v_entry_id, p_tenant_id, p_operation_id, v_entry_sequence,
      'payment_adjusted', 'TJS', p_actor_user_id, p_actor_session_id, v_created_at
    );
    INSERT INTO public.billing_journal_posting (
      tenant_id, entry_id, posting_sequence, account_code, side, amount,
      payment_id, created_at
    ) VALUES
      (p_tenant_id, v_entry_id, 1, 'tenant_credit', 'debit',
       v_credit_reversed, v_payment.id, v_created_at),
      (p_tenant_id, v_entry_id, 2, 'unapplied_cash', 'credit',
       v_credit_reversed, v_payment.id, v_created_at);
    PERFORM public.assert_billing_journal_entry_balanced(v_entry_id);
    v_entry_sequence := v_entry_sequence + 1;
  END IF;

  FOR v_candidate IN
    SELECT
      allocation.id,
      allocation.invoice_id,
      allocation.amount - COALESCE((
        SELECT sum(reversal.amount)
        FROM public.billing_payment_adjustment_allocation AS reversal
        WHERE reversal.tenant_id = allocation.tenant_id
          AND reversal.source_allocation_id = allocation.id
      ), 0)::NUMERIC(14,2) AS available_amount
    FROM public.billing_payment_allocation AS allocation
    WHERE allocation.tenant_id = p_tenant_id
      AND allocation.payment_id = v_payment.id
    ORDER BY allocation.allocation_order DESC, allocation.id DESC
  LOOP
    EXIT WHEN v_remaining <= 0;
    v_reverse := LEAST(v_remaining, v_candidate.available_amount);
    CONTINUE WHEN v_reverse <= 0;
    INSERT INTO public.billing_payment_adjustment_allocation (
      tenant_id, adjustment_id, payment_id, source_allocation_id, invoice_id,
      reversal_order, amount, currency, created_at
    ) VALUES (
      p_tenant_id, v_adjustment_record_id, v_payment.id, v_candidate.id,
      v_candidate.invoice_id, v_allocation_order, v_reverse, 'TJS', v_created_at
    );
    v_allocation_reversed := v_allocation_reversed + v_reverse;
    v_remaining := v_remaining - v_reverse;
    v_entry_id := public.gen_random_uuid();
    INSERT INTO public.billing_journal_entry (
      id, tenant_id, operation_id, entry_sequence, entry_type, currency,
      actor_user_id, actor_session_id, posted_at
    ) VALUES (
      v_entry_id, p_tenant_id, p_operation_id, v_entry_sequence,
      'payment_adjusted', 'TJS', p_actor_user_id, p_actor_session_id, v_created_at
    );
    INSERT INTO public.billing_journal_posting (
      tenant_id, entry_id, posting_sequence, account_code, side, amount,
      invoice_id, payment_id, created_at
    ) VALUES
      (p_tenant_id, v_entry_id, 1, 'accounts_receivable', 'debit', v_reverse,
       v_candidate.invoice_id, v_payment.id, v_created_at),
      (p_tenant_id, v_entry_id, 2, 'unapplied_cash', 'credit', v_reverse,
       v_candidate.invoice_id, v_payment.id, v_created_at);
    PERFORM public.assert_billing_journal_entry_balanced(v_entry_id);
    v_entry_sequence := v_entry_sequence + 1;
    v_allocation_order := v_allocation_order + 1;
  END LOOP;
  IF v_remaining <> 0 THEN
    RAISE EXCEPTION 'Billing payment adjustment cannot be reconciled'
      USING ERRCODE = '23514';
  END IF;

  v_entry_id := public.gen_random_uuid();
  INSERT INTO public.billing_journal_entry (
    id, tenant_id, operation_id, entry_sequence, entry_type, currency,
    actor_user_id, actor_session_id, posted_at
  ) VALUES (
    v_entry_id, p_tenant_id, p_operation_id, v_entry_sequence,
    'payment_adjusted', 'TJS', p_actor_user_id, p_actor_session_id, v_created_at
  );
  INSERT INTO public.billing_journal_posting (
    tenant_id, entry_id, posting_sequence, account_code, side, amount,
    payment_id, created_at
  ) VALUES
    (p_tenant_id, v_entry_id, 1, 'unapplied_cash', 'debit',
     v_request.amount, v_payment.id, v_created_at),
    (p_tenant_id, v_entry_id, 2, 'bank_cleared', 'credit',
     v_request.amount, v_payment.id, v_created_at);
  PERFORM public.assert_billing_journal_entry_balanced(v_entry_id);

  UPDATE public.billing_payment_adjustment_request
  SET status = 'approved', row_version = row_version + 1,
      decided_by = p_actor_user_id, decided_session_id = p_actor_session_id,
      decision_operation_id = p_operation_id, decided_at = v_created_at,
      updated_at = v_created_at
  WHERE tenant_id = p_tenant_id AND id = v_request.id;
  SELECT COALESCE(sum(adjustment.amount), 0)::NUMERIC(14,2)
  INTO v_total_adjusted
  FROM public.billing_payment_adjustment AS adjustment
  WHERE adjustment.tenant_id = p_tenant_id
    AND adjustment.payment_id = v_payment.id;
  v_reversible := GREATEST(v_payment.amount - v_total_adjusted, 0);
  SELECT COALESCE(sum(GREATEST(receivable.amount, 0)), 0)::NUMERIC(14,2)
  INTO v_blocking_outstanding
  FROM (
    SELECT
      invoice.id,
      COALESCE(sum(CASE posting.side
        WHEN 'debit' THEN posting.amount ELSE -posting.amount END), 0) AS amount
    FROM public.billing_invoice AS invoice
    LEFT JOIN public.billing_journal_posting AS posting
      ON posting.tenant_id = invoice.tenant_id
     AND posting.invoice_id = invoice.id
     AND posting.account_code = 'accounts_receivable'
    WHERE invoice.tenant_id = p_tenant_id
      AND invoice.document_state = 'issued'
      AND invoice.due_at <= v_created_at
    GROUP BY invoice.id
  ) AS receivable;
  v_result := pg_catalog.jsonb_build_object(
    'adjustment_id', v_request.id,
    'adjustment_record_id', v_adjustment_record_id,
    'tenant_id', p_tenant_id,
    'payment_id', v_payment.id,
    'adjustment_kind', v_request.adjustment_kind,
    'amount', v_request.amount::TEXT,
    'credit_reversed_amount', v_credit_reversed::TEXT,
    'allocation_reversed_amount', v_allocation_reversed::TEXT,
    'total_adjusted_amount', v_total_adjusted::TEXT,
    'reversible_amount', v_reversible::TEXT,
    'blocking_outstanding_amount', v_blocking_outstanding::TEXT,
    'access_review_required', v_blocking_outstanding > 0,
    'currency', 'TJS',
    'status', 'approved',
    'approved_at', v_created_at
  );
  INSERT INTO public.billing_financial_operation (
    operation_id, operation_type, tenant_id, actor_user_id, actor_session_id,
    mfa_verified_at, request_hash, request_payload, result_snapshot, created_at
  ) VALUES (
    p_operation_id, 'payment_adjustment_approved', p_tenant_id, p_actor_user_id,
    p_actor_session_id, v_mfa_at, p_request_hash, v_payload, v_result, v_created_at
  );
  INSERT INTO public.billing_outbox_event (
    tenant_id, operation_id, event_type, aggregate_type, aggregate_id,
    payload, created_at
  ) VALUES (
    p_tenant_id, p_operation_id, 'billing.payment.adjusted',
    'billing_payment_adjustment', v_adjustment_record_id,
    pg_catalog.jsonb_build_object(
      'adjustment_id', v_request.id, 'tenant_id', p_tenant_id,
      'payment_id', v_payment.id,
      'access_review_required', v_blocking_outstanding > 0
    ),
    v_created_at
  );
  RETURN QUERY SELECT v_result, true;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


REJECT_ADJUSTMENT_SQL = r"""
CREATE FUNCTION public.reject_billing_payment_adjustment(
  p_actor_user_id UUID,
  p_actor_session_id UUID,
  p_operation_id UUID,
  p_request_hash TEXT,
  p_tenant_id UUID,
  p_adjustment_id UUID,
  p_expected_row_version INTEGER,
  p_reason_code TEXT,
  p_reason_note TEXT
)
RETURNS TABLE(result JSONB, applied BOOLEAN) AS $$
DECLARE
  v_mfa_at TIMESTAMPTZ;
  v_payload JSONB;
  v_existing public.billing_financial_operation%ROWTYPE;
  v_request public.billing_payment_adjustment_request%ROWTYPE;
  v_created_at TIMESTAMPTZ;
  v_result JSONB;
BEGIN
  v_mfa_at := public.assert_and_lock_platform_recent_capability(
    p_actor_user_id, p_actor_session_id, 'platform.billing.adjustment.approve'
  );
  p_reason_note := NULLIF(pg_catalog.btrim(p_reason_note), '');
  IF p_operation_id IS NULL
    OR p_request_hash !~ '^[0-9a-f]{64}$'
    OR p_tenant_id IS NULL OR p_adjustment_id IS NULL
    OR p_expected_row_version IS NULL OR p_expected_row_version < 1
    OR p_reason_code IS NULL OR p_reason_code NOT IN (
      'bank_refund_not_verified','amount_mismatch','request_not_supported',
      'duplicate','other'
    )
    OR (p_reason_note IS NOT NULL AND pg_catalog.length(p_reason_note) > 500)
    OR (p_reason_code = 'other' AND COALESCE(pg_catalog.length(p_reason_note), 0) < 10)
  THEN
    RAISE EXCEPTION 'Invalid billing adjustment rejection request' USING ERRCODE = '22023';
  END IF;
  v_payload := pg_catalog.jsonb_strip_nulls(pg_catalog.jsonb_build_object(
    'tenant_id', p_tenant_id,
    'adjustment_id', p_adjustment_id,
    'expected_row_version', p_expected_row_version,
    'reason_code', p_reason_code,
    'reason_note', p_reason_note
  ));
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(p_operation_id::TEXT, 9501)
  );
  SELECT * INTO v_existing
  FROM public.billing_financial_operation AS financial_operation
  WHERE financial_operation.operation_id = p_operation_id;
  IF FOUND THEN
    IF v_existing.operation_type <> 'payment_adjustment_rejected'
      OR v_existing.actor_user_id <> p_actor_user_id
      OR v_existing.request_hash <> p_request_hash
      OR v_existing.request_payload <> v_payload
    THEN
      RAISE EXCEPTION 'Billing operation id was reused' USING ERRCODE = '23505';
    END IF;
    RETURN QUERY SELECT v_existing.result_snapshot, false;
    RETURN;
  END IF;
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(p_tenant_id::TEXT, 9701)
  );
  SELECT * INTO v_request
  FROM public.billing_payment_adjustment_request AS adjustment_request
  WHERE adjustment_request.tenant_id = p_tenant_id
    AND adjustment_request.id = p_adjustment_id
  FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Billing payment adjustment was not found' USING ERRCODE = 'P0002';
  END IF;
  IF v_request.status <> 'pending_approval'
    OR v_request.row_version <> p_expected_row_version
  THEN
    RAISE EXCEPTION 'Billing payment adjustment changed concurrently'
      USING ERRCODE = '40001';
  END IF;
  IF v_request.created_by = p_actor_user_id THEN
    RAISE EXCEPTION 'Billing adjustment requires an independent decision'
      USING ERRCODE = '22023';
  END IF;
  v_created_at := pg_catalog.statement_timestamp();
  v_result := pg_catalog.jsonb_build_object(
    'adjustment_id', v_request.id,
    'tenant_id', p_tenant_id,
    'payment_id', v_request.payment_id,
    'adjustment_kind', v_request.adjustment_kind,
    'amount', v_request.amount::TEXT,
    'currency', v_request.currency,
    'status', 'rejected',
    'row_version', v_request.row_version + 1,
    'created_at', v_request.created_at,
    'decided_at', v_created_at,
    'decision_reason_code', p_reason_code
  );
  INSERT INTO public.billing_financial_operation (
    operation_id, operation_type, tenant_id, actor_user_id, actor_session_id,
    mfa_verified_at, request_hash, request_payload, result_snapshot, created_at
  ) VALUES (
    p_operation_id, 'payment_adjustment_rejected', p_tenant_id, p_actor_user_id,
    p_actor_session_id, v_mfa_at, p_request_hash, v_payload, v_result, v_created_at
  );
  UPDATE public.billing_payment_adjustment_request
  SET status = 'rejected', row_version = row_version + 1,
      decided_by = p_actor_user_id, decided_session_id = p_actor_session_id,
      decision_operation_id = p_operation_id, decided_at = v_created_at,
      decision_reason_code = p_reason_code, decision_note = p_reason_note,
      updated_at = v_created_at
  WHERE tenant_id = p_tenant_id AND id = v_request.id;
  INSERT INTO public.billing_outbox_event (
    tenant_id, operation_id, event_type, aggregate_type, aggregate_id,
    payload, created_at
  ) VALUES (
    p_tenant_id, p_operation_id, 'billing.payment.adjustment_rejected',
    'billing_payment_adjustment_request', v_request.id,
    pg_catalog.jsonb_build_object(
      'adjustment_id', v_request.id, 'tenant_id', p_tenant_id,
      'payment_id', v_request.payment_id
    ),
    v_created_at
  );
  RETURN QUERY SELECT v_result, true;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


READ_FINANCIAL_ACCOUNT_SQL = r"""
CREATE OR REPLACE FUNCTION public.read_platform_billing_financial_account(
  p_actor_user_id UUID,
  p_actor_session_id UUID,
  p_tenant_id UUID
)
RETURNS JSONB AS $$
DECLARE
  v_result JSONB;
BEGIN
  PERFORM public.assert_and_lock_platform_recent_capability(
    p_actor_user_id, p_actor_session_id, 'platform.billing.view'
  );
  IF p_tenant_id IS NULL OR NOT EXISTS (
    SELECT 1 FROM public.tenant AS tenant WHERE tenant.id = p_tenant_id
  ) THEN
    RAISE EXCEPTION 'Tenant was not found' USING ERRCODE = 'P0002';
  END IF;
  SELECT pg_catalog.jsonb_build_object(
    'tenant_id', p_tenant_id,
    'currency', 'TJS',
    'outstanding_amount', COALESCE((
      SELECT sum(GREATEST(receivable.amount, 0))
      FROM (
        SELECT invoice.id, COALESCE(sum(CASE posting.side
          WHEN 'debit' THEN posting.amount ELSE -posting.amount END), 0) AS amount
        FROM public.billing_invoice AS invoice
        LEFT JOIN public.billing_journal_posting AS posting
          ON posting.tenant_id = invoice.tenant_id
         AND posting.invoice_id = invoice.id
         AND posting.account_code = 'accounts_receivable'
        WHERE invoice.tenant_id = p_tenant_id AND invoice.document_state = 'issued'
        GROUP BY invoice.id
      ) AS receivable
    ), 0)::NUMERIC(14,2)::TEXT,
    'credit_balance', COALESCE((
      SELECT sum(CASE posting.side
        WHEN 'credit' THEN posting.amount ELSE -posting.amount END)
      FROM public.billing_journal_posting AS posting
      WHERE posting.tenant_id = p_tenant_id
        AND posting.account_code = 'tenant_credit'
    ), 0)::NUMERIC(14,2)::TEXT,
    'invoices', COALESCE((
      SELECT pg_catalog.jsonb_agg(
        pg_catalog.jsonb_build_object(
          'invoice_id', invoice.id,
          'tenant_id', invoice.tenant_id,
          'subscription_id', invoice.subscription_id,
          'price_application_id', invoice.price_application_id,
          'price_application_kind', application.application_kind,
          'invoice_number', invoice.invoice_number,
          'document_state', invoice.document_state,
          'settlement_state', CASE
            WHEN GREATEST(COALESCE(receivable.amount, invoice.total_amount), 0) <= 0 THEN 'paid'
            WHEN GREATEST(COALESCE(receivable.amount, invoice.total_amount), 0)
              < invoice.total_amount THEN 'partially_paid'
            ELSE 'unpaid'
          END,
          'collection_state', CASE
            WHEN GREATEST(COALESCE(receivable.amount, invoice.total_amount), 0) <= 0 THEN 'not_due'
            WHEN invoice.due_at < pg_catalog.statement_timestamp() THEN 'overdue'
            WHEN invoice.due_at > pg_catalog.statement_timestamp() THEN 'not_due'
            ELSE 'due'
          END,
          'period_start', invoice.period_start,
          'period_end', invoice.period_end,
          'due_at', invoice.due_at,
          'total_amount', invoice.total_amount::TEXT,
          'outstanding_amount', GREATEST(
            COALESCE(receivable.amount, invoice.total_amount), 0
          )::NUMERIC(14,2)::TEXT,
          'currency', invoice.currency,
          'issued_at', invoice.issued_at
        ) ORDER BY invoice.due_at DESC, invoice.issued_at DESC, invoice.id
      )
      FROM public.billing_invoice AS invoice
      LEFT JOIN LATERAL (
        SELECT COALESCE(sum(CASE posting.side
          WHEN 'debit' THEN posting.amount ELSE -posting.amount END), 0) AS amount
        FROM public.billing_journal_posting AS posting
        WHERE posting.tenant_id = invoice.tenant_id
          AND posting.invoice_id = invoice.id
          AND posting.account_code = 'accounts_receivable'
      ) AS receivable ON true
      JOIN public.billing_subscription_price_application AS application
        ON application.tenant_id = invoice.tenant_id
       AND application.id = invoice.price_application_id
      WHERE invoice.tenant_id = p_tenant_id
    ), '[]'::JSONB),
    'payments', COALESCE((
      SELECT pg_catalog.jsonb_agg(
        pg_catalog.jsonb_build_object(
          'payment_id', payment.id,
          'amount', payment.amount::TEXT,
          'allocated_amount', GREATEST(
            COALESCE(allocated.amount, 0) - COALESCE(reversed.allocated_amount, 0), 0
          )::NUMERIC(14,2)::TEXT,
          'credit_amount', GREATEST(
            COALESCE(credit.amount, 0) - COALESCE(reversed.credit_amount, 0), 0
          )::NUMERIC(14,2)::TEXT,
          'corrected_amount', COALESCE(reversed.corrected_amount, 0)::TEXT,
          'refunded_amount', COALESCE(reversed.refunded_amount, 0)::TEXT,
          'reversible_amount', GREATEST(
            payment.amount - COALESCE(reversed.total_amount, 0), 0
          )::NUMERIC(14,2)::TEXT,
          'adjustment_pending', EXISTS (
            SELECT 1
            FROM public.billing_payment_adjustment_request AS pending_request
            WHERE pending_request.tenant_id = payment.tenant_id
              AND pending_request.payment_id = payment.id
              AND pending_request.status = 'pending_approval'
          ),
          'currency', payment.currency,
          'paid_at', payment.paid_at,
          'confirmed_at', payment.confirmed_at,
          'lifecycle_state', CASE
            WHEN payment.amount - COALESCE(reversed.total_amount, 0) <= 0
              THEN 'reversed' ELSE 'confirmed' END
        ) ORDER BY payment.paid_at DESC, payment.confirmed_at DESC, payment.id
      )
      FROM public.billing_payment AS payment
      LEFT JOIN LATERAL (
        SELECT COALESCE(sum(allocation.amount), 0)::NUMERIC(14,2) AS amount
        FROM public.billing_payment_allocation AS allocation
        WHERE allocation.tenant_id = payment.tenant_id
          AND allocation.payment_id = payment.id
      ) AS allocated ON true
      LEFT JOIN public.billing_tenant_credit AS credit
        ON credit.tenant_id = payment.tenant_id AND credit.payment_id = payment.id
      LEFT JOIN LATERAL (
        SELECT
          COALESCE(sum(adjustment.amount), 0)::NUMERIC(14,2) AS total_amount,
          COALESCE(sum(adjustment.credit_amount), 0)::NUMERIC(14,2) AS credit_amount,
          COALESCE(sum(adjustment.amount) FILTER (
            WHERE adjustment.adjustment_kind = 'correction'
          ), 0)::NUMERIC(14,2) AS corrected_amount,
          COALESCE(sum(adjustment.amount) FILTER (
            WHERE adjustment.adjustment_kind = 'bank_refund'
          ), 0)::NUMERIC(14,2) AS refunded_amount,
          COALESCE((
            SELECT sum(reversal.amount)
            FROM public.billing_payment_adjustment_allocation AS reversal
            WHERE reversal.tenant_id = payment.tenant_id
              AND reversal.payment_id = payment.id
          ), 0)::NUMERIC(14,2) AS allocated_amount
        FROM public.billing_payment_adjustment AS adjustment
        WHERE adjustment.tenant_id = payment.tenant_id
          AND adjustment.payment_id = payment.id
      ) AS reversed ON true
      WHERE payment.tenant_id = p_tenant_id
    ), '[]'::JSONB),
    'journal_balanced', NOT EXISTS (
      SELECT 1
      FROM public.billing_journal_entry AS entry
      LEFT JOIN public.billing_journal_posting AS posting
        ON posting.tenant_id = entry.tenant_id AND posting.entry_id = entry.id
      WHERE entry.tenant_id = p_tenant_id
      GROUP BY entry.id
      HAVING COALESCE(sum(posting.amount) FILTER (WHERE posting.side = 'debit'), 0)
        <> COALESCE(sum(posting.amount) FILTER (WHERE posting.side = 'credit'), 0)
    )
  ) INTO v_result;
  RETURN v_result;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


RESTORE_READ_FINANCIAL_ACCOUNT_SQL = r"""
CREATE OR REPLACE FUNCTION public.read_platform_billing_financial_account(
  p_actor_user_id UUID,
  p_actor_session_id UUID,
  p_tenant_id UUID
)
RETURNS JSONB AS $$
DECLARE
  v_result JSONB;
BEGIN
  PERFORM public.assert_and_lock_platform_recent_capability(
    p_actor_user_id, p_actor_session_id, 'platform.billing.view'
  );
  IF p_tenant_id IS NULL OR NOT EXISTS (
    SELECT 1 FROM public.tenant AS tenant WHERE tenant.id = p_tenant_id
  ) THEN
    RAISE EXCEPTION 'Tenant was not found' USING ERRCODE = 'P0002';
  END IF;
  SELECT pg_catalog.jsonb_build_object(
    'tenant_id', p_tenant_id,
    'currency', 'TJS',
    'outstanding_amount', COALESCE((
      SELECT sum(GREATEST(invoice.total_amount - COALESCE((
        SELECT sum(allocation.amount)
        FROM public.billing_payment_allocation AS allocation
        WHERE allocation.tenant_id = invoice.tenant_id
          AND allocation.invoice_id = invoice.id
      ), 0), 0))
      FROM public.billing_invoice AS invoice
      WHERE invoice.tenant_id = p_tenant_id AND invoice.document_state = 'issued'
    ), 0)::NUMERIC(14,2)::TEXT,
    'credit_balance', COALESCE((
      SELECT sum(credit.amount)
      FROM public.billing_tenant_credit AS credit
      WHERE credit.tenant_id = p_tenant_id
    ), 0)::NUMERIC(14,2)::TEXT,
    'invoices', COALESCE((
      SELECT pg_catalog.jsonb_agg(
        pg_catalog.jsonb_build_object(
          'invoice_id', invoice.id,
          'tenant_id', invoice.tenant_id,
          'subscription_id', invoice.subscription_id,
          'price_application_id', invoice.price_application_id,
          'price_application_kind', application.application_kind,
          'invoice_number', invoice.invoice_number,
          'document_state', invoice.document_state,
          'settlement_state', CASE
            WHEN invoice.total_amount - COALESCE(allocated.amount, 0) <= 0 THEN 'paid'
            WHEN COALESCE(allocated.amount, 0) > 0 THEN 'partially_paid'
            ELSE 'unpaid'
          END,
          'collection_state', CASE
            WHEN invoice.total_amount - COALESCE(allocated.amount, 0) <= 0 THEN 'not_due'
            WHEN invoice.due_at < pg_catalog.statement_timestamp() THEN 'overdue'
            WHEN invoice.due_at > pg_catalog.statement_timestamp() THEN 'not_due'
            ELSE 'due'
          END,
          'period_start', invoice.period_start,
          'period_end', invoice.period_end,
          'due_at', invoice.due_at,
          'total_amount', invoice.total_amount::TEXT,
          'outstanding_amount', GREATEST(
            invoice.total_amount - COALESCE(allocated.amount, 0), 0
          )::TEXT,
          'currency', invoice.currency,
          'issued_at', invoice.issued_at
        ) ORDER BY invoice.due_at DESC, invoice.issued_at DESC, invoice.id
      )
      FROM public.billing_invoice AS invoice
      LEFT JOIN LATERAL (
        SELECT COALESCE(sum(allocation.amount), 0)::NUMERIC(14,2) AS amount
        FROM public.billing_payment_allocation AS allocation
        WHERE allocation.tenant_id = invoice.tenant_id
          AND allocation.invoice_id = invoice.id
      ) AS allocated ON true
      JOIN public.billing_subscription_price_application AS application
        ON application.tenant_id = invoice.tenant_id
       AND application.id = invoice.price_application_id
      WHERE invoice.tenant_id = p_tenant_id
    ), '[]'::JSONB),
    'payments', COALESCE((
      SELECT pg_catalog.jsonb_agg(
        pg_catalog.jsonb_build_object(
          'payment_id', payment.id,
          'amount', payment.amount::TEXT,
          'allocated_amount', COALESCE(allocated.amount, 0)::TEXT,
          'credit_amount', COALESCE(credit.amount, 0)::TEXT,
          'currency', payment.currency,
          'paid_at', payment.paid_at,
          'confirmed_at', payment.confirmed_at,
          'lifecycle_state', payment.lifecycle_state
        ) ORDER BY payment.paid_at DESC, payment.confirmed_at DESC, payment.id
      )
      FROM public.billing_payment AS payment
      LEFT JOIN LATERAL (
        SELECT COALESCE(sum(allocation.amount), 0)::NUMERIC(14,2) AS amount
        FROM public.billing_payment_allocation AS allocation
        WHERE allocation.tenant_id = payment.tenant_id
          AND allocation.payment_id = payment.id
      ) AS allocated ON true
      LEFT JOIN public.billing_tenant_credit AS credit
        ON credit.tenant_id = payment.tenant_id AND credit.payment_id = payment.id
      WHERE payment.tenant_id = p_tenant_id
    ), '[]'::JSONB),
    'journal_balanced', NOT EXISTS (
      SELECT 1
      FROM public.billing_journal_entry AS entry
      LEFT JOIN public.billing_journal_posting AS posting
        ON posting.tenant_id = entry.tenant_id AND posting.entry_id = entry.id
      WHERE entry.tenant_id = p_tenant_id
      GROUP BY entry.id
      HAVING COALESCE(sum(posting.amount) FILTER (WHERE posting.side = 'debit'), 0)
        <> COALESCE(sum(posting.amount) FILTER (WHERE posting.side = 'credit'), 0)
    )
  ) INTO v_result;
  RETURN v_result;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


FUNCTION_SIGNATURES = (
    "public.reject_billing_bank_payment_review(UUID, UUID, UUID, TEXT, UUID, UUID, INTEGER, TEXT, TEXT)",
    "public.create_billing_payment_adjustment_request(UUID, UUID, UUID, TEXT, UUID, UUID, TEXT, NUMERIC, TEXT, TEXT, TIMESTAMPTZ, TEXT)",
    "public.list_platform_billing_payment_adjustments(UUID, UUID, UUID, INTEGER, INTEGER)",
    "public.approve_billing_payment_adjustment(UUID, UUID, UUID, TEXT, UUID, UUID, INTEGER)",
    "public.reject_billing_payment_adjustment(UUID, UUID, UUID, TEXT, UUID, UUID, INTEGER, TEXT, TEXT)",
)


def _secure_function(signature: str) -> None:
    op.execute(f"ALTER FUNCTION {signature} OWNER TO aurum_schema_owner")
    op.execute(
        f"REVOKE ALL PRIVILEGES ON FUNCTION {signature} "
        "FROM PUBLIC, aurum_app, aurum_support, aurum_mailer, "
        "aurum_edge_cash_executor, aurum_edge_cash_owner"
    )
    op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO aurum_support")


def _secure_table(table: str) -> None:
    op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
    op.execute(f"""
        CREATE POLICY {table}_owner_access ON public.{table}
        TO aurum_schema_owner USING (true) WITH CHECK (true)
        """)
    op.execute(f"""
        REVOKE ALL PRIVILEGES ON TABLE public.{table}
        FROM PUBLIC, aurum_app, aurum_support, aurum_mailer,
          aurum_edge_cash_executor, aurum_edge_cash_owner
        """)


def _grant_missing_reference_privileges() -> None:
    op.execute("""
        CREATE TEMPORARY TABLE aurum_0099_missing_reference_privilege (
          table_name TEXT PRIMARY KEY
        ) ON COMMIT DROP
        """)
    op.execute("""
        DO $$
        BEGIN
          IF NOT pg_catalog.has_table_privilege(
            'aurum_schema_owner', 'public.app_user', 'REFERENCES'
          ) THEN
            INSERT INTO pg_temp.aurum_0099_missing_reference_privilege(table_name)
            VALUES ('app_user');
            GRANT REFERENCES ON TABLE public.app_user TO aurum_schema_owner;
          END IF;
        END
        $$
        """)


def _restore_reference_privileges() -> None:
    op.execute("""
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM pg_temp.aurum_0099_missing_reference_privilege
            WHERE table_name = 'app_user'
          ) THEN
            REVOKE REFERENCES ON TABLE public.app_user FROM aurum_schema_owner;
          END IF;
        END
        $$
        """)
    op.execute("DROP TABLE pg_temp.aurum_0099_missing_reference_privilege")


def upgrade() -> None:
    _grant_missing_reference_privileges()
    op.execute(AUDIT_FINANCIAL_ROW_SQL)
    op.execute("""
        ALTER TABLE public.billing_financial_operation
        DROP CONSTRAINT ck_billing_financial_operation_type,
        ADD CONSTRAINT ck_billing_financial_operation_type CHECK (
          operation_type IN (
            'invoice_issued','payment_review_created','payment_approved',
            'payment_review_rejected','payment_adjustment_requested',
            'payment_adjustment_approved','payment_adjustment_rejected'
          )
        )
        """)
    op.execute("""
        ALTER TABLE public.billing_journal_entry
        DROP CONSTRAINT ck_billing_journal_entry_type,
        ADD CONSTRAINT ck_billing_journal_entry_type CHECK (
          entry_type IN (
            'invoice_issued','payment_confirmed','payment_allocated',
            'credit_created','payment_adjusted'
          )
        )
        """)
    op.execute("""
        ALTER TABLE public.billing_payment_review
        DROP CONSTRAINT ck_billing_review_approval,
        ADD COLUMN rejected_by UUID REFERENCES public.app_user(id) ON DELETE RESTRICT,
        ADD COLUMN rejected_session_id UUID,
        ADD COLUMN rejected_operation_id UUID,
        ADD COLUMN rejected_at TIMESTAMPTZ,
        ADD COLUMN rejection_reason_code TEXT,
        ADD COLUMN rejection_note TEXT,
        ADD CONSTRAINT fk_billing_review_rejected_operation
          FOREIGN KEY (tenant_id, rejected_operation_id)
          REFERENCES public.billing_financial_operation(tenant_id, operation_id)
          ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
        ADD CONSTRAINT ck_billing_review_rejection_reason CHECK (
          rejection_reason_code IS NULL OR rejection_reason_code IN (
            'bank_payment_not_found','amount_mismatch','date_mismatch','duplicate',
            'wrong_tenant_or_invoice','other'
          )
        ),
        ADD CONSTRAINT ck_billing_review_rejection_note CHECK (
          rejection_note IS NULL OR pg_catalog.length(rejection_note) BETWEEN 1 AND 500
        ),
        ADD CONSTRAINT ck_billing_review_approval CHECK (
          (status = 'pending_approval' AND approved_by IS NULL
            AND approved_session_id IS NULL AND approved_operation_id IS NULL
            AND approved_at IS NULL AND rejected_by IS NULL
            AND rejected_session_id IS NULL AND rejected_operation_id IS NULL
            AND rejected_at IS NULL AND rejection_reason_code IS NULL
            AND rejection_note IS NULL)
          OR
          (status = 'approved' AND approved_by IS NOT NULL
            AND approved_session_id IS NOT NULL AND approved_operation_id IS NOT NULL
            AND approved_at IS NOT NULL AND approved_by <> reviewed_by
            AND rejected_by IS NULL AND rejected_session_id IS NULL
            AND rejected_operation_id IS NULL AND rejected_at IS NULL
            AND rejection_reason_code IS NULL AND rejection_note IS NULL)
          OR
          (status IN ('rejected','duplicate') AND approved_by IS NULL
            AND approved_session_id IS NULL AND approved_operation_id IS NULL
            AND approved_at IS NULL AND rejected_by IS NOT NULL
            AND rejected_session_id IS NOT NULL AND rejected_operation_id IS NOT NULL
            AND rejected_at IS NOT NULL AND rejected_by <> reviewed_by
            AND rejection_reason_code IS NOT NULL
            AND (rejection_reason_code <> 'other'
              OR pg_catalog.length(rejection_note) >= 10))
        )
        """)
    op.execute("""
        CREATE TABLE public.billing_payment_adjustment_request (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id UUID NOT NULL,
          payment_id UUID NOT NULL,
          operation_id UUID NOT NULL UNIQUE,
          adjustment_kind TEXT NOT NULL,
          amount NUMERIC(14,2) NOT NULL,
          currency TEXT NOT NULL DEFAULT 'TJS',
          reason_code TEXT NOT NULL,
          reason_note TEXT NOT NULL,
          refunded_at TIMESTAMPTZ,
          refund_recipient_account_key TEXT,
          refund_reference TEXT,
          status TEXT NOT NULL DEFAULT 'pending_approval',
          row_version INTEGER NOT NULL DEFAULT 1,
          created_by UUID NOT NULL REFERENCES public.app_user(id) ON DELETE RESTRICT,
          created_session_id UUID NOT NULL,
          create_mfa_verified_at TIMESTAMPTZ NOT NULL,
          decided_by UUID REFERENCES public.app_user(id) ON DELETE RESTRICT,
          decided_session_id UUID,
          decision_operation_id UUID UNIQUE,
          decided_at TIMESTAMPTZ,
          decision_reason_code TEXT,
          decision_note TEXT,
          created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT uq_billing_adjustment_request_tenant_id UNIQUE (tenant_id, id),
          CONSTRAINT fk_billing_adjustment_request_payment
            FOREIGN KEY (tenant_id, payment_id)
            REFERENCES public.billing_payment(tenant_id, id) ON DELETE RESTRICT,
          CONSTRAINT fk_billing_adjustment_request_operation
            FOREIGN KEY (tenant_id, operation_id)
            REFERENCES public.billing_financial_operation(tenant_id, operation_id)
            ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
          CONSTRAINT fk_billing_adjustment_decision_operation
            FOREIGN KEY (tenant_id, decision_operation_id)
            REFERENCES public.billing_financial_operation(tenant_id, operation_id)
            ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
          CONSTRAINT ck_billing_adjustment_request_kind CHECK (
            adjustment_kind IN ('correction','bank_refund')
          ),
          CONSTRAINT ck_billing_adjustment_request_money CHECK (
            amount > 0 AND currency = 'TJS'
          ),
          CONSTRAINT ck_billing_adjustment_request_reason CHECK (
            reason_code IN (
              'payment_entered_in_error','amount_correction','bank_refund_completed',
              'contract_resolution','other'
            )
            AND pg_catalog.length(reason_note) BETWEEN 10 AND 500
          ),
          CONSTRAINT ck_billing_adjustment_request_bank_refund CHECK (
            (adjustment_kind = 'correction' AND refunded_at IS NULL
              AND refund_recipient_account_key IS NULL AND refund_reference IS NULL
              AND reason_code IN ('payment_entered_in_error','amount_correction','other'))
            OR
            (adjustment_kind = 'bank_refund' AND refunded_at IS NOT NULL
              AND refund_recipient_account_key ~ '^[a-z0-9][a-z0-9_.:-]{2,63}$'
              AND refund_reference ~ '^[A-Z0-9]{4,128}$'
              AND reason_code IN ('bank_refund_completed','contract_resolution','other'))
          ),
          CONSTRAINT ck_billing_adjustment_request_status CHECK (
            status IN ('pending_approval','approved','rejected')
          ),
          CONSTRAINT ck_billing_adjustment_request_version CHECK (row_version > 0),
          CONSTRAINT ck_billing_adjustment_request_decision CHECK (
            (status = 'pending_approval' AND decided_by IS NULL
              AND decided_session_id IS NULL AND decision_operation_id IS NULL
              AND decided_at IS NULL AND decision_reason_code IS NULL
              AND decision_note IS NULL)
            OR
            (status = 'approved' AND decided_by IS NOT NULL
              AND decided_session_id IS NOT NULL AND decision_operation_id IS NOT NULL
              AND decided_at IS NOT NULL AND decided_by <> created_by
              AND decision_reason_code IS NULL AND decision_note IS NULL)
            OR
            (status = 'rejected' AND decided_by IS NOT NULL
              AND decided_session_id IS NOT NULL AND decision_operation_id IS NOT NULL
              AND decided_at IS NOT NULL AND decided_by <> created_by
              AND decision_reason_code IN (
                'bank_refund_not_verified','amount_mismatch','request_not_supported',
                'duplicate','other'
              )
              AND (decision_note IS NULL OR pg_catalog.length(decision_note) <= 500)
              AND (decision_reason_code <> 'other'
                OR pg_catalog.length(decision_note) >= 10))
          )
        )
        """)
    op.execute("""
        CREATE UNIQUE INDEX uq_billing_adjustment_pending_payment
        ON public.billing_payment_adjustment_request(tenant_id, payment_id)
        WHERE status = 'pending_approval'
        """)
    op.execute("""
        CREATE UNIQUE INDEX uq_billing_adjustment_refund_reference
        ON public.billing_payment_adjustment_request(
          refund_recipient_account_key, refund_reference
        )
        WHERE adjustment_kind = 'bank_refund'
        """)
    op.execute("""
        CREATE TABLE public.billing_payment_adjustment (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id UUID NOT NULL,
          request_id UUID NOT NULL UNIQUE,
          payment_id UUID NOT NULL,
          operation_id UUID NOT NULL UNIQUE,
          adjustment_kind TEXT NOT NULL,
          amount NUMERIC(14,2) NOT NULL,
          credit_amount NUMERIC(14,2) NOT NULL DEFAULT 0,
          currency TEXT NOT NULL DEFAULT 'TJS',
          approved_by UUID NOT NULL REFERENCES public.app_user(id) ON DELETE RESTRICT,
          approved_session_id UUID NOT NULL,
          mfa_verified_at TIMESTAMPTZ NOT NULL,
          approved_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT uq_billing_adjustment_tenant_id UNIQUE (tenant_id, id),
          CONSTRAINT fk_billing_adjustment_request FOREIGN KEY (tenant_id, request_id)
            REFERENCES public.billing_payment_adjustment_request(tenant_id, id)
            ON DELETE RESTRICT,
          CONSTRAINT fk_billing_adjustment_payment FOREIGN KEY (tenant_id, payment_id)
            REFERENCES public.billing_payment(tenant_id, id) ON DELETE RESTRICT,
          CONSTRAINT fk_billing_adjustment_operation FOREIGN KEY (tenant_id, operation_id)
            REFERENCES public.billing_financial_operation(tenant_id, operation_id)
            ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
          CONSTRAINT ck_billing_adjustment_kind CHECK (
            adjustment_kind IN ('correction','bank_refund')
          ),
          CONSTRAINT ck_billing_adjustment_money CHECK (
            amount > 0 AND credit_amount >= 0 AND credit_amount <= amount
            AND currency = 'TJS'
          )
        )
        """)
    op.execute("""
        CREATE TABLE public.billing_payment_adjustment_allocation (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id UUID NOT NULL,
          adjustment_id UUID NOT NULL,
          payment_id UUID NOT NULL,
          source_allocation_id UUID NOT NULL,
          invoice_id UUID NOT NULL,
          reversal_order SMALLINT NOT NULL,
          amount NUMERIC(14,2) NOT NULL,
          currency TEXT NOT NULL DEFAULT 'TJS',
          created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT uq_billing_adjustment_allocation_tenant_id UNIQUE (tenant_id, id),
          CONSTRAINT uq_billing_adjustment_allocation_order
            UNIQUE (tenant_id, adjustment_id, reversal_order),
          CONSTRAINT uq_billing_adjustment_source
            UNIQUE (tenant_id, adjustment_id, source_allocation_id),
          CONSTRAINT fk_billing_adjustment_allocation_adjustment
            FOREIGN KEY (tenant_id, adjustment_id)
            REFERENCES public.billing_payment_adjustment(tenant_id, id)
            ON DELETE RESTRICT,
          CONSTRAINT fk_billing_adjustment_allocation_payment
            FOREIGN KEY (tenant_id, payment_id)
            REFERENCES public.billing_payment(tenant_id, id) ON DELETE RESTRICT,
          CONSTRAINT fk_billing_adjustment_allocation_source
            FOREIGN KEY (tenant_id, source_allocation_id)
            REFERENCES public.billing_payment_allocation(tenant_id, id)
            ON DELETE RESTRICT,
          CONSTRAINT fk_billing_adjustment_allocation_invoice
            FOREIGN KEY (tenant_id, invoice_id)
            REFERENCES public.billing_invoice(tenant_id, id) ON DELETE RESTRICT,
          CONSTRAINT ck_billing_adjustment_allocation_money CHECK (
            reversal_order > 0 AND amount > 0 AND currency = 'TJS'
          )
        )
        """)
    for table in (
        "billing_payment_adjustment_request",
        "billing_payment_adjustment",
        "billing_payment_adjustment_allocation",
    ):
        _secure_table(table)
    for table in (
        "billing_payment_adjustment",
        "billing_payment_adjustment_allocation",
    ):
        op.execute(f"""
            CREATE TRIGGER trg_immutable_{table}
            BEFORE UPDATE OR DELETE ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION
              public.trg_reject_immutable_billing_financial_mutation()
            """)
    for table in (
        "billing_payment_adjustment_request",
        "billing_payment_adjustment",
    ):
        op.execute(f"""
            CREATE TRIGGER trg_audit_{table}
            AFTER INSERT OR UPDATE ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION public.trg_audit_billing_financial_row()
            """)

    op.execute(REJECT_PAYMENT_REVIEW_SQL)
    op.execute(CREATE_ADJUSTMENT_REQUEST_SQL)
    op.execute(LIST_ADJUSTMENT_QUEUE_SQL)
    op.execute(APPROVE_ADJUSTMENT_SQL)
    op.execute(REJECT_ADJUSTMENT_SQL)
    for signature in FUNCTION_SIGNATURES:
        _secure_function(signature)
    op.execute(READ_FINANCIAL_ACCOUNT_SQL)
    _restore_reference_privileges()


def downgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM public.billing_payment_adjustment_request
            UNION ALL
            SELECT 1 FROM public.billing_payment_adjustment
            UNION ALL
            SELECT 1 FROM public.billing_payment_adjustment_allocation
            UNION ALL
            SELECT 1 FROM public.billing_payment_review
              WHERE rejected_operation_id IS NOT NULL
          ) THEN
            RAISE EXCEPTION 'Cannot downgrade billing adjustments with financial history';
          END IF;
        END
        $$
        """)
    op.execute(RESTORE_READ_FINANCIAL_ACCOUNT_SQL)
    op.execute(RESTORE_AUDIT_FINANCIAL_ROW_SQL)
    for signature in reversed(FUNCTION_SIGNATURES):
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")
    for table in (
        "billing_payment_adjustment",
        "billing_payment_adjustment_allocation",
    ):
        op.execute(f"DROP TRIGGER trg_immutable_{table} ON public.{table}")
    for table in (
        "billing_payment_adjustment_request",
        "billing_payment_adjustment",
    ):
        op.execute(f"DROP TRIGGER trg_audit_{table} ON public.{table}")
    op.execute("DROP TABLE public.billing_payment_adjustment_allocation")
    op.execute("DROP TABLE public.billing_payment_adjustment")
    op.execute("DROP TABLE public.billing_payment_adjustment_request")
    op.execute("""
        ALTER TABLE public.billing_payment_review
        DROP CONSTRAINT ck_billing_review_approval,
        DROP CONSTRAINT ck_billing_review_rejection_note,
        DROP CONSTRAINT ck_billing_review_rejection_reason,
        DROP CONSTRAINT fk_billing_review_rejected_operation,
        DROP COLUMN rejection_note,
        DROP COLUMN rejection_reason_code,
        DROP COLUMN rejected_at,
        DROP COLUMN rejected_operation_id,
        DROP COLUMN rejected_session_id,
        DROP COLUMN rejected_by,
        ADD CONSTRAINT ck_billing_review_approval CHECK (
          (status = 'pending_approval' AND approved_by IS NULL
            AND approved_session_id IS NULL AND approved_operation_id IS NULL
            AND approved_at IS NULL)
          OR
          (status = 'approved' AND approved_by IS NOT NULL
            AND approved_session_id IS NOT NULL AND approved_operation_id IS NOT NULL
            AND approved_at IS NOT NULL AND approved_by <> reviewed_by)
          OR status IN ('rejected','duplicate')
        )
        """)
    op.execute("""
        ALTER TABLE public.billing_journal_entry
        DROP CONSTRAINT ck_billing_journal_entry_type,
        ADD CONSTRAINT ck_billing_journal_entry_type CHECK (
          entry_type IN (
            'invoice_issued','payment_confirmed','payment_allocated','credit_created'
          )
        )
        """)
    op.execute("""
        ALTER TABLE public.billing_financial_operation
        DROP CONSTRAINT ck_billing_financial_operation_type,
        ADD CONSTRAINT ck_billing_financial_operation_type CHECK (
          operation_type IN ('invoice_issued','payment_review_created','payment_approved')
        )
        """)
