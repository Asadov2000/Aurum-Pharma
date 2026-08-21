"""harden onboarding readiness and trial activation

Revision ID: 0107
Revises: 0106
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0107"
down_revision: str | None = "0106"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


REFERENCE_TABLES = ("tenant", "app_user", "tenant_subscription")


def _grant_missing_reference_privileges() -> None:
    op.execute("""
        CREATE TEMPORARY TABLE aurum_0107_missing_reference_privilege (
          table_name TEXT PRIMARY KEY
        ) ON COMMIT DROP
        """)
    for table_name in REFERENCE_TABLES:
        op.execute(f"""
            DO $$
            BEGIN
              IF NOT pg_catalog.has_table_privilege(
                'aurum_schema_owner',
                'public.{table_name}',
                'REFERENCES'
              ) THEN
                INSERT INTO pg_temp.aurum_0107_missing_reference_privilege (
                  table_name
                ) VALUES ('{table_name}');
                GRANT REFERENCES ON TABLE public.{table_name}
                  TO aurum_schema_owner;
              END IF;
            END
            $$
            """)


def _restore_reference_privileges() -> None:
    for table_name in REFERENCE_TABLES:
        op.execute(f"""
            DO $$
            BEGIN
              IF EXISTS (
                SELECT 1
                FROM pg_temp.aurum_0107_missing_reference_privilege
                WHERE table_name = '{table_name}'
              ) THEN
                REVOKE REFERENCES ON TABLE public.{table_name}
                  FROM aurum_schema_owner;
              END IF;
            END
            $$
            """)
    op.execute("DROP TABLE pg_temp.aurum_0107_missing_reference_privilege")


def upgrade() -> None:
    _grant_missing_reference_privileges()
    op.execute("""
        CREATE TABLE public.trial_activation (
          tenant_id UUID PRIMARY KEY
            REFERENCES public.tenant(id) ON DELETE RESTRICT,
          operation_id UUID NOT NULL UNIQUE,
          source TEXT NOT NULL,
          actor_user_id UUID
            REFERENCES public.app_user(id) ON DELETE RESTRICT,
          actor_session_id UUID,
          subscription_id UUID NOT NULL UNIQUE,
          started_at TIMESTAMPTZ NOT NULL,
          trial_ends_at TIMESTAMPTZ NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT fk_trial_activation_subscription
            FOREIGN KEY (tenant_id, subscription_id)
            REFERENCES public.tenant_subscription(tenant_id, id)
            ON DELETE RESTRICT,
          CONSTRAINT ck_trial_activation_source
            CHECK (source IN ('manual', 'automatic', 'migration')),
          CONSTRAINT ck_trial_activation_actor CHECK (
            (source = 'manual'
              AND actor_user_id IS NOT NULL
              AND actor_session_id IS NOT NULL)
            OR
            (source IN ('automatic', 'migration')
              AND actor_user_id IS NULL
              AND actor_session_id IS NULL)
          ),
          CONSTRAINT ck_trial_activation_period
            CHECK (trial_ends_at > started_at)
        )
        """)
    _restore_reference_privileges()

    # Preserve the one-trial invariant for tenants activated before this ledger.
    op.execute("""
        INSERT INTO public.trial_activation (
          tenant_id,
          operation_id,
          source,
          actor_user_id,
          actor_session_id,
          subscription_id,
          started_at,
          trial_ends_at,
          created_at
        )
        SELECT DISTINCT ON (tenant.id)
          tenant.id,
          public.gen_random_uuid(),
          'migration',
          NULL,
          NULL,
          subscription.id,
          tenant.trial_started_at,
          COALESCE(tenant.trial_ends_at, subscription.period_end),
          tenant.trial_started_at
        FROM public.tenant AS tenant
        JOIN public.tenant_subscription AS subscription
          ON subscription.tenant_id = tenant.id
        WHERE tenant.trial_started_at IS NOT NULL
          AND COALESCE(tenant.trial_ends_at, subscription.period_end)
            > tenant.trial_started_at
        ORDER BY
          tenant.id,
          CASE
            WHEN subscription.period_start = tenant.trial_started_at THEN 0
            ELSE 1
          END,
          subscription.period_start
        """)

    op.execute("""
        CREATE FUNCTION public.trg_guard_trial_activation_immutable()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
          RAISE EXCEPTION 'Trial activation ledger is immutable';
        END;
        $function$
        """)
    op.execute(
        "REVOKE ALL ON FUNCTION public.trg_guard_trial_activation_immutable() "
        "FROM PUBLIC, aurum_app, aurum_support"
    )
    op.execute("""
        CREATE FUNCTION public.record_trial_activation(
          p_tenant_id UUID,
          p_operation_id UUID,
          p_source TEXT,
          p_actor_user_id UUID,
          p_actor_session_id UUID,
          p_subscription_id UUID,
          p_started_at TIMESTAMPTZ,
          p_trial_ends_at TIMESTAMPTZ
        )
        RETURNS UUID
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
          IF p_tenant_id IS NULL
            OR p_operation_id IS NULL
            OR p_subscription_id IS NULL
            OR p_started_at IS NULL
            OR p_trial_ends_at <= p_started_at
          THEN
            RAISE EXCEPTION 'Invalid trial activation request'
              USING ERRCODE = '22023';
          END IF;

          PERFORM pg_catalog.pg_advisory_xact_lock(
            pg_catalog.hashtextextended(p_tenant_id::TEXT, 9603)
          );

          IF p_source = 'manual' THEN
            IF p_actor_user_id IS NULL
              OR p_actor_session_id IS NULL
              OR p_tenant_id IS DISTINCT FROM public.current_tenant_id()
              OR p_actor_user_id IS DISTINCT FROM public.current_app_user_id()
              OR p_actor_session_id::TEXT IS DISTINCT FROM NULLIF(
                pg_catalog.current_setting('app.auth_session_id', true),
                ''
              )
              OR NOT EXISTS (
                SELECT 1
                FROM public.session AS auth_session
                JOIN public.app_user AS actor
                  ON actor.id = auth_session.user_id
                WHERE auth_session.id = p_actor_session_id
                  AND auth_session.user_id = p_actor_user_id
                  AND auth_session.revoked_at IS NULL
                  AND auth_session.expires_at > pg_catalog.statement_timestamp()
                  AND actor.status = 'active'
              )
              OR NOT EXISTS (
                SELECT 1
                FROM public.tenant_ownership AS ownership
                JOIN public.tenant_membership AS membership
                  ON membership.id = ownership.membership_id
                 AND membership.tenant_id = ownership.tenant_id
                WHERE ownership.tenant_id = p_tenant_id
                  AND ownership.is_active
                  AND membership.user_id = p_actor_user_id
                  AND membership.status = 'active'
              )
            THEN
              RAISE EXCEPTION 'Active owner session is required'
                USING ERRCODE = '42501';
            END IF;
          ELSIF p_source = 'automatic' THEN
            IF session_user <> 'aurum_support'
              OR p_actor_user_id IS NOT NULL
              OR p_actor_session_id IS NOT NULL
            THEN
              RAISE EXCEPTION 'Automatic trial activation is not allowed'
                USING ERRCODE = '42501';
            END IF;
          ELSE
            RAISE EXCEPTION 'Invalid trial activation source'
              USING ERRCODE = '22023';
          END IF;

          IF NOT EXISTS (
            SELECT 1
            FROM public.tenant AS tenant
            JOIN public.tenant_subscription AS subscription
              ON subscription.tenant_id = tenant.id
             AND subscription.id = p_subscription_id
            WHERE tenant.id = p_tenant_id
              AND tenant.status = 'trial'
              AND tenant.trial_started_at = p_started_at
              AND tenant.trial_ends_at = p_trial_ends_at
              AND subscription.status = 'trial'
              AND subscription.period_start = p_started_at
              AND subscription.period_end = p_trial_ends_at
          ) THEN
            RAISE EXCEPTION 'Trial state is inconsistent'
              USING ERRCODE = '40001';
          END IF;

          INSERT INTO public.trial_activation (
            tenant_id,
            operation_id,
            source,
            actor_user_id,
            actor_session_id,
            subscription_id,
            started_at,
            trial_ends_at
          ) VALUES (
            p_tenant_id,
            p_operation_id,
            p_source,
            p_actor_user_id,
            p_actor_session_id,
            p_subscription_id,
            p_started_at,
            p_trial_ends_at
          );
          RETURN p_tenant_id;
        END;
        $function$
        """)
    op.execute(
        "REVOKE ALL ON FUNCTION public.record_trial_activation("
        "UUID, UUID, TEXT, UUID, UUID, UUID, TIMESTAMPTZ, TIMESTAMPTZ) "
        "FROM PUBLIC, aurum_app, aurum_support"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION public.record_trial_activation("
        "UUID, UUID, TEXT, UUID, UUID, UUID, TIMESTAMPTZ, TIMESTAMPTZ) "
        "TO aurum_app, aurum_support"
    )
    op.execute("""
        CREATE TRIGGER trg_trial_activation_immutable
          BEFORE UPDATE OR DELETE ON public.trial_activation
          FOR EACH ROW
          EXECUTE FUNCTION public.trg_guard_trial_activation_immutable()
        """)
    op.execute("""
        CREATE TRIGGER trg_audit_trial_activation
          AFTER INSERT ON public.trial_activation
          FOR EACH ROW EXECUTE FUNCTION public.trg_audit_log()
        """)
    op.execute("""
        CREATE TRIGGER trg_audit_tenant_subscription
          AFTER INSERT OR UPDATE OR DELETE ON public.tenant_subscription
          FOR EACH ROW EXECUTE FUNCTION public.trg_audit_log()
        """)

    op.execute("ALTER TABLE public.wizard_state FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.onboarding_checklist FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.trial_activation ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.trial_activation FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY trial_activation_owner_access
        ON public.trial_activation
        TO aurum_schema_owner
        USING (true)
        WITH CHECK (true)
        """)
    op.execute("""
        CREATE POLICY trial_activation_tenant_read
        ON public.trial_activation
        FOR SELECT TO aurum_app
        USING (tenant_id = public.current_tenant_id())
        """)
    op.execute(
        "REVOKE ALL PRIVILEGES ON TABLE public.trial_activation "
        "FROM PUBLIC, aurum_app, aurum_support"
    )
    op.execute("GRANT SELECT ON TABLE public.trial_activation TO aurum_app, aurum_support")


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_audit_tenant_subscription " "ON public.tenant_subscription"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS public.record_trial_activation("
        "UUID, UUID, TEXT, UUID, UUID, UUID, TIMESTAMPTZ, TIMESTAMPTZ)"
    )
    op.execute("DROP TABLE public.trial_activation")
    op.execute("DROP FUNCTION public.trg_guard_trial_activation_immutable()")
    op.execute("ALTER TABLE public.onboarding_checklist NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.wizard_state NO FORCE ROW LEVEL SECURITY")
