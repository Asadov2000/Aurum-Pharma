"""add protected tenant billing payment submissions

Revision ID: 0101
Revises: 0100
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0101"
down_revision: str | Sequence[str] | None = "0100"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TENANT_PERMISSIONS = (
    (
        "billing.payment_submission.create",
        "Передача сведений об оплате",
        "Создание заявления о банковской оплате счёта Aurum Pharma.",
    ),
    (
        "billing.payment_submission.withdraw",
        "Отзыв заявления об оплате",
        "Отзыв своего заявления до начала проверки сотрудником Aurum Pharma.",
    ),
)


GLOBAL_OPERATION_SQL = r"""
CREATE OR REPLACE FUNCTION public.trg_enforce_global_billing_operation_id()
RETURNS TRIGGER AS $$
BEGIN
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(NEW.operation_id::TEXT, 9501)
  );
  IF TG_TABLE_NAME <> 'billing_pricing_admin_event' AND EXISTS (
    SELECT 1 FROM public.billing_pricing_admin_event AS event
    WHERE event.operation_id = NEW.operation_id
  ) THEN
    RAISE EXCEPTION 'Billing operation id was reused' USING ERRCODE = '23505';
  END IF;
  IF TG_TABLE_NAME <> 'billing_subscription_price_application' AND EXISTS (
    SELECT 1 FROM public.billing_subscription_price_application AS application
    WHERE application.operation_id = NEW.operation_id
  ) THEN
    RAISE EXCEPTION 'Billing operation id was reused' USING ERRCODE = '23505';
  END IF;
  IF TG_TABLE_NAME <> 'billing_financial_operation' AND EXISTS (
    SELECT 1 FROM public.billing_financial_operation AS financial_operation
    WHERE financial_operation.operation_id = NEW.operation_id
  ) THEN
    RAISE EXCEPTION 'Billing operation id was reused' USING ERRCODE = '23505';
  END IF;
  IF TG_TABLE_NAME <> 'billing_payment_submission_event' AND EXISTS (
    SELECT 1 FROM public.billing_payment_submission_event AS submission_event
    WHERE submission_event.operation_id = NEW.operation_id
  ) THEN
    RAISE EXCEPTION 'Billing operation id was reused' USING ERRCODE = '23505';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


RESTORE_GLOBAL_OPERATION_SQL = r"""
CREATE OR REPLACE FUNCTION public.trg_enforce_global_billing_operation_id()
RETURNS TRIGGER AS $$
BEGIN
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(NEW.operation_id::TEXT, 9501)
  );
  IF TG_TABLE_NAME <> 'billing_pricing_admin_event' AND EXISTS (
    SELECT 1 FROM public.billing_pricing_admin_event AS event
    WHERE event.operation_id = NEW.operation_id
  ) THEN
    RAISE EXCEPTION 'Billing operation id was reused' USING ERRCODE = '23505';
  END IF;
  IF TG_TABLE_NAME <> 'billing_subscription_price_application' AND EXISTS (
    SELECT 1 FROM public.billing_subscription_price_application AS application
    WHERE application.operation_id = NEW.operation_id
  ) THEN
    RAISE EXCEPTION 'Billing operation id was reused' USING ERRCODE = '23505';
  END IF;
  IF TG_TABLE_NAME <> 'billing_financial_operation' AND EXISTS (
    SELECT 1 FROM public.billing_financial_operation AS financial_operation
    WHERE financial_operation.operation_id = NEW.operation_id
  ) THEN
    RAISE EXCEPTION 'Billing operation id was reused' USING ERRCODE = '23505';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


TENANT_CONTEXT_SQL = r"""
CREATE FUNCTION public.assert_tenant_billing_payment_submission_context(
  p_actor_user_id UUID,
  p_actor_session_id UUID,
  p_tenant_id UUID,
  p_permission_code TEXT
)
RETURNS VOID AS $$
BEGIN
  IF SESSION_USER <> 'aurum_app'
    OR COALESCE(
      pg_catalog.current_setting('app.support_session', true), ''
    ) = 'true'
    OR public.is_support_session()
    OR public.is_tenant_support_session()
    OR p_actor_user_id IS NULL
    OR p_actor_user_id IS DISTINCT FROM public.current_app_user_id()
    OR p_actor_session_id IS NULL
    OR NULLIF(
      pg_catalog.current_setting('app.auth_session_id', true), ''
    ) IS DISTINCT FROM p_actor_session_id::TEXT
    OR p_tenant_id IS NULL
    OR p_tenant_id IS DISTINCT FROM public.current_tenant_id()
    OR p_permission_code IS NULL
    OR NOT public.tenant_actor_has_permission(p_tenant_id, p_permission_code)
  THEN
    RAISE EXCEPTION 'Tenant payment submission context is invalid'
      USING ERRCODE = '42501';
  END IF;

  PERFORM actor.id
  FROM public.app_user AS actor
  JOIN public.session AS auth_session
    ON auth_session.id = p_actor_session_id
   AND auth_session.user_id = actor.id
   AND auth_session.revoked_at IS NULL
   AND auth_session.expires_at > pg_catalog.statement_timestamp()
  JOIN public.tenant AS tenant
    ON tenant.id = p_tenant_id
   AND tenant.status <> 'archived'
  WHERE actor.id = p_actor_user_id
    AND actor.status = 'active'
    AND actor.home_tenant_id = p_tenant_id
  FOR SHARE OF actor, auth_session, tenant;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Tenant payment submission actor is inactive'
      USING ERRCODE = '42501';
  END IF;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


SUBMISSION_GUARD_SQL = r"""
CREATE FUNCTION public.trg_guard_billing_payment_submission_update()
RETURNS TRIGGER AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'Billing payment submissions cannot be deleted'
      USING ERRCODE = '55000';
  END IF;
  IF NEW.id IS DISTINCT FROM OLD.id
    OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
    OR NEW.target_invoice_id IS DISTINCT FROM OLD.target_invoice_id
    OR NEW.amount IS DISTINCT FROM OLD.amount
    OR NEW.currency IS DISTINCT FROM OLD.currency
    OR NEW.paid_at IS DISTINCT FROM OLD.paid_at
    OR NEW.external_reference IS DISTINCT FROM OLD.external_reference
    OR NEW.create_operation_id IS DISTINCT FROM OLD.create_operation_id
    OR NEW.submitted_by IS DISTINCT FROM OLD.submitted_by
    OR NEW.submitted_session_id IS DISTINCT FROM OLD.submitted_session_id
    OR NEW.submitted_at IS DISTINCT FROM OLD.submitted_at
  THEN
    RAISE EXCEPTION 'Billing payment submission facts are immutable'
      USING ERRCODE = '55000';
  END IF;
  IF NEW.row_version <> OLD.row_version + 1 THEN
    RAISE EXCEPTION 'Billing payment submission version is invalid'
      USING ERRCODE = '40001';
  END IF;
  IF NOT (
    (OLD.status = 'submitted' AND NEW.status IN ('under_review','rejected','withdrawn'))
    OR (
      OLD.status = 'under_review'
      AND NEW.status IN ('approved','rejected','duplicate')
    )
  ) THEN
    RAISE EXCEPTION 'Billing payment submission transition is invalid'
      USING ERRCODE = '40001';
  END IF;
  IF NEW.recipient_account_key IS DISTINCT FROM OLD.recipient_account_key
    AND NOT (
      OLD.status = 'submitted'
      AND NEW.status = 'under_review'
      AND OLD.recipient_account_key IS NULL
      AND NEW.recipient_account_key IS NOT NULL
    )
  THEN
    RAISE EXCEPTION 'Billing payment recipient is immutable after review starts'
      USING ERRCODE = '55000';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


AUDIT_SUBMISSION_SQL = r"""
CREATE FUNCTION public.trg_audit_billing_payment_submission()
RETURNS TRIGGER AS $$
DECLARE
  v_operation_id UUID;
BEGIN
  IF TG_OP = 'INSERT' THEN
    v_operation_id := NEW.create_operation_id;
  ELSE
    v_operation_id := COALESCE(
      NEW.decision_operation_id,
      NEW.reject_operation_id,
      NEW.withdraw_operation_id,
      NEW.review_operation_id,
      NEW.create_operation_id
    );
  END IF;
  INSERT INTO public.audit_log (
    tenant_id, user_id, action, table_name, record_id, metadata, created_at
  ) VALUES (
    NEW.tenant_id,
    NULLIF(pg_catalog.current_setting('app.user_id', true), '')::UUID,
    TG_OP,
    'billing_payment_submission',
    NEW.id,
    pg_catalog.jsonb_build_object(
      'operation_id', v_operation_id,
      'status', NEW.status,
      'row_version', NEW.row_version
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


CREATE_SUBMISSION_SQL = r"""
CREATE FUNCTION public.create_tenant_billing_payment_submission(
  p_actor_user_id UUID,
  p_actor_session_id UUID,
  p_operation_id UUID,
  p_request_hash TEXT,
  p_tenant_id UUID,
  p_target_invoice_id UUID,
  p_amount NUMERIC,
  p_paid_at TIMESTAMPTZ,
  p_external_reference TEXT
)
RETURNS TABLE(result JSONB, applied BOOLEAN) AS $$
DECLARE
  v_existing public.billing_payment_submission_event%ROWTYPE;
  v_submission_id UUID;
  v_invoice_number TEXT;
  v_created_at TIMESTAMPTZ;
  v_result JSONB;
BEGIN
  PERFORM public.assert_tenant_billing_payment_submission_context(
    p_actor_user_id,
    p_actor_session_id,
    p_tenant_id,
    'billing.payment_submission.create'
  );
  IF p_operation_id IS NULL
    OR p_request_hash !~ '^[0-9a-f]{64}$'
    OR p_target_invoice_id IS NULL
    OR p_amount IS NULL OR p_amount <= 0 OR p_amount > 999999999999.99
    OR p_amount <> pg_catalog.round(p_amount, 2)
    OR p_paid_at IS NULL
    OR p_paid_at > pg_catalog.statement_timestamp() + INTERVAL '5 minutes'
    OR p_paid_at < pg_catalog.statement_timestamp() - INTERVAL '366 days'
    OR p_external_reference IS NULL
    OR p_external_reference !~ '^[A-Z0-9]{4,128}$'
  THEN
    RAISE EXCEPTION 'Invalid tenant payment submission request'
      USING ERRCODE = '22023';
  END IF;

  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(p_operation_id::TEXT, 9501)
  );
  SELECT * INTO v_existing
  FROM public.billing_payment_submission_event AS submission_event
  WHERE submission_event.operation_id = p_operation_id;
  IF FOUND THEN
    IF v_existing.event_type <> 'payment_submission_created'
      OR v_existing.tenant_id <> p_tenant_id
      OR v_existing.actor_user_id <> p_actor_user_id
      OR v_existing.request_hash <> p_request_hash
    THEN
      RAISE EXCEPTION 'Billing operation id was reused' USING ERRCODE = '23505';
    END IF;
    RETURN QUERY SELECT v_existing.result_snapshot, false;
    RETURN;
  END IF;
  IF EXISTS (
    SELECT 1 FROM public.billing_pricing_admin_event
      WHERE operation_id = p_operation_id
    UNION ALL
    SELECT 1 FROM public.billing_subscription_price_application
      WHERE operation_id = p_operation_id
    UNION ALL
    SELECT 1 FROM public.billing_financial_operation
      WHERE operation_id = p_operation_id
  ) THEN
    RAISE EXCEPTION 'Billing operation id was reused' USING ERRCODE = '23505';
  END IF;

  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(p_tenant_id::TEXT, 9701)
  );
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(p_external_reference, 9702)
  );
  SELECT invoice.invoice_number INTO v_invoice_number
  FROM public.billing_invoice AS invoice
  WHERE invoice.tenant_id = p_tenant_id
    AND invoice.id = p_target_invoice_id
    AND invoice.document_state = 'issued'
  FOR SHARE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Billing invoice was not found' USING ERRCODE = 'P0002';
  END IF;
  IF EXISTS (
    SELECT 1
    FROM public.billing_payment_submission AS submission
    WHERE submission.tenant_id = p_tenant_id
      AND submission.target_invoice_id = p_target_invoice_id
      AND submission.external_reference = p_external_reference
      AND submission.status IN ('submitted','under_review','approved')
  ) THEN
    RAISE EXCEPTION 'Payment submission is already active' USING ERRCODE = '23505';
  END IF;

  v_submission_id := public.gen_random_uuid();
  v_created_at := pg_catalog.statement_timestamp();
  v_result := pg_catalog.jsonb_build_object(
    'submission_id', v_submission_id,
    'tenant_id', p_tenant_id,
    'target_invoice_id', p_target_invoice_id,
    'invoice_number', v_invoice_number,
    'amount', pg_catalog.round(p_amount, 2)::TEXT,
    'currency', 'TJS',
    'paid_at', p_paid_at,
    'reference_suffix', pg_catalog.right(p_external_reference, 4),
    'status', 'submitted',
    'row_version', 1,
    'created_at', v_created_at,
    'decided_at', NULL,
    'reason_code', NULL,
    'can_withdraw', true
  );
  INSERT INTO public.billing_payment_submission (
    id, tenant_id, target_invoice_id, amount, currency, paid_at,
    external_reference, status, row_version, create_operation_id,
    submitted_by, submitted_session_id, submitted_at, updated_at
  ) VALUES (
    v_submission_id, p_tenant_id, p_target_invoice_id,
    pg_catalog.round(p_amount, 2), 'TJS', p_paid_at,
    p_external_reference, 'submitted', 1, p_operation_id,
    p_actor_user_id, p_actor_session_id, v_created_at, v_created_at
  );
  INSERT INTO public.billing_payment_submission_event (
    operation_id, tenant_id, submission_id, event_type, actor_user_id,
    actor_session_id, mfa_verified_at, request_hash, result_snapshot, created_at
  ) VALUES (
    p_operation_id, p_tenant_id, v_submission_id, 'payment_submission_created',
    p_actor_user_id, p_actor_session_id, NULL, p_request_hash, v_result, v_created_at
  );
  RETURN QUERY SELECT v_result, true;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


LIST_TENANT_SUBMISSIONS_SQL = r"""
CREATE FUNCTION public.list_tenant_billing_payment_submissions(
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
  PERFORM public.assert_tenant_billing_payment_submission_context(
    p_actor_user_id,
    p_actor_session_id,
    p_tenant_id,
    'billing.invoice.view'
  );
  IF p_limit IS NULL OR p_limit < 1 OR p_limit > 100
    OR p_offset IS NULL OR p_offset < 0 OR p_offset > 100000
  THEN
    RAISE EXCEPTION 'Invalid tenant payment submission pagination'
      USING ERRCODE = '22023';
  END IF;

  SELECT pg_catalog.jsonb_build_object(
    'items', COALESCE((
      SELECT pg_catalog.jsonb_agg(
        pg_catalog.jsonb_build_object(
          'submission_id', submission.id,
          'tenant_id', submission.tenant_id,
          'target_invoice_id', submission.target_invoice_id,
          'invoice_number', invoice.invoice_number,
          'amount', submission.amount::TEXT,
          'currency', submission.currency,
          'paid_at', submission.paid_at,
          'reference_suffix', pg_catalog.right(
            submission.external_reference, 4
          ),
          'status', submission.status,
          'row_version', submission.row_version,
          'created_at', submission.submitted_at,
          'decided_at', COALESCE(submission.rejected_at, submission.decided_at),
          'reason_code', COALESCE(
            submission.rejection_reason_code,
            submission.decision_reason_code
          ),
          'can_withdraw', submission.status = 'submitted'
        ) ORDER BY submission.submitted_at DESC, submission.id DESC
      )
      FROM (
        SELECT item.*
        FROM public.billing_payment_submission AS item
        WHERE item.tenant_id = p_tenant_id
        ORDER BY item.submitted_at DESC, item.id DESC
        LIMIT p_limit OFFSET p_offset
      ) AS submission
      JOIN public.billing_invoice AS invoice
        ON invoice.tenant_id = submission.tenant_id
       AND invoice.id = submission.target_invoice_id
    ), '[]'::JSONB),
    'total', (
      SELECT pg_catalog.count(*)
      FROM public.billing_payment_submission AS submission
      WHERE submission.tenant_id = p_tenant_id
    )
  ) INTO v_result;
  RETURN v_result;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


WITHDRAW_SUBMISSION_SQL = r"""
CREATE FUNCTION public.withdraw_tenant_billing_payment_submission(
  p_actor_user_id UUID,
  p_actor_session_id UUID,
  p_operation_id UUID,
  p_request_hash TEXT,
  p_tenant_id UUID,
  p_submission_id UUID,
  p_expected_row_version INTEGER
)
RETURNS TABLE(result JSONB, applied BOOLEAN) AS $$
DECLARE
  v_existing public.billing_payment_submission_event%ROWTYPE;
  v_submission public.billing_payment_submission%ROWTYPE;
  v_invoice_number TEXT;
  v_created_at TIMESTAMPTZ;
  v_result JSONB;
BEGIN
  PERFORM public.assert_tenant_billing_payment_submission_context(
    p_actor_user_id,
    p_actor_session_id,
    p_tenant_id,
    'billing.payment_submission.withdraw'
  );
  IF p_operation_id IS NULL
    OR p_request_hash !~ '^[0-9a-f]{64}$'
    OR p_submission_id IS NULL
    OR p_expected_row_version IS NULL OR p_expected_row_version < 1
  THEN
    RAISE EXCEPTION 'Invalid payment submission withdrawal request'
      USING ERRCODE = '22023';
  END IF;

  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(p_operation_id::TEXT, 9501)
  );
  SELECT * INTO v_existing
  FROM public.billing_payment_submission_event AS submission_event
  WHERE submission_event.operation_id = p_operation_id;
  IF FOUND THEN
    IF v_existing.event_type <> 'payment_submission_withdrawn'
      OR v_existing.tenant_id <> p_tenant_id
      OR v_existing.submission_id <> p_submission_id
      OR v_existing.actor_user_id <> p_actor_user_id
      OR v_existing.request_hash <> p_request_hash
    THEN
      RAISE EXCEPTION 'Billing operation id was reused' USING ERRCODE = '23505';
    END IF;
    RETURN QUERY SELECT v_existing.result_snapshot, false;
    RETURN;
  END IF;
  IF EXISTS (
    SELECT 1 FROM public.billing_pricing_admin_event
      WHERE operation_id = p_operation_id
    UNION ALL
    SELECT 1 FROM public.billing_subscription_price_application
      WHERE operation_id = p_operation_id
    UNION ALL
    SELECT 1 FROM public.billing_financial_operation
      WHERE operation_id = p_operation_id
  ) THEN
    RAISE EXCEPTION 'Billing operation id was reused' USING ERRCODE = '23505';
  END IF;

  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(p_tenant_id::TEXT, 9701)
  );
  SELECT * INTO v_submission
  FROM public.billing_payment_submission AS submission
  WHERE submission.tenant_id = p_tenant_id
    AND submission.id = p_submission_id
  FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Payment submission was not found' USING ERRCODE = 'P0002';
  END IF;
  IF v_submission.status <> 'submitted'
    OR v_submission.row_version <> p_expected_row_version
  THEN
    RAISE EXCEPTION 'Payment submission changed concurrently'
      USING ERRCODE = '40001';
  END IF;
  SELECT invoice.invoice_number INTO v_invoice_number
  FROM public.billing_invoice AS invoice
  WHERE invoice.tenant_id = p_tenant_id
    AND invoice.id = v_submission.target_invoice_id;

  v_created_at := pg_catalog.statement_timestamp();
  v_result := pg_catalog.jsonb_build_object(
    'submission_id', v_submission.id,
    'tenant_id', p_tenant_id,
    'target_invoice_id', v_submission.target_invoice_id,
    'invoice_number', v_invoice_number,
    'amount', v_submission.amount::TEXT,
    'currency', v_submission.currency,
    'paid_at', v_submission.paid_at,
    'reference_suffix', pg_catalog.right(v_submission.external_reference, 4),
    'status', 'withdrawn',
    'row_version', v_submission.row_version + 1,
    'created_at', v_submission.submitted_at,
    'decided_at', v_created_at,
    'reason_code', NULL,
    'can_withdraw', false
  );
  INSERT INTO public.billing_payment_submission_event (
    operation_id, tenant_id, submission_id, event_type, actor_user_id,
    actor_session_id, mfa_verified_at, request_hash, result_snapshot, created_at
  ) VALUES (
    p_operation_id, p_tenant_id, v_submission.id, 'payment_submission_withdrawn',
    p_actor_user_id, p_actor_session_id, NULL, p_request_hash, v_result, v_created_at
  );
  UPDATE public.billing_payment_submission
  SET status = 'withdrawn', row_version = row_version + 1,
      withdraw_operation_id = p_operation_id,
      withdrawn_by = p_actor_user_id,
      withdrawn_session_id = p_actor_session_id,
      withdrawn_at = v_created_at,
      updated_at = v_created_at
  WHERE tenant_id = p_tenant_id AND id = v_submission.id;
  RETURN QUERY SELECT v_result, true;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


LIST_PLATFORM_SUBMISSIONS_SQL = r"""
CREATE FUNCTION public.list_platform_billing_payment_submissions(
  p_actor_user_id UUID,
  p_actor_session_id UUID,
  p_tenant_id UUID,
  p_status TEXT,
  p_limit INTEGER,
  p_offset INTEGER
)
RETURNS JSONB AS $$
DECLARE
  v_result JSONB;
BEGIN
  PERFORM public.assert_and_lock_platform_recent_capability(
    p_actor_user_id,
    p_actor_session_id,
    'platform.billing.payment.review'
  );
  IF p_tenant_id IS NULL
    OR (
      p_status IS NOT NULL
      AND p_status NOT IN (
        'submitted','under_review','approved','rejected','duplicate','withdrawn'
      )
    )
    OR p_limit IS NULL OR p_limit < 1 OR p_limit > 100
    OR p_offset IS NULL OR p_offset < 0 OR p_offset > 100000
    OR NOT EXISTS (
      SELECT 1 FROM public.tenant AS tenant WHERE tenant.id = p_tenant_id
    )
  THEN
    RAISE EXCEPTION 'Invalid platform payment submission list request'
      USING ERRCODE = '22023';
  END IF;

  SELECT pg_catalog.jsonb_build_object(
    'items', COALESCE((
      SELECT pg_catalog.jsonb_agg(
        pg_catalog.jsonb_build_object(
          'submission_id', submission.id,
          'tenant_id', submission.tenant_id,
          'tenant_name', tenant.name,
          'target_invoice_id', submission.target_invoice_id,
          'invoice_number', invoice.invoice_number,
          'amount', submission.amount::TEXT,
          'currency', submission.currency,
          'paid_at', submission.paid_at,
          'reference_suffix', pg_catalog.right(
            submission.external_reference, 4
          ),
          'status', submission.status,
          'row_version', submission.row_version,
          'created_at', submission.submitted_at,
          'decided_at', COALESCE(submission.rejected_at, submission.decided_at),
          'reason_code', COALESCE(
            submission.rejection_reason_code,
            submission.decision_reason_code
          ),
          'can_withdraw', submission.status = 'submitted'
        ) ORDER BY submission.submitted_at, submission.id
      )
      FROM (
        SELECT item.*
        FROM public.billing_payment_submission AS item
        WHERE item.tenant_id = p_tenant_id
          AND (p_status IS NULL OR item.status = p_status)
        ORDER BY item.submitted_at, item.id
        LIMIT p_limit OFFSET p_offset
      ) AS submission
      JOIN public.tenant AS tenant ON tenant.id = submission.tenant_id
      JOIN public.billing_invoice AS invoice
        ON invoice.tenant_id = submission.tenant_id
       AND invoice.id = submission.target_invoice_id
    ), '[]'::JSONB),
    'total', (
      SELECT pg_catalog.count(*)
      FROM public.billing_payment_submission AS submission
      WHERE submission.tenant_id = p_tenant_id
        AND (p_status IS NULL OR submission.status = p_status)
    )
  ) INTO v_result;
  RETURN v_result;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


READ_PLATFORM_SUBMISSION_SQL = r"""
CREATE FUNCTION public.read_platform_billing_payment_submission(
  p_actor_user_id UUID,
  p_actor_session_id UUID,
  p_tenant_id UUID,
  p_submission_id UUID
)
RETURNS JSONB AS $$
DECLARE
  v_submission public.billing_payment_submission%ROWTYPE;
  v_invoice_number TEXT;
  v_tenant_name TEXT;
  v_result JSONB;
BEGIN
  PERFORM public.assert_and_lock_platform_recent_capability(
    p_actor_user_id,
    p_actor_session_id,
    'platform.billing.payment.review'
  );
  IF p_tenant_id IS NULL OR p_submission_id IS NULL THEN
    RAISE EXCEPTION 'Invalid platform payment submission detail request'
      USING ERRCODE = '22023';
  END IF;
  SELECT submission.*
  INTO v_submission
  FROM public.billing_payment_submission AS submission
  WHERE submission.tenant_id = p_tenant_id
    AND submission.id = p_submission_id
    AND submission.status = 'submitted';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Payment submission was not found' USING ERRCODE = 'P0002';
  END IF;
  SELECT invoice.invoice_number, tenant.name
  INTO v_invoice_number, v_tenant_name
  FROM public.billing_invoice AS invoice
  JOIN public.tenant AS tenant ON tenant.id = invoice.tenant_id
  WHERE invoice.tenant_id = v_submission.tenant_id
    AND invoice.id = v_submission.target_invoice_id;

  INSERT INTO public.audit_log (
    tenant_id, user_id, action, table_name, record_id, metadata, created_at
  ) VALUES (
    p_tenant_id,
    p_actor_user_id,
    'VIEW',
    'billing_payment_submission_sensitive_detail',
    p_submission_id,
    pg_catalog.jsonb_strip_nulls(pg_catalog.jsonb_build_object(
      'request_id', NULLIF(
        pg_catalog.current_setting('app.request_id', true), ''
      ),
      'status', v_submission.status,
      'row_version', v_submission.row_version
    )),
    pg_catalog.statement_timestamp()
  );
  v_result := pg_catalog.jsonb_build_object(
    'submission_id', v_submission.id,
    'tenant_id', v_submission.tenant_id,
    'tenant_name', v_tenant_name,
    'target_invoice_id', v_submission.target_invoice_id,
    'invoice_number', v_invoice_number,
    'amount', v_submission.amount::TEXT,
    'currency', v_submission.currency,
    'paid_at', v_submission.paid_at,
    'reference_suffix', pg_catalog.right(v_submission.external_reference, 4),
    'external_reference', v_submission.external_reference,
    'status', v_submission.status,
    'row_version', v_submission.row_version,
    'created_at', v_submission.submitted_at,
    'decided_at', COALESCE(v_submission.rejected_at, v_submission.decided_at),
    'reason_code', COALESCE(
      v_submission.rejection_reason_code,
      v_submission.decision_reason_code
    ),
    'can_withdraw', v_submission.status = 'submitted'
  );
  RETURN v_result;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


PROMOTE_SUBMISSION_SQL = r"""
CREATE FUNCTION public.promote_billing_payment_submission_to_review(
  p_actor_user_id UUID,
  p_actor_session_id UUID,
  p_operation_id UUID,
  p_request_hash TEXT,
  p_tenant_id UUID,
  p_submission_id UUID,
  p_expected_row_version INTEGER,
  p_recipient_account_key TEXT
)
RETURNS TABLE(result JSONB, applied BOOLEAN) AS $$
DECLARE
  v_mfa_at TIMESTAMPTZ;
  v_payload JSONB;
  v_existing public.billing_financial_operation%ROWTYPE;
  v_submission public.billing_payment_submission%ROWTYPE;
  v_review_id UUID;
  v_invoice_number TEXT;
  v_created_at TIMESTAMPTZ;
  v_result JSONB;
BEGIN
  v_mfa_at := public.assert_and_lock_platform_recent_capability(
    p_actor_user_id,
    p_actor_session_id,
    'platform.billing.payment.review'
  );
  IF p_operation_id IS NULL
    OR p_request_hash !~ '^[0-9a-f]{64}$'
    OR p_tenant_id IS NULL OR p_submission_id IS NULL
    OR p_expected_row_version IS NULL OR p_expected_row_version < 1
    OR p_recipient_account_key IS NULL
    OR p_recipient_account_key !~ '^[a-z0-9][a-z0-9_.:-]{2,63}$'
  THEN
    RAISE EXCEPTION 'Invalid payment submission promotion request'
      USING ERRCODE = '22023';
  END IF;
  v_payload := pg_catalog.jsonb_build_object(
    'tenant_id', p_tenant_id,
    'submission_id', p_submission_id,
    'expected_row_version', p_expected_row_version,
    'recipient_account_key', p_recipient_account_key
  );

  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(p_operation_id::TEXT, 9501)
  );
  SELECT * INTO v_existing
  FROM public.billing_financial_operation AS financial_operation
  WHERE financial_operation.operation_id = p_operation_id;
  IF FOUND THEN
    IF v_existing.operation_type <> 'payment_submission_review_created'
      OR v_existing.tenant_id <> p_tenant_id
      OR v_existing.actor_user_id <> p_actor_user_id
      OR v_existing.request_hash <> p_request_hash
      OR v_existing.request_payload <> v_payload
    THEN
      RAISE EXCEPTION 'Billing operation id was reused' USING ERRCODE = '23505';
    END IF;
    RETURN QUERY SELECT v_existing.result_snapshot, false;
    RETURN;
  END IF;
  IF EXISTS (
    SELECT 1 FROM public.billing_pricing_admin_event
      WHERE operation_id = p_operation_id
    UNION ALL
    SELECT 1 FROM public.billing_subscription_price_application
      WHERE operation_id = p_operation_id
    UNION ALL
    SELECT 1 FROM public.billing_payment_submission_event
      WHERE operation_id = p_operation_id
  ) THEN
    RAISE EXCEPTION 'Billing operation id was reused' USING ERRCODE = '23505';
  END IF;

  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(p_tenant_id::TEXT, 9701)
  );
  SELECT * INTO v_submission
  FROM public.billing_payment_submission AS submission
  WHERE submission.tenant_id = p_tenant_id
    AND submission.id = p_submission_id
  FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Payment submission was not found' USING ERRCODE = 'P0002';
  END IF;
  IF v_submission.status <> 'submitted'
    OR v_submission.row_version <> p_expected_row_version
  THEN
    RAISE EXCEPTION 'Payment submission changed concurrently'
      USING ERRCODE = '40001';
  END IF;
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(
      p_recipient_account_key || ':' || v_submission.external_reference,
      9702
    )
  );
  SELECT invoice.invoice_number INTO v_invoice_number
  FROM public.billing_invoice AS invoice
  WHERE invoice.tenant_id = p_tenant_id
    AND invoice.id = v_submission.target_invoice_id
    AND invoice.document_state = 'issued'
  FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Billing invoice was not found' USING ERRCODE = 'P0002';
  END IF;
  IF EXISTS (
    SELECT 1
    FROM public.billing_payment AS payment
    WHERE payment.recipient_account_key = p_recipient_account_key
      AND payment.external_reference = v_submission.external_reference
  ) OR EXISTS (
    SELECT 1
    FROM public.billing_payment_review AS review
    WHERE review.recipient_account_key = p_recipient_account_key
      AND review.external_reference = v_submission.external_reference
      AND review.status = 'pending_approval'
  ) THEN
    RAISE EXCEPTION 'Bank payment is already registered' USING ERRCODE = '23505';
  END IF;

  v_review_id := public.gen_random_uuid();
  v_created_at := pg_catalog.statement_timestamp();
  v_result := pg_catalog.jsonb_build_object(
    'review_id', v_review_id,
    'tenant_id', p_tenant_id,
    'target_invoice_id', v_submission.target_invoice_id,
    'amount', v_submission.amount::TEXT,
    'currency', v_submission.currency,
    'paid_at', v_submission.paid_at,
    'status', 'pending_approval',
    'row_version', 1,
    'created_at', v_created_at,
    'decided_at', NULL,
    'reason_code', NULL
  );
  INSERT INTO public.billing_financial_operation (
    operation_id, operation_type, tenant_id, actor_user_id, actor_session_id,
    mfa_verified_at, request_hash, request_payload, result_snapshot, created_at
  ) VALUES (
    p_operation_id, 'payment_submission_review_created', p_tenant_id,
    p_actor_user_id, p_actor_session_id, v_mfa_at, p_request_hash,
    v_payload, v_result, v_created_at
  );
  INSERT INTO public.billing_payment_review (
    id, tenant_id, target_invoice_id, amount, currency, paid_at,
    recipient_account_key, external_reference, status, row_version,
    reviewed_by, reviewed_session_id, review_mfa_verified_at,
    review_operation_id, source_submission_id, created_at, updated_at
  ) VALUES (
    v_review_id, p_tenant_id, v_submission.target_invoice_id,
    v_submission.amount, v_submission.currency, v_submission.paid_at,
    p_recipient_account_key, v_submission.external_reference,
    'pending_approval', 1, p_actor_user_id, p_actor_session_id, v_mfa_at,
    p_operation_id, v_submission.id, v_created_at, v_created_at
  );
  UPDATE public.billing_payment_submission
  SET status = 'under_review', row_version = row_version + 1,
      recipient_account_key = p_recipient_account_key,
      review_id = v_review_id,
      review_operation_id = p_operation_id,
      reviewed_at = v_created_at,
      updated_at = v_created_at
  WHERE tenant_id = p_tenant_id AND id = v_submission.id;
  INSERT INTO public.billing_outbox_event (
    tenant_id, operation_id, event_type, aggregate_type, aggregate_id,
    payload, created_at
  ) VALUES (
    p_tenant_id, p_operation_id, 'billing.payment_submission.under_review',
    'billing_payment_submission', v_submission.id,
    pg_catalog.jsonb_build_object(
      'submission_id', v_submission.id,
      'tenant_id', p_tenant_id,
      'review_id', v_review_id
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


REJECT_PLATFORM_SUBMISSION_SQL = r"""
CREATE FUNCTION public.reject_platform_billing_payment_submission(
  p_actor_user_id UUID,
  p_actor_session_id UUID,
  p_operation_id UUID,
  p_request_hash TEXT,
  p_tenant_id UUID,
  p_submission_id UUID,
  p_expected_row_version INTEGER,
  p_reason_code TEXT,
  p_reason_note TEXT
)
RETURNS TABLE(result JSONB, applied BOOLEAN) AS $$
DECLARE
  v_mfa_at TIMESTAMPTZ;
  v_existing public.billing_payment_submission_event%ROWTYPE;
  v_submission public.billing_payment_submission%ROWTYPE;
  v_invoice_number TEXT;
  v_created_at TIMESTAMPTZ;
  v_result JSONB;
BEGIN
  v_mfa_at := public.assert_and_lock_platform_recent_capability(
    p_actor_user_id,
    p_actor_session_id,
    'platform.billing.payment.review'
  );
  p_reason_note := NULLIF(pg_catalog.btrim(p_reason_note), '');
  IF p_operation_id IS NULL
    OR p_request_hash !~ '^[0-9a-f]{64}$'
    OR p_tenant_id IS NULL OR p_submission_id IS NULL
    OR p_expected_row_version IS NULL OR p_expected_row_version < 1
    OR p_reason_code IS NULL
    OR p_reason_code NOT IN (
      'bank_payment_not_found','amount_mismatch',
      'date_mismatch','duplicate','wrong_tenant_or_invoice','other'
    )
    OR (p_reason_note IS NOT NULL AND pg_catalog.length(p_reason_note) > 500)
    OR (
      p_reason_code = 'other'
      AND COALESCE(pg_catalog.length(p_reason_note), 0) < 10
    )
  THEN
    RAISE EXCEPTION 'Invalid platform payment submission rejection request'
      USING ERRCODE = '22023';
  END IF;

  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(p_operation_id::TEXT, 9501)
  );
  SELECT * INTO v_existing
  FROM public.billing_payment_submission_event AS submission_event
  WHERE submission_event.operation_id = p_operation_id;
  IF FOUND THEN
    IF v_existing.event_type <> 'payment_submission_rejected'
      OR v_existing.tenant_id <> p_tenant_id
      OR v_existing.submission_id <> p_submission_id
      OR v_existing.actor_user_id <> p_actor_user_id
      OR v_existing.request_hash <> p_request_hash
    THEN
      RAISE EXCEPTION 'Billing operation id was reused' USING ERRCODE = '23505';
    END IF;
    RETURN QUERY SELECT v_existing.result_snapshot, false;
    RETURN;
  END IF;
  IF EXISTS (
    SELECT 1 FROM public.billing_pricing_admin_event
      WHERE operation_id = p_operation_id
    UNION ALL
    SELECT 1 FROM public.billing_subscription_price_application
      WHERE operation_id = p_operation_id
    UNION ALL
    SELECT 1 FROM public.billing_financial_operation
      WHERE operation_id = p_operation_id
  ) THEN
    RAISE EXCEPTION 'Billing operation id was reused' USING ERRCODE = '23505';
  END IF;

  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(p_tenant_id::TEXT, 9701)
  );
  SELECT * INTO v_submission
  FROM public.billing_payment_submission AS submission
  WHERE submission.tenant_id = p_tenant_id
    AND submission.id = p_submission_id
  FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Payment submission was not found' USING ERRCODE = 'P0002';
  END IF;
  IF v_submission.status <> 'submitted'
    OR v_submission.row_version <> p_expected_row_version
  THEN
    RAISE EXCEPTION 'Payment submission changed concurrently'
      USING ERRCODE = '40001';
  END IF;
  SELECT invoice.invoice_number INTO v_invoice_number
  FROM public.billing_invoice AS invoice
  WHERE invoice.tenant_id = p_tenant_id
    AND invoice.id = v_submission.target_invoice_id;

  v_created_at := pg_catalog.statement_timestamp();
  v_result := pg_catalog.jsonb_build_object(
    'submission_id', v_submission.id,
    'tenant_id', p_tenant_id,
    'target_invoice_id', v_submission.target_invoice_id,
    'invoice_number', v_invoice_number,
    'amount', v_submission.amount::TEXT,
    'currency', v_submission.currency,
    'paid_at', v_submission.paid_at,
    'reference_suffix', pg_catalog.right(v_submission.external_reference, 4),
    'status', 'rejected',
    'row_version', v_submission.row_version + 1,
    'created_at', v_submission.submitted_at,
    'decided_at', v_created_at,
    'reason_code', p_reason_code,
    'can_withdraw', false
  );
  INSERT INTO public.billing_payment_submission_event (
    operation_id, tenant_id, submission_id, event_type, actor_user_id,
    actor_session_id, mfa_verified_at, request_hash, result_snapshot, created_at
  ) VALUES (
    p_operation_id, p_tenant_id, v_submission.id, 'payment_submission_rejected',
    p_actor_user_id, p_actor_session_id, v_mfa_at,
    p_request_hash, v_result, v_created_at
  );
  UPDATE public.billing_payment_submission
  SET status = 'rejected', row_version = row_version + 1,
      reject_operation_id = p_operation_id,
      rejected_by = p_actor_user_id,
      rejected_session_id = p_actor_session_id,
      rejected_mfa_verified_at = v_mfa_at,
      rejection_reason_code = p_reason_code,
      rejection_note = p_reason_note,
      rejected_at = v_created_at,
      updated_at = v_created_at
  WHERE tenant_id = p_tenant_id AND id = v_submission.id;
  RETURN QUERY SELECT v_result, true;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


SYNC_REVIEW_SQL = r"""
CREATE FUNCTION public.trg_sync_billing_payment_submission_review_status()
RETURNS TRIGGER AS $$
DECLARE
  v_operation_id UUID;
  v_decided_at TIMESTAMPTZ;
BEGIN
  IF NEW.source_submission_id IS NULL
    OR NEW.status NOT IN ('approved','rejected','duplicate')
    OR NEW.status IS NOT DISTINCT FROM OLD.status
  THEN
    RETURN NEW;
  END IF;
  v_operation_id := CASE
    WHEN NEW.status = 'approved' THEN NEW.approved_operation_id
    ELSE NEW.rejected_operation_id
  END;
  v_decided_at := CASE
    WHEN NEW.status = 'approved' THEN NEW.approved_at
    ELSE NEW.rejected_at
  END;
  IF v_operation_id IS NULL OR v_decided_at IS NULL THEN
    RAISE EXCEPTION 'Payment review decision metadata is incomplete'
      USING ERRCODE = '23514';
  END IF;
  UPDATE public.billing_payment_submission
  SET status = NEW.status,
      row_version = row_version + 1,
      decision_operation_id = v_operation_id,
      decision_reason_code = NEW.rejection_reason_code,
      decided_at = v_decided_at,
      updated_at = v_decided_at
  WHERE tenant_id = NEW.tenant_id
    AND id = NEW.source_submission_id
    AND review_id = NEW.id
    AND status = 'under_review';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Payment submission changed concurrently'
      USING ERRCODE = '40001';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


TENANT_CONTEXT_SIGNATURE = (
    "public.assert_tenant_billing_payment_submission_context(UUID, UUID, UUID, TEXT)"
)
CREATE_SIGNATURE = (
    "public.create_tenant_billing_payment_submission("
    "UUID, UUID, UUID, TEXT, UUID, UUID, NUMERIC, TIMESTAMPTZ, TEXT)"
)
LIST_TENANT_SIGNATURE = (
    "public.list_tenant_billing_payment_submissions(UUID, UUID, UUID, INTEGER, INTEGER)"
)
WITHDRAW_SIGNATURE = (
    "public.withdraw_tenant_billing_payment_submission("
    "UUID, UUID, UUID, TEXT, UUID, UUID, INTEGER)"
)
LIST_PLATFORM_SIGNATURE = (
    "public.list_platform_billing_payment_submissions(" "UUID, UUID, UUID, TEXT, INTEGER, INTEGER)"
)
READ_PLATFORM_SIGNATURE = "public.read_platform_billing_payment_submission(UUID, UUID, UUID, UUID)"
PROMOTE_SIGNATURE = (
    "public.promote_billing_payment_submission_to_review("
    "UUID, UUID, UUID, TEXT, UUID, UUID, INTEGER, TEXT)"
)
REJECT_PLATFORM_SIGNATURE = (
    "public.reject_platform_billing_payment_submission("
    "UUID, UUID, UUID, TEXT, UUID, UUID, INTEGER, TEXT, TEXT)"
)


def _secure_function(signature: str, *, grantee: str | None = None) -> None:
    op.execute(f"ALTER FUNCTION {signature} OWNER TO aurum_schema_owner")
    op.execute(
        f"REVOKE ALL PRIVILEGES ON FUNCTION {signature} "
        "FROM PUBLIC, aurum_app, aurum_support, aurum_mailer, "
        "aurum_edge_cash_executor, aurum_edge_cash_owner"
    )
    if grantee is not None:
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {grantee}")


def _secure_table(table: str) -> None:
    op.execute(f"ALTER TABLE public.{table} OWNER TO aurum_schema_owner")
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
        CREATE TEMPORARY TABLE aurum_0101_missing_reference_privilege (
          table_name TEXT PRIMARY KEY
        ) ON COMMIT DROP
        """)
    op.execute("""
        DO $$
        DECLARE
          target_table TEXT;
        BEGIN
          FOREACH target_table IN ARRAY ARRAY[
            'app_user', 'tenant', 'billing_invoice',
            'billing_payment_review', 'billing_financial_operation'
          ]
          LOOP
            IF NOT pg_catalog.has_table_privilege(
              'aurum_schema_owner',
              pg_catalog.format('public.%I', target_table),
              'REFERENCES'
            ) THEN
              INSERT INTO pg_temp.aurum_0101_missing_reference_privilege(table_name)
              VALUES (target_table);
              EXECUTE pg_catalog.format(
                'GRANT REFERENCES ON TABLE public.%I TO aurum_schema_owner',
                target_table
              );
            END IF;
          END LOOP;
        END
        $$
        """)


def _restore_reference_privileges() -> None:
    op.execute("""
        DO $$
        DECLARE
          target_table TEXT;
        BEGIN
          FOR target_table IN
            SELECT table_name
            FROM pg_temp.aurum_0101_missing_reference_privilege
          LOOP
            EXECUTE pg_catalog.format(
              'REVOKE REFERENCES ON TABLE public.%I FROM aurum_schema_owner',
              target_table
            );
          END LOOP;
        END
        $$
        """)
    op.execute("DROP TABLE pg_temp.aurum_0101_missing_reference_privilege")


def _seed_permissions() -> None:
    for code, name, description in TENANT_PERMISSIONS:
        op.execute(f"""
            INSERT INTO public.permission (
              code, group_code, name, description, min_level_required,
              is_dangerous, is_active, scope_type, target_role_type, risk_level,
              developer_grantable, administrator_grantable, owner_grantable,
              developer_delegable, administrator_delegable, owner_delegable,
              requires_step_up, requires_confirmation
            ) VALUES (
              '{code}', 'billing', '{name}', '{description}', 3,
              false, true, 'TENANT_ALL', 'tenant', 'normal',
              true, true, true, true, true, true, false, false
            )
            ON CONFLICT (code) DO NOTHING
            """)
        op.execute(f"""
            INSERT INTO public.role_permission (role_id, permission_code)
            SELECT role.id, '{code}'
            FROM public.role AS role
            WHERE role.is_system = true
            ON CONFLICT (role_id, permission_code) DO NOTHING
            """)
        op.execute(f"""
            INSERT INTO public.role_template_permission (template_id, permission_code)
            SELECT template.id, '{code}'
            FROM public.role_template AS template
            WHERE template.slug = 'owner' AND template.is_active
            ON CONFLICT (template_id, permission_code) DO NOTHING
            """)
        op.execute(f"""
            INSERT INTO public.role_permission (role_id, permission_code)
            SELECT role.id, '{code}'
            FROM public.role AS role
            WHERE role.is_protected = true
              AND role.protected_kind = 'tenant_owner'
              AND role.is_active = true
            ON CONFLICT (role_id, permission_code) DO NOTHING
            """)


def upgrade() -> None:
    _grant_missing_reference_privileges()
    _seed_permissions()
    op.execute("""
        ALTER TABLE public.billing_financial_operation
        DROP CONSTRAINT ck_billing_financial_operation_type,
        ADD CONSTRAINT ck_billing_financial_operation_type CHECK (
          operation_type IN (
            'invoice_issued','payment_review_created','payment_approved',
            'payment_review_rejected','payment_adjustment_requested',
            'payment_adjustment_approved','payment_adjustment_rejected',
            'payment_submission_review_created'
          )
        )
        """)
    op.execute("""
        CREATE TABLE public.billing_payment_submission (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id UUID NOT NULL,
          target_invoice_id UUID NOT NULL,
          amount NUMERIC(14,2) NOT NULL,
          currency TEXT NOT NULL DEFAULT 'TJS',
          paid_at TIMESTAMPTZ NOT NULL,
          external_reference TEXT NOT NULL,
          recipient_account_key TEXT,
          status TEXT NOT NULL DEFAULT 'submitted',
          row_version INTEGER NOT NULL DEFAULT 1,
          create_operation_id UUID NOT NULL UNIQUE,
          submitted_by UUID NOT NULL REFERENCES public.app_user(id) ON DELETE RESTRICT,
          submitted_session_id UUID NOT NULL,
          submitted_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          review_id UUID UNIQUE,
          review_operation_id UUID UNIQUE,
          reviewed_at TIMESTAMPTZ,
          withdraw_operation_id UUID UNIQUE,
          withdrawn_by UUID REFERENCES public.app_user(id) ON DELETE RESTRICT,
          withdrawn_session_id UUID,
          withdrawn_at TIMESTAMPTZ,
          reject_operation_id UUID UNIQUE,
          rejected_by UUID REFERENCES public.app_user(id) ON DELETE RESTRICT,
          rejected_session_id UUID,
          rejected_mfa_verified_at TIMESTAMPTZ,
          rejection_reason_code TEXT,
          rejection_note TEXT,
          rejected_at TIMESTAMPTZ,
          decision_operation_id UUID UNIQUE,
          decision_reason_code TEXT,
          decided_at TIMESTAMPTZ,
          updated_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT uq_billing_payment_submission_tenant_id UNIQUE (tenant_id, id),
          CONSTRAINT fk_billing_submission_tenant FOREIGN KEY (tenant_id)
            REFERENCES public.tenant(id) ON DELETE RESTRICT,
          CONSTRAINT fk_billing_submission_invoice
            FOREIGN KEY (tenant_id, target_invoice_id)
            REFERENCES public.billing_invoice(tenant_id, id) ON DELETE RESTRICT,
          CONSTRAINT fk_billing_submission_review
            FOREIGN KEY (tenant_id, review_id)
            REFERENCES public.billing_payment_review(tenant_id, id)
            ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
          CONSTRAINT fk_billing_submission_review_operation
            FOREIGN KEY (tenant_id, review_operation_id)
            REFERENCES public.billing_financial_operation(tenant_id, operation_id)
            ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
          CONSTRAINT fk_billing_submission_decision_operation
            FOREIGN KEY (tenant_id, decision_operation_id)
            REFERENCES public.billing_financial_operation(tenant_id, operation_id)
            ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
          CONSTRAINT ck_billing_submission_money CHECK (
            amount > 0 AND amount <= 999999999999.99 AND currency = 'TJS'
          ),
          CONSTRAINT ck_billing_submission_reference CHECK (
            external_reference ~ '^[A-Z0-9]{4,128}$'
          ),
          CONSTRAINT ck_billing_submission_account_key CHECK (
            recipient_account_key IS NULL
            OR recipient_account_key ~ '^[a-z0-9][a-z0-9_.:-]{2,63}$'
          ),
          CONSTRAINT ck_billing_submission_status CHECK (
            status IN (
              'submitted','under_review','approved','rejected','duplicate','withdrawn'
            )
          ),
          CONSTRAINT ck_billing_submission_version CHECK (row_version > 0),
          CONSTRAINT ck_billing_submission_reason CHECK (
            rejection_reason_code IS NULL OR rejection_reason_code IN (
              'bank_payment_not_found','amount_mismatch',
              'date_mismatch','duplicate','wrong_tenant_or_invoice','other'
            )
          ),
          CONSTRAINT ck_billing_submission_note CHECK (
            rejection_note IS NULL
            OR pg_catalog.length(rejection_note) BETWEEN 1 AND 500
          ),
          CONSTRAINT ck_billing_submission_timestamps CHECK (
            updated_at >= submitted_at
          ),
          CONSTRAINT ck_billing_submission_lifecycle CHECK (
            (
              status = 'submitted'
              AND recipient_account_key IS NULL
              AND review_id IS NULL AND review_operation_id IS NULL
              AND reviewed_at IS NULL
              AND withdraw_operation_id IS NULL AND withdrawn_by IS NULL
              AND withdrawn_session_id IS NULL AND withdrawn_at IS NULL
              AND reject_operation_id IS NULL AND rejected_by IS NULL
              AND rejected_session_id IS NULL AND rejected_mfa_verified_at IS NULL
              AND rejection_reason_code IS NULL AND rejection_note IS NULL
              AND rejected_at IS NULL AND decision_operation_id IS NULL
              AND decision_reason_code IS NULL AND decided_at IS NULL
            ) OR (
              status = 'under_review'
              AND recipient_account_key IS NOT NULL
              AND review_id IS NOT NULL AND review_operation_id IS NOT NULL
              AND reviewed_at IS NOT NULL
              AND withdraw_operation_id IS NULL AND withdrawn_by IS NULL
              AND withdrawn_session_id IS NULL AND withdrawn_at IS NULL
              AND reject_operation_id IS NULL AND rejected_by IS NULL
              AND rejected_session_id IS NULL AND rejected_mfa_verified_at IS NULL
              AND rejection_reason_code IS NULL AND rejection_note IS NULL
              AND rejected_at IS NULL AND decision_operation_id IS NULL
              AND decision_reason_code IS NULL AND decided_at IS NULL
            ) OR (
              status = 'withdrawn'
              AND recipient_account_key IS NULL
              AND review_id IS NULL AND review_operation_id IS NULL
              AND reviewed_at IS NULL
              AND withdraw_operation_id IS NOT NULL AND withdrawn_by IS NOT NULL
              AND withdrawn_session_id IS NOT NULL AND withdrawn_at IS NOT NULL
              AND reject_operation_id IS NULL AND rejected_by IS NULL
              AND rejected_session_id IS NULL AND rejected_mfa_verified_at IS NULL
              AND rejection_reason_code IS NULL AND rejection_note IS NULL
              AND rejected_at IS NULL AND decision_operation_id IS NULL
              AND decision_reason_code IS NULL AND decided_at IS NULL
            ) OR (
              status = 'rejected'
              AND review_id IS NULL AND review_operation_id IS NULL
              AND reviewed_at IS NULL AND recipient_account_key IS NULL
              AND withdraw_operation_id IS NULL AND withdrawn_by IS NULL
              AND withdrawn_session_id IS NULL AND withdrawn_at IS NULL
              AND reject_operation_id IS NOT NULL AND rejected_by IS NOT NULL
              AND rejected_session_id IS NOT NULL
              AND rejected_mfa_verified_at IS NOT NULL
              AND rejection_reason_code IS NOT NULL AND rejected_at IS NOT NULL
              AND decision_operation_id IS NULL
              AND decision_reason_code IS NULL AND decided_at IS NULL
            ) OR (
              status = 'rejected'
              AND recipient_account_key IS NOT NULL
              AND review_id IS NOT NULL AND review_operation_id IS NOT NULL
              AND reviewed_at IS NOT NULL
              AND withdraw_operation_id IS NULL AND withdrawn_by IS NULL
              AND withdrawn_session_id IS NULL AND withdrawn_at IS NULL
              AND reject_operation_id IS NULL AND rejected_by IS NULL
              AND rejected_session_id IS NULL AND rejected_mfa_verified_at IS NULL
              AND rejection_reason_code IS NULL AND rejection_note IS NULL
              AND rejected_at IS NULL AND decision_operation_id IS NOT NULL
              AND decision_reason_code IS NOT NULL AND decided_at IS NOT NULL
            ) OR (
              status IN ('approved','duplicate')
              AND recipient_account_key IS NOT NULL
              AND review_id IS NOT NULL AND review_operation_id IS NOT NULL
              AND reviewed_at IS NOT NULL
              AND withdraw_operation_id IS NULL AND withdrawn_by IS NULL
              AND withdrawn_session_id IS NULL AND withdrawn_at IS NULL
              AND reject_operation_id IS NULL AND rejected_by IS NULL
              AND rejected_session_id IS NULL AND rejected_mfa_verified_at IS NULL
              AND rejection_reason_code IS NULL AND rejection_note IS NULL
              AND rejected_at IS NULL AND decision_operation_id IS NOT NULL
              AND decided_at IS NOT NULL
              AND (
                (status = 'approved' AND decision_reason_code IS NULL)
                OR (status = 'duplicate' AND decision_reason_code = 'duplicate')
              )
            )
          )
        )
        """)
    op.execute("""
        CREATE TABLE public.billing_payment_submission_event (
          operation_id UUID PRIMARY KEY,
          tenant_id UUID NOT NULL,
          submission_id UUID NOT NULL,
          event_type TEXT NOT NULL,
          actor_user_id UUID NOT NULL
            REFERENCES public.app_user(id) ON DELETE RESTRICT,
          actor_session_id UUID NOT NULL,
          mfa_verified_at TIMESTAMPTZ,
          request_hash TEXT NOT NULL,
          result_snapshot JSONB NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT uq_billing_submission_event_tenant_operation
            UNIQUE (tenant_id, operation_id),
          CONSTRAINT fk_billing_submission_event_submission
            FOREIGN KEY (tenant_id, submission_id)
            REFERENCES public.billing_payment_submission(tenant_id, id)
            ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
          CONSTRAINT ck_billing_submission_event_type CHECK (
            event_type IN (
              'payment_submission_created',
              'payment_submission_withdrawn',
              'payment_submission_rejected'
            )
          ),
          CONSTRAINT ck_billing_submission_event_mfa CHECK (
            (
              event_type = 'payment_submission_rejected'
              AND mfa_verified_at IS NOT NULL
            ) OR (
              event_type IN (
                'payment_submission_created','payment_submission_withdrawn'
              )
              AND mfa_verified_at IS NULL
            )
          ),
          CONSTRAINT ck_billing_submission_event_hash CHECK (
            request_hash ~ '^[0-9a-f]{64}$'
          ),
          CONSTRAINT ck_billing_submission_event_result CHECK (
            pg_catalog.jsonb_typeof(result_snapshot) = 'object'
            AND pg_catalog.octet_length(result_snapshot::TEXT) BETWEEN 2 AND 32768
            AND NOT (result_snapshot ? 'external_reference')
            AND NOT (result_snapshot ? 'recipient_account_key')
          )
        )
        """)
    op.execute("""
        ALTER TABLE public.billing_payment_submission
        ADD CONSTRAINT fk_billing_submission_create_event
          FOREIGN KEY (tenant_id, create_operation_id)
          REFERENCES public.billing_payment_submission_event(tenant_id, operation_id)
          ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
        ADD CONSTRAINT fk_billing_submission_withdraw_event
          FOREIGN KEY (tenant_id, withdraw_operation_id)
          REFERENCES public.billing_payment_submission_event(tenant_id, operation_id)
          ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
        ADD CONSTRAINT fk_billing_submission_reject_event
          FOREIGN KEY (tenant_id, reject_operation_id)
          REFERENCES public.billing_payment_submission_event(tenant_id, operation_id)
          ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED
        """)
    op.execute("""
        ALTER TABLE public.billing_payment_review
        ADD COLUMN source_submission_id UUID,
        ADD CONSTRAINT uq_billing_payment_review_source_submission
          UNIQUE (source_submission_id),
        ADD CONSTRAINT fk_billing_review_source_submission
          FOREIGN KEY (tenant_id, source_submission_id)
          REFERENCES public.billing_payment_submission(tenant_id, id)
          ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED
        """)
    op.execute("""
        CREATE UNIQUE INDEX uq_billing_payment_review_active_bank_reference
        ON public.billing_payment_review(recipient_account_key, external_reference)
        WHERE status = 'pending_approval'
        """)
    op.execute("""
        CREATE UNIQUE INDEX uq_billing_submission_active_invoice_reference
        ON public.billing_payment_submission(
          tenant_id, target_invoice_id, external_reference
        )
        WHERE status IN ('submitted','under_review','approved')
        """)
    op.execute("""
        CREATE UNIQUE INDEX uq_billing_submission_active_bank_reference
        ON public.billing_payment_submission(
          recipient_account_key, external_reference
        )
        WHERE recipient_account_key IS NOT NULL
          AND status IN ('under_review','approved')
        """)
    op.execute("""
        CREATE INDEX ix_billing_submission_tenant_status_created
        ON public.billing_payment_submission(
          tenant_id, status, submitted_at, id
        )
        """)
    op.execute("""
        CREATE INDEX ix_billing_submission_event_submission_created
        ON public.billing_payment_submission_event(
          tenant_id, submission_id, created_at
        )
        """)

    for table in (
        "billing_payment_submission",
        "billing_payment_submission_event",
    ):
        _secure_table(table)

    op.execute(GLOBAL_OPERATION_SQL)
    _secure_function("public.trg_enforce_global_billing_operation_id()")
    op.execute("""
        CREATE TRIGGER trg_global_billing_submission_operation_id
        BEFORE INSERT ON public.billing_payment_submission_event
        FOR EACH ROW EXECUTE FUNCTION public.trg_enforce_global_billing_operation_id()
        """)
    op.execute("""
        CREATE TRIGGER trg_immutable_billing_payment_submission_event
        BEFORE UPDATE OR DELETE ON public.billing_payment_submission_event
        FOR EACH ROW EXECUTE FUNCTION
          public.trg_reject_immutable_billing_financial_mutation()
        """)

    op.execute(SUBMISSION_GUARD_SQL)
    _secure_function("public.trg_guard_billing_payment_submission_update()")
    op.execute("""
        CREATE TRIGGER trg_guard_billing_payment_submission_update
        BEFORE UPDATE OR DELETE ON public.billing_payment_submission
        FOR EACH ROW EXECUTE FUNCTION
          public.trg_guard_billing_payment_submission_update()
        """)
    op.execute(AUDIT_SUBMISSION_SQL)
    _secure_function("public.trg_audit_billing_payment_submission()")
    op.execute("""
        CREATE TRIGGER trg_audit_billing_payment_submission
        AFTER INSERT OR UPDATE ON public.billing_payment_submission
        FOR EACH ROW EXECUTE FUNCTION public.trg_audit_billing_payment_submission()
        """)
    op.execute(SYNC_REVIEW_SQL)
    _secure_function("public.trg_sync_billing_payment_submission_review_status()")
    op.execute("""
        CREATE TRIGGER trg_sync_billing_payment_submission_review_status
        AFTER UPDATE OF status ON public.billing_payment_review
        FOR EACH ROW EXECUTE FUNCTION
          public.trg_sync_billing_payment_submission_review_status()
        """)

    op.execute(TENANT_CONTEXT_SQL)
    _secure_function(TENANT_CONTEXT_SIGNATURE)
    for sql, signature in (
        (CREATE_SUBMISSION_SQL, CREATE_SIGNATURE),
        (LIST_TENANT_SUBMISSIONS_SQL, LIST_TENANT_SIGNATURE),
        (WITHDRAW_SUBMISSION_SQL, WITHDRAW_SIGNATURE),
    ):
        op.execute(sql)
        _secure_function(signature, grantee="aurum_app")
    for sql, signature in (
        (LIST_PLATFORM_SUBMISSIONS_SQL, LIST_PLATFORM_SIGNATURE),
        (READ_PLATFORM_SUBMISSION_SQL, READ_PLATFORM_SIGNATURE),
        (PROMOTE_SUBMISSION_SQL, PROMOTE_SIGNATURE),
        (REJECT_PLATFORM_SUBMISSION_SQL, REJECT_PLATFORM_SIGNATURE),
    ):
        op.execute(sql)
        _secure_function(signature, grantee="aurum_support")
    _restore_reference_privileges()


def downgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM public.billing_payment_submission
            UNION ALL
            SELECT 1 FROM public.billing_payment_submission_event
            UNION ALL
            SELECT 1 FROM public.billing_payment_review
              WHERE source_submission_id IS NOT NULL
          ) THEN
            RAISE EXCEPTION
              'Cannot downgrade billing payment submissions with retained history';
          END IF;
        END
        $$
        """)

    for signature in (
        REJECT_PLATFORM_SIGNATURE,
        PROMOTE_SIGNATURE,
        READ_PLATFORM_SIGNATURE,
        LIST_PLATFORM_SIGNATURE,
        WITHDRAW_SIGNATURE,
        LIST_TENANT_SIGNATURE,
        CREATE_SIGNATURE,
    ):
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")
    op.execute(f"DROP FUNCTION IF EXISTS {TENANT_CONTEXT_SIGNATURE}")

    op.execute("""
        DROP TRIGGER trg_sync_billing_payment_submission_review_status
        ON public.billing_payment_review
        """)
    op.execute("DROP FUNCTION public.trg_sync_billing_payment_submission_review_status()")
    op.execute("""
        DROP TRIGGER trg_audit_billing_payment_submission
        ON public.billing_payment_submission
        """)
    op.execute("DROP FUNCTION public.trg_audit_billing_payment_submission()")
    op.execute("""
        DROP TRIGGER trg_guard_billing_payment_submission_update
        ON public.billing_payment_submission
        """)
    op.execute("DROP FUNCTION public.trg_guard_billing_payment_submission_update()")
    op.execute("""
        DROP TRIGGER trg_immutable_billing_payment_submission_event
        ON public.billing_payment_submission_event
        """)
    op.execute("""
        DROP TRIGGER trg_global_billing_submission_operation_id
        ON public.billing_payment_submission_event
        """)
    op.execute(RESTORE_GLOBAL_OPERATION_SQL)
    _secure_function("public.trg_enforce_global_billing_operation_id()")

    op.execute("DROP INDEX public.uq_billing_payment_review_active_bank_reference")
    op.execute("""
        ALTER TABLE public.billing_payment_review
        DROP CONSTRAINT fk_billing_review_source_submission,
        DROP CONSTRAINT uq_billing_payment_review_source_submission,
        DROP COLUMN source_submission_id
        """)
    op.execute("""
        ALTER TABLE public.billing_payment_submission
        DROP CONSTRAINT fk_billing_submission_create_event,
        DROP CONSTRAINT fk_billing_submission_withdraw_event,
        DROP CONSTRAINT fk_billing_submission_reject_event
        """)
    op.execute("DROP TABLE public.billing_payment_submission_event")
    op.execute("DROP TABLE public.billing_payment_submission")
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

    codes = ", ".join(f"'{code}'" for code, _, _ in TENANT_PERMISSIONS)
    op.execute(f"DELETE FROM public.role_template_permission WHERE permission_code IN ({codes})")
    op.execute(f"DELETE FROM public.role_permission WHERE permission_code IN ({codes})")
    op.execute(f"DELETE FROM public.permission WHERE code IN ({codes})")
