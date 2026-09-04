"""isolate system maintenance tasks behind a dedicated database role

Revision ID: 0137
Revises: 0136
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0137"
down_revision: str | None = "0136"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


WORKER_FUNCTIONS = (
    "public.worker_purge_expired_email_codes(INTEGER)",
    "public.worker_purge_expired_sessions(INTEGER)",
    "public.worker_list_automatic_trial_candidates(INTEGER, UUID[])",
    "public.worker_start_automatic_trial(UUID)",
    "public.worker_claim_notification_deliveries(INTEGER, UUID)",
    "public.worker_complete_notification_delivery(UUID, UUID, TEXT, TEXT)",
    "public.worker_purge_old_notifications(INTEGER)",
    "public.worker_enqueue_expiring_license_notifications(INTEGER)",
)
RUNTIME_ROLES = (
    "PUBLIC",
    "aurum_app",
    "aurum_support",
    "aurum_worker",
    "aurum_mailer",
    "aurum_billing_worker",
    "aurum_edge_cash_executor",
    "aurum_edge_cash_owner",
)


PURGE_EMAIL_CODES_SQL = """
CREATE FUNCTION public.worker_purge_expired_email_codes(p_limit INTEGER)
RETURNS INTEGER AS $$
DECLARE
  v_removed INTEGER;
BEGIN
  IF SESSION_USER <> 'aurum_worker' THEN
    RAISE EXCEPTION 'System worker identity is required' USING ERRCODE = '42501';
  END IF;
  IF p_limit IS NULL OR p_limit < 1 OR p_limit > 10000 THEN
    RAISE EXCEPTION 'Invalid email-code cleanup batch' USING ERRCODE = '22023';
  END IF;
  WITH candidates AS (
    SELECT code.id
    FROM public.email_code AS code
    WHERE code.expires_at < pg_catalog.statement_timestamp() - INTERVAL '24 hours'
    ORDER BY code.expires_at, code.id
    LIMIT p_limit
    FOR UPDATE SKIP LOCKED
  )
  DELETE FROM public.email_code AS code
  USING candidates
  WHERE code.id = candidates.id;
  GET DIAGNOSTICS v_removed = ROW_COUNT;
  RETURN v_removed;
END;
$$ LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
SET lock_timeout = '5s'
SET statement_timeout = '30s'
"""


PURGE_SESSIONS_SQL = """
CREATE FUNCTION public.worker_purge_expired_sessions(p_limit INTEGER)
RETURNS INTEGER AS $$
DECLARE
  v_removed INTEGER;
BEGIN
  IF SESSION_USER <> 'aurum_worker' THEN
    RAISE EXCEPTION 'System worker identity is required' USING ERRCODE = '42501';
  END IF;
  IF p_limit IS NULL OR p_limit < 1 OR p_limit > 10000 THEN
    RAISE EXCEPTION 'Invalid session cleanup batch' USING ERRCODE = '22023';
  END IF;
  WITH candidates AS (
    SELECT auth_session.id
    FROM public.session AS auth_session
    WHERE auth_session.expires_at
        < pg_catalog.statement_timestamp() - INTERVAL '30 days'
    ORDER BY auth_session.expires_at, auth_session.id
    LIMIT p_limit
    FOR UPDATE SKIP LOCKED
  )
  DELETE FROM public.session AS auth_session
  USING candidates
  WHERE auth_session.id = candidates.id;
  GET DIAGNOSTICS v_removed = ROW_COUNT;
  RETURN v_removed;
END;
$$ LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
SET lock_timeout = '5s'
SET statement_timeout = '30s'
"""


TRIAL_CANDIDATES_SQL = """
CREATE FUNCTION public.worker_list_automatic_trial_candidates(
  p_limit INTEGER,
  p_tenant_ids UUID[] DEFAULT NULL
) RETURNS TABLE(tenant_id UUID) AS $$
BEGIN
  IF SESSION_USER <> 'aurum_worker' THEN
    RAISE EXCEPTION 'System worker identity is required' USING ERRCODE = '42501';
  END IF;
  IF p_limit IS NULL OR p_limit < 1 OR p_limit > 100 THEN
    RAISE EXCEPTION 'Invalid automatic trial batch' USING ERRCODE = '22023';
  END IF;
  RETURN QUERY
    SELECT tenant.id
    FROM public.tenant AS tenant
    JOIN public.onboarding_checklist AS checklist
      ON checklist.tenant_id = tenant.id
    WHERE tenant.status = 'setup'
      AND checklist.setup_ends_at <= pg_catalog.statement_timestamp()
      AND (p_tenant_ids IS NULL OR tenant.id = ANY(p_tenant_ids))
    ORDER BY checklist.setup_ends_at, tenant.id
    LIMIT p_limit;
END;
$$ LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
SET lock_timeout = '5s'
SET statement_timeout = '30s'
"""


PURGE_NOTIFICATIONS_SQL = """
CREATE FUNCTION public.worker_purge_old_notifications(p_limit INTEGER)
RETURNS INTEGER AS $$
DECLARE
  v_removed INTEGER;
BEGIN
  IF SESSION_USER <> 'aurum_worker' THEN
    RAISE EXCEPTION 'System worker identity is required' USING ERRCODE = '42501';
  END IF;
  IF p_limit IS NULL OR p_limit < 1 OR p_limit > 10000 THEN
    RAISE EXCEPTION 'Invalid notification cleanup batch' USING ERRCODE = '22023';
  END IF;
  WITH candidates AS (
    SELECT notification.id
    FROM public.notification AS notification
    WHERE notification.read_at IS NOT NULL
      AND notification.read_at
          < pg_catalog.statement_timestamp() - INTERVAL '30 days'
      AND NOT EXISTS (
        SELECT 1
        FROM public.notification_delivery AS delivery
        WHERE delivery.notification_id = notification.id
          AND delivery.status IN ('pending', 'processing')
      )
    ORDER BY notification.read_at, notification.id
    LIMIT p_limit
    FOR UPDATE SKIP LOCKED
  )
  DELETE FROM public.notification AS notification
  USING candidates
  WHERE notification.id = candidates.id;
  GET DIAGNOSTICS v_removed = ROW_COUNT;
  RETURN v_removed;
END;
$$ LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
SET lock_timeout = '5s'
SET statement_timeout = '30s'
"""


CLAIM_DELIVERIES_SQL = """
CREATE FUNCTION public.worker_claim_notification_deliveries(
  p_limit INTEGER,
  p_claim_token UUID
) RETURNS TABLE(
  delivery_id UUID,
  notification_id UUID,
  channel TEXT,
  attempt INTEGER
) AS $$
BEGIN
  IF SESSION_USER <> 'aurum_worker' THEN
    RAISE EXCEPTION 'System worker identity is required' USING ERRCODE = '42501';
  END IF;
  IF p_limit IS NULL OR p_limit < 1 OR p_limit > 500 OR p_claim_token IS NULL THEN
    RAISE EXCEPTION 'Invalid notification claim batch' USING ERRCODE = '22023';
  END IF;
  RETURN QUERY
    WITH candidates AS (
      SELECT delivery.id
      FROM public.notification_delivery AS delivery
      WHERE (
          delivery.status = 'pending'
          AND delivery.available_at <= pg_catalog.statement_timestamp()
        ) OR (
          delivery.status = 'processing'
          AND delivery.claimed_at
              < pg_catalog.statement_timestamp() - INTERVAL '5 minutes'
        )
      ORDER BY delivery.available_at, delivery.created_at, delivery.id
      LIMIT p_limit
      FOR UPDATE SKIP LOCKED
    )
    UPDATE public.notification_delivery AS delivery
    SET status = 'processing',
        attempts = delivery.attempts + 1,
        claimed_at = pg_catalog.statement_timestamp(),
        claim_token = p_claim_token,
        last_error_code = NULL,
        error_message = NULL
    FROM candidates
    WHERE delivery.id = candidates.id
    RETURNING delivery.id, delivery.notification_id, delivery.channel,
              delivery.attempts;
END;
$$ LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
SET lock_timeout = '5s'
SET statement_timeout = '30s'
"""


COMPLETE_DELIVERY_SQL = """
CREATE FUNCTION public.worker_complete_notification_delivery(
  p_delivery_id UUID,
  p_claim_token UUID,
  p_outcome TEXT,
  p_error_code TEXT DEFAULT NULL
) RETURNS TEXT AS $$
DECLARE
  v_attempts INTEGER;
  v_status TEXT;
BEGIN
  IF SESSION_USER <> 'aurum_worker' THEN
    RAISE EXCEPTION 'System worker identity is required' USING ERRCODE = '42501';
  END IF;
  IF p_delivery_id IS NULL OR p_claim_token IS NULL
    OR p_outcome NOT IN ('sent', 'retry', 'failed')
    OR (p_error_code IS NOT NULL AND (
      pg_catalog.length(p_error_code) > 64
      OR p_error_code !~ '^[a-z0-9_]+$'
    ))
  THEN
    RAISE EXCEPTION 'Invalid notification completion' USING ERRCODE = '22023';
  END IF;

  SELECT delivery.attempts
  INTO v_attempts
  FROM public.notification_delivery AS delivery
  WHERE delivery.id = p_delivery_id
    AND delivery.status = 'processing'
    AND delivery.claim_token = p_claim_token
  FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Notification claim is stale or missing' USING ERRCODE = '40001';
  END IF;

  v_status := CASE
    WHEN p_outcome = 'sent' THEN 'sent'
    WHEN p_outcome = 'failed' OR v_attempts >= 3 THEN 'failed'
    ELSE 'pending'
  END;
  UPDATE public.notification_delivery AS delivery
  SET status = v_status,
      sent_at = CASE
        WHEN v_status = 'sent' THEN pg_catalog.statement_timestamp()
        ELSE NULL
      END,
      available_at = CASE
        WHEN v_status = 'pending'
        THEN pg_catalog.statement_timestamp() + (v_attempts * INTERVAL '5 minutes')
        ELSE delivery.available_at
      END,
      claimed_at = NULL,
      claim_token = NULL,
      last_error_code = CASE WHEN v_status = 'sent' THEN NULL ELSE p_error_code END,
      error_message = NULL
  WHERE delivery.id = p_delivery_id;
  RETURN v_status;
END;
$$ LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
SET lock_timeout = '5s'
SET statement_timeout = '30s'
"""


LICENSE_NOTIFICATIONS_SQL = """
CREATE FUNCTION public.worker_enqueue_expiring_license_notifications(
  p_limit INTEGER
) RETURNS INTEGER AS $$
DECLARE
  v_candidate RECORD;
  v_channels JSONB;
  v_enabled BOOLEAN;
  v_notification_id UUID;
  v_notified INTEGER := 0;
BEGIN
  IF SESSION_USER <> 'aurum_worker' THEN
    RAISE EXCEPTION 'System worker identity is required' USING ERRCODE = '42501';
  END IF;
  IF p_limit IS NULL OR p_limit < 1 OR p_limit > 1000 THEN
    RAISE EXCEPTION 'Invalid license notification batch' USING ERRCODE = '22023';
  END IF;

  FOR v_candidate IN
    SELECT branch.id AS branch_id,
           branch.tenant_id,
           branch.name AS branch_name,
           branch.license_expires_at,
           app_user.id AS user_id,
           app_user.email
    FROM public.branch AS branch
    JOIN public.tenant_ownership AS ownership
      ON ownership.tenant_id = branch.tenant_id AND ownership.is_active
    JOIN public.tenant_membership AS membership
      ON membership.id = ownership.membership_id
     AND membership.tenant_id = ownership.tenant_id
     AND membership.status = 'active'
    JOIN public.app_user AS app_user
      ON app_user.id = membership.user_id AND app_user.status = 'active'
    WHERE branch.is_active
      AND branch.license_expires_at >=
          (pg_catalog.statement_timestamp() AT TIME ZONE 'Asia/Dushanbe')::DATE
      AND branch.license_expires_at <=
          (pg_catalog.statement_timestamp() AT TIME ZONE 'Asia/Dushanbe')::DATE + 30
      AND NOT EXISTS (
        SELECT 1
        FROM public.notification AS existing_notification
        WHERE existing_notification.tenant_id = branch.tenant_id
          AND existing_notification.user_id = app_user.id
          AND existing_notification.event_type = 'license_expiring'
          AND existing_notification.dedupe_key =
              branch.id::TEXT || ':' || branch.license_expires_at::TEXT
      )
    ORDER BY branch.license_expires_at, branch.id, app_user.id
    LIMIT p_limit
  LOOP
    SELECT subscription.channels, subscription.is_enabled
    INTO v_channels, v_enabled
    FROM public.notification_subscription AS subscription
    WHERE subscription.user_id = v_candidate.user_id
      AND subscription.event_type = 'license_expiring';
    IF FOUND AND NOT v_enabled THEN
      CONTINUE;
    END IF;
    v_channels := COALESCE(v_channels, '["in_app"]'::JSONB);
    v_notification_id := NULL;

    INSERT INTO public.notification (
      tenant_id, user_id, event_type, title, body, data, severity, dedupe_key
    ) VALUES (
      v_candidate.tenant_id,
      v_candidate.user_id,
      'license_expiring',
      'Лицензия скоро истекает',
      pg_catalog.format(
        'У точки %s срок действия лицензии — %s. Подайте документы заранее.',
        pg_catalog.quote_literal(v_candidate.branch_name),
        v_candidate.license_expires_at
      ),
      pg_catalog.jsonb_build_object(
        'branch_id', v_candidate.branch_id,
        'license_expires_at', v_candidate.license_expires_at
      ),
      'warning',
      v_candidate.branch_id::TEXT || ':' || v_candidate.license_expires_at::TEXT
    )
    ON CONFLICT DO NOTHING
    RETURNING id INTO v_notification_id;
    IF v_notification_id IS NULL THEN
      CONTINUE;
    END IF;
    IF v_channels @> '["email"]'::JSONB THEN
      INSERT INTO public.notification_delivery (
        notification_id, channel, recipient
      ) VALUES (v_notification_id, 'email', v_candidate.email)
      ON CONFLICT ON CONSTRAINT uq_notification_delivery_notification_channel
      DO NOTHING;
    END IF;
    v_notified := v_notified + 1;
  END LOOP;
  RETURN v_notified;
END;
$$ LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
SET lock_timeout = '5s'
SET statement_timeout = '30s'
"""


START_AUTOMATIC_TRIAL_SQL = """
CREATE FUNCTION public.worker_start_automatic_trial(p_tenant_id UUID)
RETURNS TABLE(started BOOLEAN, reason TEXT) AS $$
DECLARE
  v_tenant RECORD;
  v_plan_id UUID;
  v_price_per_branch NUMERIC(14, 2);
  v_payment_methods JSONB;
  v_prescription_warning_text TEXT;
  v_expired_sale_mode TEXT;
  v_refund_reason_mode TEXT;
  v_now TIMESTAMPTZ;
  v_trial_ends_at TIMESTAMPTZ;
  v_subscription_id UUID;
  v_branch_count INTEGER;
  v_catalog_count INTEGER;
  v_ready BOOLEAN;
BEGIN
  IF SESSION_USER <> 'aurum_worker' THEN
    RAISE EXCEPTION 'System worker identity is required' USING ERRCODE = '42501';
  END IF;
  IF p_tenant_id IS NULL THEN
    RAISE EXCEPTION 'Tenant id is required' USING ERRCODE = '22023';
  END IF;

  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(p_tenant_id::TEXT, 9603)
  );
  SELECT tenant.id, tenant.name, tenant.contact_email, tenant.status
  INTO v_tenant
  FROM public.tenant AS tenant
  JOIN public.onboarding_checklist AS checklist
    ON checklist.tenant_id = tenant.id
  WHERE tenant.id = p_tenant_id
    AND tenant.status = 'setup'
    AND checklist.setup_ends_at <= pg_catalog.statement_timestamp()
  FOR UPDATE OF tenant;
  IF NOT FOUND THEN
    started := FALSE;
    reason := 'not_candidate';
    RETURN NEXT;
    RETURN;
  END IF;
  IF EXISTS (
    SELECT 1 FROM public.trial_activation AS activation
    WHERE activation.tenant_id = p_tenant_id
  ) THEN
    started := FALSE;
    reason := 'already_started';
    RETURN NEXT;
    RETURN;
  END IF;
  PERFORM subscription.id
  FROM public.tenant_subscription AS subscription
  WHERE subscription.tenant_id = p_tenant_id
    AND subscription.status IN ('trial', 'active', 'grace_period', 'suspended')
  FOR UPDATE;
  IF FOUND THEN
    started := FALSE;
    reason := 'subscription_exists';
    RETURN NEXT;
    RETURN;
  END IF;

  PERFORM branch.id FROM public.branch AS branch
    WHERE branch.tenant_id = p_tenant_id FOR SHARE;
  PERFORM register.id FROM public.register AS register
    WHERE register.tenant_id = p_tenant_id FOR SHARE;
  PERFORM catalog.id FROM public.tenant_catalog AS catalog
    WHERE catalog.tenant_id = p_tenant_id
      AND catalog.is_active AND catalog.deleted_at IS NULL
    ORDER BY catalog.id LIMIT 100 FOR SHARE;
  PERFORM settings.tenant_id FROM public.tenant_settings AS settings
    WHERE settings.tenant_id = p_tenant_id FOR SHARE;
  PERFORM ownership.id
    FROM public.tenant_ownership AS ownership
    JOIN public.tenant_membership AS membership
      ON membership.id = ownership.membership_id
     AND membership.tenant_id = ownership.tenant_id
    JOIN public.app_user AS owner_user ON owner_user.id = membership.user_id
    WHERE ownership.tenant_id = p_tenant_id
      AND ownership.is_active
      AND membership.status = 'active'
      AND owner_user.status = 'active'
    FOR SHARE OF ownership, membership, owner_user;

  SELECT pg_catalog.count(*)::INTEGER
  INTO v_branch_count
  FROM public.branch AS branch
  WHERE branch.tenant_id = p_tenant_id AND branch.is_active;
  SELECT pg_catalog.count(*)::INTEGER
  INTO v_catalog_count
  FROM public.tenant_catalog AS catalog
  WHERE catalog.tenant_id = p_tenant_id
    AND catalog.is_active AND catalog.deleted_at IS NULL;
  SELECT settings.pos_payment_methods,
         settings.prescription_warning_text,
         settings.expired_sale_mode,
         settings.refund_reason_mode
  INTO v_payment_methods,
       v_prescription_warning_text,
       v_expired_sale_mode,
       v_refund_reason_mode
  FROM public.tenant_settings AS settings
  WHERE settings.tenant_id = p_tenant_id;
  SELECT plan.id, plan.price_per_branch
  INTO v_plan_id, v_price_per_branch
  FROM public.subscription_plan AS plan
  WHERE plan.code = 'aurum_pharma' AND plan.is_active
  FOR SHARE;

  v_ready := (
    pg_catalog.btrim(v_tenant.name) <> ''
    AND pg_catalog.btrim(v_tenant.contact_email) <> ''
    AND v_branch_count > 0
    AND v_catalog_count >= 100
    AND v_plan_id IS NOT NULL
    AND v_payment_methods IS NOT NULL
    AND pg_catalog.jsonb_typeof(v_payment_methods) = 'array'
    AND pg_catalog.jsonb_array_length(v_payment_methods) > 0
    AND NULLIF(pg_catalog.btrim(v_prescription_warning_text), '') IS NOT NULL
    AND v_expired_sale_mode IN ('strict', 'warning', 'off')
    AND v_refund_reason_mode IN (
      'required', 'required_with_text', 'optional', 'off'
    )
    AND EXISTS (
      SELECT 1 FROM public.branch AS branch
      WHERE branch.tenant_id = p_tenant_id
        AND branch.is_active
        AND NULLIF(pg_catalog.btrim(branch.address), '') IS NOT NULL
        AND NULLIF(pg_catalog.btrim(branch.license_number), '') IS NOT NULL
        AND branch.license_expires_at >=
            (pg_catalog.statement_timestamp() AT TIME ZONE 'Asia/Dushanbe')::DATE
        AND pg_catalog.jsonb_typeof(branch.receipt_header) = 'object'
        AND NULLIF(pg_catalog.btrim(branch.receipt_header ->> 'line1'), '') IS NOT NULL
        AND EXISTS (
          SELECT 1 FROM public.register AS register
          WHERE register.tenant_id = p_tenant_id
            AND register.branch_id = branch.id
            AND register.is_active
        )
    )
    AND EXISTS (
      SELECT 1
      FROM public.tenant_ownership AS ownership
      JOIN public.tenant_membership AS membership
        ON membership.id = ownership.membership_id
       AND membership.tenant_id = ownership.tenant_id
      JOIN public.app_user AS owner_user ON owner_user.id = membership.user_id
      WHERE ownership.tenant_id = p_tenant_id
        AND ownership.is_active
        AND membership.status = 'active'
        AND owner_user.status = 'active'
    )
  );
  IF NOT COALESCE(v_ready, FALSE) THEN
    started := FALSE;
    reason := CASE WHEN v_plan_id IS NULL THEN 'plan_unavailable' ELSE 'not_ready' END;
    RETURN NEXT;
    RETURN;
  END IF;

  v_now := pg_catalog.statement_timestamp();
  v_trial_ends_at := v_now + INTERVAL '14 days';
  INSERT INTO public.tenant_subscription (
    tenant_id, plan_id, status, billing_period, period_start, period_end,
    branches_count, amount, currency
  ) VALUES (
    p_tenant_id, v_plan_id, 'trial', 'monthly', v_now, v_trial_ends_at,
    v_branch_count, pg_catalog.round(v_price_per_branch * v_branch_count, 2), 'TJS'
  ) RETURNING id INTO v_subscription_id;
  UPDATE public.tenant AS tenant
  SET status = 'trial', trial_started_at = v_now, trial_ends_at = v_trial_ends_at,
      updated_at = v_now
  WHERE tenant.id = p_tenant_id AND tenant.status = 'setup';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Trial readiness changed' USING ERRCODE = '40001';
  END IF;
  UPDATE public.onboarding_checklist AS checklist
  SET catalog_items_count = v_catalog_count,
      trial_started_at = v_now,
      trial_eligible = TRUE,
      updated_at = v_now
  WHERE checklist.tenant_id = p_tenant_id;
  UPDATE public.wizard_state AS wizard
  SET current_step = 8,
      steps_completed = '[1,2,3,4,5,6,7,8]'::JSONB,
      is_completed = TRUE,
      completed_at = v_now,
      updated_at = v_now
  WHERE wizard.tenant_id = p_tenant_id AND NOT wizard.is_completed;
  PERFORM public.record_trial_activation(
    p_tenant_id, public.gen_random_uuid(), 'automatic', NULL, NULL,
    v_subscription_id, v_now, v_trial_ends_at
  );
  started := TRUE;
  reason := 'started';
  RETURN NEXT;
END;
$$ LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
SET lock_timeout = '5s'
SET statement_timeout = '30s'
"""


def _record_trial_activation_sql(automatic_role: str) -> str:
    return f"""
CREATE OR REPLACE FUNCTION public.record_trial_activation(
  p_tenant_id UUID,
  p_operation_id UUID,
  p_source TEXT,
  p_actor_user_id UUID,
  p_actor_session_id UUID,
  p_subscription_id UUID,
  p_started_at TIMESTAMPTZ,
  p_trial_ends_at TIMESTAMPTZ
) RETURNS UUID AS $$
BEGIN
  IF p_tenant_id IS NULL OR p_operation_id IS NULL OR p_subscription_id IS NULL
    OR p_started_at IS NULL OR p_trial_ends_at <= p_started_at
  THEN
    RAISE EXCEPTION 'Invalid trial activation request' USING ERRCODE = '22023';
  END IF;
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(p_tenant_id::TEXT, 9603)
  );
  IF p_source = 'manual' THEN
    IF p_actor_user_id IS NULL OR p_actor_session_id IS NULL
      OR p_tenant_id IS DISTINCT FROM public.current_tenant_id()
      OR p_actor_user_id IS DISTINCT FROM public.current_app_user_id()
      OR p_actor_session_id::TEXT IS DISTINCT FROM NULLIF(
        pg_catalog.current_setting('app.auth_session_id', true), ''
      )
      OR NOT EXISTS (
        SELECT 1 FROM public.session AS auth_session
        JOIN public.app_user AS actor ON actor.id = auth_session.user_id
        WHERE auth_session.id = p_actor_session_id
          AND auth_session.user_id = p_actor_user_id
          AND auth_session.revoked_at IS NULL
          AND auth_session.expires_at > pg_catalog.statement_timestamp()
          AND actor.status = 'active'
      )
      OR NOT EXISTS (
        SELECT 1 FROM public.tenant_ownership AS ownership
        JOIN public.tenant_membership AS membership
          ON membership.id = ownership.membership_id
         AND membership.tenant_id = ownership.tenant_id
        WHERE ownership.tenant_id = p_tenant_id
          AND ownership.is_active
          AND membership.user_id = p_actor_user_id
          AND membership.status = 'active'
      )
    THEN
      RAISE EXCEPTION 'Active owner session is required' USING ERRCODE = '42501';
    END IF;
  ELSIF p_source = 'automatic' THEN
    IF SESSION_USER <> '{automatic_role}'
      OR p_actor_user_id IS NOT NULL OR p_actor_session_id IS NOT NULL
    THEN
      RAISE EXCEPTION 'Automatic trial activation is not allowed'
        USING ERRCODE = '42501';
    END IF;
  ELSE
    RAISE EXCEPTION 'Invalid trial activation source' USING ERRCODE = '22023';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.tenant AS tenant
    JOIN public.tenant_subscription AS subscription
      ON subscription.tenant_id = tenant.id AND subscription.id = p_subscription_id
    WHERE tenant.id = p_tenant_id
      AND tenant.status = 'trial'
      AND tenant.trial_started_at = p_started_at
      AND tenant.trial_ends_at = p_trial_ends_at
      AND subscription.status = 'trial'
      AND subscription.period_start = p_started_at
      AND subscription.period_end = p_trial_ends_at
  ) THEN
    RAISE EXCEPTION 'Trial state is inconsistent' USING ERRCODE = '40001';
  END IF;
  INSERT INTO public.trial_activation (
    tenant_id, operation_id, source, actor_user_id, actor_session_id,
    subscription_id, started_at, trial_ends_at
  ) VALUES (
    p_tenant_id, p_operation_id, p_source, p_actor_user_id, p_actor_session_id,
    p_subscription_id, p_started_at, p_trial_ends_at
  );
  RETURN p_tenant_id;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


def _secure_worker_function(signature: str) -> None:
    op.execute(f"ALTER FUNCTION {signature} OWNER TO aurum_schema_owner")
    op.execute(f"REVOKE ALL PRIVILEGES ON FUNCTION {signature} " f"FROM {', '.join(RUNTIME_ROLES)}")
    op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO aurum_worker")


def _secure_trial_activation() -> None:
    signature = (
        "public.record_trial_activation("
        "UUID, UUID, TEXT, UUID, UUID, UUID, TIMESTAMPTZ, TIMESTAMPTZ)"
    )
    op.execute(f"ALTER FUNCTION {signature} OWNER TO aurum_schema_owner")
    op.execute(f"REVOKE ALL PRIVILEGES ON FUNCTION {signature} " f"FROM {', '.join(RUNTIME_ROLES)}")
    op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO aurum_app, aurum_support")


def upgrade() -> None:
    op.execute("ALTER TABLE public.notification ADD COLUMN dedupe_key TEXT")
    op.execute(
        "CREATE UNIQUE INDEX uq_notification_dedupe_key "
        "ON public.notification (tenant_id, user_id, event_type, dedupe_key) "
        "WHERE dedupe_key IS NOT NULL"
    )
    op.execute(
        "ALTER TABLE public.notification_delivery "
        "DROP CONSTRAINT notification_delivery_status_check"
    )
    op.execute(
        "ALTER TABLE public.notification_delivery "
        "ADD COLUMN available_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(), "
        "ADD COLUMN claimed_at TIMESTAMPTZ, "
        "ADD COLUMN claim_token UUID, "
        "ADD COLUMN last_error_code TEXT, "
        "ADD CONSTRAINT notification_delivery_status_check "
        "CHECK (status IN ('pending','processing','sent','failed','bounced')), "
        "ADD CONSTRAINT notification_delivery_claim_state_check "
        "CHECK ((status = 'processing' "
        "AND claimed_at IS NOT NULL AND claim_token IS NOT NULL) OR "
        "(status <> 'processing' AND claimed_at IS NULL AND claim_token IS NULL)), "
        "ADD CONSTRAINT notification_delivery_last_error_code_check "
        "CHECK (last_error_code IS NULL OR "
        "(length(last_error_code) <= 64 AND last_error_code ~ '^[a-z0-9_]+$'))"
    )
    op.execute(
        "CREATE INDEX ix_notification_delivery_worker_queue "
        "ON public.notification_delivery (status, available_at, created_at, id) "
        "WHERE status IN ('pending', 'processing')"
    )
    op.execute(
        "CREATE INDEX ix_notification_read_retention "
        "ON public.notification (read_at, id) WHERE read_at IS NOT NULL"
    )
    op.execute("CREATE INDEX ix_session_worker_expiry ON public.session (expires_at, id)")

    op.execute(_record_trial_activation_sql("aurum_worker"))
    _secure_trial_activation()
    for statement in (
        PURGE_EMAIL_CODES_SQL,
        PURGE_SESSIONS_SQL,
        TRIAL_CANDIDATES_SQL,
        START_AUTOMATIC_TRIAL_SQL,
        CLAIM_DELIVERIES_SQL,
        COMPLETE_DELIVERY_SQL,
        PURGE_NOTIFICATIONS_SQL,
        LICENSE_NOTIFICATIONS_SQL,
    ):
        op.execute(statement)
    for signature in WORKER_FUNCTIONS:
        _secure_worker_function(signature)
    op.execute("GRANT USAGE ON SCHEMA public TO aurum_worker")


def downgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM public.notification_delivery
            WHERE status = 'processing'
          ) THEN
            RAISE EXCEPTION
              'Cannot downgrade while notification deliveries are processing';
          END IF;
        END
        $$
        """)
    for signature in reversed(WORKER_FUNCTIONS):
        op.execute(f"REVOKE ALL PRIVILEGES ON FUNCTION {signature} FROM aurum_worker")
        op.execute(f"DROP FUNCTION {signature}")
    op.execute("REVOKE USAGE ON SCHEMA public FROM aurum_worker")
    op.execute(_record_trial_activation_sql("aurum_support"))
    _secure_trial_activation()

    op.execute("DROP INDEX public.ix_session_worker_expiry")
    op.execute("DROP INDEX public.ix_notification_read_retention")
    op.execute("DROP INDEX public.ix_notification_delivery_worker_queue")
    op.execute(
        "ALTER TABLE public.notification_delivery "
        "DROP CONSTRAINT notification_delivery_claim_state_check"
    )
    op.execute(
        "ALTER TABLE public.notification_delivery "
        "DROP CONSTRAINT notification_delivery_last_error_code_check"
    )
    op.execute(
        "ALTER TABLE public.notification_delivery "
        "DROP CONSTRAINT notification_delivery_status_check"
    )
    op.execute(
        "ALTER TABLE public.notification_delivery "
        "DROP COLUMN last_error_code, DROP COLUMN claim_token, "
        "DROP COLUMN claimed_at, DROP COLUMN available_at, "
        "ADD CONSTRAINT notification_delivery_status_check "
        "CHECK (status IN ('pending','sent','failed','bounced'))"
    )
    op.execute("DROP INDEX public.uq_notification_dedupe_key")
    op.execute("ALTER TABLE public.notification DROP COLUMN dedupe_key")
