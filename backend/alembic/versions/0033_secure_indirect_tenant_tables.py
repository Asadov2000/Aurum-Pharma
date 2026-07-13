"""security: isolate indirectly scoped runtime tables

Revision ID: 0033
Revises: 0032
Create Date: 2026-07-13

Some tables inherit their security boundary from a parent row instead of
carrying tenant_id directly.  They still need RLS because aurum_app has direct
table privileges and must not be able to bypass repository joins.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0033"
down_revision: str | Sequence[str] | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


RLS_POLICY_GUARD_SQL = """
DO $$
DECLARE
  v_unprotected_table TEXT;
  v_policy_count INTEGER;
BEGIN
  SELECT expected.table_name
  INTO v_unprotected_table
  FROM (
    VALUES
      ('role_permission'),
      ('notification_subscription'),
      ('notification_delivery')
  ) AS expected(table_name)
  LEFT JOIN pg_catalog.pg_class AS relations
    ON relations.oid = pg_catalog.to_regclass('public.' || expected.table_name)
  WHERE relations.relrowsecurity IS DISTINCT FROM true
  LIMIT 1;

  IF v_unprotected_table IS NOT NULL THEN
    RAISE EXCEPTION 'RLS is not enabled on public.%', v_unprotected_table;
  END IF;

  SELECT count(*)
  INTO v_policy_count
  FROM pg_catalog.pg_policies
  WHERE schemaname = 'public'
    AND tablename IN (
      'role_permission',
      'notification_subscription',
      'notification_delivery'
    );

  IF v_policy_count <> 4 THEN
    RAISE EXCEPTION
      'Expected exactly four indirect-scope RLS policies, found %',
      v_policy_count;
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_policies
    WHERE schemaname = 'public'
      AND tablename = 'role_permission'
      AND policyname = 'role_permission_read'
      AND permissive = 'PERMISSIVE'
      AND cmd = 'SELECT'
  ) OR NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_policies
    WHERE schemaname = 'public'
      AND tablename = 'role_permission'
      AND policyname = 'role_permission_write'
      AND permissive = 'PERMISSIVE'
      AND cmd = 'ALL'
      AND with_check IS NOT NULL
  ) OR NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_policies
    WHERE schemaname = 'public'
      AND tablename = 'notification_subscription'
      AND policyname = 'user_isolation'
      AND permissive = 'PERMISSIVE'
      AND cmd = 'ALL'
      AND with_check IS NOT NULL
  ) OR NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_policies
    WHERE schemaname = 'public'
      AND tablename = 'notification_delivery'
      AND policyname = 'tenant_isolation'
      AND permissive = 'PERMISSIVE'
      AND cmd = 'ALL'
      AND with_check IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'Indirect-scope RLS policy contract is incomplete';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM pg_catalog.pg_policies
    WHERE schemaname = 'public'
      AND tablename IN (
        'role_permission',
        'notification_subscription',
        'notification_delivery'
      )
      AND (
        COALESCE(qual, '') LIKE '%is_support_session%'
        OR COALESCE(with_check, '') LIKE '%is_support_session%'
      )
  ) THEN
    RAISE EXCEPTION 'RLS policies must not trust a caller-controlled support flag';
  END IF;
END
$$
"""


def upgrade() -> None:
    op.execute("ALTER TABLE public.role_permission ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY role_permission_read ON public.role_permission
          FOR SELECT
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
    op.execute("""
        CREATE POLICY role_permission_write ON public.role_permission
          FOR ALL
          USING (
            EXISTS (
              SELECT 1
              FROM public.role AS scoped_role
              WHERE scoped_role.id = role_permission.role_id
                AND scoped_role.tenant_id = public.current_tenant_id()
                AND NOT scoped_role.is_system
            )
          )
          WITH CHECK (
            EXISTS (
              SELECT 1
              FROM public.role AS scoped_role
              WHERE scoped_role.id = role_permission.role_id
                AND scoped_role.tenant_id = public.current_tenant_id()
                AND NOT scoped_role.is_system
            )
          )
        """)

    op.execute("ALTER TABLE public.notification_subscription ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY user_isolation ON public.notification_subscription
          FOR ALL
          USING (user_id = public.current_app_user_id())
          WITH CHECK (user_id = public.current_app_user_id())
        """)

    op.execute("ALTER TABLE public.notification_delivery ENABLE ROW LEVEL SECURITY")
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

    op.execute(RLS_POLICY_GUARD_SQL)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON public.notification_delivery")
    op.execute("ALTER TABLE public.notification_delivery DISABLE ROW LEVEL SECURITY")

    op.execute("DROP POLICY IF EXISTS user_isolation ON public.notification_subscription")
    op.execute("ALTER TABLE public.notification_subscription DISABLE ROW LEVEL SECURITY")

    op.execute("DROP POLICY IF EXISTS role_permission_write ON public.role_permission")
    op.execute("DROP POLICY IF EXISTS role_permission_read ON public.role_permission")
    op.execute("ALTER TABLE public.role_permission DISABLE ROW LEVEL SECURITY")
