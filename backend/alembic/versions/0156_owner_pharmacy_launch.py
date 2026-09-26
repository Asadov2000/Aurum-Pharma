"""Checked owner launch independent of subscription history."""

from alembic import op

revision = "0156"
down_revision = "0155"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TEMPORARY TABLE aurum_0156_missing_tenant_reference(missing BOOLEAN NOT NULL)
          ON COMMIT DROP
        """)
    op.execute("""
        DO $$ BEGIN
          IF NOT has_table_privilege('aurum_schema_owner', 'public.tenant', 'REFERENCES') THEN
            INSERT INTO pg_temp.aurum_0156_missing_tenant_reference VALUES (true);
            GRANT REFERENCES ON TABLE public.tenant TO aurum_schema_owner;
          END IF;
        END $$
        """)
    op.execute("""
        CREATE TABLE public.tenant_launch_activation (
          tenant_id UUID PRIMARY KEY REFERENCES public.tenant(id),
          id UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),
          operation_id UUID NOT NULL UNIQUE,
          actor_user_id UUID NOT NULL,
          actor_session_id UUID NOT NULL,
          launched_at TIMESTAMPTZ NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          created_by UUID
        )
        """)
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_temp.aurum_0156_missing_tenant_reference) THEN
            REVOKE REFERENCES ON TABLE public.tenant FROM aurum_schema_owner;
          END IF;
        END $$
        """)
    op.execute("DROP TABLE pg_temp.aurum_0156_missing_tenant_reference")
    op.execute("ALTER TABLE public.tenant_launch_activation OWNER TO aurum_schema_owner")
    op.execute("ALTER TABLE public.tenant_launch_activation ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.tenant_launch_activation FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY launch_owner_access ON public.tenant_launch_activation
          TO aurum_schema_owner USING (true) WITH CHECK (true)
        """)
    op.execute("""
        CREATE POLICY launch_tenant_read ON public.tenant_launch_activation
          FOR SELECT TO aurum_app USING (tenant_id = public.current_tenant_id())
        """)
    op.execute(
        "REVOKE ALL ON public.tenant_launch_activation FROM PUBLIC, aurum_app, aurum_support"
    )
    op.execute("GRANT SELECT ON public.tenant_launch_activation TO aurum_app, aurum_support")
    op.execute("""
        CREATE FUNCTION public.trg_guard_tenant_launch_immutable() RETURNS TRIGGER
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
        BEGIN
          RAISE EXCEPTION 'Launch history is immutable' USING ERRCODE = '55000';
        END;
        $$
        """)
    op.execute("""
        CREATE TRIGGER trg_launch_immutable BEFORE UPDATE OR DELETE
          ON public.tenant_launch_activation FOR EACH ROW
          EXECUTE FUNCTION public.trg_guard_tenant_launch_immutable()
        """)
    op.execute("""
        CREATE TRIGGER trg_launch_created BEFORE INSERT ON public.tenant_launch_activation
          FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_meta()
        """)
    op.execute("""
        CREATE TRIGGER trg_audit_tenant_launch AFTER INSERT ON public.tenant_launch_activation
          FOR EACH ROW EXECUTE FUNCTION public.trg_audit_log()
        """)
    op.execute("""
        CREATE FUNCTION public.trg_guard_tenant_checked_launch() RETURNS TRIGGER
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
        BEGIN
          IF NEW.status IN ('trial', 'grace_period') AND NEW.status IS DISTINCT FROM OLD.status
            AND session_user IN ('aurum_app','aurum_support','aurum_billing_worker','aurum_worker')
          THEN
            RAISE EXCEPTION 'Subscriptions are disabled' USING ERRCODE = '42501';
          END IF;
          IF OLD.status <> 'active' AND NEW.status = 'active' AND NOT EXISTS (
            SELECT 1 FROM public.tenant_launch_activation WHERE tenant_id = NEW.id
          ) AND NOT EXISTS (
            SELECT 1 FROM public.trial_activation WHERE tenant_id = NEW.id
          ) THEN
            RAISE EXCEPTION 'Checked owner launch is required' USING ERRCODE = '42501';
          END IF;
          RETURN NEW;
        END;
        $$
        """)
    op.execute("""
        CREATE TRIGGER a01_guard_tenant_checked_launch BEFORE UPDATE OF status ON public.tenant
          FOR EACH ROW EXECUTE FUNCTION public.trg_guard_tenant_checked_launch()
        """)
    op.execute("""
        CREATE FUNCTION public.launch_tenant(
          p_tenant_id UUID, p_operation_id UUID, p_actor_user_id UUID, p_actor_session_id UUID
        ) RETURNS UUID
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
        DECLARE
          v_tenant public.tenant%ROWTYPE;
          v_activation public.tenant_launch_activation%ROWTYPE;
          v_today DATE := (statement_timestamp() AT TIME ZONE 'Asia/Dushanbe')::DATE;
          v_launched_at TIMESTAMPTZ := statement_timestamp();
        BEGIN
          IF p_operation_id IS NULL OR substring(p_operation_id::TEXT, 15, 1) <> '4'
            OR substring(p_operation_id::TEXT, 20, 1) NOT IN ('8','9','a','b') THEN
            RAISE EXCEPTION 'Invalid launch operation' USING ERRCODE = '22023';
          END IF;
          IF p_tenant_id IS NULL OR p_actor_user_id IS NULL OR p_actor_session_id IS NULL
            OR p_tenant_id IS DISTINCT FROM public.current_tenant_id()
            OR p_actor_user_id IS DISTINCT FROM public.current_app_user_id()
            OR p_actor_session_id::TEXT IS DISTINCT FROM NULLIF(
              current_setting('app.auth_session_id', true), '') THEN
            RAISE EXCEPTION 'Active owner session is required' USING ERRCODE = '42501';
          END IF;
          PERFORM pg_advisory_xact_lock(hashtextextended('tenant-access:' || p_tenant_id::TEXT, 0));
          PERFORM pg_advisory_xact_lock(hashtextextended(p_tenant_id::TEXT, 9603));
          SELECT * INTO v_tenant FROM public.tenant WHERE id = p_tenant_id FOR UPDATE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'Tenant launch is unavailable' USING ERRCODE = '22023';
          END IF;
          PERFORM auth_session.id
          FROM public.session AS auth_session
          JOIN public.app_user AS actor ON actor.id = auth_session.user_id
          JOIN public.tenant_membership AS membership ON membership.user_id = actor.id
            AND membership.tenant_id = p_tenant_id
          JOIN public.tenant_ownership AS ownership ON ownership.membership_id = membership.id
            AND ownership.tenant_id = membership.tenant_id
          WHERE auth_session.id = p_actor_session_id AND actor.id = p_actor_user_id
            AND auth_session.revoked_at IS NULL AND auth_session.expires_at > v_launched_at
            AND actor.status = 'active' AND membership.status = 'active' AND ownership.is_active
          FOR SHARE OF auth_session, actor, membership, ownership;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'Active owner session is required' USING ERRCODE = '42501';
          END IF;
          SELECT * INTO v_activation FROM public.tenant_launch_activation
            WHERE tenant_id = p_tenant_id;
          IF FOUND THEN
            IF v_activation.operation_id <> p_operation_id OR v_tenant.status <> 'active' THEN
              RAISE EXCEPTION 'Launch operation conflicts with history' USING ERRCODE = '23505';
            END IF;
            RETURN p_tenant_id;
          END IF;
          IF v_tenant.status <> 'setup' THEN
            RAISE EXCEPTION 'Tenant is not awaiting launch' USING ERRCODE = '55000';
          END IF;
          IF NOT EXISTS (SELECT 1 FROM public.tenant_access_policy WHERE tenant_id = p_tenant_id)
            OR EXISTS (
              SELECT 1 FROM public.tenant_access_policy WHERE tenant_id = p_tenant_id
                AND (expires_at IS NULL OR expires_at > v_launched_at)
                AND (mode = 'readonly' OR 'settings' = ANY(blocked_modules))
            ) THEN
            RAISE EXCEPTION 'tenant_access_restricted' USING ERRCODE = '42501';
          END IF;
          PERFORM branch.id FROM public.branch AS branch
            WHERE branch.tenant_id = p_tenant_id FOR SHARE;
          PERFORM register.id FROM public.register AS register
            WHERE register.tenant_id = p_tenant_id FOR SHARE;
          PERFORM catalog.id FROM public.tenant_catalog AS catalog
            WHERE catalog.tenant_id = p_tenant_id AND catalog.is_active
              AND catalog.deleted_at IS NULL ORDER BY catalog.id LIMIT 100 FOR SHARE;
          PERFORM settings.tenant_id FROM public.tenant_settings AS settings
            WHERE settings.tenant_id = p_tenant_id FOR SHARE;
          -- Required readiness mirrors the canonical overview, not wizard/task counters.
          IF btrim(v_tenant.name) = '' OR btrim(v_tenant.contact_email) = ''
            OR (SELECT count(*) FROM public.tenant_catalog WHERE tenant_id = p_tenant_id
                AND is_active AND deleted_at IS NULL) < 100
            OR NOT EXISTS (
              SELECT 1 FROM public.branch AS branch
              WHERE branch.tenant_id = p_tenant_id AND branch.is_active
                AND NULLIF(btrim(branch.address), '') IS NOT NULL
                AND NULLIF(btrim(branch.license_number), '') IS NOT NULL
                AND branch.license_expires_at >= v_today
                AND jsonb_typeof(branch.receipt_header) = 'object'
                AND NULLIF(btrim(branch.receipt_header ->> 'line1'), '') IS NOT NULL
                AND EXISTS (SELECT 1 FROM public.register AS register
                  WHERE register.tenant_id = p_tenant_id AND register.branch_id = branch.id
                    AND register.is_active)
            ) OR NOT EXISTS (
              SELECT 1 FROM public.tenant_settings AS settings
              WHERE settings.tenant_id = p_tenant_id
                AND jsonb_typeof(settings.pos_payment_methods) = 'array'
                AND jsonb_array_length(settings.pos_payment_methods) > 0
                AND NULLIF(btrim(settings.prescription_warning_text), '') IS NOT NULL
                AND settings.expired_sale_mode IN ('strict', 'warning', 'off')
                AND settings.refund_reason_mode IN (
                  'required', 'required_with_text', 'optional', 'off')
            ) THEN
            RAISE EXCEPTION 'Launch checklist is not complete' USING ERRCODE = '23514';
          END IF;
          INSERT INTO public.tenant_launch_activation(
            tenant_id, operation_id, actor_user_id, actor_session_id, launched_at
          ) VALUES (
            p_tenant_id, p_operation_id, p_actor_user_id, p_actor_session_id, v_launched_at);
          UPDATE public.tenant SET status = 'active' WHERE id = p_tenant_id;
          UPDATE public.wizard_state SET current_step = 8,
            steps_completed = '[1,2,3,4,5,6,7,8]'::JSONB,
            is_completed = true, completed_at = v_launched_at, updated_at = v_launched_at
            WHERE tenant_id = p_tenant_id AND NOT is_completed;
          RETURN p_tenant_id;
        END;
        $$
        """)
    for signature in (
        "trg_guard_tenant_launch_immutable()",
        "trg_guard_tenant_checked_launch()",
        "launch_tenant(UUID, UUID, UUID, UUID)",
    ):
        op.execute(f"ALTER FUNCTION public.{signature} OWNER TO aurum_schema_owner")
        op.execute(
            f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC, aurum_app, aurum_support"
        )
    op.execute(
        "GRANT EXECUTE ON FUNCTION public.launch_tenant(UUID, UUID, UUID, UUID) "
        "TO aurum_app, aurum_support"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER a01_guard_tenant_checked_launch ON public.tenant")
    op.execute("DROP FUNCTION public.trg_guard_tenant_checked_launch()")
    op.execute("DROP FUNCTION public.launch_tenant(UUID, UUID, UUID, UUID)")
    op.execute("DROP TABLE public.tenant_launch_activation")
    op.execute("DROP FUNCTION public.trg_guard_tenant_launch_immutable()")
