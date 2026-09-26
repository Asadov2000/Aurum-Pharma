"""Persist provisioned local authentication guards and non-secret sessions."""

from alembic import op

revision = "0154"
down_revision = "0153"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Restore REFERENCES only for table creation, preserving the existing ACL.
    op.execute("""
        CREATE TEMPORARY TABLE aurum_0154_missing_tenant_reference (
          missing BOOLEAN NOT NULL
        ) ON COMMIT DROP
    """)
    op.execute("""
        DO $$
        BEGIN
          IF NOT pg_catalog.has_table_privilege(
            'aurum_schema_owner', 'public.tenant', 'REFERENCES'
          ) THEN
            INSERT INTO pg_temp.aurum_0154_missing_tenant_reference VALUES (true);
            GRANT REFERENCES ON TABLE public.tenant TO aurum_schema_owner;
          END IF;
        END
        $$
    """)
    op.execute("""
        CREATE TABLE public.local_auth_guard (
          tenant_id UUID NOT NULL REFERENCES public.tenant(id),
          edge_node_id UUID NOT NULL,
          user_id UUID NOT NULL,
          credential_id UUID NOT NULL,
          branch_id UUID NOT NULL,
          credential_revision BIGINT NOT NULL
            CONSTRAINT ck_local_auth_guard_revision CHECK (credential_revision > 0),
          context_digest VARCHAR(64) NOT NULL
            CONSTRAINT ck_local_auth_guard_digest CHECK (context_digest ~ '^[0-9a-f]{64}$'),
          failed_attempts INTEGER NOT NULL DEFAULT 0
            CONSTRAINT ck_local_auth_guard_attempts CHECK (failed_attempts >= 0),
          attempt_sequence BIGINT NOT NULL DEFAULT 0
            CONSTRAINT ck_local_auth_guard_sequence CHECK (attempt_sequence >= 0),
          blocked_until TIMESTAMPTZ,
          last_utc TIMESTAMPTZ NOT NULL,
          last_counter BIGINT NOT NULL
            CONSTRAINT ck_local_auth_guard_counter CHECK (last_counter >= 0),
          continuity_id VARCHAR(128) NOT NULL
            CONSTRAINT ck_local_auth_guard_continuity CHECK (length(continuity_id) > 0),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_by UUID,
          updated_by UUID,
          PRIMARY KEY (tenant_id, edge_node_id, user_id, credential_id)
        )
    """)
    op.execute("""
        CREATE TABLE public.local_auth_session (
          id UUID PRIMARY KEY,
          tenant_id UUID NOT NULL REFERENCES public.tenant(id),
          branch_id UUID NOT NULL,
          edge_node_id UUID NOT NULL,
          register_id UUID NOT NULL,
          user_id UUID NOT NULL,
          credential_id UUID NOT NULL,
          payload JSONB NOT NULL,
          revoked_at TIMESTAMPTZ,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_by UUID,
          updated_by UUID
        )
    """)
    op.execute("""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_temp.aurum_0154_missing_tenant_reference) THEN
            REVOKE REFERENCES ON TABLE public.tenant FROM aurum_schema_owner;
          END IF;
        END
        $$
    """)
    op.execute("DROP TABLE pg_temp.aurum_0154_missing_tenant_reference")
    op.execute("""
        CREATE UNIQUE INDEX uq_local_auth_session_current_workplace
          ON public.local_auth_session (tenant_id, branch_id, edge_node_id, register_id)
          WHERE revoked_at IS NULL
    """)
    for table in ("local_auth_guard", "local_auth_session"):
        op.execute(f"ALTER TABLE public.{table} OWNER TO aurum_schema_owner")
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation ON public.{table}
              USING (tenant_id = public.current_tenant_id())
              WITH CHECK (tenant_id = public.current_tenant_id())
        """)
        op.execute(f"REVOKE ALL ON public.{table} FROM PUBLIC, aurum_app, aurum_support")
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON public.{table} TO aurum_app, aurum_support")
        op.execute(f"""
            CREATE TRIGGER trg_{table}_created BEFORE INSERT ON public.{table}
              FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_meta()
        """)
        op.execute(f"""
            CREATE TRIGGER trg_{table}_updated BEFORE UPDATE ON public.{table}
              FOR EACH ROW EXECUTE FUNCTION public.trg_set_updated_meta()
        """)
        op.execute(f"""
            CREATE TRIGGER trg_{table}_audit AFTER INSERT OR UPDATE OR DELETE ON public.{table}
              FOR EACH ROW EXECUTE FUNCTION public.trg_audit_log()
        """)


def downgrade() -> None:
    op.execute("DROP TABLE public.local_auth_session")
    op.execute("DROP TABLE public.local_auth_guard")
