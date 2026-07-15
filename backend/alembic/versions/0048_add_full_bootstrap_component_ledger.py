"""sync: require a complete component ledger for the full bootstrap profile

Revision ID: 0048
Revises: 0047
Create Date: 2026-07-15

This migration adds only the immutable container and fail-closed DB gate. It
does not publish or prepare cash_sale_v1_full_v1 until every component payload
and the device-bound offline-auth design are implemented.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0048"
down_revision: str | Sequence[str] | None = "0047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


FOUNDATION_GUARD_SQL = """
CREATE OR REPLACE FUNCTION public.trg_guard_sync_activation_foundation() RETURNS TRIGGER AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF SESSION_USER = 'aurum_support'
      AND pg_catalog.right(pg_catalog.current_database(), 5) = '_test'
    THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'Activation foundation snapshot is immutable'
      USING ERRCODE = '55000';
  ELSIF TG_OP = 'UPDATE' THEN
    RAISE EXCEPTION 'Activation foundation snapshot is immutable'
      USING ERRCODE = '55000';
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM public.sync_activation_bootstrap AS bootstrap
    WHERE bootstrap.activation_id = NEW.activation_id
      AND bootstrap.tenant_id = NEW.tenant_id
      AND bootstrap.branch_id = NEW.branch_id
      AND bootstrap.edge_node_id = NEW.edge_node_id
      AND bootstrap.register_id = NEW.register_id
      AND bootstrap.writer_epoch = NEW.writer_epoch
      AND bootstrap.foundation_hash = NEW.payload_hash
      AND (
        (
          bootstrap.profile = 'foundation_shadow_v1'
          AND NOT bootstrap.readiness_eligible
          AND bootstrap.component_manifest_hash IS NULL
        )
        OR (
          bootstrap.profile = 'cash_sale_v1_full_v1'
          AND bootstrap.readiness_eligible
          AND bootstrap.component_manifest_hash ~ '^[0-9a-f]{64}$'
        )
      )
  ) THEN
    RAISE EXCEPTION 'Foundation snapshot scope does not match activation bootstrap'
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


COMPONENT_GUARD_SQL = """
CREATE FUNCTION public.trg_guard_sync_activation_bootstrap_component() RETURNS TRIGGER AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF SESSION_USER = 'aurum_support'
      AND pg_catalog.right(pg_catalog.current_database(), 5) = '_test'
    THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'Activation bootstrap component ledger is immutable'
      USING ERRCODE = '55000';
  ELSIF TG_OP = 'UPDATE' THEN
    RAISE EXCEPTION 'Activation bootstrap component ledger is immutable'
      USING ERRCODE = '55000';
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM public.sync_activation_bootstrap AS bootstrap
    WHERE bootstrap.activation_id = NEW.activation_id
      AND bootstrap.tenant_id = NEW.tenant_id
      AND bootstrap.branch_id = NEW.branch_id
      AND bootstrap.edge_node_id = NEW.edge_node_id
      AND bootstrap.register_id = NEW.register_id
      AND bootstrap.writer_epoch = NEW.writer_epoch
      AND bootstrap.profile = 'cash_sale_v1_full_v1'
      AND bootstrap.readiness_eligible
      AND bootstrap.component_manifest_hash ~ '^[0-9a-f]{64}$'
  ) THEN
    RAISE EXCEPTION 'Bootstrap component scope does not match a full activation profile'
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


CHUNK_GUARD_SQL = """
CREATE FUNCTION public.trg_guard_sync_activation_bootstrap_chunk() RETURNS TRIGGER AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF SESSION_USER = 'aurum_support'
      AND pg_catalog.right(pg_catalog.current_database(), 5) = '_test'
    THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'Activation bootstrap chunk ledger is immutable'
      USING ERRCODE = '55000';
  ELSIF TG_OP = 'UPDATE' THEN
    RAISE EXCEPTION 'Activation bootstrap chunk ledger is immutable'
      USING ERRCODE = '55000';
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM public.sync_activation_bootstrap_component AS component
    WHERE component.activation_id = NEW.activation_id
      AND component.tenant_id = NEW.tenant_id
      AND component.branch_id = NEW.branch_id
      AND component.edge_node_id = NEW.edge_node_id
      AND component.register_id = NEW.register_id
      AND component.writer_epoch = NEW.writer_epoch
      AND component.component = NEW.component
      AND component.schema_version = NEW.schema_version
      AND NEW.chunk_index < component.chunk_count
  ) THEN
    RAISE EXCEPTION 'Bootstrap chunk scope does not match its component'
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


COMPLETENESS_SQL = """
CREATE FUNCTION public.is_cash_sale_v1_bootstrap_complete(
  p_activation_id UUID
) RETURNS BOOLEAN AS $$
DECLARE
  v_bootstrap public.sync_activation_bootstrap%ROWTYPE;
  v_component_names TEXT[];
  v_component_manifest_hash TEXT;
BEGIN
  SELECT bootstrap.*
  INTO v_bootstrap
  FROM public.sync_activation_bootstrap AS bootstrap
  WHERE bootstrap.activation_id = p_activation_id;

  IF v_bootstrap.activation_id IS NULL
    OR v_bootstrap.profile <> 'cash_sale_v1_full_v1'
    OR NOT v_bootstrap.readiness_eligible
    OR v_bootstrap.component_manifest_hash !~ '^[0-9a-f]{64}$'
  THEN
    RETURN false;
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM public.sync_writer_activation AS activation
    WHERE activation.activation_id = v_bootstrap.activation_id
      AND activation.tenant_id = v_bootstrap.tenant_id
      AND activation.branch_id = v_bootstrap.branch_id
      AND activation.writer_node_id = v_bootstrap.edge_node_id
      AND activation.allowed_register_id = v_bootstrap.register_id
      AND activation.writer_epoch = v_bootstrap.writer_epoch
      AND activation.capability = v_bootstrap.capability
      AND activation.bootstrap_snapshot_hash = v_bootstrap.snapshot_hash
      AND activation.activation_manifest_hash = v_bootstrap.activation_manifest_hash
      AND v_bootstrap.snapshot_hash = pg_catalog.encode(
        pg_catalog.sha256(
          pg_catalog.convert_to(
            'aurum:activation-full-snapshot:v1:'
            || '1:cash_sale_v1_full_v1:true:'
            || activation.activation_id::TEXT || ':'
            || activation.tenant_id::TEXT || ':'
            || activation.branch_id::TEXT || ':'
            || activation.writer_node_id::TEXT || ':'
            || activation.allowed_register_id::TEXT || ':'
            || 'cash_sale_v1:'
            || activation.writer_epoch::TEXT || ':'
            || activation.previous_writer_epoch::TEXT || ':'
            || activation.previous_terminal_sequence::TEXT || ':'
            || activation.previous_terminal_source_checksum || ':'
            || activation.previous_terminal_projection_checksum || ':'
            || activation.receipt_baseline_seq::TEXT || ':'
            || v_bootstrap.foundation_hash || ':'
            || v_bootstrap.component_manifest_hash,
            'UTF8'
          )
        ),
        'hex'
      )
  ) THEN
    RETURN false;
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM public.sync_activation_foundation AS foundation
    WHERE foundation.activation_id = v_bootstrap.activation_id
      AND foundation.tenant_id = v_bootstrap.tenant_id
      AND foundation.branch_id = v_bootstrap.branch_id
      AND foundation.edge_node_id = v_bootstrap.edge_node_id
      AND foundation.register_id = v_bootstrap.register_id
      AND foundation.writer_epoch = v_bootstrap.writer_epoch
      AND foundation.schema_version = 1
      AND foundation.payload_hash = v_bootstrap.foundation_hash
  ) THEN
    RETURN false;
  END IF;

  SELECT pg_catalog.array_agg(component.component ORDER BY component.component)
  INTO v_component_names
  FROM public.sync_activation_bootstrap_component AS component
  WHERE component.activation_id = p_activation_id;

  IF v_component_names IS DISTINCT FROM ARRAY[
    'authorization',
    'catalog',
    'inventory',
    'offline_auth',
    'pos_materialization',
    'shift'
  ]::TEXT[] THEN
    RETURN false;
  END IF;

  IF EXISTS (
    SELECT 1
    FROM public.sync_activation_bootstrap_component AS component
    LEFT JOIN LATERAL (
      SELECT
        pg_catalog.count(*)::INTEGER AS chunk_count,
        COALESCE(
          pg_catalog.sum(chunk.item_count)::BIGINT,
          0::BIGINT
        ) AS item_count,
        pg_catalog.min(chunk.chunk_index) AS min_chunk_index,
        pg_catalog.max(chunk.chunk_index) AS max_chunk_index,
        pg_catalog.encode(
          pg_catalog.sha256(
            pg_catalog.convert_to(
              'aurum:activation-bootstrap-component:v1:'
              || component.component || ':'
              || component.schema_version::TEXT || ':'
              || component.item_count::TEXT || ':'
              || component.chunk_count::TEXT || ':'
              || COALESCE(
                pg_catalog.string_agg(
                  chunk.chunk_index::TEXT || ':'
                  || chunk.item_count::TEXT || ':'
                  || chunk.payload_hash,
                  '|' ORDER BY chunk.chunk_index
                ),
                ''
              ),
              'UTF8'
            )
          ),
          'hex'
        ) AS component_hash
      FROM public.sync_activation_bootstrap_chunk AS chunk
      WHERE chunk.activation_id = component.activation_id
        AND chunk.component = component.component
    ) AS summary ON true
    WHERE component.activation_id = p_activation_id
      AND (
        summary.chunk_count IS DISTINCT FROM component.chunk_count
        OR summary.item_count IS DISTINCT FROM component.item_count
        OR summary.min_chunk_index IS DISTINCT FROM 0
        OR summary.max_chunk_index IS DISTINCT FROM component.chunk_count - 1
        OR summary.component_hash IS DISTINCT FROM component.component_hash
      )
  ) THEN
    RETURN false;
  END IF;

  SELECT pg_catalog.encode(
    pg_catalog.sha256(
      pg_catalog.convert_to(
        'aurum:activation-bootstrap-components:v1:'
        || pg_catalog.string_agg(
          component.component || ':'
          || component.schema_version::TEXT || ':'
          || component.item_count::TEXT || ':'
          || component.chunk_count::TEXT || ':'
          || component.component_hash,
          '|' ORDER BY component.component
        ),
        'UTF8'
      )
    ),
    'hex'
  )
  INTO v_component_manifest_hash
  FROM public.sync_activation_bootstrap_component AS component
  WHERE component.activation_id = p_activation_id;

  RETURN v_component_manifest_hash = v_bootstrap.component_manifest_hash;
END;
$$ LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


DEFERRED_COMPLETENESS_GUARD_SQL = """
CREATE FUNCTION public.trg_require_complete_full_activation_bootstrap() RETURNS TRIGGER AS $$
BEGIN
  IF NEW.profile = 'cash_sale_v1_full_v1'
    AND NOT public.is_cash_sale_v1_bootstrap_complete(NEW.activation_id)
  THEN
    RAISE EXCEPTION 'Full activation bootstrap must be complete in one transaction'
      USING ERRCODE = '55000';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


READINESS_PREREQUISITE_SQL = """
CREATE OR REPLACE FUNCTION public.trg_require_full_activation_bootstrap() RETURNS TRIGGER AS $$
BEGIN
  IF SESSION_USER = 'aurum_support'
    AND pg_catalog.right(pg_catalog.current_database(), 5) = '_test'
  THEN
    RETURN NEW;
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM public.sync_activation_bootstrap AS bootstrap
    WHERE bootstrap.activation_id = NEW.activation_id
      AND bootstrap.tenant_id = NEW.tenant_id
      AND bootstrap.branch_id = NEW.branch_id
      AND bootstrap.edge_node_id = NEW.edge_node_id
      AND bootstrap.register_id = NEW.register_id
      AND bootstrap.writer_epoch = NEW.writer_epoch
      AND bootstrap.snapshot_hash = NEW.bootstrap_snapshot_hash
      AND bootstrap.activation_manifest_hash = NEW.activation_manifest_hash
      AND bootstrap.profile = 'cash_sale_v1_full_v1'
      AND bootstrap.readiness_eligible
  ) OR NOT public.is_cash_sale_v1_bootstrap_complete(NEW.activation_id) THEN
    RAISE EXCEPTION 'Complete activation bootstrap is required for writer readiness'
      USING ERRCODE = '55000';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


ACTIVATION_PREREQUISITE_SQL = """
CREATE OR REPLACE FUNCTION public.trg_require_full_bootstrap_transition() RETURNS TRIGGER AS $$
BEGIN
  IF SESSION_USER = 'aurum_support'
    AND pg_catalog.right(pg_catalog.current_database(), 5) = '_test'
  THEN
    RETURN NEW;
  END IF;

  IF NEW.state IN ('ready', 'activated')
    AND OLD.state IS DISTINCT FROM NEW.state
    AND (
      NOT EXISTS (
        SELECT 1
        FROM public.sync_activation_bootstrap AS bootstrap
        WHERE bootstrap.activation_id = NEW.activation_id
          AND bootstrap.tenant_id = NEW.tenant_id
          AND bootstrap.branch_id = NEW.branch_id
          AND bootstrap.edge_node_id = NEW.writer_node_id
          AND bootstrap.register_id = NEW.allowed_register_id
          AND bootstrap.writer_epoch = NEW.writer_epoch
          AND bootstrap.snapshot_hash = NEW.bootstrap_snapshot_hash
          AND bootstrap.activation_manifest_hash = NEW.activation_manifest_hash
          AND bootstrap.profile = 'cash_sale_v1_full_v1'
          AND bootstrap.readiness_eligible
      )
      OR NOT public.is_cash_sale_v1_bootstrap_complete(NEW.activation_id)
    )
  THEN
    RAISE EXCEPTION 'Complete activation bootstrap is required for writer transition'
      USING ERRCODE = '55000';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


LEGACY_FOUNDATION_GUARD_SQL = """
CREATE OR REPLACE FUNCTION public.trg_guard_sync_activation_foundation() RETURNS TRIGGER AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF SESSION_USER = 'aurum_support'
      AND pg_catalog.right(pg_catalog.current_database(), 5) = '_test'
    THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'Activation foundation snapshot is immutable'
      USING ERRCODE = '55000';
  ELSIF TG_OP = 'UPDATE' THEN
    RAISE EXCEPTION 'Activation foundation snapshot is immutable'
      USING ERRCODE = '55000';
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM public.sync_activation_bootstrap AS bootstrap
    WHERE bootstrap.activation_id = NEW.activation_id
      AND bootstrap.tenant_id = NEW.tenant_id
      AND bootstrap.branch_id = NEW.branch_id
      AND bootstrap.edge_node_id = NEW.edge_node_id
      AND bootstrap.register_id = NEW.register_id
      AND bootstrap.writer_epoch = NEW.writer_epoch
      AND bootstrap.foundation_hash = NEW.payload_hash
      AND bootstrap.profile = 'foundation_shadow_v1'
      AND NOT bootstrap.readiness_eligible
  ) THEN
    RAISE EXCEPTION 'Foundation snapshot scope does not match activation bootstrap'
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


LEGACY_READINESS_PREREQUISITE_SQL = """
CREATE OR REPLACE FUNCTION public.trg_require_full_activation_bootstrap() RETURNS TRIGGER AS $$
BEGIN
  IF SESSION_USER = 'aurum_support'
    AND pg_catalog.right(pg_catalog.current_database(), 5) = '_test'
  THEN
    RETURN NEW;
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM public.sync_activation_bootstrap AS bootstrap
    WHERE bootstrap.activation_id = NEW.activation_id
      AND bootstrap.tenant_id = NEW.tenant_id
      AND bootstrap.branch_id = NEW.branch_id
      AND bootstrap.edge_node_id = NEW.edge_node_id
      AND bootstrap.register_id = NEW.register_id
      AND bootstrap.writer_epoch = NEW.writer_epoch
      AND bootstrap.snapshot_hash = NEW.bootstrap_snapshot_hash
      AND bootstrap.activation_manifest_hash = NEW.activation_manifest_hash
      AND bootstrap.profile = 'cash_sale_v1_full_v1'
      AND bootstrap.readiness_eligible
  ) THEN
    RAISE EXCEPTION 'Full activation bootstrap is required for writer readiness'
      USING ERRCODE = '55000';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


LEGACY_ACTIVATION_PREREQUISITE_SQL = """
CREATE OR REPLACE FUNCTION public.trg_require_full_bootstrap_transition() RETURNS TRIGGER AS $$
BEGIN
  IF SESSION_USER = 'aurum_support'
    AND pg_catalog.right(pg_catalog.current_database(), 5) = '_test'
  THEN
    RETURN NEW;
  END IF;

  IF NEW.state IN ('ready', 'activated')
    AND OLD.state IS DISTINCT FROM NEW.state
    AND NOT EXISTS (
      SELECT 1
      FROM public.sync_activation_bootstrap AS bootstrap
      WHERE bootstrap.activation_id = NEW.activation_id
        AND bootstrap.tenant_id = NEW.tenant_id
        AND bootstrap.branch_id = NEW.branch_id
        AND bootstrap.edge_node_id = NEW.writer_node_id
        AND bootstrap.register_id = NEW.allowed_register_id
        AND bootstrap.writer_epoch = NEW.writer_epoch
        AND bootstrap.snapshot_hash = NEW.bootstrap_snapshot_hash
        AND bootstrap.activation_manifest_hash = NEW.activation_manifest_hash
        AND bootstrap.profile = 'cash_sale_v1_full_v1'
        AND bootstrap.readiness_eligible
    )
  THEN
    RAISE EXCEPTION 'Full activation bootstrap is required for writer transition'
      USING ERRCODE = '55000';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


def _secure_function(signature: str) -> None:
    op.execute(f"ALTER FUNCTION {signature} OWNER TO aurum_support")
    op.execute(f"REVOKE ALL PRIVILEGES ON FUNCTION {signature} FROM PUBLIC, aurum_app")


def _meta_triggers(table: str, *, audit: bool) -> None:
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
    if audit:
        op.execute(f"""
            CREATE TRIGGER trg_audit_{table}
            AFTER INSERT OR UPDATE OR DELETE ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION public.trg_audit_log()
            """)


def _edge_scoped_rls(table: str) -> None:
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
                pg_catalog.current_setting('app.branch_id', true),
                ''
              )::UUID
              AND edge_node_id = NULLIF(
                pg_catalog.current_setting('app.edge_node_id', true),
                ''
              )::UUID
            )
          )
        )
        WITH CHECK (
          tenant_id = public.current_tenant_id()
          AND branch_id = NULLIF(
            pg_catalog.current_setting('app.branch_id', true),
            ''
          )::UUID
          AND edge_node_id = NULLIF(
            pg_catalog.current_setting('app.edge_node_id', true),
            ''
          )::UUID
        )
        """)
    op.execute(f"REVOKE ALL PRIVILEGES ON TABLE public.{table} FROM PUBLIC, aurum_app")
    op.execute(f"GRANT SELECT ON TABLE public.{table} TO aurum_app")


def upgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM public.sync_activation_bootstrap
            WHERE profile = 'cash_sale_v1_full_v1'
          ) THEN
            RAISE EXCEPTION '0048 requires no pre-existing full bootstrap profiles';
          END IF;
        END;
        $$
        """)
    op.execute("""
        ALTER TABLE public.sync_activation_bootstrap
        ADD COLUMN component_manifest_hash TEXT
        """)
    op.execute("""
        ALTER TABLE public.sync_activation_bootstrap
        DROP CONSTRAINT ck_sync_activation_bootstrap_profile,
        ADD CONSTRAINT ck_sync_activation_bootstrap_profile CHECK (
          capability = 'cash_sale_v1'
          AND (
            (
              profile = 'foundation_shadow_v1'
              AND NOT readiness_eligible
              AND component_manifest_hash IS NULL
            )
            OR (
              profile = 'cash_sale_v1_full_v1'
              AND readiness_eligible
              AND component_manifest_hash ~ '^[0-9a-f]{64}$'
            )
          )
        )
        """)

    op.execute("""
        CREATE TABLE public.sync_activation_bootstrap_component (
          activation_id      UUID NOT NULL,
          component          TEXT NOT NULL,
          tenant_id          UUID NOT NULL,
          branch_id          UUID NOT NULL,
          edge_node_id       UUID NOT NULL,
          register_id        UUID NOT NULL,
          writer_epoch       BIGINT NOT NULL CHECK (writer_epoch > 0),
          schema_version     INTEGER NOT NULL CHECK (schema_version = 1),
          item_count         BIGINT NOT NULL CHECK (item_count >= 0),
          chunk_count        INTEGER NOT NULL CHECK (chunk_count > 0),
          component_hash     TEXT NOT NULL CHECK (component_hash ~ '^[0-9a-f]{64}$'),
          created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_by         UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
          updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_by         UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
          CONSTRAINT pk_sync_activation_bootstrap_component PRIMARY KEY (
            activation_id,
            component
          ),
          CONSTRAINT ck_sync_activation_bootstrap_component_name CHECK (
            component IN (
              'authorization',
              'catalog',
              'inventory',
              'offline_auth',
              'pos_materialization',
              'shift'
            )
          ),
          CONSTRAINT uq_sync_activation_bootstrap_component_scope UNIQUE (
            activation_id,
            tenant_id,
            branch_id,
            edge_node_id,
            register_id,
            writer_epoch,
            component
          ),
          CONSTRAINT fk_sync_activation_bootstrap_component_bootstrap FOREIGN KEY (
            activation_id,
            tenant_id,
            branch_id,
            edge_node_id,
            register_id,
            writer_epoch
          ) REFERENCES public.sync_activation_bootstrap (
            activation_id,
            tenant_id,
            branch_id,
            edge_node_id,
            register_id,
            writer_epoch
          ) ON DELETE RESTRICT
        )
        """)
    _meta_triggers("sync_activation_bootstrap_component", audit=True)
    _edge_scoped_rls("sync_activation_bootstrap_component")

    op.execute("""
        CREATE TABLE public.sync_activation_bootstrap_chunk (
          activation_id      UUID NOT NULL,
          component          TEXT NOT NULL,
          chunk_index        INTEGER NOT NULL CHECK (chunk_index >= 0),
          tenant_id          UUID NOT NULL,
          branch_id          UUID NOT NULL,
          edge_node_id       UUID NOT NULL,
          register_id        UUID NOT NULL,
          writer_epoch       BIGINT NOT NULL CHECK (writer_epoch > 0),
          schema_version     INTEGER NOT NULL CHECK (schema_version = 1),
          item_count         BIGINT NOT NULL CHECK (item_count >= 0),
          payload            JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
          payload_hash       TEXT NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
          created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_by         UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
          updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_by         UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
          CONSTRAINT pk_sync_activation_bootstrap_chunk PRIMARY KEY (
            activation_id,
            component,
            chunk_index
          ),
          CONSTRAINT fk_sync_activation_bootstrap_chunk_component FOREIGN KEY (
            activation_id,
            tenant_id,
            branch_id,
            edge_node_id,
            register_id,
            writer_epoch,
            component
          ) REFERENCES public.sync_activation_bootstrap_component (
            activation_id,
            tenant_id,
            branch_id,
            edge_node_id,
            register_id,
            writer_epoch,
            component
          ) ON DELETE RESTRICT
        )
        """)
    _meta_triggers("sync_activation_bootstrap_chunk", audit=False)
    _edge_scoped_rls("sync_activation_bootstrap_chunk")

    op.execute(FOUNDATION_GUARD_SQL)
    _secure_function("public.trg_guard_sync_activation_foundation()")

    op.execute(COMPONENT_GUARD_SQL)
    _secure_function("public.trg_guard_sync_activation_bootstrap_component()")
    op.execute("""
        CREATE TRIGGER trg_guard_sync_activation_bootstrap_component
        BEFORE INSERT OR UPDATE OR DELETE ON public.sync_activation_bootstrap_component
        FOR EACH ROW EXECUTE FUNCTION public.trg_guard_sync_activation_bootstrap_component()
        """)

    op.execute(CHUNK_GUARD_SQL)
    _secure_function("public.trg_guard_sync_activation_bootstrap_chunk()")
    op.execute("""
        CREATE TRIGGER trg_guard_sync_activation_bootstrap_chunk
        BEFORE INSERT OR UPDATE OR DELETE ON public.sync_activation_bootstrap_chunk
        FOR EACH ROW EXECUTE FUNCTION public.trg_guard_sync_activation_bootstrap_chunk()
        """)

    op.execute(COMPLETENESS_SQL)
    _secure_function("public.is_cash_sale_v1_bootstrap_complete(UUID)")
    op.execute(DEFERRED_COMPLETENESS_GUARD_SQL)
    _secure_function("public.trg_require_complete_full_activation_bootstrap()")
    op.execute("""
        CREATE CONSTRAINT TRIGGER trg_require_complete_full_activation_bootstrap
        AFTER INSERT ON public.sync_activation_bootstrap
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION public.trg_require_complete_full_activation_bootstrap()
        """)
    op.execute(READINESS_PREREQUISITE_SQL)
    _secure_function("public.trg_require_full_activation_bootstrap()")
    op.execute(ACTIVATION_PREREQUISITE_SQL)
    _secure_function("public.trg_require_full_bootstrap_transition()")


def downgrade() -> None:
    op.execute(LEGACY_READINESS_PREREQUISITE_SQL)
    _secure_function("public.trg_require_full_activation_bootstrap()")
    op.execute(LEGACY_ACTIVATION_PREREQUISITE_SQL)
    _secure_function("public.trg_require_full_bootstrap_transition()")
    op.execute(LEGACY_FOUNDATION_GUARD_SQL)
    _secure_function("public.trg_guard_sync_activation_foundation()")

    op.execute(
        "DROP TRIGGER IF EXISTS trg_require_complete_full_activation_bootstrap "
        "ON public.sync_activation_bootstrap"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS public.trg_require_complete_full_activation_bootstrap()"
    )
    op.execute("DROP FUNCTION public.is_cash_sale_v1_bootstrap_complete(UUID)")
    op.execute(
        "DROP TRIGGER trg_guard_sync_activation_bootstrap_chunk "
        "ON public.sync_activation_bootstrap_chunk"
    )
    op.execute("DROP FUNCTION public.trg_guard_sync_activation_bootstrap_chunk()")
    op.execute(
        "DROP TRIGGER trg_guard_sync_activation_bootstrap_component "
        "ON public.sync_activation_bootstrap_component"
    )
    op.execute("DROP FUNCTION public.trg_guard_sync_activation_bootstrap_component()")
    op.execute("DROP TABLE public.sync_activation_bootstrap_chunk")
    op.execute("DROP TABLE public.sync_activation_bootstrap_component")

    op.execute("""
        ALTER TABLE public.sync_activation_bootstrap
        DROP CONSTRAINT ck_sync_activation_bootstrap_profile,
        DROP COLUMN component_manifest_hash,
        ADD CONSTRAINT ck_sync_activation_bootstrap_profile CHECK (
          capability = 'cash_sale_v1'
          AND (
            (profile = 'foundation_shadow_v1' AND NOT readiness_eligible)
            OR (profile = 'cash_sale_v1_full_v1' AND readiness_eligible)
          )
        )
        """)
