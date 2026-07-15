"""sync: persist activation-bound foundation bootstrap

Revision ID: 0047
Revises: 0046
Create Date: 2026-07-15

The first writer bootstrap profile is deliberately not readiness-eligible. It
persists a server-owned foundation snapshot while catalog, stock, identity, and
POS materialization remain future activation gates.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0047"
down_revision: str | Sequence[str] | None = "0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


PREPARE_FOUNDATION_HANDOVER_SQL = """
CREATE FUNCTION public.prepare_edge_writer_foundation_handover(
  p_activation_id UUID,
  p_tenant_id UUID,
  p_branch_id UUID,
  p_edge_node_id UUID,
  p_register_id UUID,
  p_expected_writer_epoch BIGINT,
  p_expected_sequence BIGINT,
  p_expected_source_checksum TEXT,
  p_expected_projection_checksum TEXT,
  p_foundation_hash TEXT,
  p_request_hash TEXT
) RETURNS UUID AS $$
DECLARE
  v_receipt_baseline BIGINT;
  v_snapshot_hash TEXT;
BEGIN
  IF SESSION_USER <> 'aurum_support'
    OR COALESCE(
      pg_catalog.current_setting('app.support_session', true),
      'false'
    ) <> 'true'
  THEN
    RAISE EXCEPTION 'Writer handover preparation requires a support session'
      USING ERRCODE = '42501';
  END IF;

  IF p_activation_id IS NULL
    OR p_tenant_id IS NULL
    OR p_branch_id IS NULL
    OR p_edge_node_id IS NULL
    OR p_register_id IS NULL
    OR p_expected_writer_epoch <= 0
    OR p_expected_sequence < 0
    OR p_expected_source_checksum !~ '^[0-9a-f]{64}$'
    OR p_expected_projection_checksum !~ '^[0-9a-f]{64}$'
    OR p_foundation_hash !~ '^[0-9a-f]{64}$'
    OR p_request_hash !~ '^[0-9a-f]{64}$'
  THEN
    RAISE EXCEPTION 'Invalid foundation handover preparation'
      USING ERRCODE = '22023';
  END IF;

  PERFORM 1
  FROM public.sync_stream AS sync_stream
  WHERE sync_stream.tenant_id = p_tenant_id
    AND sync_stream.branch_id = p_branch_id
  FOR UPDATE;

  INSERT INTO public.register_receipt_counter (
    tenant_id,
    branch_id,
    register_id,
    writer_epoch,
    last_receipt_seq
  ) VALUES (
    p_tenant_id,
    p_branch_id,
    p_register_id,
    p_expected_writer_epoch,
    0
  )
  ON CONFLICT (tenant_id, register_id) DO NOTHING;

  SELECT counter.last_receipt_seq
  INTO v_receipt_baseline
  FROM public.register_receipt_counter AS counter
  WHERE counter.tenant_id = p_tenant_id
    AND counter.branch_id = p_branch_id
    AND counter.register_id = p_register_id
  FOR UPDATE;

  IF v_receipt_baseline IS NULL THEN
    RAISE EXCEPTION 'Receipt counter is unavailable for foundation handover'
      USING ERRCODE = '55000';
  END IF;

  v_snapshot_hash := pg_catalog.encode(
    pg_catalog.sha256(
      pg_catalog.convert_to(
        'aurum:activation-foundation-snapshot:v1:'
        || '1:foundation_shadow_v1:false:'
        || p_activation_id::TEXT || ':'
        || p_tenant_id::TEXT || ':'
        || p_branch_id::TEXT || ':'
        || p_edge_node_id::TEXT || ':'
        || p_register_id::TEXT || ':'
        || 'cash_sale_v1:'
        || (p_expected_writer_epoch + 1)::TEXT || ':'
        || p_expected_writer_epoch::TEXT || ':'
        || p_expected_sequence::TEXT || ':'
        || p_expected_source_checksum || ':'
        || p_expected_projection_checksum || ':'
        || v_receipt_baseline::TEXT || ':'
        || p_foundation_hash,
        'UTF8'
      )
    ),
    'hex'
  );

  RETURN public.prepare_edge_writer_handover(
    p_activation_id,
    p_tenant_id,
    p_branch_id,
    p_edge_node_id,
    p_register_id,
    p_expected_writer_epoch,
    p_expected_sequence,
    p_expected_source_checksum,
    p_expected_projection_checksum,
    v_snapshot_hash,
    p_request_hash
  );
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


BOOTSTRAP_GUARD_SQL = """
CREATE FUNCTION public.trg_guard_sync_activation_bootstrap() RETURNS TRIGGER AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF SESSION_USER = 'aurum_support'
      AND pg_catalog.right(pg_catalog.current_database(), 5) = '_test'
    THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'Activation bootstrap ledger is immutable'
      USING ERRCODE = '55000';
  ELSIF TG_OP = 'UPDATE' THEN
    RAISE EXCEPTION 'Activation bootstrap ledger is immutable'
      USING ERRCODE = '55000';
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM public.sync_writer_activation AS activation
    WHERE activation.activation_id = NEW.activation_id
      AND activation.tenant_id = NEW.tenant_id
      AND activation.branch_id = NEW.branch_id
      AND activation.writer_node_id = NEW.edge_node_id
      AND activation.allowed_register_id = NEW.register_id
      AND activation.writer_epoch = NEW.writer_epoch
      AND activation.capability = NEW.capability
      AND activation.bootstrap_snapshot_hash = NEW.snapshot_hash
      AND activation.activation_manifest_hash = NEW.activation_manifest_hash
  ) THEN
    RAISE EXCEPTION 'Activation bootstrap scope does not match handover'
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


FOUNDATION_GUARD_SQL = """
CREATE FUNCTION public.trg_guard_sync_activation_foundation() RETURNS TRIGGER AS $$
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


READINESS_PREREQUISITE_SQL = """
CREATE FUNCTION public.trg_require_full_activation_bootstrap() RETURNS TRIGGER AS $$
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


ACTIVATION_PREREQUISITE_SQL = """
CREATE FUNCTION public.trg_require_full_bootstrap_transition() RETURNS TRIGGER AS $$
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
    op.execute(PREPARE_FOUNDATION_HANDOVER_SQL)
    _secure_function(
        "public.prepare_edge_writer_foundation_handover("
        "UUID, UUID, UUID, UUID, UUID, BIGINT, BIGINT, TEXT, TEXT, TEXT, TEXT)"
    )

    op.execute("""
        CREATE TABLE public.sync_activation_bootstrap (
          activation_id              UUID PRIMARY KEY,
          tenant_id                  UUID NOT NULL,
          branch_id                  UUID NOT NULL,
          edge_node_id               UUID NOT NULL,
          register_id                UUID NOT NULL,
          writer_epoch               BIGINT NOT NULL CHECK (writer_epoch > 0),
          capability                 TEXT NOT NULL,
          profile                    TEXT NOT NULL,
          readiness_eligible         BOOLEAN NOT NULL DEFAULT false,
          foundation_hash            TEXT NOT NULL
                                     CHECK (foundation_hash ~ '^[0-9a-f]{64}$'),
          snapshot_hash              TEXT NOT NULL
                                     CHECK (snapshot_hash ~ '^[0-9a-f]{64}$'),
          activation_manifest_hash   TEXT NOT NULL
                                     CHECK (activation_manifest_hash ~ '^[0-9a-f]{64}$'),
          created_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_by                 UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
          updated_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_by                 UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
          CONSTRAINT uq_sync_activation_bootstrap_scope UNIQUE (
            activation_id,
            tenant_id,
            branch_id,
            edge_node_id,
            register_id,
            writer_epoch
          ),
          CONSTRAINT ck_sync_activation_bootstrap_profile CHECK (
            capability = 'cash_sale_v1'
            AND (
              (profile = 'foundation_shadow_v1' AND NOT readiness_eligible)
              OR (profile = 'cash_sale_v1_full_v1' AND readiness_eligible)
            )
          ),
          CONSTRAINT fk_sync_activation_bootstrap_activation FOREIGN KEY (
            activation_id,
            tenant_id,
            branch_id,
            writer_epoch
          ) REFERENCES public.sync_writer_activation (
            activation_id,
            tenant_id,
            branch_id,
            writer_epoch
          ) ON DELETE RESTRICT,
          CONSTRAINT fk_sync_activation_bootstrap_node FOREIGN KEY (
            edge_node_id,
            tenant_id,
            branch_id
          ) REFERENCES public.sync_node (id, tenant_id, branch_id) ON DELETE RESTRICT,
          CONSTRAINT fk_sync_activation_bootstrap_register FOREIGN KEY (
            register_id,
            tenant_id,
            branch_id
          ) REFERENCES public.register (id, tenant_id, branch_id) ON DELETE RESTRICT
        )
        """)
    _meta_triggers("sync_activation_bootstrap", audit=True)
    _edge_scoped_rls("sync_activation_bootstrap")

    op.execute("""
        CREATE TABLE public.sync_activation_foundation (
          activation_id      UUID PRIMARY KEY,
          tenant_id          UUID NOT NULL,
          branch_id          UUID NOT NULL,
          edge_node_id       UUID NOT NULL,
          register_id        UUID NOT NULL,
          writer_epoch       BIGINT NOT NULL CHECK (writer_epoch > 0),
          schema_version     INTEGER NOT NULL CHECK (schema_version = 1),
          payload            JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
          payload_hash       TEXT NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
          created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_by         UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
          updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_by         UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
          CONSTRAINT fk_sync_activation_foundation_bootstrap FOREIGN KEY (
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
    _meta_triggers("sync_activation_foundation", audit=False)
    _edge_scoped_rls("sync_activation_foundation")

    op.execute(BOOTSTRAP_GUARD_SQL)
    _secure_function("public.trg_guard_sync_activation_bootstrap()")
    op.execute("""
        CREATE TRIGGER trg_guard_sync_activation_bootstrap
        BEFORE INSERT OR UPDATE OR DELETE ON public.sync_activation_bootstrap
        FOR EACH ROW EXECUTE FUNCTION public.trg_guard_sync_activation_bootstrap()
        """)

    op.execute(FOUNDATION_GUARD_SQL)
    _secure_function("public.trg_guard_sync_activation_foundation()")
    op.execute("""
        CREATE TRIGGER trg_guard_sync_activation_foundation
        BEFORE INSERT OR UPDATE OR DELETE ON public.sync_activation_foundation
        FOR EACH ROW EXECUTE FUNCTION public.trg_guard_sync_activation_foundation()
        """)

    op.execute(READINESS_PREREQUISITE_SQL)
    _secure_function("public.trg_require_full_activation_bootstrap()")
    op.execute("""
        CREATE TRIGGER trg_require_full_activation_bootstrap
        BEFORE INSERT ON public.sync_writer_readiness
        FOR EACH ROW EXECUTE FUNCTION public.trg_require_full_activation_bootstrap()
        """)

    op.execute(ACTIVATION_PREREQUISITE_SQL)
    _secure_function("public.trg_require_full_bootstrap_transition()")
    op.execute("""
        CREATE TRIGGER trg_require_full_bootstrap_transition
        BEFORE UPDATE ON public.sync_writer_activation
        FOR EACH ROW EXECUTE FUNCTION public.trg_require_full_bootstrap_transition()
        """)


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER trg_require_full_bootstrap_transition " "ON public.sync_writer_activation"
    )
    op.execute("DROP FUNCTION public.trg_require_full_bootstrap_transition()")
    op.execute(
        "DROP TRIGGER trg_require_full_activation_bootstrap " "ON public.sync_writer_readiness"
    )
    op.execute("DROP FUNCTION public.trg_require_full_activation_bootstrap()")

    op.execute(
        "DROP TRIGGER trg_guard_sync_activation_foundation " "ON public.sync_activation_foundation"
    )
    op.execute("DROP FUNCTION public.trg_guard_sync_activation_foundation()")
    op.execute(
        "DROP TRIGGER trg_guard_sync_activation_bootstrap " "ON public.sync_activation_bootstrap"
    )
    op.execute("DROP FUNCTION public.trg_guard_sync_activation_bootstrap()")

    op.execute("DROP TABLE public.sync_activation_foundation")
    op.execute("DROP TABLE public.sync_activation_bootstrap")
    op.execute(
        "DROP FUNCTION IF EXISTS public.prepare_edge_writer_foundation_handover("
        "UUID, UUID, UUID, UUID, UUID, BIGINT, BIGINT, TEXT, TEXT, TEXT, TEXT)"
    )
