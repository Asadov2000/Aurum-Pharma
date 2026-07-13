"""security: harden role and notification boundaries

Revision ID: 0034
Revises: 0033
Create Date: 2026-07-13

Tenant roles must never become global or system roles. Notification
preferences remain private to their owner, while two narrow security-definer
functions let application workflows resolve a recipient's preference and
enqueue an email without exposing the delivery outbox itself.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0034"
down_revision: str | Sequence[str] | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


RESOLVE_NOTIFICATION_SUBSCRIPTION_SQL = """
CREATE FUNCTION public.resolve_notification_subscription(
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


ENQUEUE_NOTIFICATION_DELIVERY_SQL = """
CREATE FUNCTION public.enqueue_notification_delivery(
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


SECURITY_GUARD_SQL = """
DO $$
DECLARE
  v_function REGPROCEDURE;
  v_function_owner TEXT;
  v_is_security_definer BOOLEAN;
  v_function_config TEXT[];
  v_policy_count INTEGER;
BEGIN
  IF NOT (
    SELECT relations.relrowsecurity
    FROM pg_catalog.pg_class AS relations
    WHERE relations.oid = 'public.notification_delivery'::REGCLASS
  ) THEN
    RAISE EXCEPTION 'notification_delivery must keep RLS enabled';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM pg_catalog.unnest(ARRAY[
      'SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER'
    ]) AS privileges(privilege)
    WHERE pg_catalog.has_table_privilege(
      'aurum_app',
      'public.notification_delivery',
      privileges.privilege
    )
  ) THEN
    RAISE EXCEPTION 'aurum_app must not access notification_delivery directly';
  END IF;

  SELECT count(*)
  INTO v_policy_count
  FROM pg_catalog.pg_policies
  WHERE schemaname = 'public'
    AND tablename = 'notification_delivery';

  IF v_policy_count <> 0 THEN
    RAISE EXCEPTION 'notification_delivery must be reachable only through its API';
  END IF;

  SELECT count(*)
  INTO v_policy_count
  FROM pg_catalog.pg_policies
  WHERE schemaname = 'public'
    AND tablename = 'role';

  IF v_policy_count <> 2 OR NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_policies
    WHERE schemaname = 'public'
      AND tablename = 'role'
      AND policyname = 'role_read'
      AND cmd = 'SELECT'
  ) OR NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_policies
    WHERE schemaname = 'public'
      AND tablename = 'role'
      AND policyname = 'role_write'
      AND cmd = 'ALL'
      AND with_check IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'role RLS policy contract is incomplete';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM pg_catalog.pg_policies
    WHERE schemaname = 'public'
      AND tablename IN ('role', 'role_permission')
      AND (
        COALESCE(qual, '') LIKE '%is_support_session%'
        OR COALESCE(with_check, '') LIKE '%is_support_session%'
      )
  ) THEN
    RAISE EXCEPTION 'Role policies must not trust a support flag';
  END IF;

  FOR v_function IN
    SELECT functions.function_name
    FROM pg_catalog.unnest(ARRAY[
      'public.resolve_notification_subscription(UUID, UUID, TEXT)'::REGPROCEDURE,
      'public.enqueue_notification_delivery(UUID, TEXT)'::REGPROCEDURE
    ]) AS functions(function_name)
  LOOP
    SELECT
      pg_catalog.pg_get_userbyid(routines.proowner),
      routines.prosecdef,
      routines.proconfig
    INTO v_function_owner, v_is_security_definer, v_function_config
    FROM pg_catalog.pg_proc AS routines
    WHERE routines.oid = v_function;

    IF v_function_owner IS DISTINCT FROM 'aurum_support'
      OR v_is_security_definer IS DISTINCT FROM true
      OR NOT (
        'search_path=pg_catalog, pg_temp' = ANY(
          COALESCE(v_function_config, ARRAY[]::TEXT[])
        )
      )
      OR NOT pg_catalog.has_function_privilege(
        'aurum_app', v_function, 'EXECUTE'
      )
      OR pg_catalog.has_function_privilege(
        'aurum_app', v_function, 'EXECUTE WITH GRANT OPTION'
      )
    THEN
      RAISE EXCEPTION 'Notification function % is not hardened', v_function;
    END IF;
  END LOOP;
END
$$
"""


def upgrade() -> None:
    op.execute("DROP POLICY tenant_isolation ON public.role")
    op.execute("""
        CREATE POLICY role_read ON public.role
          FOR SELECT
          USING (
            (is_system AND tenant_id IS NULL)
            OR (NOT is_system AND tenant_id = public.current_tenant_id())
          )
        """)
    op.execute("""
        CREATE POLICY role_write ON public.role
          FOR ALL
          USING (
            NOT is_system
            AND tenant_id = public.current_tenant_id()
          )
          WITH CHECK (
            NOT is_system
            AND tenant_id = public.current_tenant_id()
          )
        """)
    op.execute("""
        ALTER POLICY role_permission_read ON public.role_permission
          USING (
            EXISTS (
              SELECT 1
              FROM public.role AS scoped_role
              WHERE scoped_role.id = role_permission.role_id
                AND (
                  (scoped_role.is_system AND scoped_role.tenant_id IS NULL)
                  OR (
                    NOT scoped_role.is_system
                    AND scoped_role.tenant_id = public.current_tenant_id()
                  )
                )
            )
          )
        """)

    op.execute("DROP POLICY tenant_isolation ON public.notification_delivery")
    op.execute("REVOKE ALL PRIVILEGES ON TABLE public.notification_delivery FROM aurum_app")

    op.execute(RESOLVE_NOTIFICATION_SUBSCRIPTION_SQL)
    op.execute(ENQUEUE_NOTIFICATION_DELIVERY_SQL)
    for function in (
        "public.resolve_notification_subscription(UUID, UUID, TEXT)",
        "public.enqueue_notification_delivery(UUID, TEXT)",
    ):
        op.execute(f"ALTER FUNCTION {function} OWNER TO aurum_support")
        op.execute(f"REVOKE ALL PRIVILEGES ON FUNCTION {function} FROM PUBLIC, aurum_app")
        op.execute(f"GRANT EXECUTE ON FUNCTION {function} TO aurum_app, aurum_support")

    op.execute(
        "COMMENT ON FUNCTION public.resolve_notification_subscription(UUID, UUID, TEXT) "
        "IS 'Resolve one recipient preference without exposing subscription rows'"
    )
    op.execute(
        "COMMENT ON FUNCTION public.enqueue_notification_delivery(UUID, TEXT) "
        "IS 'Queue a delivery with a database-resolved recipient'"
    )
    op.execute(SECURITY_GUARD_SQL)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS public.enqueue_notification_delivery(UUID, TEXT)")
    op.execute("DROP FUNCTION IF EXISTS public.resolve_notification_subscription(UUID, UUID, TEXT)")

    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE " "ON TABLE public.notification_delivery TO aurum_app"
    )
    op.execute("""
        CREATE POLICY tenant_isolation ON public.notification_delivery
          FOR ALL
          USING (
            EXISTS (
              SELECT 1
              FROM public.notification AS scoped_notification
              WHERE scoped_notification.id = notification_delivery.notification_id
                AND scoped_notification.tenant_id = public.current_tenant_id()
            )
          )
          WITH CHECK (
            EXISTS (
              SELECT 1
              FROM public.notification AS scoped_notification
              WHERE scoped_notification.id = notification_delivery.notification_id
                AND scoped_notification.tenant_id = public.current_tenant_id()
            )
          )
        """)

    op.execute("""
        ALTER POLICY role_permission_read ON public.role_permission
          USING (
            EXISTS (
              SELECT 1
              FROM public.role AS scoped_role
              WHERE scoped_role.id = role_permission.role_id
                AND (
                  scoped_role.tenant_id IS NULL
                  OR scoped_role.tenant_id = public.current_tenant_id()
                )
            )
          )
        """)
    op.execute("DROP POLICY role_write ON public.role")
    op.execute("DROP POLICY role_read ON public.role")
    op.execute("""
        CREATE POLICY tenant_isolation ON public.role
          FOR ALL
          USING (
            tenant_id IS NULL
            OR tenant_id = public.current_tenant_id()
          )
        """)
