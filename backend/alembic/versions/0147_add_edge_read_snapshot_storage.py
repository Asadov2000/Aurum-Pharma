"""add independent read-only Edge snapshot storage

Revision ID: 0147
Revises: 0146
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0147"
down_revision: str | Sequence[str] | None = "0146"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLES = (
    "sync_read_snapshot",
    "sync_read_snapshot_component",
    "sync_read_snapshot_chunk",
    "sync_read_snapshot_cursor",
)
REFERENCE_TABLES = ("tenant", "app_user", "branch", "sync_node")


IMMUTABLE_STORAGE_GUARD_SQL = """
CREATE FUNCTION public.trg_guard_sync_read_snapshot_storage()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  v_snapshot_status TEXT;
BEGIN
  IF TG_OP IN ('UPDATE', 'DELETE') THEN
    RAISE EXCEPTION 'Read snapshot components and chunks are immutable'
      USING ERRCODE = '55000';
  END IF;

  SELECT snapshot.status
  INTO v_snapshot_status
  FROM public.sync_read_snapshot AS snapshot
  WHERE snapshot.snapshot_id = NEW.snapshot_id
    AND snapshot.tenant_id = NEW.tenant_id
    AND snapshot.branch_id = NEW.branch_id
    AND snapshot.node_id = NEW.node_id
    AND snapshot.source_revision = NEW.source_revision;

  IF v_snapshot_status IS DISTINCT FROM 'draft' THEN
    RAISE EXCEPTION 'Read snapshot content can only be added while draft'
      USING ERRCODE = '55000';
  END IF;
  RETURN NEW;
END;
$$;
"""


SNAPSHOT_GUARD_SQL = """
CREATE FUNCTION public.trg_guard_sync_read_snapshot()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'Read snapshots are immutable'
      USING ERRCODE = '55000';
  END IF;

  IF OLD.status <> 'draft'
    OR NEW.status <> 'ready'
    OR (pg_catalog.to_jsonb(NEW) - ARRAY['status', 'updated_at', 'updated_by'])
       IS DISTINCT FROM
       (pg_catalog.to_jsonb(OLD) - ARRAY['status', 'updated_at', 'updated_by'])
  THEN
    RAISE EXCEPTION 'Read snapshot contents are immutable'
      USING ERRCODE = '55000';
  END IF;

  IF (
    SELECT pg_catalog.count(*)
    FROM public.sync_read_snapshot_component AS component
    WHERE component.snapshot_id = NEW.snapshot_id
      AND component.tenant_id = NEW.tenant_id
      AND component.branch_id = NEW.branch_id
      AND component.node_id = NEW.node_id
      AND component.source_revision = NEW.source_revision
  ) <> 3 OR EXISTS (
    SELECT 1
    FROM public.sync_read_snapshot_component AS component
    LEFT JOIN LATERAL (
      SELECT
        pg_catalog.count(*) AS chunk_count,
        pg_catalog.count(DISTINCT chunk.chunk_index) AS distinct_chunk_count,
        pg_catalog.min(chunk.chunk_index) AS min_chunk_index,
        pg_catalog.max(chunk.chunk_index) AS max_chunk_index,
        COALESCE(pg_catalog.sum(chunk.item_count), 0) AS item_count
      FROM public.sync_read_snapshot_chunk AS chunk
      WHERE chunk.snapshot_id = component.snapshot_id
        AND chunk.tenant_id = component.tenant_id
        AND chunk.branch_id = component.branch_id
        AND chunk.node_id = component.node_id
        AND chunk.source_revision = component.source_revision
        AND chunk.component = component.component
        AND chunk.schema_version = component.schema_version
    ) AS chunks ON true
    WHERE component.snapshot_id = NEW.snapshot_id
      AND component.tenant_id = NEW.tenant_id
      AND component.branch_id = NEW.branch_id
      AND component.node_id = NEW.node_id
      AND component.source_revision = NEW.source_revision
      AND (
        chunks.chunk_count <> component.chunk_count
        OR chunks.distinct_chunk_count <> component.chunk_count
        OR chunks.min_chunk_index IS DISTINCT FROM 0
        OR chunks.max_chunk_index IS DISTINCT FROM component.chunk_count - 1
        OR chunks.item_count <> component.item_count
        OR (
          component.item_count > 0
          AND EXISTS (
            SELECT 1
            FROM public.sync_read_snapshot_chunk AS nonempty_chunk
            WHERE nonempty_chunk.snapshot_id = component.snapshot_id
              AND nonempty_chunk.component = component.component
              AND nonempty_chunk.item_count = 0
          )
        )
      )
  ) THEN
    RAISE EXCEPTION 'Read snapshot cannot be finalized with incomplete components'
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$$;
"""


CURSOR_GUARD_SQL = """
CREATE FUNCTION public.trg_guard_sync_read_snapshot_cursor()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'Read snapshot cursor cannot be deleted'
      USING ERRCODE = '55000';
  END IF;

  IF TG_OP = 'UPDATE' AND (
    NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
    OR NEW.branch_id IS DISTINCT FROM OLD.branch_id
    OR NEW.node_id IS DISTINCT FROM OLD.node_id
    OR NEW.created_at IS DISTINCT FROM OLD.created_at
    OR NEW.created_by IS DISTINCT FROM OLD.created_by
    OR NEW.active_snapshot_id IS NOT DISTINCT FROM OLD.active_snapshot_id
    OR NEW.version <> OLD.version + 1
  ) THEN
    RAISE EXCEPTION 'Read snapshot cursor update is not a valid pointer advance'
      USING ERRCODE = '23514';
  END IF;

  NEW.activated_at := pg_catalog.statement_timestamp();

  IF NOT EXISTS (
    SELECT 1
    FROM public.sync_read_snapshot AS snapshot
    WHERE snapshot.snapshot_id = NEW.active_snapshot_id
      AND snapshot.tenant_id = NEW.tenant_id
      AND snapshot.branch_id = NEW.branch_id
      AND snapshot.node_id = NEW.node_id
      AND snapshot.status = 'ready'
  ) THEN
    RAISE EXCEPTION 'Read snapshot cursor requires a ready snapshot in the same scope'
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$$;
"""


AUDIT_GUARD_SQL = """
CREATE FUNCTION public.trg_audit_sync_read_snapshot_storage()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  v_old JSONB;
  v_new JSONB;
  v_row JSONB;
  v_record_id UUID;
BEGIN
  IF TG_OP = 'INSERT' THEN
    v_new := pg_catalog.to_jsonb(NEW) - 'payload';
    v_row := v_new;
  ELSIF TG_OP = 'UPDATE' THEN
    v_old := pg_catalog.to_jsonb(OLD) - 'payload';
    v_new := pg_catalog.to_jsonb(NEW) - 'payload';
    v_row := v_new;
  ELSE
    v_old := pg_catalog.to_jsonb(OLD) - 'payload';
    v_row := v_old;
  END IF;

  v_record_id := COALESCE(
    NULLIF(v_row ->> 'snapshot_id', '')::UUID,
    NULLIF(v_row ->> 'node_id', '')::UUID
  );
  INSERT INTO public.audit_log (
    tenant_id,
    user_id,
    action,
    table_name,
    record_id,
    old_values,
    new_values,
    changed_fields,
    created_at
  ) VALUES (
    NULLIF(v_row ->> 'tenant_id', '')::UUID,
    public.current_app_user_id(),
    TG_OP::TEXT,
    TG_TABLE_NAME::TEXT,
    v_record_id,
    public.audit_redact_table_snapshot(TG_TABLE_NAME::TEXT, v_old),
    public.audit_redact_table_snapshot(TG_TABLE_NAME::TEXT, v_new),
    CASE
      WHEN TG_OP = 'UPDATE' THEN public.audit_redact_table_snapshot(
        TG_TABLE_NAME::TEXT,
        (
          SELECT pg_catalog.jsonb_object_agg(entry.key, entry.value)
          FROM pg_catalog.jsonb_each(v_new) AS entry
          WHERE v_old -> entry.key IS DISTINCT FROM entry.value
        )
      )
      ELSE NULL
    END,
    pg_catalog.statement_timestamp()
  );
  RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$;
"""


def _meta_and_audit_triggers(table: str) -> None:
    op.execute(f"""
        CREATE TRIGGER trg_{table}_created
        BEFORE INSERT ON public.{table}
        FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_meta()
        """)
    op.execute(f"""
        CREATE TRIGGER trg_{table}_updated
        BEFORE UPDATE ON public.{table}
        FOR EACH ROW EXECUTE FUNCTION public.trg_set_updated_meta()
        """)
    op.execute(f"""
        CREATE TRIGGER trg_audit_{table}
        AFTER INSERT OR UPDATE OR DELETE ON public.{table}
        FOR EACH ROW EXECUTE FUNCTION public.trg_audit_sync_read_snapshot_storage()
        """)


def _edge_scoped_rls(table: str, *, runtime_privileges: str) -> None:
    op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
    op.execute(f"""
        CREATE POLICY tenant_isolation ON public.{table}
        USING (
          tenant_id = public.current_tenant_id()
          AND (
            NULLIF(pg_catalog.current_setting('app.edge_node_id', true), '') IS NULL
            OR (
              branch_id = NULLIF(
                pg_catalog.current_setting('app.branch_id', true), ''
              )::UUID
              AND node_id = NULLIF(
                pg_catalog.current_setting('app.edge_node_id', true), ''
              )::UUID
            )
          )
        )
        WITH CHECK (
          tenant_id = public.current_tenant_id()
          AND branch_id = NULLIF(
            pg_catalog.current_setting('app.branch_id', true), ''
          )::UUID
          AND node_id = NULLIF(
            pg_catalog.current_setting('app.edge_node_id', true), ''
          )::UUID
        )
        """)
    op.execute(
        f"REVOKE ALL PRIVILEGES ON TABLE public.{table} FROM PUBLIC, aurum_app"
    )
    op.execute(
        f"GRANT {runtime_privileges} ON TABLE public.{table} TO aurum_app"
    )


def _secure_trigger_function(signature: str) -> None:
    op.execute(
        f"REVOKE ALL PRIVILEGES ON FUNCTION {signature} "
        "FROM PUBLIC, aurum_app, aurum_support"
    )


def _grant_missing_reference_privileges() -> None:
    op.execute("""CREATE TEMPORARY TABLE aurum_0147_missing_reference_privilege (
      table_name TEXT PRIMARY KEY
    ) ON COMMIT DROP""")
    for table_name in REFERENCE_TABLES:
        op.execute(f"""DO $$
        BEGIN
          IF NOT pg_catalog.has_table_privilege(
            'aurum_schema_owner', 'public.{table_name}', 'REFERENCES'
          ) THEN
            INSERT INTO pg_temp.aurum_0147_missing_reference_privilege(table_name)
            VALUES ('{table_name}');
            GRANT REFERENCES ON TABLE public.{table_name} TO aurum_schema_owner;
          END IF;
        END $$""")


def _restore_reference_privileges() -> None:
    for table_name in REFERENCE_TABLES:
        op.execute(f"""DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_temp.aurum_0147_missing_reference_privilege
            WHERE table_name='{table_name}'
          ) THEN
            REVOKE REFERENCES ON TABLE public.{table_name} FROM aurum_schema_owner;
          END IF;
        END $$""")
    op.execute("DROP TABLE pg_temp.aurum_0147_missing_reference_privilege")


def upgrade() -> None:
    _grant_missing_reference_privileges()
    op.execute("""
        CREATE TABLE public.sync_read_snapshot (
          snapshot_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id UUID NOT NULL REFERENCES public.tenant(id) ON DELETE RESTRICT,
          branch_id UUID NOT NULL,
          node_id UUID NOT NULL,
          status TEXT NOT NULL DEFAULT 'draft'
            CHECK (status IN ('draft', 'ready')),
          schema_version INTEGER NOT NULL CHECK (schema_version = 1),
          source_revision BIGINT NOT NULL CHECK (source_revision >= 1),
          credential_kid UUID NOT NULL,
          manifest_hash TEXT NOT NULL CHECK (manifest_hash ~ '^[0-9a-f]{64}$'),
          snapshot_hash TEXT NOT NULL CHECK (snapshot_hash ~ '^[0-9a-f]{64}$'),
          signature_algorithm TEXT NOT NULL CHECK (
            signature_algorithm = 'hmac-sha256-edge-read-snapshot-v1'
          ),
          signature TEXT NOT NULL CHECK (signature ~ '^[0-9a-f]{64}$'),
          source_generated_at TIMESTAMPTZ NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          created_by UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
          updated_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          updated_by UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
          CONSTRAINT uq_sync_read_snapshot_scope UNIQUE (
            snapshot_id, tenant_id, branch_id, node_id
          ),
          CONSTRAINT uq_sync_read_snapshot_revision_scope UNIQUE (
            snapshot_id, tenant_id, branch_id, node_id, source_revision
          ),
          CONSTRAINT uq_sync_read_snapshot_source_revision UNIQUE (
            tenant_id, branch_id, node_id, source_revision
          ),
          CONSTRAINT fk_sync_read_snapshot_branch_scope
            FOREIGN KEY (tenant_id, branch_id)
            REFERENCES public.branch(tenant_id, id) ON DELETE RESTRICT,
          CONSTRAINT fk_sync_read_snapshot_node_scope
            FOREIGN KEY (node_id, tenant_id, branch_id)
            REFERENCES public.sync_node(id, tenant_id, branch_id) ON DELETE RESTRICT
        )
        """)
    op.execute("""
        CREATE INDEX ix_sync_read_snapshot_scope_created
        ON public.sync_read_snapshot(
          tenant_id, branch_id, node_id, created_at DESC
        )
        """)

    op.execute("""
        CREATE TABLE public.sync_read_snapshot_component (
          snapshot_id UUID NOT NULL,
          component TEXT NOT NULL
            CHECK (component IN ('foundation', 'catalog', 'inventory')),
          tenant_id UUID NOT NULL,
          branch_id UUID NOT NULL,
          node_id UUID NOT NULL,
          source_revision BIGINT NOT NULL CHECK (source_revision >= 1),
          schema_version INTEGER NOT NULL CHECK (schema_version = 1),
          item_count BIGINT NOT NULL CHECK (item_count >= 0),
          chunk_count INTEGER NOT NULL CHECK (chunk_count > 0),
          component_hash TEXT NOT NULL CHECK (component_hash ~ '^[0-9a-f]{64}$'),
          created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          created_by UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
          updated_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          updated_by UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
          CONSTRAINT pk_sync_read_snapshot_component PRIMARY KEY (
            snapshot_id, component
          ),
          CONSTRAINT uq_sync_read_snapshot_component_scope UNIQUE (
            snapshot_id, tenant_id, branch_id, node_id, source_revision,
            component, schema_version
          ),
          CONSTRAINT fk_sync_read_snapshot_component_snapshot
            FOREIGN KEY (
              snapshot_id, tenant_id, branch_id, node_id, source_revision
            )
            REFERENCES public.sync_read_snapshot(
              snapshot_id, tenant_id, branch_id, node_id, source_revision
            ) ON DELETE RESTRICT
        )
        """)

    op.execute("""
        CREATE TABLE public.sync_read_snapshot_chunk (
          snapshot_id UUID NOT NULL,
          component TEXT NOT NULL,
          chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
          tenant_id UUID NOT NULL,
          branch_id UUID NOT NULL,
          node_id UUID NOT NULL,
          source_revision BIGINT NOT NULL CHECK (source_revision >= 1),
          schema_version INTEGER NOT NULL CHECK (schema_version = 1),
          item_count BIGINT NOT NULL CHECK (item_count >= 0),
          payload JSONB NOT NULL CHECK (
            jsonb_typeof(payload) = 'object'
            AND jsonb_typeof(payload -> 'items') = 'array'
            AND jsonb_array_length(payload -> 'items') = item_count
          ),
          payload_hash TEXT NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
          created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          created_by UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
          updated_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          updated_by UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
          CONSTRAINT pk_sync_read_snapshot_chunk PRIMARY KEY (
            snapshot_id, component, chunk_index
          ),
          CONSTRAINT fk_sync_read_snapshot_chunk_component
            FOREIGN KEY (
              snapshot_id, tenant_id, branch_id, node_id, source_revision,
              component, schema_version
            ) REFERENCES public.sync_read_snapshot_component(
              snapshot_id, tenant_id, branch_id, node_id, source_revision,
              component, schema_version
            ) ON DELETE RESTRICT
        )
        """)
    op.execute("""
        CREATE INDEX ix_sync_read_snapshot_chunk_scope
        ON public.sync_read_snapshot_chunk(
          tenant_id, branch_id, node_id, snapshot_id
        )
        """)

    op.execute("""
        CREATE TABLE public.sync_read_snapshot_cursor (
          tenant_id UUID NOT NULL,
          branch_id UUID NOT NULL,
          node_id UUID NOT NULL,
          active_snapshot_id UUID NOT NULL,
          version BIGINT NOT NULL DEFAULT 1 CHECK (version >= 1),
          activated_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          created_by UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
          updated_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          updated_by UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
          CONSTRAINT pk_sync_read_snapshot_cursor PRIMARY KEY (
            tenant_id, branch_id, node_id
          ),
          CONSTRAINT fk_sync_read_snapshot_cursor_active
            FOREIGN KEY (active_snapshot_id, tenant_id, branch_id, node_id)
            REFERENCES public.sync_read_snapshot(
              snapshot_id, tenant_id, branch_id, node_id
            ) ON DELETE RESTRICT
        )
        """)
    _restore_reference_privileges()

    op.execute(IMMUTABLE_STORAGE_GUARD_SQL)
    _secure_trigger_function("public.trg_guard_sync_read_snapshot_storage()")
    op.execute(SNAPSHOT_GUARD_SQL)
    _secure_trigger_function("public.trg_guard_sync_read_snapshot()")
    op.execute(CURSOR_GUARD_SQL)
    _secure_trigger_function("public.trg_guard_sync_read_snapshot_cursor()")
    op.execute(AUDIT_GUARD_SQL)
    _secure_trigger_function("public.trg_audit_sync_read_snapshot_storage()")

    op.execute("""
        CREATE TRIGGER trg_guard_sync_read_snapshot
        BEFORE UPDATE OR DELETE ON public.sync_read_snapshot
        FOR EACH ROW EXECUTE FUNCTION public.trg_guard_sync_read_snapshot()
        """)
    for table in ("sync_read_snapshot_component", "sync_read_snapshot_chunk"):
        op.execute(f"""
            CREATE TRIGGER trg_guard_{table}
            BEFORE INSERT OR UPDATE OR DELETE ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION public.trg_guard_sync_read_snapshot_storage()
            """)
    op.execute("""
        CREATE TRIGGER trg_guard_sync_read_snapshot_cursor
        BEFORE INSERT OR UPDATE OR DELETE ON public.sync_read_snapshot_cursor
        FOR EACH ROW EXECUTE FUNCTION public.trg_guard_sync_read_snapshot_cursor()
        """)

    runtime_privileges = {
        "sync_read_snapshot": "SELECT, INSERT, UPDATE",
        "sync_read_snapshot_component": "SELECT, INSERT",
        "sync_read_snapshot_chunk": "SELECT, INSERT",
        "sync_read_snapshot_cursor": "SELECT, INSERT, UPDATE",
    }
    for table in TABLES:
        _meta_and_audit_triggers(table)
        _edge_scoped_rls(table, runtime_privileges=runtime_privileges[table])


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE public.{table}")
    op.execute("DROP FUNCTION public.trg_audit_sync_read_snapshot_storage()")
    op.execute("DROP FUNCTION public.trg_guard_sync_read_snapshot_cursor()")
    op.execute("DROP FUNCTION public.trg_guard_sync_read_snapshot()")
    op.execute("DROP FUNCTION public.trg_guard_sync_read_snapshot_storage()")
