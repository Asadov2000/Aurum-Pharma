"""add immutable billing financial kernel

Revision ID: 0097
Revises: 0096
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0097"
down_revision: str | None = "0096"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


IMMUTABLE_FINANCIAL_ROW_SQL = r"""
CREATE FUNCTION public.trg_reject_immutable_billing_financial_mutation()
RETURNS TRIGGER AS $$
BEGIN
  RAISE EXCEPTION 'Finalized billing financial records are immutable'
    USING ERRCODE = '55000';
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


AUDIT_FINANCIAL_ROW_SQL = r"""
CREATE FUNCTION public.trg_audit_billing_financial_row()
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


GLOBAL_BILLING_OPERATION_SQL = r"""
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


RESTORE_GLOBAL_BILLING_OPERATION_SQL = r"""
CREATE OR REPLACE FUNCTION public.trg_enforce_global_billing_operation_id()
RETURNS TRIGGER AS $$
BEGIN
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(NEW.operation_id::TEXT, 9501)
  );
  IF TG_TABLE_NAME = 'billing_pricing_admin_event' THEN
    IF EXISTS (
      SELECT 1 FROM public.billing_subscription_price_application AS application
      WHERE application.operation_id = NEW.operation_id
    ) THEN
      RAISE EXCEPTION 'Billing operation id was reused' USING ERRCODE = '23505';
    END IF;
  ELSIF EXISTS (
    SELECT 1 FROM public.billing_pricing_admin_event AS event
    WHERE event.operation_id = NEW.operation_id
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


ASSERT_BALANCED_ENTRY_SQL = r"""
CREATE FUNCTION public.assert_billing_journal_entry_balanced(p_entry_id UUID)
RETURNS VOID AS $$
DECLARE
  v_debit NUMERIC(14,2);
  v_credit NUMERIC(14,2);
BEGIN
  SELECT
    COALESCE(sum(posting.amount) FILTER (WHERE posting.side = 'debit'), 0),
    COALESCE(sum(posting.amount) FILTER (WHERE posting.side = 'credit'), 0)
  INTO v_debit, v_credit
  FROM public.billing_journal_posting AS posting
  WHERE posting.entry_id = p_entry_id;
  IF v_debit <> v_credit THEN
    RAISE EXCEPTION 'Billing journal entry is not balanced'
      USING ERRCODE = '23514';
  END IF;
END;
$$ LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


ASSERT_INVOICE_TOTAL_SQL = r"""
CREATE FUNCTION public.assert_billing_invoice_totals(p_invoice_id UUID)
RETURNS VOID AS $$
DECLARE
  v_invoice public.billing_invoice%ROWTYPE;
  v_subtotal NUMERIC(14,2);
  v_discount NUMERIC(14,2);
  v_tax NUMERIC(14,2);
  v_total NUMERIC(14,2);
BEGIN
  SELECT * INTO v_invoice
  FROM public.billing_invoice AS invoice
  WHERE invoice.id = p_invoice_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Billing invoice was not found' USING ERRCODE = 'P0002';
  END IF;
  SELECT
    COALESCE(sum(line.subtotal_amount), 0),
    COALESCE(sum(line.discount_amount), 0),
    COALESCE(sum(line.tax_amount), 0),
    COALESCE(sum(line.total_amount), 0)
  INTO v_subtotal, v_discount, v_tax, v_total
  FROM public.billing_invoice_line AS line
  WHERE line.tenant_id = v_invoice.tenant_id
    AND line.invoice_id = v_invoice.id;
  IF v_subtotal <> v_invoice.subtotal_amount
    OR v_discount <> v_invoice.discount_amount
    OR v_tax <> v_invoice.tax_amount
    OR v_total <> v_invoice.total_amount
  THEN
    RAISE EXCEPTION 'Billing invoice totals do not match its lines'
      USING ERRCODE = '23514';
  END IF;
END;
$$ LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


ISSUE_INVOICE_SQL = r"""
CREATE FUNCTION public.issue_billing_subscription_invoice(
  p_actor_user_id UUID,
  p_actor_session_id UUID,
  p_operation_id UUID,
  p_request_hash TEXT,
  p_tenant_id UUID,
  p_subscription_id UUID,
  p_expected_row_version INTEGER
)
RETURNS TABLE(result JSONB, applied BOOLEAN) AS $$
DECLARE
  v_mfa_at TIMESTAMPTZ;
  v_payload JSONB;
  v_existing public.billing_financial_operation%ROWTYPE;
  v_subscription public.tenant_subscription%ROWTYPE;
  v_tenant public.tenant%ROWTYPE;
  v_application public.billing_subscription_price_application%ROWTYPE;
  v_initial_application public.billing_subscription_price_application%ROWTYPE;
  v_previous_application public.billing_subscription_price_application%ROWTYPE;
  v_plan public.billing_plan%ROWTYPE;
  v_price public.billing_price_version%ROWTYPE;
  v_override public.billing_contract_override%ROWTYPE;
  v_source_count INTEGER;
  v_active_branches INTEGER;
  v_period_start TIMESTAMPTZ;
  v_period_end TIMESTAMPTZ;
  v_anchor_day SMALLINT;
  v_source_type TEXT;
  v_monthly_price NUMERIC(14,2);
  v_annual_discount NUMERIC(5,2);
  v_terms JSONB;
  v_amount NUMERIC(14,2);
  v_application_id UUID;
  v_application_operation_id UUID;
  v_application_hash TEXT;
  v_application_result JSONB;
  v_invoice_id UUID;
  v_invoice_number TEXT;
  v_entry_id UUID;
  v_created_at TIMESTAMPTZ;
  v_result JSONB;
BEGIN
  v_mfa_at := public.assert_and_lock_platform_recent_capability(
    p_actor_user_id, p_actor_session_id, 'platform.billing.invoice.issue'
  );
  IF p_operation_id IS NULL
    OR p_request_hash !~ '^[0-9a-f]{64}$'
    OR p_tenant_id IS NULL
    OR p_subscription_id IS NULL
    OR p_expected_row_version < 1
  THEN
    RAISE EXCEPTION 'Invalid billing invoice request' USING ERRCODE = '22023';
  END IF;

  v_payload := pg_catalog.jsonb_build_object(
    'tenant_id', p_tenant_id,
    'subscription_id', p_subscription_id,
    'expected_row_version', p_expected_row_version
  );
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(p_operation_id::TEXT, 9501)
  );
  SELECT * INTO v_existing
  FROM public.billing_financial_operation AS financial_operation
  WHERE financial_operation.operation_id = p_operation_id;
  IF FOUND THEN
    IF v_existing.operation_type <> 'invoice_issued'
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
  ) THEN
    RAISE EXCEPTION 'Billing operation id was reused' USING ERRCODE = '23505';
  END IF;

  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(p_tenant_id::TEXT, 9701)
  );
  SELECT * INTO v_subscription
  FROM public.tenant_subscription AS subscription
  WHERE subscription.tenant_id = p_tenant_id
    AND subscription.id = p_subscription_id
  FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Tenant subscription was not found' USING ERRCODE = 'P0002';
  END IF;
  IF v_subscription.row_version <> p_expected_row_version
    OR v_subscription.status NOT IN ('trial', 'active', 'grace_period', 'suspended')
  THEN
    RAISE EXCEPTION 'Tenant subscription changed concurrently' USING ERRCODE = '40001';
  END IF;
  SELECT * INTO v_tenant
  FROM public.tenant AS tenant
  WHERE tenant.id = p_tenant_id
  FOR UPDATE;
  IF NOT FOUND OR v_tenant.status NOT IN ('trial', 'active', 'readonly') THEN
    RAISE EXCEPTION 'Tenant billing state is not eligible' USING ERRCODE = '40001';
  END IF;

  v_period_start := v_subscription.period_end;
  IF v_period_start > pg_catalog.statement_timestamp() + INTERVAL '7 days' THEN
    RAISE EXCEPTION 'Renewal invoice cannot be issued more than seven days early'
      USING ERRCODE = '22023';
  END IF;
  IF EXISTS (
    SELECT 1 FROM public.billing_invoice AS invoice
    WHERE invoice.tenant_id = p_tenant_id
      AND invoice.subscription_id = p_subscription_id
      AND invoice.document_type = 'invoice'
      AND invoice.period_start = v_period_start
      AND invoice.document_state = 'issued'
  ) THEN
    RAISE EXCEPTION 'Subscription period already has an issued invoice'
      USING ERRCODE = '23505';
  END IF;

  SELECT * INTO v_application
  FROM public.billing_subscription_price_application AS application
  WHERE application.tenant_id = p_tenant_id
    AND application.subscription_id = p_subscription_id
    AND application.period_start = v_period_start;

  IF NOT FOUND THEN
    IF v_subscription.status = 'trial' THEN
      RAISE EXCEPTION 'Initial subscription price must be fixed before invoicing'
        USING ERRCODE = 'P0001';
    END IF;
    SELECT * INTO v_initial_application
    FROM public.billing_subscription_price_application AS application
    WHERE application.tenant_id = p_tenant_id
      AND application.subscription_id = p_subscription_id
    ORDER BY application.period_start, application.created_at
    LIMIT 1;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'Subscription has no billing calendar anchor'
        USING ERRCODE = 'P0001';
    END IF;
    v_anchor_day := v_initial_application.calendar_anchor_day;
    SELECT * INTO v_previous_application
    FROM public.billing_subscription_price_application AS application
    WHERE application.tenant_id = p_tenant_id
      AND application.subscription_id = p_subscription_id
      AND application.period_start < v_period_start
    ORDER BY application.period_start DESC, application.created_at DESC
    LIMIT 1;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'Subscription billing calendar moved backwards'
        USING ERRCODE = '40001';
    END IF;

    SELECT count(*)::INTEGER INTO v_active_branches
    FROM public.branch AS branch
    WHERE branch.tenant_id = p_tenant_id AND branch.is_active;
    IF v_active_branches < 1 OR v_active_branches <> v_subscription.branches_count THEN
      RAISE EXCEPTION 'Tenant branch count changed concurrently'
        USING ERRCODE = '40001';
    END IF;
    SELECT * INTO v_plan
    FROM public.billing_plan AS plan
    WHERE plan.legacy_subscription_plan_id = v_subscription.plan_id
    FOR SHARE;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'Versioned billing plan is not linked' USING ERRCODE = 'P0002';
    END IF;
    PERFORM pg_catalog.pg_advisory_xact_lock(
      pg_catalog.hashtextextended(v_plan.id::TEXT, 9602)
    );
    IF EXISTS (
      SELECT 1 FROM public.billing_price_version AS price
      WHERE price.plan_id = v_plan.id
        AND price.status = 'scheduled'
        AND price.audience = 'default'
        AND price.effective_from <= v_period_start
    ) OR EXISTS (
      SELECT 1 FROM public.billing_contract_override AS contract
      WHERE contract.tenant_id = p_tenant_id
        AND contract.plan_id = v_plan.id
        AND contract.status = 'scheduled'
        AND contract.valid_from <= v_period_start
        AND (contract.valid_until IS NULL OR contract.valid_until > v_period_start)
    ) THEN
      RAISE EXCEPTION 'Eligible billing price activation is pending'
        USING ERRCODE = '40001';
    END IF;

    SELECT count(*)::INTEGER INTO v_source_count
    FROM public.billing_contract_override AS contract
    WHERE contract.tenant_id = p_tenant_id
      AND contract.plan_id = v_plan.id
      AND contract.status = 'active'
      AND contract.valid_from <= v_period_start
      AND (contract.valid_until IS NULL OR contract.valid_until > v_period_start);
    IF v_source_count > 1 THEN
      RAISE EXCEPTION 'Ambiguous active billing contract override'
        USING ERRCODE = 'P0001';
    ELSIF v_source_count = 1 THEN
      SELECT * INTO STRICT v_override
      FROM public.billing_contract_override AS contract
      WHERE contract.tenant_id = p_tenant_id
        AND contract.plan_id = v_plan.id
        AND contract.status = 'active'
        AND contract.valid_from <= v_period_start
        AND (contract.valid_until IS NULL OR contract.valid_until > v_period_start);
      v_source_type := 'contract_override';
      v_monthly_price := v_override.monthly_price_per_branch;
      v_annual_discount := v_override.annual_discount_pct;
      v_terms := v_override.terms_snapshot;
    ELSE
      SELECT count(*)::INTEGER INTO v_source_count
      FROM public.billing_price_version AS price
      WHERE price.plan_id = v_plan.id
        AND price.status = 'active'
        AND price.audience = 'default'
        AND price.effective_from <= v_period_start;
      IF v_source_count > 1 THEN
        RAISE EXCEPTION 'A single active renewal price is required'
          USING ERRCODE = 'P0001';
      ELSIF v_source_count = 1 THEN
        SELECT * INTO STRICT v_price
        FROM public.billing_price_version AS price
        WHERE price.plan_id = v_plan.id
          AND price.status = 'active'
          AND price.audience = 'default'
          AND price.effective_from <= v_period_start;
        v_monthly_price := v_price.monthly_price_per_branch;
        v_annual_discount := v_price.annual_discount_pct;
        v_terms := v_price.terms_snapshot;
      ELSIF v_previous_application.source_type = 'price_version' THEN
        SELECT * INTO STRICT v_price
        FROM public.billing_price_version AS price
        WHERE price.plan_id = v_plan.id
          AND price.id = v_previous_application.price_version_id;
        v_monthly_price := v_previous_application.monthly_price_per_branch;
        v_annual_discount := v_previous_application.annual_discount_pct;
        v_terms := v_previous_application.terms_snapshot;
      ELSE
        RAISE EXCEPTION 'No eligible renewal price is available'
          USING ERRCODE = 'P0001';
      END IF;
      v_source_type := 'price_version';
    END IF;

    v_period_end := public.calculate_billing_period_end(
      v_period_start, v_subscription.billing_period, 'Asia/Dushanbe', v_anchor_day
    );
    v_amount := pg_catalog.round(
      v_monthly_price * v_active_branches
        * CASE v_subscription.billing_period
            WHEN 'yearly' THEN 12 * (1 - v_annual_discount / 100)
            ELSE 1
          END,
      2
    );
    v_application_id := public.gen_random_uuid();
    v_application_operation_id := public.gen_random_uuid();
    v_application_hash := pg_catalog.encode(
      pg_catalog.sha256(pg_catalog.convert_to(
        p_operation_id::TEXT || '-renewal-price-application', 'UTF8'
      )),
      'hex'
    );
    v_application_result := pg_catalog.jsonb_build_object(
      'application_id', v_application_id,
      'subscription_id', v_subscription.id,
      'application_kind', 'renewal',
      'source_type', v_source_type,
      'plan_code', v_plan.code,
      'plan_name', v_plan.name,
      'billing_period', v_subscription.billing_period,
      'period_start', v_period_start,
      'period_end', v_period_end,
      'timezone', 'Asia/Dushanbe',
      'branches_count', v_active_branches,
      'monthly_price_per_branch', v_monthly_price::TEXT,
      'annual_discount_pct', v_annual_discount::TEXT,
      'calculated_amount', v_amount::TEXT,
      'currency', 'TJS',
      'created_at', pg_catalog.statement_timestamp()
    );
    INSERT INTO public.billing_subscription_price_application (
      id, tenant_id, subscription_id, plan_id, application_kind, source_type,
      price_version_id, contract_override_id, plan_code, plan_name,
      billing_period, period_start, period_end, calendar_anchor_day, timezone,
      branches_count, monthly_price_per_branch, annual_discount_pct,
      calculated_amount, currency, terms_snapshot, operation_id, request_hash,
      request_payload, actor_user_id, actor_session_id, mfa_verified_at,
      result_snapshot, created_at
    ) VALUES (
      v_application_id, p_tenant_id, v_subscription.id, v_plan.id, 'renewal',
      v_source_type,
      CASE WHEN v_source_type = 'price_version' THEN v_price.id ELSE NULL END,
      CASE WHEN v_source_type = 'contract_override' THEN v_override.id ELSE NULL END,
      v_plan.code, v_plan.name, v_subscription.billing_period, v_period_start,
      v_period_end, v_anchor_day, 'Asia/Dushanbe', v_active_branches,
      v_monthly_price, v_annual_discount, v_amount, 'TJS', v_terms,
      v_application_operation_id, v_application_hash,
      pg_catalog.jsonb_build_object(
        'parent_operation_id', p_operation_id,
        'subscription_id', p_subscription_id,
        'period_start', v_period_start
      ),
      p_actor_user_id, p_actor_session_id, v_mfa_at, v_application_result,
      pg_catalog.statement_timestamp()
    ) RETURNING * INTO v_application;
  END IF;

  v_created_at := pg_catalog.statement_timestamp();
  v_invoice_id := public.gen_random_uuid();
  v_invoice_number := 'AP-' || to_char(v_created_at AT TIME ZONE 'Asia/Dushanbe', 'YYYY')
    || '-' || lpad(nextval('public.billing_invoice_number_seq')::TEXT, 10, '0');
  INSERT INTO public.billing_invoice (
    id, tenant_id, subscription_id, price_application_id, operation_id,
    invoice_number, document_type, document_state, period_start, period_end,
    due_at, subtotal_amount, discount_amount, tax_amount, total_amount, currency,
    issuer_snapshot, customer_snapshot, template_version, issued_by,
    issued_session_id, mfa_verified_at, issued_at
  ) VALUES (
    v_invoice_id, p_tenant_id, p_subscription_id, v_application.id, p_operation_id,
    v_invoice_number, 'invoice', 'issued', v_application.period_start,
    v_application.period_end, v_application.period_start,
    v_application.calculated_amount, 0, 0, v_application.calculated_amount, 'TJS',
    pg_catalog.jsonb_build_object(
      'product', 'Aurum Pharma', 'legal_details_status', 'pending_configuration'
    ),
    pg_catalog.jsonb_build_object('tenant_id', v_tenant.id, 'tenant_name', v_tenant.name),
    'billing-invoice-v1', p_actor_user_id, p_actor_session_id, v_mfa_at, v_created_at
  );
  INSERT INTO public.billing_invoice_line (
    tenant_id, invoice_id, line_number, line_type, description, quantity,
    unit_price, price_version_id, contract_override_id, period_start, period_end,
    subtotal_amount, discount_amount, tax_amount, total_amount, currency, created_at
  ) VALUES (
    p_tenant_id, v_invoice_id, 1, 'subscription',
    v_application.plan_name || ' / ' || v_application.billing_period
      || ' / branches: ' || v_application.branches_count::TEXT,
    1,
    v_application.calculated_amount,
    v_application.price_version_id, v_application.contract_override_id,
    v_application.period_start, v_application.period_end,
    v_application.calculated_amount, 0, 0, v_application.calculated_amount,
    'TJS', v_created_at
  );
  PERFORM public.assert_billing_invoice_totals(v_invoice_id);

  v_entry_id := public.gen_random_uuid();
  INSERT INTO public.billing_journal_entry (
    id, tenant_id, operation_id, entry_sequence, entry_type, currency,
    actor_user_id, actor_session_id, posted_at
  ) VALUES (
    v_entry_id, p_tenant_id, p_operation_id, 1, 'invoice_issued', 'TJS',
    p_actor_user_id, p_actor_session_id, v_created_at
  );
  IF v_application.calculated_amount > 0 THEN
    INSERT INTO public.billing_journal_posting (
      tenant_id, entry_id, posting_sequence, account_code, side, amount,
      invoice_id, created_at
    ) VALUES
      (p_tenant_id, v_entry_id, 1, 'accounts_receivable', 'debit',
       v_application.calculated_amount, v_invoice_id, v_created_at),
      (p_tenant_id, v_entry_id, 2, 'subscription_revenue', 'credit',
       v_application.calculated_amount, v_invoice_id, v_created_at);
  END IF;
  PERFORM public.assert_billing_journal_entry_balanced(v_entry_id);

  v_result := pg_catalog.jsonb_build_object(
    'invoice_id', v_invoice_id,
    'tenant_id', p_tenant_id,
    'subscription_id', p_subscription_id,
    'price_application_id', v_application.id,
    'price_application_kind', v_application.application_kind,
    'invoice_number', v_invoice_number,
    'document_state', 'issued',
    'settlement_state', CASE WHEN v_application.calculated_amount = 0
      THEN 'paid' ELSE 'unpaid' END,
    'collection_state', CASE WHEN v_application.period_start > v_created_at
      THEN 'not_due' ELSE 'due' END,
    'period_start', v_application.period_start,
    'period_end', v_application.period_end,
    'due_at', v_application.period_start,
    'total_amount', v_application.calculated_amount::TEXT,
    'outstanding_amount', v_application.calculated_amount::TEXT,
    'currency', 'TJS',
    'issued_at', v_created_at
  );
  INSERT INTO public.billing_financial_operation (
    operation_id, operation_type, tenant_id, actor_user_id, actor_session_id,
    mfa_verified_at, request_hash, request_payload, result_snapshot, created_at
  ) VALUES (
    p_operation_id, 'invoice_issued', p_tenant_id, p_actor_user_id,
    p_actor_session_id, v_mfa_at, p_request_hash, v_payload, v_result, v_created_at
  );
  INSERT INTO public.billing_outbox_event (
    tenant_id, operation_id, event_type, aggregate_type, aggregate_id,
    payload, created_at
  ) VALUES (
    p_tenant_id, p_operation_id, 'billing.invoice.issued', 'billing_invoice',
    v_invoice_id,
    pg_catalog.jsonb_build_object(
      'invoice_id', v_invoice_id, 'tenant_id', p_tenant_id,
      'invoice_number', v_invoice_number
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


CREATE_PAYMENT_REVIEW_SQL = r"""
CREATE FUNCTION public.create_billing_bank_payment_review(
  p_actor_user_id UUID,
  p_actor_session_id UUID,
  p_operation_id UUID,
  p_request_hash TEXT,
  p_tenant_id UUID,
  p_target_invoice_id UUID,
  p_amount NUMERIC,
  p_paid_at TIMESTAMPTZ,
  p_recipient_account_key TEXT,
  p_external_reference TEXT
)
RETURNS TABLE(result JSONB, applied BOOLEAN) AS $$
DECLARE
  v_mfa_at TIMESTAMPTZ;
  v_payload JSONB;
  v_existing public.billing_financial_operation%ROWTYPE;
  v_invoice public.billing_invoice%ROWTYPE;
  v_review_id UUID;
  v_created_at TIMESTAMPTZ;
  v_result JSONB;
BEGIN
  v_mfa_at := public.assert_and_lock_platform_recent_capability(
    p_actor_user_id, p_actor_session_id, 'platform.billing.payment.review'
  );
  IF p_operation_id IS NULL
    OR p_request_hash !~ '^[0-9a-f]{64}$'
    OR p_tenant_id IS NULL
    OR p_target_invoice_id IS NULL
    OR p_amount <= 0 OR p_amount > 999999999999.99
    OR p_amount <> pg_catalog.round(p_amount, 2)
    OR p_paid_at IS NULL OR p_paid_at > pg_catalog.statement_timestamp() + INTERVAL '5 minutes'
    OR p_paid_at < pg_catalog.statement_timestamp() - INTERVAL '366 days'
    OR p_recipient_account_key !~ '^[a-z0-9][a-z0-9_.:-]{2,63}$'
    OR p_external_reference !~ '^[A-Z0-9]{4,128}$'
  THEN
    RAISE EXCEPTION 'Invalid bank payment review request' USING ERRCODE = '22023';
  END IF;
  v_payload := pg_catalog.jsonb_build_object(
    'tenant_id', p_tenant_id,
    'target_invoice_id', p_target_invoice_id,
    'amount', pg_catalog.round(p_amount, 2)::TEXT,
    'paid_at', p_paid_at,
    'recipient_account_key', p_recipient_account_key,
    'external_reference', p_external_reference
  );
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(p_operation_id::TEXT, 9501)
  );
  SELECT * INTO v_existing
  FROM public.billing_financial_operation AS financial_operation
  WHERE financial_operation.operation_id = p_operation_id;
  IF FOUND THEN
    IF v_existing.operation_type <> 'payment_review_created'
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
    SELECT 1 FROM public.billing_pricing_admin_event WHERE operation_id = p_operation_id
    UNION ALL
    SELECT 1 FROM public.billing_subscription_price_application
    WHERE operation_id = p_operation_id
  ) THEN
    RAISE EXCEPTION 'Billing operation id was reused' USING ERRCODE = '23505';
  END IF;
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(p_tenant_id::TEXT, 9701)
  );
  SELECT * INTO v_invoice
  FROM public.billing_invoice AS invoice
  WHERE invoice.tenant_id = p_tenant_id
    AND invoice.id = p_target_invoice_id
    AND invoice.document_state = 'issued'
  FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Billing invoice was not found' USING ERRCODE = 'P0002';
  END IF;
  IF EXISTS (
    SELECT 1 FROM public.billing_payment AS payment
    WHERE payment.recipient_account_key = p_recipient_account_key
      AND payment.external_reference = p_external_reference
  ) THEN
    RAISE EXCEPTION 'Bank payment was already confirmed' USING ERRCODE = '23505';
  END IF;
  v_review_id := public.gen_random_uuid();
  v_created_at := pg_catalog.statement_timestamp();
  v_result := pg_catalog.jsonb_build_object(
    'review_id', v_review_id,
    'tenant_id', p_tenant_id,
    'target_invoice_id', p_target_invoice_id,
    'amount', pg_catalog.round(p_amount, 2)::TEXT,
    'currency', 'TJS',
    'paid_at', p_paid_at,
    'status', 'pending_approval',
    'row_version', 1,
    'created_at', v_created_at
  );
  INSERT INTO public.billing_payment_review (
    id, tenant_id, target_invoice_id, amount, currency, paid_at,
    recipient_account_key, external_reference, status, row_version,
    reviewed_by, reviewed_session_id, review_mfa_verified_at,
    review_operation_id, created_at, updated_at
  ) VALUES (
    v_review_id, p_tenant_id, p_target_invoice_id, pg_catalog.round(p_amount, 2),
    'TJS', p_paid_at, p_recipient_account_key, p_external_reference,
    'pending_approval', 1, p_actor_user_id, p_actor_session_id, v_mfa_at,
    p_operation_id, v_created_at, v_created_at
  );
  INSERT INTO public.billing_financial_operation (
    operation_id, operation_type, tenant_id, actor_user_id, actor_session_id,
    mfa_verified_at, request_hash, request_payload, result_snapshot, created_at
  ) VALUES (
    p_operation_id, 'payment_review_created', p_tenant_id, p_actor_user_id,
    p_actor_session_id, v_mfa_at, p_request_hash, v_payload, v_result, v_created_at
  );
  INSERT INTO public.billing_outbox_event (
    tenant_id, operation_id, event_type, aggregate_type, aggregate_id,
    payload, created_at
  ) VALUES (
    p_tenant_id, p_operation_id, 'billing.payment.reviewed',
    'billing_payment_review', v_review_id,
    pg_catalog.jsonb_build_object(
      'review_id', v_review_id, 'tenant_id', p_tenant_id,
      'target_invoice_id', p_target_invoice_id
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


APPROVE_PAYMENT_SQL = r"""
CREATE FUNCTION public.approve_billing_bank_payment(
  p_actor_user_id UUID,
  p_actor_session_id UUID,
  p_operation_id UUID,
  p_request_hash TEXT,
  p_tenant_id UUID,
  p_review_id UUID,
  p_expected_row_version INTEGER
)
RETURNS TABLE(result JSONB, applied BOOLEAN) AS $$
DECLARE
  v_mfa_at TIMESTAMPTZ;
  v_payload JSONB;
  v_existing public.billing_financial_operation%ROWTYPE;
  v_review public.billing_payment_review%ROWTYPE;
  v_target_invoice public.billing_invoice%ROWTYPE;
  v_subscription public.tenant_subscription%ROWTYPE;
  v_candidate RECORD;
  v_payment_id UUID;
  v_credit_id UUID;
  v_remaining NUMERIC(14,2);
  v_allocate NUMERIC(14,2);
  v_target_outstanding NUMERIC(14,2);
  v_blocking_outstanding NUMERIC(14,2);
  v_allocated_total NUMERIC(14,2) := 0;
  v_credit_amount NUMERIC(14,2) := 0;
  v_allocations JSONB := '[]'::JSONB;
  v_entry_id UUID;
  v_entry_sequence INTEGER := 1;
  v_allocation_order INTEGER := 1;
  v_access_restored BOOLEAN := false;
  v_activation_start TIMESTAMPTZ;
  v_activation_end TIMESTAMPTZ;
  v_anchor_day SMALLINT;
  v_created_at TIMESTAMPTZ;
  v_result JSONB;
BEGIN
  v_mfa_at := public.assert_and_lock_platform_recent_capability(
    p_actor_user_id, p_actor_session_id, 'platform.billing.payment.approve'
  );
  IF p_operation_id IS NULL
    OR p_request_hash !~ '^[0-9a-f]{64}$'
    OR p_tenant_id IS NULL OR p_review_id IS NULL
    OR p_expected_row_version < 1
  THEN
    RAISE EXCEPTION 'Invalid bank payment approval request' USING ERRCODE = '22023';
  END IF;
  v_payload := pg_catalog.jsonb_build_object(
    'tenant_id', p_tenant_id,
    'review_id', p_review_id,
    'expected_row_version', p_expected_row_version
  );
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(p_operation_id::TEXT, 9501)
  );
  SELECT * INTO v_existing
  FROM public.billing_financial_operation AS financial_operation
  WHERE financial_operation.operation_id = p_operation_id;
  IF FOUND THEN
    IF v_existing.operation_type <> 'payment_approved'
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
    SELECT 1 FROM public.billing_pricing_admin_event WHERE operation_id = p_operation_id
    UNION ALL
    SELECT 1 FROM public.billing_subscription_price_application
    WHERE operation_id = p_operation_id
  ) THEN
    RAISE EXCEPTION 'Billing operation id was reused' USING ERRCODE = '23505';
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
    RAISE EXCEPTION 'Bank payment requires an independent approver'
      USING ERRCODE = '22023';
  END IF;
  IF EXISTS (
    SELECT 1 FROM public.billing_payment AS payment
    WHERE payment.recipient_account_key = v_review.recipient_account_key
      AND payment.external_reference = v_review.external_reference
  ) THEN
    RAISE EXCEPTION 'Bank payment was already confirmed' USING ERRCODE = '23505';
  END IF;
  SELECT * INTO v_target_invoice
  FROM public.billing_invoice AS invoice
  WHERE invoice.tenant_id = p_tenant_id
    AND invoice.id = v_review.target_invoice_id
    AND invoice.document_state = 'issued'
  FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Target billing invoice was not found' USING ERRCODE = 'P0002';
  END IF;

  v_created_at := pg_catalog.statement_timestamp();
  v_payment_id := public.gen_random_uuid();
  v_remaining := v_review.amount;
  INSERT INTO public.billing_payment (
    id, tenant_id, review_id, operation_id, amount, currency, paid_at,
    recipient_account_key, external_reference, lifecycle_state,
    confirmed_by, confirmed_session_id, mfa_verified_at, confirmed_at
  ) VALUES (
    v_payment_id, p_tenant_id, v_review.id, p_operation_id, v_review.amount,
    'TJS', v_review.paid_at, v_review.recipient_account_key,
    v_review.external_reference, 'confirmed', p_actor_user_id,
    p_actor_session_id, v_mfa_at, v_created_at
  );

  v_entry_id := public.gen_random_uuid();
  INSERT INTO public.billing_journal_entry (
    id, tenant_id, operation_id, entry_sequence, entry_type, currency,
    actor_user_id, actor_session_id, posted_at
  ) VALUES (
    v_entry_id, p_tenant_id, p_operation_id, v_entry_sequence,
    'payment_confirmed', 'TJS', p_actor_user_id, p_actor_session_id, v_created_at
  );
  INSERT INTO public.billing_journal_posting (
    tenant_id, entry_id, posting_sequence, account_code, side, amount,
    payment_id, created_at
  ) VALUES
    (p_tenant_id, v_entry_id, 1, 'bank_cleared', 'debit',
     v_review.amount, v_payment_id, v_created_at),
    (p_tenant_id, v_entry_id, 2, 'unapplied_cash', 'credit',
     v_review.amount, v_payment_id, v_created_at);
  PERFORM public.assert_billing_journal_entry_balanced(v_entry_id);

  FOR v_candidate IN
    SELECT
      invoice.id,
      invoice.invoice_number,
      invoice.total_amount - COALESCE((
        SELECT sum(allocation.amount)
        FROM public.billing_payment_allocation AS allocation
        WHERE allocation.tenant_id = invoice.tenant_id
          AND allocation.invoice_id = invoice.id
      ), 0)::NUMERIC(14,2) AS outstanding_amount
    FROM public.billing_invoice AS invoice
    WHERE invoice.tenant_id = p_tenant_id
      AND invoice.document_state = 'issued'
      AND invoice.due_at <= v_created_at
      AND invoice.total_amount > COALESCE((
        SELECT sum(allocation.amount)
        FROM public.billing_payment_allocation AS allocation
        WHERE allocation.tenant_id = invoice.tenant_id
          AND allocation.invoice_id = invoice.id
      ), 0)
    ORDER BY invoice.due_at, invoice.issued_at, invoice.id
    FOR UPDATE OF invoice
  LOOP
    EXIT WHEN v_remaining <= 0;
    v_allocate := LEAST(v_remaining, v_candidate.outstanding_amount);
    INSERT INTO public.billing_payment_allocation (
      tenant_id, payment_id, invoice_id, allocation_order, amount, currency,
      allocated_at
    ) VALUES (
      p_tenant_id, v_payment_id, v_candidate.id, v_allocation_order,
      v_allocate, 'TJS', v_created_at
    );
    v_allocations := v_allocations || pg_catalog.jsonb_build_array(
      pg_catalog.jsonb_build_object(
        'invoice_id', v_candidate.id,
        'invoice_number', v_candidate.invoice_number,
        'amount', v_allocate::TEXT,
        'allocation_order', v_allocation_order
      )
    );
    v_remaining := v_remaining - v_allocate;
    v_allocated_total := v_allocated_total + v_allocate;
    v_allocation_order := v_allocation_order + 1;
    v_entry_sequence := v_entry_sequence + 1;
    v_entry_id := public.gen_random_uuid();
    INSERT INTO public.billing_journal_entry (
      id, tenant_id, operation_id, entry_sequence, entry_type, currency,
      actor_user_id, actor_session_id, posted_at
    ) VALUES (
      v_entry_id, p_tenant_id, p_operation_id, v_entry_sequence,
      'payment_allocated', 'TJS', p_actor_user_id, p_actor_session_id, v_created_at
    );
    INSERT INTO public.billing_journal_posting (
      tenant_id, entry_id, posting_sequence, account_code, side, amount,
      invoice_id, payment_id, created_at
    ) VALUES
      (p_tenant_id, v_entry_id, 1, 'unapplied_cash', 'debit', v_allocate,
       v_candidate.id, v_payment_id, v_created_at),
      (p_tenant_id, v_entry_id, 2, 'accounts_receivable', 'credit', v_allocate,
       v_candidate.id, v_payment_id, v_created_at);
    PERFORM public.assert_billing_journal_entry_balanced(v_entry_id);
  END LOOP;

  IF v_remaining > 0 THEN
    SELECT v_target_invoice.total_amount - COALESCE(sum(allocation.amount), 0)
    INTO v_target_outstanding
    FROM public.billing_payment_allocation AS allocation
    WHERE allocation.tenant_id = p_tenant_id
      AND allocation.invoice_id = v_target_invoice.id;
    v_target_outstanding := COALESCE(v_target_outstanding, v_target_invoice.total_amount);
    IF v_target_outstanding > 0 THEN
      v_allocate := LEAST(v_remaining, v_target_outstanding);
      INSERT INTO public.billing_payment_allocation (
        tenant_id, payment_id, invoice_id, allocation_order, amount, currency,
        allocated_at
      ) VALUES (
        p_tenant_id, v_payment_id, v_target_invoice.id, v_allocation_order,
        v_allocate, 'TJS', v_created_at
      );
      v_allocations := v_allocations || pg_catalog.jsonb_build_array(
        pg_catalog.jsonb_build_object(
          'invoice_id', v_target_invoice.id,
          'invoice_number', v_target_invoice.invoice_number,
          'amount', v_allocate::TEXT,
          'allocation_order', v_allocation_order
        )
      );
      v_remaining := v_remaining - v_allocate;
      v_allocated_total := v_allocated_total + v_allocate;
      v_entry_sequence := v_entry_sequence + 1;
      v_entry_id := public.gen_random_uuid();
      INSERT INTO public.billing_journal_entry (
        id, tenant_id, operation_id, entry_sequence, entry_type, currency,
        actor_user_id, actor_session_id, posted_at
      ) VALUES (
        v_entry_id, p_tenant_id, p_operation_id, v_entry_sequence,
        'payment_allocated', 'TJS', p_actor_user_id, p_actor_session_id, v_created_at
      );
      INSERT INTO public.billing_journal_posting (
        tenant_id, entry_id, posting_sequence, account_code, side, amount,
        invoice_id, payment_id, created_at
      ) VALUES
        (p_tenant_id, v_entry_id, 1, 'unapplied_cash', 'debit', v_allocate,
         v_target_invoice.id, v_payment_id, v_created_at),
        (p_tenant_id, v_entry_id, 2, 'accounts_receivable', 'credit', v_allocate,
         v_target_invoice.id, v_payment_id, v_created_at);
      PERFORM public.assert_billing_journal_entry_balanced(v_entry_id);
    END IF;
  END IF;

  IF v_remaining > 0 THEN
    v_credit_id := public.gen_random_uuid();
    v_credit_amount := v_remaining;
    INSERT INTO public.billing_tenant_credit (
      id, tenant_id, payment_id, amount, currency, created_at
    ) VALUES (
      v_credit_id, p_tenant_id, v_payment_id, v_credit_amount, 'TJS', v_created_at
    );
    v_entry_sequence := v_entry_sequence + 1;
    v_entry_id := public.gen_random_uuid();
    INSERT INTO public.billing_journal_entry (
      id, tenant_id, operation_id, entry_sequence, entry_type, currency,
      actor_user_id, actor_session_id, posted_at
    ) VALUES (
      v_entry_id, p_tenant_id, p_operation_id, v_entry_sequence,
      'credit_created', 'TJS', p_actor_user_id, p_actor_session_id, v_created_at
    );
    INSERT INTO public.billing_journal_posting (
      tenant_id, entry_id, posting_sequence, account_code, side, amount,
      payment_id, created_at
    ) VALUES
      (p_tenant_id, v_entry_id, 1, 'unapplied_cash', 'debit', v_credit_amount,
       v_payment_id, v_created_at),
      (p_tenant_id, v_entry_id, 2, 'tenant_credit', 'credit', v_credit_amount,
       v_payment_id, v_created_at);
    PERFORM public.assert_billing_journal_entry_balanced(v_entry_id);
  END IF;

  SELECT v_target_invoice.total_amount - COALESCE(sum(allocation.amount), 0)
  INTO v_target_outstanding
  FROM public.billing_payment_allocation AS allocation
  WHERE allocation.tenant_id = p_tenant_id
    AND allocation.invoice_id = v_target_invoice.id;
  v_target_outstanding := GREATEST(
    COALESCE(v_target_outstanding, v_target_invoice.total_amount), 0
  );
  SELECT COALESCE(sum(
    invoice.total_amount - COALESCE((
      SELECT sum(allocation.amount)
      FROM public.billing_payment_allocation AS allocation
      WHERE allocation.tenant_id = invoice.tenant_id
        AND allocation.invoice_id = invoice.id
    ), 0)
  ), 0)::NUMERIC(14,2)
  INTO v_blocking_outstanding
  FROM public.billing_invoice AS invoice
  WHERE invoice.tenant_id = p_tenant_id
    AND invoice.document_state = 'issued'
    AND invoice.due_at <= v_created_at
    AND invoice.total_amount > COALESCE((
      SELECT sum(allocation.amount)
      FROM public.billing_payment_allocation AS allocation
      WHERE allocation.tenant_id = invoice.tenant_id
        AND allocation.invoice_id = invoice.id
    ), 0);

  SELECT * INTO v_subscription
  FROM public.tenant_subscription AS subscription
  WHERE subscription.tenant_id = p_tenant_id
    AND subscription.id = v_target_invoice.subscription_id
  FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Tenant subscription was not found' USING ERRCODE = 'P0002';
  END IF;
  IF v_target_outstanding = 0 AND v_blocking_outstanding = 0
    AND v_subscription.status IN ('trial', 'active', 'grace_period', 'suspended')
    AND (
      v_target_invoice.period_end > v_subscription.period_end
      OR v_subscription.status = 'suspended'
    )
  THEN
    SELECT application.calendar_anchor_day INTO v_anchor_day
    FROM public.billing_subscription_price_application AS application
    WHERE application.id = v_target_invoice.price_application_id;
    IF v_subscription.status = 'suspended' THEN
      v_activation_start := v_created_at;
      v_activation_end := public.calculate_billing_period_end(
        v_activation_start, v_subscription.billing_period, 'Asia/Dushanbe', v_anchor_day
      );
    ELSE
      v_activation_start := v_target_invoice.period_start;
      v_activation_end := v_target_invoice.period_end;
    END IF;
    UPDATE public.tenant_subscription
    SET status = 'active', period_start = v_activation_start,
        period_end = v_activation_end, amount = v_target_invoice.total_amount,
        updated_at = v_created_at
    WHERE tenant_id = p_tenant_id AND id = v_subscription.id;
    UPDATE public.tenant
    SET status = 'active', updated_at = v_created_at
    WHERE id = p_tenant_id;
    v_access_restored := true;
  END IF;

  UPDATE public.billing_payment_review
  SET status = 'approved', row_version = row_version + 1,
      approved_by = p_actor_user_id, approved_session_id = p_actor_session_id,
      approved_operation_id = p_operation_id, approved_at = v_created_at,
      updated_at = v_created_at
  WHERE id = v_review.id AND tenant_id = p_tenant_id;

  v_result := pg_catalog.jsonb_build_object(
    'review_id', v_review.id,
    'payment_id', v_payment_id,
    'tenant_id', p_tenant_id,
    'target_invoice_id', v_target_invoice.id,
    'amount', v_review.amount::TEXT,
    'currency', 'TJS',
    'paid_at', v_review.paid_at,
    'confirmed_at', v_created_at,
    'lifecycle_state', 'confirmed',
    'allocated_amount', v_allocated_total::TEXT,
    'credit_amount', v_credit_amount::TEXT,
    'target_outstanding_amount', v_target_outstanding::TEXT,
    'blocking_outstanding_amount', v_blocking_outstanding::TEXT,
    'allocations', v_allocations,
    'access_restored', v_access_restored,
    'subscription_status', CASE WHEN v_access_restored THEN 'active'
      ELSE v_subscription.status END,
    'subscription_period_start', CASE WHEN v_access_restored
      THEN v_activation_start ELSE v_subscription.period_start END,
    'subscription_period_end', CASE WHEN v_access_restored
      THEN v_activation_end ELSE v_subscription.period_end END
  );
  INSERT INTO public.billing_financial_operation (
    operation_id, operation_type, tenant_id, actor_user_id, actor_session_id,
    mfa_verified_at, request_hash, request_payload, result_snapshot, created_at
  ) VALUES (
    p_operation_id, 'payment_approved', p_tenant_id, p_actor_user_id,
    p_actor_session_id, v_mfa_at, p_request_hash, v_payload, v_result, v_created_at
  );
  INSERT INTO public.billing_outbox_event (
    tenant_id, operation_id, event_type, aggregate_type, aggregate_id,
    payload, created_at
  ) VALUES (
    p_tenant_id, p_operation_id, 'billing.payment.confirmed',
    'billing_payment', v_payment_id,
    pg_catalog.jsonb_build_object(
      'payment_id', v_payment_id, 'tenant_id', p_tenant_id,
      'access_restored', v_access_restored
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
CREATE FUNCTION public.read_platform_billing_financial_account(
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
      WHERE invoice.tenant_id = p_tenant_id
        AND invoice.document_state = 'issued'
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


def _secure_function(signature: str, *, grant_support: bool = False) -> None:
    op.execute(f"ALTER FUNCTION {signature} OWNER TO aurum_schema_owner")
    op.execute(
        f"REVOKE ALL PRIVILEGES ON FUNCTION {signature} "
        "FROM PUBLIC, aurum_app, aurum_support, aurum_mailer, "
        "aurum_edge_cash_executor, aurum_edge_cash_owner"
    )
    if grant_support:
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO aurum_support")


def _create_rls(table: str, *, tenant_read: bool) -> None:
    op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
    op.execute(f"""
        CREATE POLICY {table}_owner_access ON public.{table}
        TO aurum_schema_owner USING (true) WITH CHECK (true)
        """)
    if tenant_read:
        op.execute(f"""
            CREATE POLICY {table}_tenant_read ON public.{table}
            FOR SELECT TO aurum_app
            USING (tenant_id = public.current_tenant_id())
            """)


def _revoke_table(table: str, *, tenant_read: bool) -> None:
    op.execute(f"""
        REVOKE ALL PRIVILEGES ON TABLE public.{table}
        FROM PUBLIC, aurum_app, aurum_support, aurum_mailer,
          aurum_edge_cash_executor, aurum_edge_cash_owner
        """)
    if tenant_read:
        op.execute(f"GRANT SELECT ON TABLE public.{table} TO aurum_app")


def _grant_missing_reference_privileges() -> None:
    op.execute("""
        CREATE TEMPORARY TABLE aurum_0097_missing_reference_privilege (
          table_name TEXT PRIMARY KEY
        ) ON COMMIT DROP
        """)
    op.execute("""
        DO $$
        DECLARE
          target_table TEXT;
        BEGIN
          FOREACH target_table IN ARRAY ARRAY[
            'app_user', 'billing_subscription_price_application',
            'tenant', 'tenant_subscription'
          ]
          LOOP
            IF NOT pg_catalog.has_table_privilege(
              'aurum_schema_owner',
              pg_catalog.format('public.%I', target_table),
              'REFERENCES'
            ) THEN
              INSERT INTO pg_temp.aurum_0097_missing_reference_privilege(table_name)
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
            FROM pg_temp.aurum_0097_missing_reference_privilege
          LOOP
            EXECUTE pg_catalog.format(
              'REVOKE REFERENCES ON TABLE public.%I FROM aurum_schema_owner',
              target_table
            );
          END LOOP;
        END
        $$
        """)
    op.execute("DROP TABLE pg_temp.aurum_0097_missing_reference_privilege")


def upgrade() -> None:
    _grant_missing_reference_privileges()
    op.execute("""
        ALTER TABLE public.billing_subscription_price_application
        ADD CONSTRAINT uq_billing_application_tenant_id UNIQUE (tenant_id, id)
        """)
    op.execute("CREATE SEQUENCE public.billing_invoice_number_seq AS BIGINT")
    op.execute("ALTER SEQUENCE public.billing_invoice_number_seq OWNER TO aurum_schema_owner")
    op.execute("""
        CREATE TABLE public.billing_financial_operation (
          operation_id UUID PRIMARY KEY,
          operation_type TEXT NOT NULL,
          tenant_id UUID NOT NULL REFERENCES public.tenant(id) ON DELETE RESTRICT,
          actor_user_id UUID NOT NULL REFERENCES public.app_user(id) ON DELETE RESTRICT,
          actor_session_id UUID NOT NULL,
          mfa_verified_at TIMESTAMPTZ NOT NULL,
          request_hash TEXT NOT NULL,
          request_payload JSONB NOT NULL,
          result_snapshot JSONB NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT uq_billing_financial_operation_tenant UNIQUE (tenant_id, operation_id),
          CONSTRAINT ck_billing_financial_operation_type CHECK (
            operation_type IN ('invoice_issued','payment_review_created','payment_approved')
          ),
          CONSTRAINT ck_billing_financial_operation_hash CHECK (
            request_hash ~ '^[0-9a-f]{64}$'
          ),
          CONSTRAINT ck_billing_financial_operation_payload CHECK (
            jsonb_typeof(request_payload) = 'object'
            AND octet_length(request_payload::TEXT) BETWEEN 2 AND 65536
          ),
          CONSTRAINT ck_billing_financial_operation_result CHECK (
            jsonb_typeof(result_snapshot) = 'object'
            AND octet_length(result_snapshot::TEXT) BETWEEN 2 AND 131072
          )
        )
        """)
    op.execute("""
        CREATE TABLE public.billing_invoice (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id UUID NOT NULL,
          subscription_id UUID NOT NULL,
          price_application_id UUID NOT NULL,
          operation_id UUID NOT NULL UNIQUE,
          invoice_number TEXT NOT NULL UNIQUE,
          document_type TEXT NOT NULL,
          document_state TEXT NOT NULL,
          period_start TIMESTAMPTZ NOT NULL,
          period_end TIMESTAMPTZ NOT NULL,
          due_at TIMESTAMPTZ NOT NULL,
          subtotal_amount NUMERIC(14,2) NOT NULL,
          discount_amount NUMERIC(14,2) NOT NULL DEFAULT 0,
          tax_amount NUMERIC(14,2) NOT NULL DEFAULT 0,
          total_amount NUMERIC(14,2) NOT NULL,
          currency TEXT NOT NULL DEFAULT 'TJS',
          issuer_snapshot JSONB NOT NULL,
          customer_snapshot JSONB NOT NULL,
          template_version TEXT NOT NULL,
          issued_by UUID NOT NULL REFERENCES public.app_user(id) ON DELETE RESTRICT,
          issued_session_id UUID NOT NULL,
          mfa_verified_at TIMESTAMPTZ NOT NULL,
          issued_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT uq_billing_invoice_tenant_id UNIQUE (tenant_id, id),
          CONSTRAINT fk_billing_invoice_subscription FOREIGN KEY (tenant_id, subscription_id)
            REFERENCES public.tenant_subscription(tenant_id, id) ON DELETE RESTRICT,
          CONSTRAINT fk_billing_invoice_application FOREIGN KEY (tenant_id, price_application_id)
            REFERENCES public.billing_subscription_price_application(tenant_id, id)
            ON DELETE RESTRICT,
          CONSTRAINT fk_billing_invoice_operation FOREIGN KEY (tenant_id, operation_id)
            REFERENCES public.billing_financial_operation(tenant_id, operation_id)
            ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
          CONSTRAINT ck_billing_invoice_type CHECK (
            document_type IN ('invoice','credit_note','debit_note')
          ),
          CONSTRAINT ck_billing_invoice_state CHECK (document_state IN ('issued','void')),
          CONSTRAINT ck_billing_invoice_period CHECK (period_end > period_start),
          CONSTRAINT ck_billing_invoice_money CHECK (
            subtotal_amount >= 0 AND discount_amount >= 0 AND tax_amount >= 0
            AND total_amount >= 0
            AND total_amount = subtotal_amount - discount_amount + tax_amount
          ),
          CONSTRAINT ck_billing_invoice_currency CHECK (currency = 'TJS'),
          CONSTRAINT ck_billing_invoice_snapshots CHECK (
            jsonb_typeof(issuer_snapshot) = 'object'
            AND jsonb_typeof(customer_snapshot) = 'object'
          )
        )
        """)
    op.execute("""
        CREATE UNIQUE INDEX uq_billing_invoice_regular_period
        ON public.billing_invoice(tenant_id, subscription_id, document_type, period_start)
        WHERE document_state = 'issued'
        """)
    op.execute("""
        CREATE INDEX ix_billing_invoice_tenant_due
        ON public.billing_invoice(tenant_id, due_at, issued_at)
        """)
    op.execute("""
        CREATE TABLE public.billing_invoice_line (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id UUID NOT NULL,
          invoice_id UUID NOT NULL,
          line_number SMALLINT NOT NULL,
          line_type TEXT NOT NULL,
          description TEXT NOT NULL,
          quantity NUMERIC(14,4) NOT NULL,
          unit_price NUMERIC(14,2) NOT NULL,
          price_version_id UUID,
          contract_override_id UUID,
          period_start TIMESTAMPTZ,
          period_end TIMESTAMPTZ,
          subtotal_amount NUMERIC(14,2) NOT NULL,
          discount_amount NUMERIC(14,2) NOT NULL DEFAULT 0,
          tax_amount NUMERIC(14,2) NOT NULL DEFAULT 0,
          total_amount NUMERIC(14,2) NOT NULL,
          currency TEXT NOT NULL DEFAULT 'TJS',
          created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT uq_billing_invoice_line_tenant_id UNIQUE (tenant_id, id),
          CONSTRAINT uq_billing_invoice_line_number UNIQUE (tenant_id, invoice_id, line_number),
          CONSTRAINT fk_billing_invoice_line_invoice FOREIGN KEY (tenant_id, invoice_id)
            REFERENCES public.billing_invoice(tenant_id, id) ON DELETE RESTRICT,
          CONSTRAINT ck_billing_invoice_line_type CHECK (
            line_type IN ('subscription','branch_prorata','service','discount','adjustment')
          ),
          CONSTRAINT ck_billing_invoice_line_quantity CHECK (quantity > 0),
          CONSTRAINT ck_billing_invoice_line_period CHECK (
            (period_start IS NULL AND period_end IS NULL)
            OR (period_start IS NOT NULL AND period_end > period_start)
          ),
          CONSTRAINT ck_billing_invoice_line_money CHECK (
            unit_price >= 0 AND subtotal_amount >= 0 AND discount_amount >= 0
            AND tax_amount >= 0 AND total_amount >= 0
            AND total_amount = subtotal_amount - discount_amount + tax_amount
          ),
          CONSTRAINT ck_billing_invoice_line_currency CHECK (currency = 'TJS')
        )
        """)
    op.execute("""
        CREATE TABLE public.billing_payment_review (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id UUID NOT NULL,
          target_invoice_id UUID NOT NULL,
          amount NUMERIC(14,2) NOT NULL,
          currency TEXT NOT NULL DEFAULT 'TJS',
          paid_at TIMESTAMPTZ NOT NULL,
          recipient_account_key TEXT NOT NULL,
          external_reference TEXT NOT NULL,
          status TEXT NOT NULL,
          row_version INTEGER NOT NULL DEFAULT 1,
          reviewed_by UUID NOT NULL REFERENCES public.app_user(id) ON DELETE RESTRICT,
          reviewed_session_id UUID NOT NULL,
          review_mfa_verified_at TIMESTAMPTZ NOT NULL,
          review_operation_id UUID NOT NULL UNIQUE,
          approved_by UUID REFERENCES public.app_user(id) ON DELETE RESTRICT,
          approved_session_id UUID,
          approved_operation_id UUID UNIQUE,
          approved_at TIMESTAMPTZ,
          created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT uq_billing_payment_review_tenant_id UNIQUE (tenant_id, id),
          CONSTRAINT fk_billing_review_invoice FOREIGN KEY (tenant_id, target_invoice_id)
            REFERENCES public.billing_invoice(tenant_id, id) ON DELETE RESTRICT,
          CONSTRAINT fk_billing_review_operation FOREIGN KEY (tenant_id, review_operation_id)
            REFERENCES public.billing_financial_operation(tenant_id, operation_id)
            ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
          CONSTRAINT fk_billing_review_approved_operation
            FOREIGN KEY (tenant_id, approved_operation_id)
            REFERENCES public.billing_financial_operation(tenant_id, operation_id)
            ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
          CONSTRAINT ck_billing_review_amount CHECK (amount > 0),
          CONSTRAINT ck_billing_review_currency CHECK (currency = 'TJS'),
          CONSTRAINT ck_billing_review_status CHECK (
            status IN ('pending_approval','approved','rejected','duplicate')
          ),
          CONSTRAINT ck_billing_review_version CHECK (row_version > 0),
          CONSTRAINT ck_billing_review_account_key CHECK (
            recipient_account_key ~ '^[a-z0-9][a-z0-9_.:-]{2,63}$'
          ),
          CONSTRAINT ck_billing_review_reference CHECK (
            external_reference ~ '^[A-Z0-9]{4,128}$'
          ),
          CONSTRAINT ck_billing_review_approval CHECK (
            (status = 'pending_approval' AND approved_by IS NULL
              AND approved_session_id IS NULL AND approved_operation_id IS NULL
              AND approved_at IS NULL)
            OR
            (status = 'approved' AND approved_by IS NOT NULL
              AND approved_session_id IS NOT NULL AND approved_operation_id IS NOT NULL
              AND approved_at IS NOT NULL AND approved_by <> reviewed_by)
            OR status IN ('rejected','duplicate')
          )
        )
        """)
    op.execute("""
        CREATE TABLE public.billing_payment (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id UUID NOT NULL,
          review_id UUID NOT NULL UNIQUE,
          operation_id UUID NOT NULL UNIQUE,
          amount NUMERIC(14,2) NOT NULL,
          currency TEXT NOT NULL DEFAULT 'TJS',
          paid_at TIMESTAMPTZ NOT NULL,
          recipient_account_key TEXT NOT NULL,
          external_reference TEXT NOT NULL,
          lifecycle_state TEXT NOT NULL,
          confirmed_by UUID NOT NULL REFERENCES public.app_user(id) ON DELETE RESTRICT,
          confirmed_session_id UUID NOT NULL,
          mfa_verified_at TIMESTAMPTZ NOT NULL,
          confirmed_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT uq_billing_payment_tenant_id UNIQUE (tenant_id, id),
          CONSTRAINT uq_billing_payment_bank_reference
            UNIQUE (recipient_account_key, external_reference),
          CONSTRAINT fk_billing_payment_review FOREIGN KEY (tenant_id, review_id)
            REFERENCES public.billing_payment_review(tenant_id, id) ON DELETE RESTRICT,
          CONSTRAINT fk_billing_payment_operation FOREIGN KEY (tenant_id, operation_id)
            REFERENCES public.billing_financial_operation(tenant_id, operation_id)
            ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
          CONSTRAINT ck_billing_payment_amount CHECK (amount > 0),
          CONSTRAINT ck_billing_payment_currency CHECK (currency = 'TJS'),
          CONSTRAINT ck_billing_payment_state CHECK (lifecycle_state IN ('confirmed','reversed'))
        )
        """)
    op.execute("""
        CREATE TABLE public.billing_payment_allocation (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id UUID NOT NULL,
          payment_id UUID NOT NULL,
          invoice_id UUID NOT NULL,
          allocation_order SMALLINT NOT NULL,
          amount NUMERIC(14,2) NOT NULL,
          currency TEXT NOT NULL DEFAULT 'TJS',
          allocated_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT uq_billing_allocation_tenant_id UNIQUE (tenant_id, id),
          CONSTRAINT uq_billing_allocation_payment_invoice
            UNIQUE (tenant_id, payment_id, invoice_id),
          CONSTRAINT uq_billing_allocation_payment_order
            UNIQUE (tenant_id, payment_id, allocation_order),
          CONSTRAINT fk_billing_allocation_payment FOREIGN KEY (tenant_id, payment_id)
            REFERENCES public.billing_payment(tenant_id, id) ON DELETE RESTRICT,
          CONSTRAINT fk_billing_allocation_invoice FOREIGN KEY (tenant_id, invoice_id)
            REFERENCES public.billing_invoice(tenant_id, id) ON DELETE RESTRICT,
          CONSTRAINT ck_billing_allocation_order CHECK (allocation_order > 0),
          CONSTRAINT ck_billing_allocation_amount CHECK (amount > 0),
          CONSTRAINT ck_billing_allocation_currency CHECK (currency = 'TJS')
        )
        """)
    op.execute("""
        CREATE TABLE public.billing_tenant_credit (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id UUID NOT NULL,
          payment_id UUID NOT NULL UNIQUE,
          amount NUMERIC(14,2) NOT NULL,
          currency TEXT NOT NULL DEFAULT 'TJS',
          created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT uq_billing_credit_tenant_id UNIQUE (tenant_id, id),
          CONSTRAINT fk_billing_credit_payment FOREIGN KEY (tenant_id, payment_id)
            REFERENCES public.billing_payment(tenant_id, id) ON DELETE RESTRICT,
          CONSTRAINT ck_billing_credit_amount CHECK (amount > 0),
          CONSTRAINT ck_billing_credit_currency CHECK (currency = 'TJS')
        )
        """)
    op.execute("""
        CREATE TABLE public.billing_journal_entry (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id UUID NOT NULL,
          operation_id UUID NOT NULL,
          entry_sequence SMALLINT NOT NULL,
          entry_type TEXT NOT NULL,
          currency TEXT NOT NULL DEFAULT 'TJS',
          actor_user_id UUID NOT NULL REFERENCES public.app_user(id) ON DELETE RESTRICT,
          actor_session_id UUID NOT NULL,
          posted_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT uq_billing_journal_entry_tenant_id UNIQUE (tenant_id, id),
          CONSTRAINT uq_billing_journal_operation_sequence
            UNIQUE (tenant_id, operation_id, entry_sequence),
          CONSTRAINT fk_billing_journal_operation FOREIGN KEY (tenant_id, operation_id)
            REFERENCES public.billing_financial_operation(tenant_id, operation_id)
            ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
          CONSTRAINT ck_billing_journal_entry_sequence CHECK (entry_sequence > 0),
          CONSTRAINT ck_billing_journal_entry_type CHECK (
            entry_type IN (
              'invoice_issued','payment_confirmed','payment_allocated','credit_created'
            )
          ),
          CONSTRAINT ck_billing_journal_currency CHECK (currency = 'TJS')
        )
        """)
    op.execute("""
        CREATE TABLE public.billing_journal_posting (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id UUID NOT NULL,
          entry_id UUID NOT NULL,
          posting_sequence SMALLINT NOT NULL,
          account_code TEXT NOT NULL,
          side TEXT NOT NULL,
          amount NUMERIC(14,2) NOT NULL,
          invoice_id UUID,
          payment_id UUID,
          created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT uq_billing_journal_posting_tenant_id UNIQUE (tenant_id, id),
          CONSTRAINT uq_billing_journal_posting_sequence
            UNIQUE (tenant_id, entry_id, posting_sequence),
          CONSTRAINT fk_billing_posting_entry FOREIGN KEY (tenant_id, entry_id)
            REFERENCES public.billing_journal_entry(tenant_id, id) ON DELETE RESTRICT,
          CONSTRAINT fk_billing_posting_invoice FOREIGN KEY (tenant_id, invoice_id)
            REFERENCES public.billing_invoice(tenant_id, id) ON DELETE RESTRICT,
          CONSTRAINT fk_billing_posting_payment FOREIGN KEY (tenant_id, payment_id)
            REFERENCES public.billing_payment(tenant_id, id) ON DELETE RESTRICT,
          CONSTRAINT ck_billing_posting_sequence CHECK (posting_sequence > 0),
          CONSTRAINT ck_billing_posting_account CHECK (
            account_code IN ('accounts_receivable','subscription_revenue',
              'bank_cleared','unapplied_cash','tenant_credit')
          ),
          CONSTRAINT ck_billing_posting_side CHECK (side IN ('debit','credit')),
          CONSTRAINT ck_billing_posting_amount CHECK (amount > 0)
        )
        """)
    op.execute("""
        CREATE TABLE public.billing_outbox_event (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id UUID NOT NULL,
          operation_id UUID NOT NULL,
          event_type TEXT NOT NULL,
          aggregate_type TEXT NOT NULL,
          aggregate_id UUID NOT NULL,
          payload JSONB NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending',
          attempt_count INTEGER NOT NULL DEFAULT 0,
          available_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          claimed_at TIMESTAMPTZ,
          claim_token UUID,
          delivered_at TIMESTAMPTZ,
          created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT uq_billing_outbox_operation_event UNIQUE (operation_id, event_type),
          CONSTRAINT fk_billing_outbox_operation FOREIGN KEY (tenant_id, operation_id)
            REFERENCES public.billing_financial_operation(tenant_id, operation_id)
            ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
          CONSTRAINT ck_billing_outbox_payload CHECK (
            jsonb_typeof(payload) = 'object'
            AND octet_length(payload::TEXT) BETWEEN 2 AND 16384
          ),
          CONSTRAINT ck_billing_outbox_status CHECK (
            status IN ('pending','claimed','delivered','dead_letter')
          ),
          CONSTRAINT ck_billing_outbox_attempts CHECK (attempt_count >= 0)
        )
        """)
    op.execute("""
        CREATE INDEX ix_billing_outbox_ready
        ON public.billing_outbox_event(status, available_at, created_at)
        WHERE status IN ('pending','claimed')
        """)

    op.execute(IMMUTABLE_FINANCIAL_ROW_SQL)
    _secure_function("public.trg_reject_immutable_billing_financial_mutation()")
    immutable_tables = (
        "billing_financial_operation",
        "billing_invoice",
        "billing_invoice_line",
        "billing_payment",
        "billing_payment_allocation",
        "billing_tenant_credit",
        "billing_journal_entry",
        "billing_journal_posting",
    )
    for table in immutable_tables:
        op.execute(f"""
            CREATE TRIGGER trg_immutable_{table}
            BEFORE UPDATE OR DELETE ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION
              public.trg_reject_immutable_billing_financial_mutation()
            """)

    op.execute(AUDIT_FINANCIAL_ROW_SQL)
    _secure_function("public.trg_audit_billing_financial_row()")
    audited_tables = (
        "billing_invoice",
        "billing_payment_review",
        "billing_payment",
        "billing_payment_allocation",
        "billing_tenant_credit",
        "billing_journal_entry",
    )
    for table in audited_tables:
        op.execute(f"""
            CREATE TRIGGER trg_audit_{table}
            AFTER INSERT OR UPDATE ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION public.trg_audit_billing_financial_row()
            """)

    tenant_read_tables = {
        "billing_invoice",
        "billing_invoice_line",
        "billing_payment",
        "billing_payment_allocation",
        "billing_tenant_credit",
    }
    all_tables = (
        "billing_financial_operation",
        "billing_invoice",
        "billing_invoice_line",
        "billing_payment_review",
        "billing_payment",
        "billing_payment_allocation",
        "billing_tenant_credit",
        "billing_journal_entry",
        "billing_journal_posting",
        "billing_outbox_event",
    )
    for table in all_tables:
        _create_rls(table, tenant_read=table in tenant_read_tables)
        _revoke_table(table, tenant_read=table in tenant_read_tables)
    op.execute("""
        REVOKE ALL PRIVILEGES ON SEQUENCE public.billing_invoice_number_seq
        FROM PUBLIC, aurum_app, aurum_support, aurum_mailer,
          aurum_edge_cash_executor, aurum_edge_cash_owner
        """)

    op.execute(GLOBAL_BILLING_OPERATION_SQL)
    _secure_function("public.trg_enforce_global_billing_operation_id()")
    op.execute("""
        CREATE TRIGGER trg_global_billing_operation_id
        BEFORE INSERT ON public.billing_financial_operation
        FOR EACH ROW EXECUTE FUNCTION public.trg_enforce_global_billing_operation_id()
        """)
    op.execute(ASSERT_BALANCED_ENTRY_SQL)
    _secure_function("public.assert_billing_journal_entry_balanced(UUID)")
    op.execute(ASSERT_INVOICE_TOTAL_SQL)
    _secure_function("public.assert_billing_invoice_totals(UUID)")
    op.execute(ISSUE_INVOICE_SQL)
    _secure_function(
        "public.issue_billing_subscription_invoice(UUID, UUID, UUID, TEXT, UUID, UUID, INTEGER)",
        grant_support=True,
    )
    op.execute(CREATE_PAYMENT_REVIEW_SQL)
    _secure_function(
        "public.create_billing_bank_payment_review("
        "UUID, UUID, UUID, TEXT, UUID, UUID, NUMERIC, TIMESTAMPTZ, TEXT, TEXT)",
        grant_support=True,
    )
    op.execute(APPROVE_PAYMENT_SQL)
    _secure_function(
        "public.approve_billing_bank_payment(UUID, UUID, UUID, TEXT, UUID, UUID, INTEGER)",
        grant_support=True,
    )
    op.execute(READ_FINANCIAL_ACCOUNT_SQL)
    _secure_function(
        "public.read_platform_billing_financial_account(UUID, UUID, UUID)",
        grant_support=True,
    )
    _restore_reference_privileges()


def downgrade() -> None:
    signatures = (
        "public.read_platform_billing_financial_account(UUID, UUID, UUID)",
        "public.approve_billing_bank_payment(UUID, UUID, UUID, TEXT, UUID, UUID, INTEGER)",
        "public.create_billing_bank_payment_review("
        "UUID, UUID, UUID, TEXT, UUID, UUID, NUMERIC, TIMESTAMPTZ, TEXT, TEXT)",
        "public.issue_billing_subscription_invoice(UUID, UUID, UUID, TEXT, UUID, UUID, INTEGER)",
    )
    for signature in signatures:
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")
    op.execute("DROP FUNCTION IF EXISTS public.assert_billing_invoice_totals(UUID)")
    op.execute("DROP FUNCTION public.assert_billing_journal_entry_balanced(UUID)")
    op.execute("""
        DROP TRIGGER trg_global_billing_operation_id
        ON public.billing_financial_operation
        """)
    op.execute(RESTORE_GLOBAL_BILLING_OPERATION_SQL)
    _secure_function("public.trg_enforce_global_billing_operation_id()")

    audited_tables = (
        "billing_invoice",
        "billing_payment_review",
        "billing_payment",
        "billing_payment_allocation",
        "billing_tenant_credit",
        "billing_journal_entry",
    )
    for table in audited_tables:
        op.execute(f"DROP TRIGGER trg_audit_{table} ON public.{table}")
    op.execute("DROP FUNCTION public.trg_audit_billing_financial_row()")
    immutable_tables = (
        "billing_financial_operation",
        "billing_invoice",
        "billing_invoice_line",
        "billing_payment",
        "billing_payment_allocation",
        "billing_tenant_credit",
        "billing_journal_entry",
        "billing_journal_posting",
    )
    for table in immutable_tables:
        op.execute(f"DROP TRIGGER trg_immutable_{table} ON public.{table}")
    op.execute("DROP FUNCTION public.trg_reject_immutable_billing_financial_mutation()")

    tables = (
        "billing_outbox_event",
        "billing_journal_posting",
        "billing_journal_entry",
        "billing_tenant_credit",
        "billing_payment_allocation",
        "billing_payment",
        "billing_payment_review",
        "billing_invoice_line",
        "billing_invoice",
        "billing_financial_operation",
    )
    for table in tables:
        op.execute(f"DROP TABLE public.{table}")
    op.execute("DROP SEQUENCE public.billing_invoice_number_seq")
    op.execute("""
        ALTER TABLE public.billing_subscription_price_application
        DROP CONSTRAINT uq_billing_application_tenant_id
        """)
