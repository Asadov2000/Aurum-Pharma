"""Store tenant-wide reusable catalog field choices."""

from alembic import op

revision = "0153"
down_revision = "0152"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Cluster upgrades run as aurum_schema_owner after older migrations revoked
    # REFERENCES on tenant. Restore only when missing, then return the prior ACL.
    op.execute("""
        CREATE TEMPORARY TABLE aurum_0153_missing_tenant_reference (
          missing BOOLEAN NOT NULL
        ) ON COMMIT DROP
    """)
    op.execute("""
        DO $$
        BEGIN
          IF NOT pg_catalog.has_table_privilege(
            'aurum_schema_owner', 'public.tenant', 'REFERENCES'
          ) THEN
            INSERT INTO pg_temp.aurum_0153_missing_tenant_reference VALUES (true);
            GRANT REFERENCES ON TABLE public.tenant TO aurum_schema_owner;
          END IF;
        END
        $$
    """)
    op.execute("""
        CREATE TABLE public.catalog_field_template (
          id UUID PRIMARY KEY,
          tenant_id UUID NOT NULL REFERENCES public.tenant(id),
          kind TEXT NOT NULL CONSTRAINT ck_catalog_template_kind
            CHECK (kind IN ('category','dosage','pack_size')),
          value TEXT NOT NULL CONSTRAINT ck_catalog_template_value
            CHECK (length(value) BETWEEN 1 AND 120 AND value = btrim(value)),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_by UUID,
          updated_by UUID,
          deleted_at TIMESTAMPTZ
        )
    """)
    op.execute("""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_temp.aurum_0153_missing_tenant_reference) THEN
            REVOKE REFERENCES ON TABLE public.tenant FROM aurum_schema_owner;
          END IF;
        END
        $$
    """)
    op.execute("DROP TABLE pg_temp.aurum_0153_missing_tenant_reference")
    op.execute("""
        CREATE UNIQUE INDEX uq_catalog_field_template_active_value
          ON public.catalog_field_template(tenant_id, kind, lower(value))
          WHERE deleted_at IS NULL
    """)
    op.execute("""
        CREATE INDEX ix_catalog_field_template_list
          ON public.catalog_field_template(tenant_id, kind, value)
          WHERE deleted_at IS NULL
    """)
    op.execute("ALTER TABLE public.catalog_field_template OWNER TO aurum_schema_owner")
    op.execute("ALTER TABLE public.catalog_field_template ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.catalog_field_template FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation ON public.catalog_field_template
          USING (tenant_id = public.current_tenant_id())
          WITH CHECK (tenant_id = public.current_tenant_id())
    """)
    op.execute(
        "REVOKE ALL ON public.catalog_field_template FROM PUBLIC, aurum_app, aurum_support"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON public.catalog_field_template TO aurum_app, aurum_support"
    )
    op.execute("""
        CREATE TRIGGER trg_catalog_field_template_created
          BEFORE INSERT ON public.catalog_field_template
          FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_meta()
    """)
    op.execute("""
        CREATE TRIGGER trg_catalog_field_template_updated
          BEFORE UPDATE ON public.catalog_field_template
          FOR EACH ROW EXECUTE FUNCTION public.trg_set_updated_meta()
    """)
    op.execute("""
        CREATE TRIGGER trg_catalog_field_template_audit
          AFTER INSERT OR UPDATE OR DELETE ON public.catalog_field_template
          FOR EACH ROW EXECUTE FUNCTION public.trg_audit_log()
    """)


def downgrade() -> None:
    op.execute("DROP TABLE public.catalog_field_template")
