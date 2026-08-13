"""add protected billing pricing administration commands

Revision ID: 0095
Revises: 0094
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0095"
down_revision: str | None = "0094"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ASSERT_RECENT_CAPABILITY_SQL = r"""
CREATE FUNCTION public.assert_and_lock_platform_recent_capability(
  p_actor_user_id UUID,
  p_actor_session_id UUID,
  p_capability TEXT
)
RETURNS TIMESTAMPTZ AS $$
DECLARE
  v_mfa_claim TEXT;
  v_mfa_verified_at TIMESTAMPTZ;
  v_grant_id UUID;
BEGIN
  v_mfa_claim := NULLIF(
    pg_catalog.current_setting('app.mfa_verified_at', true),
    ''
  );
  IF v_mfa_claim IS NULL OR v_mfa_claim !~ '^[0-9]{1,12}$' THEN
    RAISE EXCEPTION 'Recent platform MFA is required' USING ERRCODE = '42501';
  END IF;
  v_mfa_verified_at := pg_catalog.to_timestamp(v_mfa_claim::DOUBLE PRECISION);

  IF public.current_app_user_id() IS DISTINCT FROM p_actor_user_id
    OR public.current_tenant_id() IS NOT NULL
    OR NOT public.is_support_session()
    OR NULLIF(
      pg_catalog.current_setting('app.auth_session_id', true),
      ''
    )::UUID IS DISTINCT FROM p_actor_session_id
    OR v_mfa_verified_at < pg_catalog.statement_timestamp() - INTERVAL '15 minutes'
    OR v_mfa_verified_at > pg_catalog.statement_timestamp() + INTERVAL '1 minute'
  THEN
    RAISE EXCEPTION 'Platform authorization context is invalid'
      USING ERRCODE = '42501';
  END IF;

  PERFORM actor.id
  FROM public.app_user AS actor
  WHERE actor.id = p_actor_user_id
    AND actor.status = 'active'
    AND (actor.is_developer OR actor.is_administrator)
  FOR SHARE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Platform actor is inactive' USING ERRCODE = '42501';
  END IF;

  PERFORM auth_session.id
  FROM public.session AS auth_session
  WHERE auth_session.id = p_actor_session_id
    AND auth_session.user_id = p_actor_user_id
    AND auth_session.revoked_at IS NULL
    AND auth_session.expires_at > pg_catalog.statement_timestamp()
    AND auth_session.mfa_verified_at IS NOT NULL
  FOR SHARE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Platform session is inactive' USING ERRCODE = '42501';
  END IF;

  SELECT actor_grant.id
  INTO v_grant_id
  FROM public.platform_access_grant AS actor_grant
  JOIN public.platform_access_grant_permission AS assignment
    ON assignment.grant_id = actor_grant.id
  JOIN public.permission AS permission
    ON permission.code = assignment.permission_code
  WHERE actor_grant.user_id = p_actor_user_id
    AND actor_grant.status = 'active'
    AND assignment.permission_code = p_capability
    AND permission.is_active
    AND permission.scope_type = 'PLATFORM'
    AND permission.target_role_type = 'platform'
  ORDER BY actor_grant.id
  LIMIT 1
  FOR SHARE OF actor_grant, assignment, permission;
  IF v_grant_id IS NULL THEN
    RAISE EXCEPTION 'Platform capability is unavailable' USING ERRCODE = '42501';
  END IF;

  RETURN v_mfa_verified_at;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


LIST_PLANS_SQL = r"""
CREATE FUNCTION public.list_platform_billing_plans(
  p_actor_user_id UUID,
  p_actor_session_id UUID,
  p_limit INTEGER,
  p_offset INTEGER
)
RETURNS TABLE(
  items JSONB,
  total_count BIGINT
) AS $$
BEGIN
  PERFORM public.assert_and_lock_platform_recent_capability(
    p_actor_user_id,
    p_actor_session_id,
    'platform.billing.view'
  );
  IF p_limit IS NULL OR p_limit < 1 OR p_limit > 100
    OR p_offset IS NULL OR p_offset < 0 OR p_offset > 100000
  THEN
    RAISE EXCEPTION 'Invalid billing plan pagination' USING ERRCODE = '22023';
  END IF;

  RETURN QUERY
  WITH paged_plans AS (
    SELECT plan.*
    FROM public.billing_plan AS plan
    ORDER BY plan.created_at DESC, plan.id
    LIMIT p_limit OFFSET p_offset
  ), serialized_plans AS (
    SELECT
      plan.created_at,
      plan.id,
      pg_catalog.jsonb_build_object(
        'plan_id', plan.id,
        'code', plan.code,
        'name', plan.name,
        'description', plan.description,
        'currency', plan.currency,
        'is_active', plan.is_active,
        'created_by', plan.created_by,
        'created_at', plan.created_at,
        'updated_at', plan.updated_at,
        'versions', COALESCE(version_rows.versions, '[]'::JSONB)
      ) AS item
    FROM paged_plans AS plan
    LEFT JOIN LATERAL (
      SELECT pg_catalog.jsonb_agg(
        pg_catalog.jsonb_build_object(
          'price_version_id', price.id,
          'plan_id', price.plan_id,
          'version_number', price.version_number,
          'status', price.status,
          'monthly_price_per_branch', price.monthly_price_per_branch::TEXT,
          'annual_discount_pct', price.annual_discount_pct::TEXT,
          'currency', price.currency,
          'audience', price.audience,
          'effective_from', price.effective_from,
          'notice_days', price.notice_days,
          'change_reason', price.reason,
          'created_by', price.created_by,
          'approved_by', price.approved_by,
          'approved_at', price.approved_at,
          'activated_at', price.activated_at,
          'archived_at', price.archived_at,
          'row_version', price.row_version,
          'created_at', price.created_at
        ) ORDER BY price.version_number DESC
      ) AS versions
      FROM public.billing_price_version AS price
      WHERE price.plan_id = plan.id
    ) AS version_rows ON true
  )
  SELECT
    COALESCE(
      pg_catalog.jsonb_agg(
        serialized_plans.item
        ORDER BY serialized_plans.created_at DESC, serialized_plans.id
      ),
      '[]'::JSONB
    ),
    (SELECT pg_catalog.count(*) FROM public.billing_plan)
  FROM serialized_plans;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


CREATE_PLAN_SQL = r"""
CREATE FUNCTION public.create_billing_plan_draft(
  p_actor_user_id UUID,
  p_actor_session_id UUID,
  p_operation_id UUID,
  p_request_hash TEXT,
  p_code TEXT,
  p_name TEXT,
  p_description TEXT
)
RETURNS TABLE(result JSONB, applied BOOLEAN) AS $$
DECLARE
  v_mfa_at TIMESTAMPTZ;
  v_payload JSONB;
  v_event public.billing_pricing_admin_event%ROWTYPE;
  v_plan public.billing_plan%ROWTYPE;
  v_result JSONB;
BEGIN
  v_mfa_at := public.assert_and_lock_platform_recent_capability(
    p_actor_user_id, p_actor_session_id, 'platform.billing.plan.manage'
  );
  IF p_operation_id IS NULL OR p_request_hash !~ '^[0-9a-f]{64}$'
    OR p_code !~ '^[a-z][a-z0-9_]{2,63}$'
    OR pg_catalog.char_length(pg_catalog.btrim(p_name)) NOT BETWEEN 2 AND 160
    OR (p_description IS NOT NULL AND pg_catalog.char_length(p_description) > 2000)
  THEN
    RAISE EXCEPTION 'Invalid billing plan draft' USING ERRCODE = '22023';
  END IF;
  v_payload := pg_catalog.jsonb_build_object(
    'code', p_code,
    'name', pg_catalog.btrim(p_name),
    'description', NULLIF(pg_catalog.btrim(p_description), '')
  );

  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(p_operation_id::TEXT, 9501)
  );
  SELECT * INTO v_event
  FROM public.billing_pricing_admin_event
  WHERE operation_id = p_operation_id;
  IF FOUND THEN
    IF v_event.event_type <> 'plan_created'
      OR v_event.actor_user_id <> p_actor_user_id
      OR v_event.request_hash <> p_request_hash
      OR v_event.request_payload <> v_payload
    THEN
      RAISE EXCEPTION 'Billing operation id was reused'
        USING ERRCODE = '23505';
    END IF;
    RETURN QUERY SELECT v_event.result_snapshot, false;
    RETURN;
  END IF;

  INSERT INTO public.billing_plan (
    code, name, description, currency, is_active, created_by, updated_by
  ) VALUES (
    p_code,
    pg_catalog.btrim(p_name),
    NULLIF(pg_catalog.btrim(p_description), ''),
    'TJS',
    false,
    p_actor_user_id,
    p_actor_user_id
  ) RETURNING * INTO v_plan;

  v_result := pg_catalog.jsonb_build_object(
    'plan_id', v_plan.id,
    'code', v_plan.code,
    'name', v_plan.name,
    'description', v_plan.description,
    'currency', v_plan.currency,
    'is_active', v_plan.is_active,
    'created_by', v_plan.created_by,
    'created_at', v_plan.created_at,
    'updated_at', v_plan.updated_at,
    'versions', '[]'::JSONB
  );
  INSERT INTO public.billing_pricing_admin_event (
    operation_id, request_hash, request_payload, event_type, plan_id,
    actor_user_id, actor_session_id, mfa_verified_at,
    result_status, result_row_version, result_snapshot
  ) VALUES (
    p_operation_id, p_request_hash, v_payload, 'plan_created', v_plan.id,
    p_actor_user_id, p_actor_session_id, v_mfa_at,
    'draft', 1, v_result
  );
  RETURN QUERY SELECT v_result, true;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


CREATE_PRICE_SQL = r"""
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
DECLARE
  v_mfa_at TIMESTAMPTZ;
  v_payload JSONB;
  v_event public.billing_pricing_admin_event%ROWTYPE;
  v_price public.billing_price_version%ROWTYPE;
  v_version INTEGER;
  v_result JSONB;
BEGIN
  v_mfa_at := public.assert_and_lock_platform_recent_capability(
    p_actor_user_id, p_actor_session_id, 'platform.billing.plan.manage'
  );
  IF p_operation_id IS NULL OR p_request_hash !~ '^[0-9a-f]{64}$'
    OR p_plan_id IS NULL
    OR p_monthly_price_per_branch IS NULL
    OR p_monthly_price_per_branch::TEXT IN ('NaN', 'Infinity', '-Infinity')
    OR p_monthly_price_per_branch < 0
    OR p_monthly_price_per_branch <> pg_catalog.round(p_monthly_price_per_branch, 2)
    OR p_annual_discount_pct IS NULL
    OR p_annual_discount_pct::TEXT IN ('NaN', 'Infinity', '-Infinity')
    OR p_annual_discount_pct < 0 OR p_annual_discount_pct >= 100
    OR p_annual_discount_pct <> pg_catalog.round(p_annual_discount_pct, 2)
    OR p_audience NOT IN ('default', 'new_customers')
    OR p_notice_days NOT BETWEEN 0 AND 365
    OR (p_audience = 'default' AND p_notice_days < 30)
    OR pg_catalog.char_length(pg_catalog.btrim(p_change_reason)) NOT BETWEEN 10 AND 1000
    OR pg_catalog.jsonb_typeof(p_terms_snapshot) IS DISTINCT FROM 'object'
    OR pg_catalog.octet_length(p_terms_snapshot::TEXT) NOT BETWEEN 2 AND 65536
  THEN
    RAISE EXCEPTION 'Invalid billing price draft' USING ERRCODE = '22023';
  END IF;
  v_payload := pg_catalog.jsonb_build_object(
    'plan_id', p_plan_id,
    'monthly_price_per_branch', p_monthly_price_per_branch::TEXT,
    'annual_discount_pct', p_annual_discount_pct::TEXT,
    'audience', p_audience,
    'notice_days', p_notice_days,
    'change_reason', pg_catalog.btrim(p_change_reason),
    'terms_snapshot', p_terms_snapshot
  );

  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(p_operation_id::TEXT, 9501)
  );
  SELECT * INTO v_event
  FROM public.billing_pricing_admin_event
  WHERE operation_id = p_operation_id;
  IF FOUND THEN
    IF v_event.event_type <> 'price_draft_created'
      OR v_event.actor_user_id <> p_actor_user_id
      OR v_event.request_hash <> p_request_hash
      OR v_event.request_payload <> v_payload
    THEN
      RAISE EXCEPTION 'Billing operation id was reused' USING ERRCODE = '23505';
    END IF;
    RETURN QUERY SELECT v_event.result_snapshot, false;
    RETURN;
  END IF;

  PERFORM plan.id
  FROM public.billing_plan AS plan
  WHERE plan.id = p_plan_id
  FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Billing plan was not found' USING ERRCODE = 'P0002';
  END IF;
  SELECT COALESCE(pg_catalog.max(price.version_number), 0) + 1
  INTO v_version
  FROM public.billing_price_version AS price
  WHERE price.plan_id = p_plan_id;

  INSERT INTO public.billing_price_version (
    plan_id, version_number, status, monthly_price_per_branch,
    annual_discount_pct, currency, audience, notice_days, reason,
    terms_snapshot, created_by
  ) VALUES (
    p_plan_id, v_version, 'draft', p_monthly_price_per_branch,
    p_annual_discount_pct, 'TJS', p_audience, p_notice_days,
    pg_catalog.btrim(p_change_reason), p_terms_snapshot, p_actor_user_id
  ) RETURNING * INTO v_price;

  v_result := public.billing_price_result_json(v_price);
  INSERT INTO public.billing_pricing_admin_event (
    operation_id, request_hash, request_payload, event_type, plan_id,
    price_version_id, actor_user_id, actor_session_id, mfa_verified_at,
    result_status, result_row_version, result_snapshot
  ) VALUES (
    p_operation_id, p_request_hash, v_payload, 'price_draft_created', p_plan_id,
    v_price.id, p_actor_user_id, p_actor_session_id, v_mfa_at,
    v_price.status, v_price.row_version, v_result
  );
  RETURN QUERY SELECT v_result, true;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


SCHEDULE_PRICE_SQL = r"""
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
  v_mfa_at TIMESTAMPTZ;
  v_payload JSONB;
  v_event public.billing_pricing_admin_event%ROWTYPE;
  v_price public.billing_price_version%ROWTYPE;
  v_terms_hash TEXT;
  v_result JSONB;
BEGIN
  v_mfa_at := public.assert_and_lock_platform_recent_capability(
    p_actor_user_id, p_actor_session_id, 'platform.billing.plan.manage'
  );
  IF p_operation_id IS NULL OR p_request_hash !~ '^[0-9a-f]{64}$'
    OR p_price_version_id IS NULL OR p_expected_row_version < 1
    OR p_effective_from IS NULL
  THEN
    RAISE EXCEPTION 'Invalid billing price schedule request' USING ERRCODE = '22023';
  END IF;
  v_payload := pg_catalog.jsonb_build_object(
    'price_version_id', p_price_version_id,
    'expected_row_version', p_expected_row_version,
    'effective_from', p_effective_from
  );
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(p_operation_id::TEXT, 9501)
  );
  SELECT * INTO v_event FROM public.billing_pricing_admin_event
  WHERE operation_id = p_operation_id;
  IF FOUND THEN
    IF v_event.event_type <> 'price_scheduled'
      OR v_event.actor_user_id <> p_actor_user_id
      OR v_event.request_hash <> p_request_hash
      OR v_event.request_payload <> v_payload
    THEN
      RAISE EXCEPTION 'Billing operation id was reused' USING ERRCODE = '23505';
    END IF;
    RETURN QUERY SELECT v_event.result_snapshot, false;
    RETURN;
  END IF;

  SELECT * INTO v_price FROM public.billing_price_version
  WHERE id = p_price_version_id FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Billing price version was not found' USING ERRCODE = 'P0002';
  END IF;
  IF v_price.status <> 'draft' OR v_price.row_version <> p_expected_row_version THEN
    RAISE EXCEPTION 'Billing price version changed concurrently' USING ERRCODE = '40001';
  END IF;
  IF v_price.created_by = p_actor_user_id THEN
    RAISE EXCEPTION 'A billing price requires an independent approver'
      USING ERRCODE = '22023';
  END IF;

  v_terms_hash := pg_catalog.encode(
    pg_catalog.sha256(
      pg_catalog.convert_to(
        pg_catalog.jsonb_build_object(
          'plan_id', v_price.plan_id,
          'version_number', v_price.version_number,
          'monthly_price_per_branch', v_price.monthly_price_per_branch::TEXT,
          'annual_discount_pct', v_price.annual_discount_pct::TEXT,
          'currency', v_price.currency,
          'audience', v_price.audience,
          'effective_from', p_effective_from,
          'notice_days', v_price.notice_days,
          'reason', v_price.reason,
          'terms_snapshot', v_price.terms_snapshot
        )::TEXT,
        'UTF8'
      )
    ),
    'hex'
  );
  UPDATE public.billing_price_version
  SET status = 'scheduled', effective_from = p_effective_from,
      approved_by = p_actor_user_id,
      approved_at = pg_catalog.statement_timestamp()
  WHERE id = p_price_version_id
  RETURNING * INTO v_price;

  v_result := public.billing_price_result_json(v_price);
  INSERT INTO public.billing_pricing_admin_event (
    operation_id, request_hash, request_payload, event_type, plan_id,
    price_version_id, actor_user_id, actor_session_id, mfa_verified_at,
    approval_terms_hash, result_status, result_row_version, result_snapshot
  ) VALUES (
    p_operation_id, p_request_hash, v_payload, 'price_scheduled', v_price.plan_id,
    v_price.id, p_actor_user_id, p_actor_session_id, v_mfa_at,
    v_terms_hash, v_price.status, v_price.row_version, v_result
  );
  RETURN QUERY SELECT v_result, true;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


ACTIVATE_PRICE_SQL = r"""
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
  v_mfa_at TIMESTAMPTZ;
  v_payload JSONB;
  v_event public.billing_pricing_admin_event%ROWTYPE;
  v_schedule_event public.billing_pricing_admin_event%ROWTYPE;
  v_price public.billing_price_version%ROWTYPE;
  v_previous public.billing_price_version%ROWTYPE;
  v_terms_hash TEXT;
  v_result JSONB;
BEGIN
  v_mfa_at := public.assert_and_lock_platform_recent_capability(
    p_actor_user_id, p_actor_session_id, 'platform.billing.plan.manage'
  );
  IF p_operation_id IS NULL OR p_request_hash !~ '^[0-9a-f]{64}$'
    OR p_price_version_id IS NULL OR p_expected_row_version < 1
  THEN
    RAISE EXCEPTION 'Invalid billing price activation request' USING ERRCODE = '22023';
  END IF;
  v_payload := pg_catalog.jsonb_build_object(
    'price_version_id', p_price_version_id,
    'expected_row_version', p_expected_row_version
  );
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(p_operation_id::TEXT, 9501)
  );
  SELECT * INTO v_event FROM public.billing_pricing_admin_event
  WHERE operation_id = p_operation_id;
  IF FOUND THEN
    IF v_event.event_type <> 'price_activated'
      OR v_event.actor_user_id <> p_actor_user_id
      OR v_event.request_hash <> p_request_hash
      OR v_event.request_payload <> v_payload
    THEN
      RAISE EXCEPTION 'Billing operation id was reused' USING ERRCODE = '23505';
    END IF;
    RETURN QUERY SELECT v_event.result_snapshot, false;
    RETURN;
  END IF;

  SELECT * INTO v_price FROM public.billing_price_version
  WHERE id = p_price_version_id FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Billing price version was not found' USING ERRCODE = 'P0002';
  END IF;
  IF v_price.status <> 'scheduled' OR v_price.row_version <> p_expected_row_version THEN
    RAISE EXCEPTION 'Billing price version changed concurrently' USING ERRCODE = '40001';
  END IF;
  IF pg_catalog.statement_timestamp() < v_price.effective_from THEN
    RAISE EXCEPTION 'Billing price cannot be activated before its effective date'
      USING ERRCODE = '22023';
  END IF;

  PERFORM plan.id FROM public.billing_plan AS plan
  WHERE plan.id = v_price.plan_id FOR UPDATE;
  PERFORM price.id FROM public.billing_price_version AS price
  WHERE price.plan_id = v_price.plan_id AND price.audience = v_price.audience
  ORDER BY price.id FOR UPDATE;

  SELECT * INTO v_schedule_event
  FROM public.billing_pricing_admin_event
  WHERE price_version_id = v_price.id AND event_type = 'price_scheduled'
  ORDER BY created_at DESC LIMIT 1;
  v_terms_hash := pg_catalog.encode(
    pg_catalog.sha256(
      pg_catalog.convert_to(
        pg_catalog.jsonb_build_object(
          'plan_id', v_price.plan_id,
          'version_number', v_price.version_number,
          'monthly_price_per_branch', v_price.monthly_price_per_branch::TEXT,
          'annual_discount_pct', v_price.annual_discount_pct::TEXT,
          'currency', v_price.currency,
          'audience', v_price.audience,
          'effective_from', v_price.effective_from,
          'notice_days', v_price.notice_days,
          'reason', v_price.reason,
          'terms_snapshot', v_price.terms_snapshot
        )::TEXT,
        'UTF8'
      )
    ),
    'hex'
  );
  IF v_schedule_event.id IS NULL
    OR v_schedule_event.approval_terms_hash IS DISTINCT FROM v_terms_hash
  THEN
    RAISE EXCEPTION 'Billing price approval terms do not match'
      USING ERRCODE = '55000';
  END IF;

  SELECT * INTO v_previous FROM public.billing_price_version
  WHERE plan_id = v_price.plan_id AND audience = v_price.audience
    AND status = 'active' AND id <> v_price.id
  LIMIT 1;
  IF v_previous.id IS NOT NULL AND v_previous.effective_from > v_price.effective_from THEN
    RAISE EXCEPTION 'A newer billing price is already active' USING ERRCODE = '40001';
  END IF;
  IF v_previous.id IS NOT NULL THEN
    UPDATE public.billing_price_version
    SET status = 'archived', archived_at = pg_catalog.statement_timestamp()
    WHERE id = v_previous.id;
  END IF;

  UPDATE public.billing_price_version
  SET status = 'active', activated_at = pg_catalog.statement_timestamp()
  WHERE id = v_price.id
  RETURNING * INTO v_price;
  UPDATE public.billing_plan
  SET is_active = true, updated_by = p_actor_user_id,
      updated_at = pg_catalog.statement_timestamp()
  WHERE id = v_price.plan_id;

  v_result := public.billing_price_result_json(v_price);
  INSERT INTO public.billing_pricing_admin_event (
    operation_id, request_hash, request_payload, event_type, plan_id,
    price_version_id, actor_user_id, actor_session_id, mfa_verified_at,
    approval_terms_hash, previous_price_version_id,
    result_status, result_row_version, result_snapshot
  ) VALUES (
    p_operation_id, p_request_hash, v_payload, 'price_activated', v_price.plan_id,
    v_price.id, p_actor_user_id, p_actor_session_id, v_mfa_at,
    v_terms_hash, v_previous.id, v_price.status, v_price.row_version, v_result
  );
  RETURN QUERY SELECT v_result, true;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


CANCEL_PRICE_SQL = r"""
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
  v_mfa_at TIMESTAMPTZ;
  v_payload JSONB;
  v_event public.billing_pricing_admin_event%ROWTYPE;
  v_price public.billing_price_version%ROWTYPE;
  v_result JSONB;
BEGIN
  v_mfa_at := public.assert_and_lock_platform_recent_capability(
    p_actor_user_id, p_actor_session_id, 'platform.billing.plan.manage'
  );
  IF p_operation_id IS NULL OR p_request_hash !~ '^[0-9a-f]{64}$'
    OR p_price_version_id IS NULL OR p_expected_row_version < 1
    OR p_reason_code NOT IN (
      'pricing_error', 'commercial_change', 'legal_requirement',
      'security_incident', 'other'
    )
    OR pg_catalog.char_length(pg_catalog.btrim(p_reason)) NOT BETWEEN 10 AND 500
  THEN
    RAISE EXCEPTION 'Invalid billing price cancellation request'
      USING ERRCODE = '22023';
  END IF;
  v_payload := pg_catalog.jsonb_build_object(
    'price_version_id', p_price_version_id,
    'expected_row_version', p_expected_row_version,
    'reason_code', p_reason_code,
    'reason', pg_catalog.btrim(p_reason)
  );
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(p_operation_id::TEXT, 9501)
  );
  SELECT * INTO v_event FROM public.billing_pricing_admin_event
  WHERE operation_id = p_operation_id;
  IF FOUND THEN
    IF v_event.event_type <> 'price_cancelled'
      OR v_event.actor_user_id <> p_actor_user_id
      OR v_event.request_hash <> p_request_hash
      OR v_event.request_payload <> v_payload
    THEN
      RAISE EXCEPTION 'Billing operation id was reused' USING ERRCODE = '23505';
    END IF;
    RETURN QUERY SELECT v_event.result_snapshot, false;
    RETURN;
  END IF;

  SELECT * INTO v_price FROM public.billing_price_version
  WHERE id = p_price_version_id FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Billing price version was not found' USING ERRCODE = 'P0002';
  END IF;
  IF v_price.status <> 'scheduled' OR v_price.row_version <> p_expected_row_version THEN
    RAISE EXCEPTION 'Billing price version changed concurrently' USING ERRCODE = '40001';
  END IF;
  UPDATE public.billing_price_version SET status = 'cancelled'
  WHERE id = v_price.id RETURNING * INTO v_price;

  v_result := public.billing_price_result_json(v_price);
  INSERT INTO public.billing_pricing_admin_event (
    operation_id, request_hash, request_payload, event_type, plan_id,
    price_version_id, actor_user_id, actor_session_id, mfa_verified_at,
    result_status, result_row_version, reason_code, reason, result_snapshot
  ) VALUES (
    p_operation_id, p_request_hash, v_payload, 'price_cancelled', v_price.plan_id,
    v_price.id, p_actor_user_id, p_actor_session_id, v_mfa_at,
    v_price.status, v_price.row_version, p_reason_code,
    pg_catalog.btrim(p_reason), v_result
  );
  RETURN QUERY SELECT v_result, true;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


PRICE_RESULT_SQL = r"""
CREATE FUNCTION public.billing_price_result_json(
  p_price public.billing_price_version
)
RETURNS JSONB AS $$
  SELECT pg_catalog.jsonb_build_object(
    'price_version_id', p_price.id,
    'plan_id', p_price.plan_id,
    'version_number', p_price.version_number,
    'status', p_price.status,
    'monthly_price_per_branch', p_price.monthly_price_per_branch::TEXT,
    'annual_discount_pct', p_price.annual_discount_pct::TEXT,
    'currency', p_price.currency,
    'audience', p_price.audience,
    'effective_from', p_price.effective_from,
    'notice_days', p_price.notice_days,
    'change_reason', p_price.reason,
    'created_by', p_price.created_by,
    'approved_by', p_price.approved_by,
    'approved_at', p_price.approved_at,
    'activated_at', p_price.activated_at,
    'archived_at', p_price.archived_at,
    'row_version', p_price.row_version,
    'created_at', p_price.created_at
  )
$$ LANGUAGE SQL
IMMUTABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


IMMUTABLE_EVENT_SQL = r"""
CREATE FUNCTION public.trg_reject_billing_pricing_event_mutation()
RETURNS TRIGGER AS $$
BEGIN
  RAISE EXCEPTION 'Billing pricing administration events are immutable'
    USING ERRCODE = '42501';
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


AUDIT_EVENT_SQL = r"""
CREATE FUNCTION public.trg_audit_billing_pricing_admin_event()
RETURNS TRIGGER AS $$
BEGIN
  INSERT INTO public.audit_log (
    tenant_id, user_id, action, table_name, record_id, metadata, created_at
  ) VALUES (
    NULL,
    NEW.actor_user_id,
    'INSERT',
    'billing_pricing_admin_event',
    COALESCE(NEW.price_version_id, NEW.plan_id),
    pg_catalog.jsonb_build_object(
      'event_type', NEW.event_type,
      'operation_id', NEW.operation_id,
      'request_hash', NEW.request_hash,
      'result_status', NEW.result_status,
      'result_row_version', NEW.result_row_version,
      'reason_code', NEW.reason_code
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


def _secure_function(signature: str, *, grant_support: bool = False) -> None:
    op.execute(f"ALTER FUNCTION {signature} OWNER TO aurum_schema_owner")
    op.execute(
        f"REVOKE ALL PRIVILEGES ON FUNCTION {signature} "
        "FROM PUBLIC, aurum_app, aurum_support, aurum_mailer, "
        "aurum_edge_cash_executor, aurum_edge_cash_owner"
    )
    if grant_support:
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO aurum_support")


def _grant_missing_reference_privileges() -> None:
    op.execute("""
        CREATE TEMPORARY TABLE aurum_0095_missing_reference_privilege (
          table_name TEXT PRIMARY KEY
        ) ON COMMIT DROP
        """)
    op.execute("""
        DO $$
        DECLARE
          target_table TEXT;
        BEGIN
          FOREACH target_table IN ARRAY ARRAY['app_user']
          LOOP
            IF NOT pg_catalog.has_table_privilege(
              'aurum_schema_owner',
              pg_catalog.format('public.%I', target_table),
              'REFERENCES'
            ) THEN
              INSERT INTO pg_temp.aurum_0095_missing_reference_privilege (
                table_name
              ) VALUES (target_table);
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
            FROM pg_temp.aurum_0095_missing_reference_privilege
          LOOP
            EXECUTE pg_catalog.format(
              'REVOKE REFERENCES ON TABLE public.%I FROM aurum_schema_owner',
              target_table
            );
          END LOOP;
        END
        $$
        """)
    op.execute("DROP TABLE pg_temp.aurum_0095_missing_reference_privilege")


def upgrade() -> None:
    _grant_missing_reference_privileges()
    op.execute("""
        CREATE TABLE public.billing_pricing_admin_event (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          operation_id UUID NOT NULL UNIQUE,
          request_hash TEXT NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
          request_payload JSONB NOT NULL CHECK (
            jsonb_typeof(request_payload) = 'object'
            AND octet_length(request_payload::TEXT) BETWEEN 2 AND 131072
          ),
          event_type TEXT NOT NULL CHECK (event_type IN (
            'plan_created', 'price_draft_created', 'price_scheduled',
            'price_activated', 'price_cancelled'
          )),
          plan_id UUID NOT NULL
            REFERENCES public.billing_plan(id) ON DELETE RESTRICT,
          price_version_id UUID
            REFERENCES public.billing_price_version(id) ON DELETE RESTRICT,
          actor_user_id UUID NOT NULL
            REFERENCES public.app_user(id) ON DELETE RESTRICT,
          actor_session_id UUID NOT NULL,
          mfa_verified_at TIMESTAMPTZ NOT NULL,
          approval_terms_hash TEXT CHECK (
            approval_terms_hash IS NULL OR approval_terms_hash ~ '^[0-9a-f]{64}$'
          ),
          previous_price_version_id UUID
            REFERENCES public.billing_price_version(id) ON DELETE RESTRICT,
          result_status TEXT NOT NULL CHECK (
            result_status IN ('draft', 'scheduled', 'active', 'cancelled')
          ),
          result_row_version INTEGER NOT NULL CHECK (result_row_version > 0),
          reason_code TEXT,
          reason TEXT CHECK (
            reason IS NULL OR char_length(btrim(reason)) BETWEEN 10 AND 500
          ),
          result_snapshot JSONB NOT NULL CHECK (
            jsonb_typeof(result_snapshot) = 'object'
            AND octet_length(result_snapshot::TEXT) BETWEEN 2 AND 131072
          ),
          created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          CHECK (
            (event_type = 'plan_created' AND price_version_id IS NULL)
            OR (event_type <> 'plan_created' AND price_version_id IS NOT NULL)
          ),
          CHECK (
            (event_type IN ('price_scheduled', 'price_activated')
              AND approval_terms_hash IS NOT NULL)
            OR (event_type NOT IN ('price_scheduled', 'price_activated')
              AND approval_terms_hash IS NULL)
          ),
          CHECK (
            (event_type = 'price_cancelled'
              AND reason_code IS NOT NULL AND reason IS NOT NULL)
            OR (event_type <> 'price_cancelled'
              AND reason_code IS NULL AND reason IS NULL)
          )
        )
        """)
    _restore_reference_privileges()
    op.execute("""
        CREATE INDEX ix_billing_pricing_event_price_created
        ON public.billing_pricing_admin_event(price_version_id, created_at DESC)
        """)
    op.execute("""
        CREATE UNIQUE INDEX uq_billing_price_one_scheduled_audience
        ON public.billing_price_version(plan_id, audience)
        WHERE status = 'scheduled'
        """)

    op.execute(IMMUTABLE_EVENT_SQL)
    _secure_function("public.trg_reject_billing_pricing_event_mutation()")
    op.execute("""
        CREATE TRIGGER trg_immutable_billing_pricing_admin_event
        BEFORE UPDATE OR DELETE ON public.billing_pricing_admin_event
        FOR EACH ROW EXECUTE FUNCTION public.trg_reject_billing_pricing_event_mutation()
        """)
    op.execute(AUDIT_EVENT_SQL)
    _secure_function("public.trg_audit_billing_pricing_admin_event()")
    op.execute("""
        CREATE TRIGGER trg_audit_billing_pricing_admin_event
        AFTER INSERT ON public.billing_pricing_admin_event
        FOR EACH ROW EXECUTE FUNCTION public.trg_audit_billing_pricing_admin_event()
        """)

    op.execute(ASSERT_RECENT_CAPABILITY_SQL)
    _secure_function(
        "public.assert_and_lock_platform_recent_capability(UUID, UUID, TEXT)"
    )
    op.execute(PRICE_RESULT_SQL)
    _secure_function(
        "public.billing_price_result_json(public.billing_price_version)"
    )
    functions = (
        (
            LIST_PLANS_SQL,
            "public.list_platform_billing_plans(UUID, UUID, INTEGER, INTEGER)",
        ),
        (
            CREATE_PLAN_SQL,
            "public.create_billing_plan_draft(UUID, UUID, UUID, TEXT, TEXT, TEXT, TEXT)",
        ),
        (
            CREATE_PRICE_SQL,
            "public.create_billing_price_draft(UUID, UUID, UUID, TEXT, UUID, NUMERIC, "
            "NUMERIC, TEXT, SMALLINT, TEXT, JSONB)",
        ),
        (
            SCHEDULE_PRICE_SQL,
            "public.approve_and_schedule_billing_price(UUID, UUID, UUID, TEXT, UUID, "
            "INTEGER, TIMESTAMPTZ)",
        ),
        (
            ACTIVATE_PRICE_SQL,
            "public.activate_billing_price_version(UUID, UUID, UUID, TEXT, UUID, INTEGER)",
        ),
        (
            CANCEL_PRICE_SQL,
            "public.cancel_scheduled_billing_price(UUID, UUID, UUID, TEXT, UUID, "
            "INTEGER, TEXT, TEXT)",
        ),
    )
    for statement, signature in functions:
        op.execute(statement)
        _secure_function(signature, grant_support=True)

    op.execute("""
        REVOKE ALL PRIVILEGES ON TABLE public.billing_pricing_admin_event
        FROM PUBLIC, aurum_app, aurum_support, aurum_mailer,
          aurum_edge_cash_executor, aurum_edge_cash_owner
        """)


def downgrade() -> None:
    op.execute("""
        DO $guard$
        BEGIN
          IF EXISTS (SELECT 1 FROM public.billing_pricing_admin_event) THEN
            RAISE EXCEPTION
              'Refusing to remove a non-empty billing pricing operation ledger';
          END IF;
        END
        $guard$
        """)
    op.execute(
        "DROP FUNCTION public.cancel_scheduled_billing_price("
        "UUID, UUID, UUID, TEXT, UUID, INTEGER, TEXT, TEXT)"
    )
    op.execute(
        "DROP FUNCTION public.activate_billing_price_version("
        "UUID, UUID, UUID, TEXT, UUID, INTEGER)"
    )
    op.execute(
        "DROP FUNCTION public.approve_and_schedule_billing_price("
        "UUID, UUID, UUID, TEXT, UUID, INTEGER, TIMESTAMPTZ)"
    )
    op.execute(
        "DROP FUNCTION public.create_billing_price_draft("
        "UUID, UUID, UUID, TEXT, UUID, NUMERIC, NUMERIC, TEXT, SMALLINT, TEXT, JSONB)"
    )
    op.execute(
        "DROP FUNCTION public.create_billing_plan_draft("
        "UUID, UUID, UUID, TEXT, TEXT, TEXT, TEXT)"
    )
    op.execute(
        "DROP FUNCTION public.list_platform_billing_plans(UUID, UUID, INTEGER, INTEGER)"
    )
    op.execute("DROP FUNCTION public.billing_price_result_json(public.billing_price_version)")
    op.execute(
        "DROP FUNCTION public.assert_and_lock_platform_recent_capability(UUID, UUID, TEXT)"
    )
    op.execute("DROP TRIGGER trg_audit_billing_pricing_admin_event ON public.billing_pricing_admin_event")
    op.execute("DROP FUNCTION public.trg_audit_billing_pricing_admin_event()")
    op.execute("DROP TRIGGER trg_immutable_billing_pricing_admin_event ON public.billing_pricing_admin_event")
    op.execute("DROP FUNCTION public.trg_reject_billing_pricing_event_mutation()")
    op.execute("DROP TABLE public.billing_pricing_admin_event")
    op.execute("DROP INDEX public.uq_billing_price_one_scheduled_audience")
