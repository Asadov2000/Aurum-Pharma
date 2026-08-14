"""add immutable subscription price applications

Revision ID: 0096
Revises: 0095
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0096"
down_revision: str | None = "0095"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


PERIOD_END_SQL = r"""
CREATE FUNCTION public.calculate_billing_period_end(
  p_period_start TIMESTAMPTZ,
  p_billing_period TEXT,
  p_timezone TEXT,
  p_anchor_day SMALLINT
)
RETURNS TIMESTAMPTZ AS $$
DECLARE
  v_local_start TIMESTAMP;
  v_target_month TIMESTAMP;
  v_last_day DATE;
  v_target_date DATE;
  v_local_time INTERVAL;
BEGIN
  IF p_period_start IS NULL
    OR p_billing_period NOT IN ('monthly', 'yearly')
    OR p_timezone <> 'Asia/Dushanbe'
    OR p_anchor_day NOT BETWEEN 1 AND 31
  THEN
    RAISE EXCEPTION 'Invalid billing calendar input' USING ERRCODE = '22023';
  END IF;

  v_local_start := p_period_start AT TIME ZONE p_timezone;
  v_target_month := pg_catalog.date_trunc('month', v_local_start)
    + CASE p_billing_period
        WHEN 'monthly' THEN INTERVAL '1 month'
        ELSE INTERVAL '12 months'
      END;
  v_last_day := (v_target_month + INTERVAL '1 month - 1 day')::DATE;
  v_target_date := pg_catalog.make_date(
    EXTRACT(year FROM v_target_month)::INTEGER,
    EXTRACT(month FROM v_target_month)::INTEGER,
    LEAST(
      p_anchor_day::INTEGER,
      EXTRACT(day FROM v_last_day)::INTEGER
    )
  );
  v_local_time := v_local_start - pg_catalog.date_trunc('day', v_local_start);
  RETURN (v_target_date::TIMESTAMP + v_local_time) AT TIME ZONE p_timezone;
END;
$$ LANGUAGE plpgsql
IMMUTABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


ROW_VERSION_SQL = r"""
CREATE FUNCTION public.trg_billing_subscription_row_version()
RETURNS TRIGGER AS $$
BEGIN
  NEW.row_version := OLD.row_version + 1;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


BIND_LEGACY_PLAN_SQL = r"""
CREATE FUNCTION public.trg_bind_billing_plan_legacy()
RETURNS TRIGGER AS $$
BEGIN
  IF NEW.legacy_subscription_plan_id IS NULL THEN
    SELECT legacy_plan.id
    INTO NEW.legacy_subscription_plan_id
    FROM public.subscription_plan AS legacy_plan
    WHERE legacy_plan.code = NEW.code;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


IMMUTABLE_APPLICATION_SQL = r"""
CREATE FUNCTION public.trg_reject_subscription_price_application_mutation()
RETURNS TRIGGER AS $$
BEGIN
  RAISE EXCEPTION 'Subscription price applications are immutable'
    USING ERRCODE = '55000';
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


AUDIT_APPLICATION_SQL = r"""
CREATE FUNCTION public.trg_audit_subscription_price_application()
RETURNS TRIGGER AS $$
BEGIN
  INSERT INTO public.audit_log (
    tenant_id, user_id, action, table_name, record_id, metadata, created_at
  ) VALUES (
    NEW.tenant_id,
    NEW.actor_user_id,
    'INSERT',
    'billing_subscription_price_application',
    NEW.id,
    pg_catalog.jsonb_build_object(
      'application_kind', NEW.application_kind,
      'source_type', NEW.source_type,
      'subscription_id', NEW.subscription_id,
      'plan_id', NEW.plan_id,
      'operation_id', NEW.operation_id,
      'request_hash', NEW.request_hash,
      'period_start', NEW.period_start,
      'period_end', NEW.period_end,
      'currency', NEW.currency
    ),
    NEW.created_at
  );
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


PRICING_PLAN_LOCK_SQL = r"""
CREATE FUNCTION public.trg_lock_billing_pricing_plan()
RETURNS TRIGGER AS $$
DECLARE
  v_plan_id UUID;
BEGIN
  IF TG_TABLE_NAME = 'billing_plan' THEN
    v_plan_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;
  ELSE
    v_plan_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.plan_id ELSE NEW.plan_id END;
  END IF;
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(v_plan_id::TEXT, 9602)
  );
  IF TG_OP = 'DELETE' THEN
    RETURN OLD;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


GLOBAL_BILLING_OPERATION_SQL = r"""
CREATE FUNCTION public.trg_enforce_global_billing_operation_id()
RETURNS TRIGGER AS $$
BEGIN
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(NEW.operation_id::TEXT, 9501)
  );
  IF TG_TABLE_NAME = 'billing_pricing_admin_event' THEN
    IF EXISTS (
      SELECT 1
      FROM public.billing_subscription_price_application AS application
      WHERE application.operation_id = NEW.operation_id
    ) THEN
      RAISE EXCEPTION 'Billing operation id was reused'
        USING ERRCODE = '23505';
    END IF;
  ELSIF EXISTS (
    SELECT 1
    FROM public.billing_pricing_admin_event AS event
    WHERE event.operation_id = NEW.operation_id
  ) THEN
    RAISE EXCEPTION 'Billing operation id was reused'
      USING ERRCODE = '23505';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


TENANT_BILLING_SCOPE_LOCK_SQL = r"""
CREATE FUNCTION public.trg_lock_tenant_billing_scope()
RETURNS TRIGGER AS $$
DECLARE
  v_first_tenant_id UUID;
  v_second_tenant_id UUID;
BEGIN
  IF TG_OP = 'UPDATE' AND OLD.tenant_id IS DISTINCT FROM NEW.tenant_id THEN
    IF OLD.tenant_id::TEXT < NEW.tenant_id::TEXT THEN
      v_first_tenant_id := OLD.tenant_id;
      v_second_tenant_id := NEW.tenant_id;
    ELSE
      v_first_tenant_id := NEW.tenant_id;
      v_second_tenant_id := OLD.tenant_id;
    END IF;
  ELSE
    v_first_tenant_id := CASE
      WHEN TG_OP = 'DELETE' THEN OLD.tenant_id
      ELSE NEW.tenant_id
    END;
  END IF;
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(v_first_tenant_id::TEXT, 9603)
  );
  IF v_second_tenant_id IS NOT NULL THEN
    PERFORM pg_catalog.pg_advisory_xact_lock(
      pg_catalog.hashtextextended(v_second_tenant_id::TEXT, 9603)
    );
  END IF;
  IF TG_OP = 'DELETE' THEN
    RETURN OLD;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


PRICING_COMMAND_WRAPPERS_SQL = r"""
ALTER FUNCTION public.create_billing_price_draft(
  UUID, UUID, UUID, TEXT, UUID, NUMERIC, NUMERIC, TEXT, SMALLINT, TEXT, JSONB
) RENAME TO create_billing_price_draft_unlocked_0095;
-- WRAPPER_SPLIT
ALTER FUNCTION public.approve_and_schedule_billing_price(
  UUID, UUID, UUID, TEXT, UUID, INTEGER, TIMESTAMPTZ
) RENAME TO approve_and_schedule_billing_price_unlocked_0095;
-- WRAPPER_SPLIT
ALTER FUNCTION public.activate_billing_price_version(
  UUID, UUID, UUID, TEXT, UUID, INTEGER
) RENAME TO activate_billing_price_version_unlocked_0095;
-- WRAPPER_SPLIT
ALTER FUNCTION public.cancel_scheduled_billing_price(
  UUID, UUID, UUID, TEXT, UUID, INTEGER, TEXT, TEXT
) RENAME TO cancel_scheduled_billing_price_unlocked_0095;
-- WRAPPER_SPLIT
CREATE FUNCTION public.create_billing_price_draft(
  p_actor_user_id UUID,
  p_actor_session_id UUID,
  p_operation_id UUID,
  p_request_hash TEXT,
  p_plan_id UUID,
  p_monthly_price_per_branch NUMERIC,
  p_annual_discount_pct NUMERIC,
  p_audience TEXT,
  p_notice_days SMALLINT,
  p_change_reason TEXT,
  p_terms_snapshot JSONB
)
RETURNS TABLE(result JSONB, applied BOOLEAN) AS $$
BEGIN
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(p_operation_id::TEXT, 9501)
  );
  IF EXISTS (
    SELECT 1 FROM public.billing_pricing_admin_event
    WHERE operation_id = p_operation_id
  ) THEN
    RETURN QUERY
    SELECT command.result, command.applied
    FROM public.create_billing_price_draft_unlocked_0095(
      p_actor_user_id, p_actor_session_id, p_operation_id, p_request_hash,
      p_plan_id, p_monthly_price_per_branch, p_annual_discount_pct,
      p_audience, p_notice_days, p_change_reason, p_terms_snapshot
    ) AS command;
    RETURN;
  END IF;
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(p_plan_id::TEXT, 9602)
  );
  RETURN QUERY
  SELECT command.result, command.applied
  FROM public.create_billing_price_draft_unlocked_0095(
    p_actor_user_id, p_actor_session_id, p_operation_id, p_request_hash,
    p_plan_id, p_monthly_price_per_branch, p_annual_discount_pct,
    p_audience, p_notice_days, p_change_reason, p_terms_snapshot
  ) AS command;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp;
-- WRAPPER_SPLIT
CREATE FUNCTION public.approve_and_schedule_billing_price(
  p_actor_user_id UUID,
  p_actor_session_id UUID,
  p_operation_id UUID,
  p_request_hash TEXT,
  p_price_version_id UUID,
  p_expected_row_version INTEGER,
  p_effective_from TIMESTAMPTZ
)
RETURNS TABLE(result JSONB, applied BOOLEAN) AS $$
DECLARE
  v_plan_id UUID;
BEGIN
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(p_operation_id::TEXT, 9501)
  );
  IF EXISTS (
    SELECT 1 FROM public.billing_pricing_admin_event
    WHERE operation_id = p_operation_id
  ) THEN
    RETURN QUERY
    SELECT command.result, command.applied
    FROM public.approve_and_schedule_billing_price_unlocked_0095(
      p_actor_user_id, p_actor_session_id, p_operation_id, p_request_hash,
      p_price_version_id, p_expected_row_version, p_effective_from
    ) AS command;
    RETURN;
  END IF;
  SELECT price.plan_id INTO v_plan_id
  FROM public.billing_price_version AS price
  WHERE price.id = p_price_version_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Billing price version was not found' USING ERRCODE = 'P0002';
  END IF;
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(v_plan_id::TEXT, 9602)
  );
  RETURN QUERY
  SELECT command.result, command.applied
  FROM public.approve_and_schedule_billing_price_unlocked_0095(
    p_actor_user_id, p_actor_session_id, p_operation_id, p_request_hash,
    p_price_version_id, p_expected_row_version, p_effective_from
  ) AS command;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp;
-- WRAPPER_SPLIT
CREATE FUNCTION public.activate_billing_price_version(
  p_actor_user_id UUID,
  p_actor_session_id UUID,
  p_operation_id UUID,
  p_request_hash TEXT,
  p_price_version_id UUID,
  p_expected_row_version INTEGER
)
RETURNS TABLE(result JSONB, applied BOOLEAN) AS $$
DECLARE
  v_plan_id UUID;
BEGIN
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(p_operation_id::TEXT, 9501)
  );
  IF EXISTS (
    SELECT 1 FROM public.billing_pricing_admin_event
    WHERE operation_id = p_operation_id
  ) THEN
    RETURN QUERY
    SELECT command.result, command.applied
    FROM public.activate_billing_price_version_unlocked_0095(
      p_actor_user_id, p_actor_session_id, p_operation_id, p_request_hash,
      p_price_version_id, p_expected_row_version
    ) AS command;
    RETURN;
  END IF;
  SELECT price.plan_id INTO v_plan_id
  FROM public.billing_price_version AS price
  WHERE price.id = p_price_version_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Billing price version was not found' USING ERRCODE = 'P0002';
  END IF;
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(v_plan_id::TEXT, 9602)
  );
  RETURN QUERY
  SELECT command.result, command.applied
  FROM public.activate_billing_price_version_unlocked_0095(
    p_actor_user_id, p_actor_session_id, p_operation_id, p_request_hash,
    p_price_version_id, p_expected_row_version
  ) AS command;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp;
-- WRAPPER_SPLIT
CREATE FUNCTION public.cancel_scheduled_billing_price(
  p_actor_user_id UUID,
  p_actor_session_id UUID,
  p_operation_id UUID,
  p_request_hash TEXT,
  p_price_version_id UUID,
  p_expected_row_version INTEGER,
  p_reason_code TEXT,
  p_reason TEXT
)
RETURNS TABLE(result JSONB, applied BOOLEAN) AS $$
DECLARE
  v_plan_id UUID;
BEGIN
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(p_operation_id::TEXT, 9501)
  );
  IF EXISTS (
    SELECT 1 FROM public.billing_pricing_admin_event
    WHERE operation_id = p_operation_id
  ) THEN
    RETURN QUERY
    SELECT command.result, command.applied
    FROM public.cancel_scheduled_billing_price_unlocked_0095(
      p_actor_user_id, p_actor_session_id, p_operation_id, p_request_hash,
      p_price_version_id, p_expected_row_version, p_reason_code, p_reason
    ) AS command;
    RETURN;
  END IF;
  SELECT price.plan_id INTO v_plan_id
  FROM public.billing_price_version AS price
  WHERE price.id = p_price_version_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Billing price version was not found' USING ERRCODE = 'P0002';
  END IF;
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(v_plan_id::TEXT, 9602)
  );
  RETURN QUERY
  SELECT command.result, command.applied
  FROM public.cancel_scheduled_billing_price_unlocked_0095(
    p_actor_user_id, p_actor_session_id, p_operation_id, p_request_hash,
    p_price_version_id, p_expected_row_version, p_reason_code, p_reason
  ) AS command;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp;
"""


APPLY_INITIAL_PRICE_SQL = r"""
CREATE FUNCTION public.apply_initial_subscription_price(
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
  v_existing public.billing_subscription_price_application%ROWTYPE;
  v_subscription public.tenant_subscription%ROWTYPE;
  v_tenant public.tenant%ROWTYPE;
  v_plan_id UUID;
  v_plan public.billing_plan%ROWTYPE;
  v_price public.billing_price_version%ROWTYPE;
  v_override public.billing_contract_override%ROWTYPE;
  v_application_id UUID;
  v_created_at TIMESTAMPTZ;
  v_source_type TEXT;
  v_source_count INTEGER;
  v_monthly_price NUMERIC(14,2);
  v_annual_discount NUMERIC(5,2);
  v_terms JSONB;
  v_period_start TIMESTAMPTZ;
  v_period_end TIMESTAMPTZ;
  v_anchor_day SMALLINT;
  v_active_branches INTEGER;
  v_amount NUMERIC(14,2);
  v_result JSONB;
BEGIN
  v_mfa_at := public.assert_and_lock_platform_recent_capability(
    p_actor_user_id,
    p_actor_session_id,
    'platform.billing.plan.manage'
  );
  IF p_operation_id IS NULL
    OR p_request_hash !~ '^[0-9a-f]{64}$'
    OR p_tenant_id IS NULL
    OR p_subscription_id IS NULL
    OR p_expected_row_version < 1
  THEN
    RAISE EXCEPTION 'Invalid subscription price application request'
      USING ERRCODE = '22023';
  END IF;

  v_payload := pg_catalog.jsonb_build_object(
    'tenant_id', p_tenant_id,
    'subscription_id', p_subscription_id,
    'expected_row_version', p_expected_row_version
  );
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(p_operation_id::TEXT, 9501)
  );
  IF EXISTS (
    SELECT 1
    FROM public.billing_pricing_admin_event AS event
    WHERE event.operation_id = p_operation_id
  ) THEN
    RAISE EXCEPTION 'Billing operation id was reused'
      USING ERRCODE = '23505';
  END IF;
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(p_tenant_id::TEXT, 9603)
  );
  SELECT * INTO v_existing
  FROM public.billing_subscription_price_application
  WHERE operation_id = p_operation_id;
  IF FOUND THEN
    IF v_existing.actor_user_id <> p_actor_user_id
      OR v_existing.request_hash <> p_request_hash
      OR v_existing.request_payload <> v_payload
    THEN
      RAISE EXCEPTION 'Billing operation id was reused'
        USING ERRCODE = '23505';
    END IF;
    RETURN QUERY SELECT v_existing.result_snapshot, false;
    RETURN;
  END IF;

  SELECT * INTO v_subscription
  FROM public.tenant_subscription
  WHERE id = p_subscription_id
    AND tenant_id = p_tenant_id
  FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Tenant subscription was not found' USING ERRCODE = 'P0002';
  END IF;
  IF v_subscription.status <> 'trial'
    OR v_subscription.row_version <> p_expected_row_version
  THEN
    RAISE EXCEPTION 'Tenant subscription changed concurrently'
      USING ERRCODE = '40001';
  END IF;
  IF v_subscription.period_end < pg_catalog.statement_timestamp() THEN
    RAISE EXCEPTION 'Expired trial pricing cannot be applied retroactively'
      USING ERRCODE = '22023';
  END IF;
  SELECT * INTO v_tenant
  FROM public.tenant AS tenant
  WHERE tenant.id = p_tenant_id
  FOR SHARE;
  IF NOT FOUND
    OR v_tenant.status <> 'trial'
    OR v_tenant.trial_started_at IS DISTINCT FROM v_subscription.period_start
    OR v_tenant.trial_ends_at IS DISTINCT FROM v_subscription.period_end
  THEN
    RAISE EXCEPTION 'Tenant trial state does not match its subscription'
      USING ERRCODE = '40001';
  END IF;
  SELECT pg_catalog.count(*)::INTEGER
  INTO v_active_branches
  FROM public.branch AS branch
  WHERE branch.tenant_id = p_tenant_id
    AND branch.is_active;
  IF v_active_branches < 1 THEN
    RAISE EXCEPTION 'Tenant has no active branches'
      USING ERRCODE = '22023';
  END IF;
  IF v_active_branches <> v_subscription.branches_count THEN
    RAISE EXCEPTION 'Tenant branch count changed concurrently'
      USING ERRCODE = '40001';
  END IF;
  IF EXISTS (
    SELECT 1
    FROM public.billing_subscription_price_application AS application
    WHERE application.subscription_id = v_subscription.id
      AND application.application_kind = 'initial'
  ) THEN
    RAISE EXCEPTION 'Initial subscription price is already fixed'
      USING ERRCODE = '23505';
  END IF;

  SELECT plan.id INTO v_plan_id
  FROM public.billing_plan AS plan
  WHERE plan.legacy_subscription_plan_id = v_subscription.plan_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Versioned billing plan is not linked'
      USING ERRCODE = 'P0002';
  END IF;

  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(v_plan_id::TEXT, 9602)
  );
  SELECT * INTO v_plan
  FROM public.billing_plan AS plan
  WHERE plan.id = v_plan_id
    AND plan.legacy_subscription_plan_id = v_subscription.plan_id
  FOR SHARE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Versioned billing plan changed concurrently'
      USING ERRCODE = '40001';
  END IF;

  v_period_start := v_subscription.period_end;
  v_anchor_day := EXTRACT(
    day FROM (v_period_start AT TIME ZONE 'Asia/Dushanbe')
  )::SMALLINT;
  v_period_end := public.calculate_billing_period_end(
    v_period_start,
    v_subscription.billing_period,
    'Asia/Dushanbe',
    v_anchor_day
  );

  PERFORM contract.id
  FROM public.billing_contract_override AS contract
  WHERE contract.tenant_id = p_tenant_id
    AND contract.plan_id = v_plan.id
    AND contract.status = 'scheduled'
    AND contract.valid_from <= v_period_start
    AND (contract.valid_until IS NULL OR contract.valid_until > v_period_start)
  ;
  IF FOUND THEN
    RAISE EXCEPTION 'Billing contract activation is pending'
      USING ERRCODE = '40001';
  END IF;

  PERFORM price.id
  FROM public.billing_price_version AS price
  WHERE price.plan_id = v_plan.id
    AND price.status = 'scheduled'
    AND price.audience IN ('new_customers', 'default')
    AND price.effective_from <= v_period_start
  ;
  IF FOUND THEN
    RAISE EXCEPTION 'Billing price activation is pending'
      USING ERRCODE = '40001';
  END IF;

  SELECT pg_catalog.count(*)::INTEGER
  INTO v_source_count
  FROM public.billing_contract_override AS contract
  WHERE contract.tenant_id = p_tenant_id
    AND contract.plan_id = v_plan.id
    AND contract.status = 'active'
    AND contract.valid_from <= v_period_start
    AND (contract.valid_until IS NULL OR contract.valid_until > v_period_start);
  IF v_source_count > 1 THEN
    RAISE EXCEPTION 'Ambiguous active billing contract override'
      USING ERRCODE = 'P0001';
  END IF;
  IF v_source_count = 1 THEN
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
    SELECT pg_catalog.count(*)::INTEGER
    INTO v_source_count
    FROM public.billing_price_version AS price
    WHERE price.plan_id = v_plan.id
      AND price.status = 'active'
      AND price.audience = 'new_customers'
      AND price.effective_from <= v_period_start;
    IF v_source_count > 1 THEN
      RAISE EXCEPTION 'Ambiguous new customer billing price'
        USING ERRCODE = 'P0001';
    END IF;
    IF v_source_count = 1 THEN
      SELECT * INTO STRICT v_price
      FROM public.billing_price_version AS price
      WHERE price.plan_id = v_plan.id
        AND price.status = 'active'
        AND price.audience = 'new_customers'
        AND price.effective_from <= v_period_start;
    ELSE
      SELECT pg_catalog.count(*)::INTEGER
      INTO v_source_count
      FROM public.billing_price_version AS price
      WHERE price.plan_id = v_plan.id
        AND price.status = 'active'
        AND price.audience = 'default'
        AND price.effective_from <= v_period_start;
      IF v_source_count <> 1 THEN
        IF v_source_count = 0 THEN
          RAISE EXCEPTION 'No active billing price is available'
            USING ERRCODE = 'P0001';
        END IF;
        RAISE EXCEPTION 'Ambiguous default billing price'
          USING ERRCODE = 'P0001';
      END IF;
      SELECT * INTO STRICT v_price
      FROM public.billing_price_version AS price
      WHERE price.plan_id = v_plan.id
        AND price.status = 'active'
        AND price.audience = 'default'
        AND price.effective_from <= v_period_start;
    END IF;
    v_source_type := 'price_version';
    v_monthly_price := v_price.monthly_price_per_branch;
    v_annual_discount := v_price.annual_discount_pct;
    v_terms := v_price.terms_snapshot;
  END IF;

  v_amount := pg_catalog.round(
    v_monthly_price * v_active_branches
      * CASE v_subscription.billing_period
          WHEN 'yearly' THEN 12 * (1 - v_annual_discount / 100)
          ELSE 1
        END,
    2
  );

  v_application_id := public.gen_random_uuid();
  v_created_at := pg_catalog.statement_timestamp();
  v_result := pg_catalog.jsonb_build_object(
    'application_id', v_application_id,
    'subscription_id', v_subscription.id,
    'application_kind', 'initial',
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
    'created_at', v_created_at
  );

  INSERT INTO public.billing_subscription_price_application (
    id,
    tenant_id,
    subscription_id,
    plan_id,
    application_kind,
    source_type,
    price_version_id,
    contract_override_id,
    plan_code,
    plan_name,
    billing_period,
    period_start,
    period_end,
    calendar_anchor_day,
    timezone,
    branches_count,
    monthly_price_per_branch,
    annual_discount_pct,
    calculated_amount,
    currency,
    terms_snapshot,
    operation_id,
    request_hash,
    request_payload,
    actor_user_id,
    actor_session_id,
    mfa_verified_at,
    result_snapshot,
    created_at
  ) VALUES (
    v_application_id,
    p_tenant_id,
    v_subscription.id,
    v_plan.id,
    'initial',
    v_source_type,
    CASE WHEN v_source_type = 'price_version' THEN v_price.id ELSE NULL END,
    CASE WHEN v_source_type = 'contract_override' THEN v_override.id ELSE NULL END,
    v_plan.code,
    v_plan.name,
    v_subscription.billing_period,
    v_period_start,
    v_period_end,
    v_anchor_day,
    'Asia/Dushanbe',
    v_active_branches,
    v_monthly_price,
    v_annual_discount,
    v_amount,
    'TJS',
    v_terms,
    p_operation_id,
    p_request_hash,
    v_payload,
    p_actor_user_id,
    p_actor_session_id,
    v_mfa_at,
    v_result,
    v_created_at
  );
  RETURN QUERY SELECT v_result, true;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


PRICING_COMMAND_FUNCTIONS = (
    (
        "create_billing_price_draft",
        "UUID, UUID, UUID, TEXT, UUID, NUMERIC, NUMERIC, TEXT, SMALLINT, TEXT, JSONB",
    ),
    (
        "approve_and_schedule_billing_price",
        "UUID, UUID, UUID, TEXT, UUID, INTEGER, TIMESTAMPTZ",
    ),
    (
        "activate_billing_price_version",
        "UUID, UUID, UUID, TEXT, UUID, INTEGER",
    ),
    (
        "cancel_scheduled_billing_price",
        "UUID, UUID, UUID, TEXT, UUID, INTEGER, TEXT, TEXT",
    ),
)


def _secure_function(signature: str, *, grant_support: bool = False) -> None:
    op.execute(f"ALTER FUNCTION {signature} OWNER TO aurum_schema_owner")
    op.execute(
        f"REVOKE ALL PRIVILEGES ON FUNCTION {signature} "
        "FROM PUBLIC, aurum_app, aurum_support, aurum_mailer, "
        "aurum_edge_cash_executor, aurum_edge_cash_owner"
    )
    if grant_support:
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO aurum_support")


def _install_pricing_command_wrappers() -> None:
    for statement in PRICING_COMMAND_WRAPPERS_SQL.split("-- WRAPPER_SPLIT"):
        op.execute(statement.strip())
    for name, arguments in PRICING_COMMAND_FUNCTIONS:
        _secure_function(f"public.{name}_unlocked_0095({arguments})")
        _secure_function(f"public.{name}({arguments})", grant_support=True)


def _restore_pricing_commands() -> None:
    for name, arguments in PRICING_COMMAND_FUNCTIONS:
        op.execute(f"""
            DO $$
            BEGIN
              IF pg_catalog.to_regprocedure(
                'public.{name}_unlocked_0095({arguments})'
              ) IS NOT NULL THEN
                DROP FUNCTION public.{name}({arguments});
                ALTER FUNCTION public.{name}_unlocked_0095({arguments})
                RENAME TO {name};
              END IF;
            END
            $$
            """)
        _secure_function(f"public.{name}({arguments})", grant_support=True)


def _grant_missing_reference_privileges() -> None:
    op.execute("""
        CREATE TEMPORARY TABLE aurum_0096_missing_reference_privilege (
          table_name TEXT PRIMARY KEY
        ) ON COMMIT DROP
        """)
    op.execute("""
        DO $$
        DECLARE
          target_table TEXT;
        BEGIN
          FOREACH target_table IN ARRAY ARRAY[
            'app_user', 'subscription_plan', 'tenant_subscription'
          ]
          LOOP
            IF NOT pg_catalog.has_table_privilege(
              'aurum_schema_owner',
              pg_catalog.format('public.%I', target_table),
              'REFERENCES'
            ) THEN
              INSERT INTO pg_temp.aurum_0096_missing_reference_privilege(table_name)
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
            FROM pg_temp.aurum_0096_missing_reference_privilege
          LOOP
            EXECUTE pg_catalog.format(
              'REVOKE REFERENCES ON TABLE public.%I FROM aurum_schema_owner',
              target_table
            );
          END LOOP;
        END
        $$
        """)
    op.execute("DROP TABLE pg_temp.aurum_0096_missing_reference_privilege")


def upgrade() -> None:
    _grant_missing_reference_privileges()
    op.execute("""
        ALTER TABLE public.billing_plan
        ADD COLUMN legacy_subscription_plan_id UUID
          REFERENCES public.subscription_plan(id) ON DELETE RESTRICT,
        ADD CONSTRAINT uq_billing_plan_legacy_subscription_plan
          UNIQUE (legacy_subscription_plan_id)
        """)
    op.execute("""
        UPDATE public.billing_plan AS plan
        SET legacy_subscription_plan_id = legacy_plan.id
        FROM public.subscription_plan AS legacy_plan
        WHERE plan.code = legacy_plan.code
          AND plan.legacy_subscription_plan_id IS NULL
        """)
    op.execute(BIND_LEGACY_PLAN_SQL)
    _secure_function("public.trg_bind_billing_plan_legacy()")
    op.execute("""
        CREATE TRIGGER trg_bind_billing_plan_legacy
        BEFORE INSERT ON public.billing_plan
        FOR EACH ROW EXECUTE FUNCTION public.trg_bind_billing_plan_legacy()
        """)
    op.execute(PRICING_PLAN_LOCK_SQL)
    _secure_function("public.trg_lock_billing_pricing_plan()")
    for table in (
        "billing_plan",
        "billing_price_version",
        "billing_contract_override",
    ):
        op.execute(f"""
            CREATE TRIGGER trg_00_lock_billing_pricing_plan
            BEFORE INSERT OR UPDATE OR DELETE ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION public.trg_lock_billing_pricing_plan()
            """)
    op.execute(TENANT_BILLING_SCOPE_LOCK_SQL)
    _secure_function("public.trg_lock_tenant_billing_scope()")
    op.execute("""
        CREATE TRIGGER trg_00_lock_tenant_billing_scope
        BEFORE INSERT OR UPDATE OR DELETE ON public.branch
        FOR EACH ROW EXECUTE FUNCTION public.trg_lock_tenant_billing_scope()
        """)
    _install_pricing_command_wrappers()

    op.execute("""
        ALTER TABLE public.tenant_subscription
        ADD COLUMN row_version INTEGER NOT NULL DEFAULT 1,
        ADD CONSTRAINT ck_tenant_subscription_row_version CHECK (row_version > 0),
        ADD CONSTRAINT uq_tenant_subscription_tenant_id UNIQUE (tenant_id, id)
        """)
    op.execute("""
        DO $guard$
        DECLARE
          duplicate_tenant UUID;
        BEGIN
          SELECT tenant_id
          INTO duplicate_tenant
          FROM public.tenant_subscription
          WHERE status IN ('trial', 'active', 'grace_period', 'suspended')
          GROUP BY tenant_id
          HAVING pg_catalog.count(*) > 1
          ORDER BY tenant_id
          LIMIT 1;
          IF duplicate_tenant IS NOT NULL THEN
            RAISE EXCEPTION
              'Duplicate current tenant subscriptions require manual reconciliation: %',
              duplicate_tenant
              USING ERRCODE = '23505';
          END IF;
        END
        $guard$
        """)
    op.execute("""
        CREATE UNIQUE INDEX uq_tenant_subscription_one_current
        ON public.tenant_subscription(tenant_id)
        WHERE status IN ('trial', 'active', 'grace_period', 'suspended')
        """)
    op.execute(ROW_VERSION_SQL)
    _secure_function("public.trg_billing_subscription_row_version()")
    op.execute("""
        CREATE TRIGGER trg_billing_subscription_row_version
        BEFORE UPDATE ON public.tenant_subscription
        FOR EACH ROW EXECUTE FUNCTION public.trg_billing_subscription_row_version()
        """)
    op.execute("ALTER TABLE public.tenant_subscription FORCE ROW LEVEL SECURITY")
    op.execute("DROP VIEW public.v_active_subscription")
    op.execute("""
        CREATE VIEW public.v_active_subscription
        WITH (security_invoker = true) AS
        SELECT
          subscription.id,
          subscription.tenant_id,
          subscription.plan_id,
          subscription.status,
          subscription.billing_period,
          subscription.period_start,
          subscription.period_end,
          subscription.branches_count,
          subscription.amount,
          subscription.currency,
          subscription.created_at,
          subscription.updated_at,
          subscription.cancelled_at,
          plan.name AS plan_name,
          plan.code AS plan_code,
          plan.features AS plan_features,
          subscription.row_version
        FROM public.tenant_subscription AS subscription
        JOIN public.subscription_plan AS plan ON plan.id = subscription.plan_id
        WHERE subscription.status NOT IN ('cancelled', 'archived')
        """)
    op.execute("REVOKE ALL PRIVILEGES ON TABLE public.v_active_subscription FROM PUBLIC")
    op.execute(
        "GRANT SELECT ON TABLE public.v_active_subscription TO aurum_app, aurum_support"
    )

    op.execute("""
        ALTER TABLE public.billing_price_version
        ADD CONSTRAINT uq_billing_price_plan_id UNIQUE (plan_id, id)
        """)
    op.execute("""
        ALTER TABLE public.billing_contract_override
        ADD CONSTRAINT uq_billing_contract_tenant_plan_id UNIQUE (tenant_id, plan_id, id)
        """)

    op.execute(PERIOD_END_SQL)
    _secure_function(
        "public.calculate_billing_period_end(TIMESTAMPTZ, TEXT, TEXT, SMALLINT)"
    )
    op.execute("""
        CREATE TABLE public.billing_subscription_price_application (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id UUID NOT NULL,
          subscription_id UUID NOT NULL,
          plan_id UUID NOT NULL REFERENCES public.billing_plan(id) ON DELETE RESTRICT,
          application_kind TEXT NOT NULL,
          source_type TEXT NOT NULL,
          price_version_id UUID,
          contract_override_id UUID,
          plan_code TEXT NOT NULL,
          plan_name TEXT NOT NULL,
          billing_period TEXT NOT NULL,
          period_start TIMESTAMPTZ NOT NULL,
          period_end TIMESTAMPTZ NOT NULL,
          calendar_anchor_day SMALLINT NOT NULL,
          timezone TEXT NOT NULL,
          branches_count INTEGER NOT NULL,
          monthly_price_per_branch NUMERIC(14,2) NOT NULL,
          annual_discount_pct NUMERIC(5,2) NOT NULL,
          calculated_amount NUMERIC(14,2) NOT NULL,
          currency TEXT NOT NULL,
          terms_snapshot JSONB NOT NULL,
          operation_id UUID NOT NULL UNIQUE,
          request_hash TEXT NOT NULL,
          request_payload JSONB NOT NULL,
          actor_user_id UUID NOT NULL REFERENCES public.app_user(id) ON DELETE RESTRICT,
          actor_session_id UUID NOT NULL,
          mfa_verified_at TIMESTAMPTZ NOT NULL,
          result_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
          created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT fk_billing_application_subscription
            FOREIGN KEY (tenant_id, subscription_id)
            REFERENCES public.tenant_subscription(tenant_id, id) ON DELETE RESTRICT,
          CONSTRAINT fk_billing_application_price
            FOREIGN KEY (plan_id, price_version_id)
            REFERENCES public.billing_price_version(plan_id, id) ON DELETE RESTRICT,
          CONSTRAINT fk_billing_application_contract
            FOREIGN KEY (tenant_id, plan_id, contract_override_id)
            REFERENCES public.billing_contract_override(tenant_id, plan_id, id)
            ON DELETE RESTRICT,
          CONSTRAINT ck_billing_application_kind CHECK (
            application_kind IN ('initial', 'renewal')
          ),
          CONSTRAINT ck_billing_application_source CHECK (
            (source_type = 'price_version'
              AND price_version_id IS NOT NULL AND contract_override_id IS NULL)
            OR
            (source_type = 'contract_override'
              AND price_version_id IS NULL AND contract_override_id IS NOT NULL)
          ),
          CONSTRAINT ck_billing_application_period CHECK (period_end > period_start),
          CONSTRAINT ck_billing_application_billing_period CHECK (
            billing_period IN ('monthly', 'yearly')
          ),
          CONSTRAINT ck_billing_application_anchor CHECK (
            calendar_anchor_day BETWEEN 1 AND 31
          ),
          CONSTRAINT ck_billing_application_timezone CHECK (timezone = 'Asia/Dushanbe'),
          CONSTRAINT ck_billing_application_branches CHECK (branches_count > 0),
          CONSTRAINT ck_billing_application_amounts CHECK (
            monthly_price_per_branch >= 0
            AND annual_discount_pct >= 0 AND annual_discount_pct < 100
            AND calculated_amount >= 0
          ),
          CONSTRAINT ck_billing_application_currency CHECK (currency = 'TJS'),
          CONSTRAINT ck_billing_application_terms CHECK (
            jsonb_typeof(terms_snapshot) = 'object'
            AND octet_length(terms_snapshot::TEXT) BETWEEN 2 AND 65536
          ),
          CONSTRAINT ck_billing_application_request_hash CHECK (
            request_hash ~ '^[0-9a-f]{64}$'
          ),
          CONSTRAINT ck_billing_application_request_payload CHECK (
            jsonb_typeof(request_payload) = 'object'
            AND octet_length(request_payload::TEXT) BETWEEN 2 AND 65536
          ),
          CONSTRAINT ck_billing_application_result_snapshot CHECK (
            jsonb_typeof(result_snapshot) = 'object'
            AND octet_length(result_snapshot::TEXT) BETWEEN 2 AND 65536
          )
        )
        """)
    _restore_reference_privileges()
    op.execute("""
        CREATE UNIQUE INDEX uq_billing_application_initial_subscription
        ON public.billing_subscription_price_application(subscription_id)
        WHERE application_kind = 'initial'
        """)
    op.execute("""
        CREATE UNIQUE INDEX uq_billing_application_period
        ON public.billing_subscription_price_application(
          tenant_id, subscription_id, period_start
        )
        """)
    op.execute("""
        CREATE INDEX ix_billing_application_tenant_period
        ON public.billing_subscription_price_application(tenant_id, period_start DESC)
        """)

    op.execute(IMMUTABLE_APPLICATION_SQL)
    _secure_function("public.trg_reject_subscription_price_application_mutation()")
    op.execute("""
        CREATE TRIGGER trg_immutable_subscription_price_application
        BEFORE UPDATE OR DELETE ON public.billing_subscription_price_application
        FOR EACH ROW
        EXECUTE FUNCTION public.trg_reject_subscription_price_application_mutation()
        """)
    op.execute(AUDIT_APPLICATION_SQL)
    _secure_function("public.trg_audit_subscription_price_application()")
    op.execute("""
        CREATE TRIGGER trg_audit_billing_subscription_price_application
        AFTER INSERT ON public.billing_subscription_price_application
        FOR EACH ROW
        EXECUTE FUNCTION public.trg_audit_subscription_price_application()
        """)
    op.execute("ALTER TABLE public.billing_subscription_price_application ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.billing_subscription_price_application FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY billing_application_owner_access
        ON public.billing_subscription_price_application
        TO aurum_schema_owner
        USING (true)
        WITH CHECK (true)
        """)
    op.execute("""
        CREATE POLICY billing_application_tenant_read
        ON public.billing_subscription_price_application
        FOR SELECT TO aurum_app
        USING (tenant_id = public.current_tenant_id())
        """)
    op.execute("""
        REVOKE ALL PRIVILEGES ON TABLE public.billing_subscription_price_application
        FROM PUBLIC, aurum_app, aurum_support, aurum_mailer,
          aurum_edge_cash_executor, aurum_edge_cash_owner
        """)
    op.execute(
        "GRANT SELECT ON TABLE public.billing_subscription_price_application TO aurum_app"
    )

    op.execute(GLOBAL_BILLING_OPERATION_SQL)
    _secure_function("public.trg_enforce_global_billing_operation_id()")
    op.execute("""
        CREATE TRIGGER trg_global_billing_operation_id
        BEFORE INSERT ON public.billing_pricing_admin_event
        FOR EACH ROW
        EXECUTE FUNCTION public.trg_enforce_global_billing_operation_id()
        """)
    op.execute("""
        CREATE TRIGGER trg_global_billing_operation_id
        BEFORE INSERT ON public.billing_subscription_price_application
        FOR EACH ROW
        EXECUTE FUNCTION public.trg_enforce_global_billing_operation_id()
        """)

    op.execute(APPLY_INITIAL_PRICE_SQL)
    _secure_function(
        "public.apply_initial_subscription_price(UUID, UUID, UUID, TEXT, UUID, UUID, INTEGER)",
        grant_support=True,
    )


def downgrade() -> None:
    op.execute("""
        DO $guard$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM public.billing_subscription_price_application
          ) THEN
            RAISE EXCEPTION
              'Refusing to remove non-empty subscription price applications';
          END IF;
        END
        $guard$
        """)
    op.execute(
        "DROP FUNCTION public.apply_initial_subscription_price("
        "UUID, UUID, UUID, TEXT, UUID, UUID, INTEGER)"
    )
    _restore_pricing_commands()
    op.execute(
        "DROP TRIGGER IF EXISTS trg_global_billing_operation_id "
        "ON public.billing_pricing_admin_event"
    )
    op.execute("DROP TABLE public.billing_subscription_price_application")
    op.execute("DROP FUNCTION IF EXISTS public.trg_enforce_global_billing_operation_id()")
    op.execute("DROP FUNCTION public.trg_audit_subscription_price_application()")
    op.execute("DROP FUNCTION public.trg_reject_subscription_price_application_mutation()")
    op.execute(
        "DROP FUNCTION public.calculate_billing_period_end("
        "TIMESTAMPTZ, TEXT, TEXT, SMALLINT)"
    )
    op.execute(
        "ALTER TABLE public.billing_contract_override "
        "DROP CONSTRAINT uq_billing_contract_tenant_plan_id"
    )
    op.execute(
        "ALTER TABLE public.billing_price_version "
        "DROP CONSTRAINT uq_billing_price_plan_id"
    )
    op.execute("ALTER TABLE public.tenant_subscription NO FORCE ROW LEVEL SECURITY")
    op.execute("DROP VIEW public.v_active_subscription")
    op.execute(
        "DROP TRIGGER trg_billing_subscription_row_version "
        "ON public.tenant_subscription"
    )
    op.execute("DROP FUNCTION public.trg_billing_subscription_row_version()")
    op.execute("DROP INDEX public.uq_tenant_subscription_one_current")
    op.execute("""
        ALTER TABLE public.tenant_subscription
        DROP CONSTRAINT uq_tenant_subscription_tenant_id,
        DROP CONSTRAINT ck_tenant_subscription_row_version,
        DROP COLUMN row_version
        """)
    op.execute("""
        CREATE VIEW public.v_active_subscription
        WITH (security_invoker = true) AS
        SELECT
          subscription.*,
          plan.name AS plan_name,
          plan.code AS plan_code,
          plan.features AS plan_features
        FROM public.tenant_subscription AS subscription
        JOIN public.subscription_plan AS plan ON plan.id = subscription.plan_id
        WHERE subscription.status NOT IN ('cancelled', 'archived')
        """)
    op.execute("REVOKE ALL PRIVILEGES ON TABLE public.v_active_subscription FROM PUBLIC")
    op.execute(
        "GRANT SELECT ON TABLE public.v_active_subscription TO aurum_app, aurum_support"
    )
    op.execute("DROP TRIGGER trg_bind_billing_plan_legacy ON public.billing_plan")
    op.execute("DROP FUNCTION public.trg_bind_billing_plan_legacy()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_00_lock_tenant_billing_scope ON public.branch"
    )
    op.execute("DROP FUNCTION IF EXISTS public.trg_lock_tenant_billing_scope()")
    for table in (
        "billing_plan",
        "billing_price_version",
        "billing_contract_override",
    ):
        op.execute(
            "DROP TRIGGER IF EXISTS trg_00_lock_billing_pricing_plan "
            f"ON public.{table}"
        )
    op.execute("DROP FUNCTION IF EXISTS public.trg_lock_billing_pricing_plan()")
    op.execute("""
        ALTER TABLE public.billing_plan
        DROP CONSTRAINT uq_billing_plan_legacy_subscription_plan,
        DROP COLUMN legacy_subscription_plan_id
        """)
