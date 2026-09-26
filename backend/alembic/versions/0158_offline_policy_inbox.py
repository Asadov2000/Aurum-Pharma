"""Store received offline policies without activating protected authorization."""

from alembic import op

revision = "0158"
down_revision = "0157"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TEMP TABLE aurum_0158_missing_references (relation TEXT NOT NULL) ON COMMIT DROP
    """)
    op.execute("""
        DO $$
        DECLARE relation TEXT;
        BEGIN
          FOREACH relation IN ARRAY ARRAY['tenant','sync_writer_activation','sync_node','register']
          LOOP
            IF NOT has_table_privilege('aurum_schema_owner', 'public.' || relation, 'REFERENCES') THEN
              INSERT INTO pg_temp.aurum_0158_missing_references VALUES (relation);
              EXECUTE format('GRANT REFERENCES ON TABLE public.%I TO aurum_schema_owner', relation);
            END IF;
          END LOOP;
        END;
        $$
    """)
    op.execute("""
        CREATE TABLE public.offline_policy_inbox (
          id UUID PRIMARY KEY,
          tenant_id UUID NOT NULL REFERENCES public.tenant(id) ON DELETE RESTRICT,
          activation_id UUID NOT NULL,
          branch_id UUID NOT NULL,
          edge_node_id UUID NOT NULL,
          register_id UUID NOT NULL,
          writer_epoch BIGINT NOT NULL CONSTRAINT ck_offline_policy_inbox_epoch CHECK (writer_epoch > 0),
          capability TEXT NOT NULL CONSTRAINT ck_offline_policy_inbox_capability CHECK (capability = 'cash_sale_v1'),
          delivery_sequence BIGINT NOT NULL CONSTRAINT ck_offline_policy_inbox_sequence CHECK (delivery_sequence > 0),
          tenant_access_revision BIGINT NOT NULL CONSTRAINT ck_offline_policy_inbox_revision CHECK (tenant_access_revision > 0),
          issued_at TIMESTAMPTZ NOT NULL,
          claims_hash VARCHAR(64) NOT NULL CONSTRAINT ck_offline_policy_inbox_hash CHECK (claims_hash ~ '^[0-9a-f]{64}$'),
          payload JSONB NOT NULL CONSTRAINT ck_offline_policy_inbox_payload CHECK (jsonb_typeof(payload) = 'object'),
          status TEXT NOT NULL DEFAULT 'pending_protected_acceptance'
            CONSTRAINT ck_offline_policy_inbox_status CHECK (status = 'pending_protected_acceptance'),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_by UUID,
          updated_by UUID,
          CONSTRAINT uq_offline_policy_inbox_sequence
            UNIQUE (tenant_id, activation_id, edge_node_id, writer_epoch, delivery_sequence),
          CONSTRAINT fk_offline_policy_inbox_activation
            FOREIGN KEY (activation_id, tenant_id, branch_id, writer_epoch)
            REFERENCES public.sync_writer_activation (activation_id, tenant_id, branch_id, writer_epoch)
            ON DELETE RESTRICT,
          CONSTRAINT fk_offline_policy_inbox_node
            FOREIGN KEY (edge_node_id, tenant_id, branch_id)
            REFERENCES public.sync_node (id, tenant_id, branch_id) ON DELETE RESTRICT,
          CONSTRAINT fk_offline_policy_inbox_register
            FOREIGN KEY (register_id, tenant_id, branch_id)
            REFERENCES public.register (id, tenant_id, branch_id) ON DELETE RESTRICT
        )
    """)
    op.execute("""
        DO $$
        DECLARE relation TEXT;
        BEGIN
          FOR relation IN SELECT missing.relation FROM pg_temp.aurum_0158_missing_references AS missing
          LOOP
            EXECUTE format('REVOKE REFERENCES ON TABLE public.%I FROM aurum_schema_owner', relation);
          END LOOP;
        END;
        $$
    """)
    op.execute("DROP TABLE pg_temp.aurum_0158_missing_references")
    op.execute("ALTER TABLE public.offline_policy_inbox OWNER TO aurum_schema_owner")
    op.execute("ALTER TABLE public.offline_policy_inbox ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.offline_policy_inbox FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation ON public.offline_policy_inbox
          USING (tenant_id = public.current_tenant_id())
          WITH CHECK (tenant_id = public.current_tenant_id())
    """)
    op.execute("REVOKE ALL ON public.offline_policy_inbox FROM PUBLIC, aurum_app, aurum_support")
    op.execute("GRANT SELECT, INSERT ON public.offline_policy_inbox TO aurum_app")
    op.execute("GRANT SELECT ON public.offline_policy_inbox TO aurum_support")
    op.execute("""
        CREATE FUNCTION public.trg_offline_policy_inbox_immutable() RETURNS TRIGGER
        LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
        BEGIN
          RAISE EXCEPTION 'Offline policy inbox is immutable' USING ERRCODE='42501';
        END;
        $$
    """)
    op.execute("ALTER FUNCTION public.trg_offline_policy_inbox_immutable() OWNER TO aurum_schema_owner")
    op.execute("REVOKE ALL ON FUNCTION public.trg_offline_policy_inbox_immutable() FROM PUBLIC, aurum_app, aurum_support")
    op.execute("""
        CREATE TRIGGER trg_offline_policy_inbox_immutable
          BEFORE UPDATE OR DELETE ON public.offline_policy_inbox
          FOR EACH ROW EXECUTE FUNCTION public.trg_offline_policy_inbox_immutable()
    """)
    op.execute("""
        CREATE TRIGGER trg_offline_policy_inbox_created BEFORE INSERT ON public.offline_policy_inbox
          FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_meta()
    """)
    op.execute("""
        CREATE TRIGGER trg_offline_policy_inbox_audit AFTER INSERT ON public.offline_policy_inbox
          FOR EACH ROW EXECUTE FUNCTION public.trg_audit_log()
    """)


def downgrade() -> None:
    op.execute("DROP TABLE public.offline_policy_inbox")
    op.execute("DROP FUNCTION public.trg_offline_policy_inbox_immutable()")
