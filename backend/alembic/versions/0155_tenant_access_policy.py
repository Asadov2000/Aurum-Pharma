"""Independent tenant access policy and database mutation boundaries."""

from alembic import op

revision = "0155"
down_revision = "0154"
branch_labels = None
depends_on = None

# Read projections, authentication and financial ledgers deliberately have their
# own security boundaries. These are the authoritative business write tables.
MODULE_TABLES = {
    "catalog": ("tenant_catalog", "barcode", "catalog_field_template", "catalog_import_job"),
    "incoming": ("incoming_document", "incoming_item", "incoming_draft_row"),
    "inventory": ("batch", "batch_movement", "write_off"),
    "suppliers": ("supplier", "supplier_return"),
    "pos": (
        "shift",
        "pos_favorite",
        "pos_command",
        "sale",
        "sale_item",
        "sale_payment",
        "pos_payment_attempt",
        "pos_refund_attempt",
        "pos_refund_reference",
        "prescription_log",
        "customer_return_quarantine_item",
        "customer_return_disposition",
    ),
    "staff": (
        "tenant_membership",
        "tenant_invitation",
        "user_assignment",
        "role",
        "access_role_version",
    ),
    "settings": ("branch", "register", "tenant_settings", "wizard_state", "onboarding_checklist"),
}


def _sql(source: str) -> None:
    lines: list[str] = []
    body = False
    for line in source.splitlines():
        lines.append(line)
        if line.count("$$") % 2:
            body = not body
        if not body and line.rstrip().endswith(";"):
            op.execute("\n".join(lines))
            lines = []
    if "\n".join(lines).strip():
        raise ValueError("Unterminated migration statement")


def upgrade() -> None:
    _sql("""
      CREATE TABLE public.tenant_access_policy (
        tenant_id UUID PRIMARY KEY,
        id UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),
        mode TEXT NOT NULL DEFAULT 'full' CHECK (mode IN ('full','readonly')),
        blocked_modules TEXT[] NOT NULL DEFAULT '{}' CHECK (
          blocked_modules <@ ARRAY[
            'catalog','incoming','inventory','suppliers','pos','staff','settings']::TEXT[]
          AND array_position(blocked_modules, NULL) IS NULL
        ),
        max_branches INTEGER CHECK (max_branches > 0),
        max_users INTEGER CHECK (max_users > 0),
        max_registers INTEGER CHECK (max_registers > 0),
        reason TEXT NOT NULL DEFAULT 'default-access'
          CHECK (length(btrim(reason)) BETWEEN 3 AND 500),
        expires_at TIMESTAMPTZ,
        legacy_review_required BOOLEAN NOT NULL DEFAULT false,
        version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        created_by UUID,
        updated_by UUID
      );
      ALTER TABLE public.tenant_access_policy OWNER TO aurum_schema_owner;
      ALTER TABLE public.tenant_access_policy ENABLE ROW LEVEL SECURITY;
      ALTER TABLE public.tenant_access_policy FORCE ROW LEVEL SECURITY;
      CREATE POLICY tenant_isolation ON public.tenant_access_policy
        USING (tenant_id = public.current_tenant_id())
        WITH CHECK (tenant_id = public.current_tenant_id());
      REVOKE ALL ON public.tenant_access_policy FROM PUBLIC, aurum_app, aurum_support;
      GRANT SELECT ON public.tenant_access_policy TO aurum_app, aurum_support;
      GRANT UPDATE ON public.tenant_access_policy TO aurum_support;
      INSERT INTO public.tenant_access_policy(tenant_id,mode,reason,legacy_review_required)
      SELECT id, CASE WHEN status IN ('readonly','suspended','archived')
        THEN 'readonly' ELSE 'full' END,
        CASE WHEN status IN ('readonly','suspended','archived')
          THEN 'legacy-review' ELSE 'default-access' END,
        status IN ('readonly','suspended','archived')
      FROM public.tenant;
      CREATE FUNCTION public.trg_seed_tenant_access_policy() RETURNS TRIGGER
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
      BEGIN
        INSERT INTO public.tenant_access_policy(tenant_id,mode,reason,legacy_review_required)
        VALUES (NEW.id, CASE WHEN NEW.status IN ('readonly','suspended','archived')
          THEN 'readonly' ELSE 'full' END,
          CASE WHEN NEW.status IN ('readonly','suspended','archived')
            THEN 'legacy-review' ELSE 'default-access' END,
          NEW.status IN ('readonly','suspended','archived'));
        RETURN NEW;
      END;
      $$;
      CREATE TRIGGER a00_seed_tenant_access_policy AFTER INSERT ON public.tenant
        FOR EACH ROW EXECUTE FUNCTION public.trg_seed_tenant_access_policy();
      CREATE FUNCTION public.assert_tenant_settlement_complete(p_tenant_id UUID) RETURNS VOID
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
      BEGIN
        IF EXISTS (
          SELECT 1 FROM public.pos_payment_attempt WHERE tenant_id=p_tenant_id
            AND status IN ('pending','requires_reconciliation','confirmed')
        ) OR EXISTS (
          SELECT 1 FROM public.pos_refund_attempt WHERE tenant_id=p_tenant_id
            AND status IN ('pending','requires_reconciliation','confirmed')
        ) THEN
          RAISE EXCEPTION 'tenant_access_pending_settlement' USING ERRCODE='23514';
        END IF;
      END;
      $$;
      CREATE FUNCTION public.trg_guard_tenant_access_policy() RETURNS TRIGGER
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
      BEGIN
        PERFORM pg_advisory_xact_lock(hashtextextended('tenant-access:' || OLD.tenant_id::TEXT, 0));
        BEGIN
          PERFORM public.assert_and_lock_platform_recent_capability(
            public.current_app_user_id(),
            NULLIF(current_setting('app.auth_session_id', true),'')::UUID,
            'platform.tenants.manage'
          );
        EXCEPTION WHEN insufficient_privilege THEN
          RAISE EXCEPTION 'tenant_access_policy_denied' USING ERRCODE='42501';
        END;
        IF (NEW.expires_at IS NULL OR NEW.expires_at > statement_timestamp())
          AND (NEW.mode='readonly' OR NEW.blocked_modules && ARRAY['pos','inventory']::TEXT[])
        THEN
          PERFORM public.assert_tenant_settlement_complete(OLD.tenant_id);
        END IF;
        IF NEW.tenant_id <> OLD.tenant_id OR NEW.id <> OLD.id
          OR NEW.created_at <> OLD.created_at OR NEW.created_by IS DISTINCT FROM OLD.created_by
          OR NEW.version <> OLD.version + 1 THEN
          RAISE EXCEPTION 'tenant_access_policy_conflict' USING ERRCODE='23514';
        END IF;
        IF NEW.expires_at IS NOT NULL AND NEW.expires_at <= statement_timestamp() THEN
          RAISE EXCEPTION 'tenant_access_policy_expiry' USING ERRCODE='23514';
        END IF;
        NEW.legacy_review_required := OLD.legacy_review_required AND NEW.mode <> 'full';
        RETURN NEW;
      END;
      $$;
      CREATE TRIGGER a00_guard_tenant_access_policy BEFORE UPDATE ON public.tenant_access_policy
        FOR EACH ROW EXECUTE FUNCTION public.trg_guard_tenant_access_policy();
      CREATE TRIGGER trg_tenant_access_policy_created BEFORE INSERT ON public.tenant_access_policy
        FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_meta();
      CREATE TRIGGER trg_tenant_access_policy_updated BEFORE UPDATE ON public.tenant_access_policy
        FOR EACH ROW EXECUTE FUNCTION public.trg_set_updated_meta();
      CREATE TRIGGER trg_audit_tenant_access_policy
        AFTER INSERT OR UPDATE OR DELETE ON public.tenant_access_policy
        FOR EACH ROW EXECUTE FUNCTION public.trg_audit_log();
      CREATE FUNCTION public.trg_lock_tenant_access_status() RETURNS TRIGGER
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
      BEGIN
        PERFORM pg_advisory_xact_lock(hashtextextended('tenant-access:' || OLD.id::TEXT, 0));
        IF SESSION_USER <> 'aurum_migrator' AND NEW.status IN ('readonly','suspended','archived')
          AND NEW.status IS DISTINCT FROM OLD.status THEN
          PERFORM public.assert_tenant_settlement_complete(OLD.id);
        END IF;
        RETURN NEW;
      END;
      $$;
      CREATE TRIGGER a00_lock_tenant_access_status BEFORE UPDATE OF status ON public.tenant
        FOR EACH ROW EXECUTE FUNCTION public.trg_lock_tenant_access_status();
      CREATE FUNCTION public.trg_review_legacy_tenant_access() RETURNS TRIGGER
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
      DECLARE v_launched BOOLEAN;
      BEGIN
        IF OLD.legacy_review_required AND NEW.mode='full' THEN
          SELECT EXISTS(SELECT 1 FROM public.trial_activation WHERE tenant_id=NEW.tenant_id)
            INTO v_launched;
          IF NOT v_launched AND to_regclass('public.tenant_launch_activation') IS NOT NULL THEN
            EXECUTE 'SELECT EXISTS(SELECT 1 FROM public.tenant_launch_activation '
              'WHERE tenant_id=$1)'
              INTO v_launched USING NEW.tenant_id;
          END IF;
          UPDATE public.tenant SET status=CASE WHEN v_launched THEN 'active' ELSE 'setup' END
            WHERE id=NEW.tenant_id AND status='readonly';
        END IF;
        RETURN NEW;
      END;
      $$;
      CREATE TRIGGER trg_review_legacy_tenant_access AFTER UPDATE ON public.tenant_access_policy
        FOR EACH ROW EXECUTE FUNCTION public.trg_review_legacy_tenant_access();
    """)
    # Grant REFERENCES temporarily: tenant predates role separation and its owner
    # may be aurum_support. Restore the exact privilege after adding the FK.
    _sql("""
      DO $$
      DECLARE v_had BOOLEAN := has_table_privilege(
        'aurum_schema_owner','public.tenant','REFERENCES');
      BEGIN
        IF NOT v_had THEN GRANT REFERENCES ON public.tenant TO aurum_schema_owner; END IF;
        ALTER TABLE public.tenant_access_policy ADD CONSTRAINT fk_tenant_access_tenant
          FOREIGN KEY(tenant_id) REFERENCES public.tenant(id) ON DELETE CASCADE;
        IF NOT v_had THEN REVOKE REFERENCES ON public.tenant FROM aurum_schema_owner; END IF;
      END;
      $$;
    """)
    _sql("""
      CREATE FUNCTION public.trg_enforce_tenant_access() RETURNS TRIGGER
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
      DECLARE
        v_row JSONB := CASE WHEN TG_OP='DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
        v_old JSONB := CASE WHEN TG_OP='UPDATE' THEN to_jsonb(OLD) ELSE '{}'::JSONB END;
        v_tenant UUID := (v_row->>'tenant_id')::UUID;
        v_policy public.tenant_access_policy%ROWTYPE;
        v_status TEXT;
        v_growing BOOLEAN := false;
        v_limit INTEGER;
        v_count BIGINT;
        v_key BIGINT;
      BEGIN
        -- The non-runtime migration identity already owns DDL. Historical
        -- imports and disposable-database cleanup must not require live access.
        -- SESSION_USER survives SECURITY DEFINER; an application GUC cannot
        -- activate this exception.
        IF SESSION_USER='aurum_migrator' THEN
          IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
        END IF;
        IF TG_OP='UPDATE' AND (v_old->>'tenant_id')::UUID IS DISTINCT FROM v_tenant THEN
          RAISE EXCEPTION 'tenant_access_identity_immutable' USING ERRCODE='23514';
        END IF;
        -- Global role templates are outside a tenant's policy.
        IF v_tenant IS NULL THEN
          IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
        END IF;
        IF TG_TABLE_NAME IN ('branch','register') AND TG_OP <> 'DELETE' THEN
          v_growing := (v_row->>'is_active')::BOOLEAN
            AND (TG_OP='INSERT' OR NOT (v_old->>'is_active')::BOOLEAN);
        ELSIF TG_TABLE_NAME='tenant_membership' AND TG_OP <> 'DELETE' THEN
          v_growing := v_row->>'status' IN ('pending','active')
            AND (TG_OP='INSERT' OR v_old->>'status' NOT IN ('pending','active'));
        END IF;
        v_key := hashtextextended('tenant-access:' || v_tenant::TEXT, 0);
        IF v_growing THEN PERFORM pg_advisory_xact_lock(v_key);
        ELSE PERFORM pg_advisory_xact_lock_shared(v_key); END IF;
        SELECT status INTO v_status FROM public.tenant WHERE id=v_tenant;
        SELECT * INTO v_policy FROM public.tenant_access_policy WHERE tenant_id=v_tenant;
        IF NOT FOUND THEN
          RAISE EXCEPTION 'tenant_access_policy_missing' USING ERRCODE='42501';
        END IF;
        -- Revoking a person's access must remain possible even in read-only mode.
        IF TG_TABLE_NAME='tenant_membership' AND TG_OP='UPDATE'
          AND v_row->>'status' IN ('suspended','offboarded')
          AND (v_row - ARRAY['status','suspended_at','offboarded_at','updated_at','updated_by'])
            = (v_old - ARRAY['status','suspended_at','offboarded_at','updated_at','updated_by'])
          THEN RETURN NEW; END IF;
        IF TG_TABLE_NAME='user_assignment' AND TG_OP='UPDATE'
          AND (v_old->>'is_active')::BOOLEAN AND NOT (v_row->>'is_active')::BOOLEAN
          AND (v_row - ARRAY['is_active','updated_at','updated_by'])
            = (v_old - ARRAY['is_active','updated_at','updated_by']) THEN RETURN NEW; END IF;
        -- A denied import must still be able to record its terminal failure.
        IF TG_TABLE_NAME='catalog_import_job' AND TG_OP='UPDATE'
          AND v_row->>'status'='failed'
          AND (v_row - ARRAY['status','errors','finished_at','valid_rows','error_rows'])
            = (v_old - ARRAY['status','errors','finished_at','valid_rows','error_rows'])
          THEN RETURN NEW; END IF;
        IF v_status IN ('readonly','suspended','archived') OR (
          (v_policy.expires_at IS NULL OR v_policy.expires_at > statement_timestamp())
          AND (v_policy.mode='readonly' OR TG_ARGV[0]=ANY(v_policy.blocked_modules))
        ) THEN
          RAISE EXCEPTION 'tenant_access_restricted' USING ERRCODE='42501';
        END IF;
        IF v_growing AND (v_policy.expires_at IS NULL
          OR v_policy.expires_at > statement_timestamp()) THEN
          IF TG_TABLE_NAME='branch' THEN
            v_limit := v_policy.max_branches;
            SELECT count(*) INTO v_count FROM public.branch WHERE tenant_id=v_tenant AND is_active;
          ELSIF TG_TABLE_NAME='register' THEN
            v_limit := v_policy.max_registers;
            SELECT count(*) INTO v_count FROM public.register
              WHERE tenant_id=v_tenant AND is_active;
          ELSE
            v_limit := v_policy.max_users;
            SELECT count(*) INTO v_count FROM public.tenant_membership
              WHERE tenant_id=v_tenant AND status IN ('pending','active');
          END IF;
          IF v_limit IS NOT NULL AND v_count >= v_limit THEN
            RAISE EXCEPTION 'tenant_access_quota_exceeded' USING ERRCODE='23514';
          END IF;
        END IF;
        IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
      END;
      $$;
    """)
    for module, tables in MODULE_TABLES.items():
        for table in tables:
            op.execute(f"""
              DO $$
              DECLARE v_had BOOLEAN := has_table_privilege(
                'aurum_schema_owner','public.{table}','TRIGGER');
              BEGIN
                IF NOT v_had THEN GRANT TRIGGER ON public.{table} TO aurum_schema_owner; END IF;
                CREATE TRIGGER a01_tenant_access BEFORE INSERT OR UPDATE OR DELETE ON public.{table}
                  FOR EACH ROW EXECUTE FUNCTION public.trg_enforce_tenant_access('{module}');
                IF NOT v_had THEN REVOKE TRIGGER ON public.{table} FROM aurum_schema_owner; END IF;
              END;
              $$
            """)
    for function in (
        "trg_seed_tenant_access_policy",
        "trg_guard_tenant_access_policy",
        "trg_lock_tenant_access_status",
        "trg_enforce_tenant_access",
        "trg_review_legacy_tenant_access",
    ):
        op.execute(f"ALTER FUNCTION public.{function}() OWNER TO aurum_schema_owner")
        op.execute(
            f"REVOKE ALL ON FUNCTION public.{function}() FROM PUBLIC, aurum_app, aurum_support"
        )
    op.execute(
        "ALTER FUNCTION public.assert_tenant_settlement_complete(UUID) OWNER TO aurum_schema_owner"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.assert_tenant_settlement_complete(UUID) "
        "FROM PUBLIC, aurum_app, aurum_support"
    )


def downgrade() -> None:
    for tables in MODULE_TABLES.values():
        for table in tables:
            op.execute(f"DROP TRIGGER a01_tenant_access ON public.{table}")
    op.execute("DROP TRIGGER a00_lock_tenant_access_status ON public.tenant")
    op.execute("DROP TRIGGER a00_seed_tenant_access_policy ON public.tenant")
    op.execute("DROP TABLE public.tenant_access_policy")
    for function in (
        "trg_seed_tenant_access_policy",
        "trg_guard_tenant_access_policy",
        "trg_lock_tenant_access_status",
        "trg_enforce_tenant_access",
        "trg_review_legacy_tenant_access",
    ):
        op.execute(f"DROP FUNCTION public.{function}()")
    op.execute("DROP FUNCTION public.assert_tenant_settlement_complete(UUID)")
