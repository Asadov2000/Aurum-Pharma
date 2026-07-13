"""security: make notification delivery enqueue atomic

Revision ID: 0035
Revises: 0034
Create Date: 2026-07-13

Resolve preferences under a row lock and re-check them when enqueueing. A
unique constraint makes retries idempotent, and the guard verifies that the
two security-definer functions are not executable through PUBLIC.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0035"
down_revision: str | Sequence[str] | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ATOMIC_RESOLVE_SUBSCRIPTION_SQL = """
CREATE OR REPLACE FUNCTION public.resolve_notification_subscription(
  p_tenant_id UUID,
  p_user_id UUID,
  p_event_type TEXT
) RETURNS TABLE(channels JSONB, is_enabled BOOLEAN) AS $$
BEGIN
  IF session_user <> 'aurum_support' THEN
    IF p_tenant_id IS NULL
      OR p_tenant_id IS DISTINCT FROM public.current_tenant_id()
    THEN
      RAISE EXCEPTION 'Notification recipient is outside the active tenant'
        USING ERRCODE = '42501';
    END IF;

    IF NOT EXISTS (
      SELECT 1
      FROM public.user_assignment AS assignment
      WHERE assignment.tenant_id = p_tenant_id
        AND assignment.user_id = p_user_id
        AND assignment.is_active
    ) THEN
      RAISE EXCEPTION 'Notification recipient is outside the active tenant'
        USING ERRCODE = '42501';
    END IF;
  END IF;

  RETURN QUERY
  SELECT subscription.channels, subscription.is_enabled
  FROM public.notification_subscription AS subscription
  WHERE subscription.user_id = p_user_id
    AND subscription.event_type = p_event_type
  FOR SHARE;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


ATOMIC_ENQUEUE_DELIVERY_SQL = """
CREATE OR REPLACE FUNCTION public.enqueue_notification_delivery(
  p_notification_id UUID,
  p_channel TEXT
) RETURNS UUID AS $$
DECLARE
  v_channels JSONB;
  v_delivery_id UUID;
  v_event_type TEXT;
  v_is_enabled BOOLEAN;
  v_recipient TEXT;
  v_tenant_id UUID;
  v_user_id UUID;
BEGIN
  IF p_channel IS DISTINCT FROM 'email' THEN
    RAISE EXCEPTION 'Only configured delivery channels may be enqueued'
      USING ERRCODE = '22023';
  END IF;

  SELECT
    notification.tenant_id,
    notification.user_id,
    notification.event_type,
    app_user.email
  INTO v_tenant_id, v_user_id, v_event_type, v_recipient
  FROM public.notification
  JOIN public.app_user ON app_user.id = notification.user_id
  WHERE notification.id = p_notification_id;

  IF NOT FOUND OR NULLIF(pg_catalog.btrim(v_recipient), '') IS NULL THEN
    RAISE EXCEPTION 'Notification is unavailable for delivery'
      USING ERRCODE = '42501';
  END IF;

  IF session_user <> 'aurum_support' THEN
    IF v_tenant_id IS DISTINCT FROM public.current_tenant_id()
      OR NOT EXISTS (
        SELECT 1
        FROM public.user_assignment AS assignment
        WHERE assignment.tenant_id = v_tenant_id
          AND assignment.user_id = v_user_id
          AND assignment.is_active
      )
    THEN
      RAISE EXCEPTION 'Notification is unavailable for delivery'
        USING ERRCODE = '42501';
    END IF;
  END IF;

  SELECT subscription.channels, subscription.is_enabled
  INTO v_channels, v_is_enabled
  FROM public.notification_subscription AS subscription
  WHERE subscription.user_id = v_user_id
    AND subscription.event_type = v_event_type
  FOR SHARE;

  IF NOT FOUND
    OR NOT v_is_enabled
    OR NOT v_channels @> pg_catalog.jsonb_build_array('email')
  THEN
    RAISE EXCEPTION 'Email delivery is not enabled for this notification'
      USING ERRCODE = '42501';
  END IF;

  INSERT INTO public.notification_delivery (
    notification_id,
    channel,
    recipient
  ) VALUES (
    p_notification_id,
    p_channel,
    v_recipient
  )
  ON CONFLICT ON CONSTRAINT uq_notification_delivery_notification_channel
    DO NOTHING
  RETURNING id INTO v_delivery_id;

  IF v_delivery_id IS NULL THEN
    SELECT delivery.id
    INTO v_delivery_id
    FROM public.notification_delivery AS delivery
    WHERE delivery.notification_id = p_notification_id
      AND delivery.channel = p_channel;
  END IF;

  RETURN v_delivery_id;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


LEGACY_RESOLVE_SUBSCRIPTION_SQL = """
CREATE OR REPLACE FUNCTION public.resolve_notification_subscription(
  p_tenant_id UUID,
  p_user_id UUID,
  p_event_type TEXT
) RETURNS TABLE(channels JSONB, is_enabled BOOLEAN) AS $$
BEGIN
  IF session_user <> 'aurum_support' THEN
    IF p_tenant_id IS NULL
      OR p_tenant_id IS DISTINCT FROM public.current_tenant_id()
    THEN
      RAISE EXCEPTION 'Notification recipient is outside the active tenant'
        USING ERRCODE = '42501';
    END IF;

    IF NOT EXISTS (
      SELECT 1
      FROM public.user_assignment AS assignment
      WHERE assignment.tenant_id = p_tenant_id
        AND assignment.user_id = p_user_id
        AND assignment.is_active
    ) THEN
      RAISE EXCEPTION 'Notification recipient is outside the active tenant'
        USING ERRCODE = '42501';
    END IF;
  END IF;

  RETURN QUERY
  SELECT subscription.channels, subscription.is_enabled
  FROM public.notification_subscription AS subscription
  WHERE subscription.user_id = p_user_id
    AND subscription.event_type = p_event_type;
END;
$$ LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


LEGACY_ENQUEUE_DELIVERY_SQL = """
CREATE OR REPLACE FUNCTION public.enqueue_notification_delivery(
  p_notification_id UUID,
  p_channel TEXT
) RETURNS UUID AS $$
DECLARE
  v_delivery_id UUID;
  v_recipient TEXT;
  v_tenant_id UUID;
  v_user_id UUID;
BEGIN
  IF p_channel IS DISTINCT FROM 'email' THEN
    RAISE EXCEPTION 'Only configured delivery channels may be enqueued'
      USING ERRCODE = '22023';
  END IF;

  SELECT notification.tenant_id, notification.user_id, app_user.email
  INTO v_tenant_id, v_user_id, v_recipient
  FROM public.notification
  JOIN public.app_user ON app_user.id = notification.user_id
  WHERE notification.id = p_notification_id;

  IF NOT FOUND OR NULLIF(pg_catalog.btrim(v_recipient), '') IS NULL THEN
    RAISE EXCEPTION 'Notification is unavailable for delivery'
      USING ERRCODE = '42501';
  END IF;

  IF session_user <> 'aurum_support' THEN
    IF v_tenant_id IS DISTINCT FROM public.current_tenant_id()
      OR NOT EXISTS (
        SELECT 1
        FROM public.user_assignment AS assignment
        WHERE assignment.tenant_id = v_tenant_id
          AND assignment.user_id = v_user_id
          AND assignment.is_active
      )
    THEN
      RAISE EXCEPTION 'Notification is unavailable for delivery'
        USING ERRCODE = '42501';
    END IF;
  END IF;

  INSERT INTO public.notification_delivery (
    notification_id,
    channel,
    recipient
  ) VALUES (
    p_notification_id,
    p_channel,
    v_recipient
  )
  RETURNING id INTO v_delivery_id;

  RETURN v_delivery_id;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


ATOMIC_ENQUEUE_GUARD_SQL = """
DO $$
DECLARE
  v_function REGPROCEDURE;
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_catalog.pg_constraint AS constraints
    WHERE constraints.conrelid = 'public.notification_delivery'::REGCLASS
      AND constraints.conname = 'uq_notification_delivery_notification_channel'
      AND constraints.contype = 'u'
  ) THEN
    RAISE EXCEPTION 'Notification delivery idempotency constraint is missing';
  END IF;

  FOR v_function IN
    SELECT functions.function_name
    FROM pg_catalog.unnest(ARRAY[
      'public.resolve_notification_subscription(UUID, UUID, TEXT)'::REGPROCEDURE,
      'public.enqueue_notification_delivery(UUID, TEXT)'::REGPROCEDURE
    ]) AS functions(function_name)
  LOOP
    IF EXISTS (
      SELECT 1
      FROM pg_catalog.pg_proc AS routines
      CROSS JOIN LATERAL pg_catalog.aclexplode(
        COALESCE(
          routines.proacl,
          pg_catalog.acldefault('f'::"char", routines.proowner)
        )
      ) AS privileges
      LEFT JOIN pg_catalog.pg_roles AS grantees
        ON grantees.oid = privileges.grantee
      WHERE routines.oid = v_function
        AND privileges.privilege_type = 'EXECUTE'
        AND COALESCE(grantees.rolname, 'PUBLIC')
          NOT IN ('aurum_app', 'aurum_support')
    ) OR NOT pg_catalog.has_function_privilege(
      'aurum_app', v_function, 'EXECUTE'
    ) OR pg_catalog.has_function_privilege(
      'aurum_app', v_function, 'EXECUTE WITH GRANT OPTION'
    ) THEN
      RAISE EXCEPTION 'Notification function % has unsafe ACL', v_function;
    END IF;
  END LOOP;
END
$$
"""


def _harden_function_acl() -> None:
    for function in (
        "public.resolve_notification_subscription(UUID, UUID, TEXT)",
        "public.enqueue_notification_delivery(UUID, TEXT)",
    ):
        op.execute(f"ALTER FUNCTION {function} OWNER TO aurum_support")
        op.execute(f"REVOKE ALL PRIVILEGES ON FUNCTION {function} FROM PUBLIC, aurum_app")
        op.execute(f"GRANT EXECUTE ON FUNCTION {function} TO aurum_app, aurum_support")


def upgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM public.notification_delivery
            GROUP BY notification_id, channel
            HAVING count(*) > 1
          ) THEN
            RAISE EXCEPTION
              'Duplicate notification deliveries must be resolved before migration';
          END IF;
        END
        $$
        """)
    op.execute(
        "ALTER TABLE public.notification_delivery "
        "ADD CONSTRAINT uq_notification_delivery_notification_channel "
        "UNIQUE (notification_id, channel)"
    )
    op.execute(ATOMIC_RESOLVE_SUBSCRIPTION_SQL)
    op.execute(ATOMIC_ENQUEUE_DELIVERY_SQL)
    _harden_function_acl()
    op.execute(ATOMIC_ENQUEUE_GUARD_SQL)


def downgrade() -> None:
    op.execute(LEGACY_RESOLVE_SUBSCRIPTION_SQL)
    op.execute(LEGACY_ENQUEUE_DELIVERY_SQL)
    _harden_function_acl()
    op.execute(
        "ALTER TABLE public.notification_delivery "
        "DROP CONSTRAINT uq_notification_delivery_notification_channel"
    )
