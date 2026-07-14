"""sync: add immutable writer epochs and Edge readiness ledger

Revision ID: 0044
Revises: 0043
Create Date: 2026-07-14

Writer ownership is recorded per epoch instead of being represented only by
the mutable sync_stream pointer. Sequence numbers restart inside every epoch;
the epoch root links a handover to the terminal checkpoint of its predecessor.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0044"
down_revision: str | Sequence[str] | None = "0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ZERO_CHECKSUM = "0" * 64


ALLOCATE_REGISTER_RECEIPT_SQL = """
CREATE FUNCTION public.allocate_register_receipt(
  p_tenant_id UUID,
  p_register_id UUID
) RETURNS TABLE(
  receipt_seq BIGINT,
  receipt_number TEXT
) AS $$
DECLARE
  v_branch_id UUID;
  v_writer_node_id UUID;
  v_writer_epoch BIGINT;
  v_receipt_seq BIGINT;
BEGIN
  SELECT register.branch_id
  INTO v_branch_id
  FROM public.register AS register
  WHERE register.id = p_register_id
    AND register.tenant_id = p_tenant_id
  FOR UPDATE;

  IF v_branch_id IS NULL THEN
    RETURN;
  END IF;

  IF SESSION_USER = 'aurum_app'
    AND (
      public.current_tenant_id() IS DISTINCT FROM p_tenant_id
      OR NULLIF(
        pg_catalog.current_setting('app.branch_id', true),
        ''
      )::UUID IS DISTINCT FROM v_branch_id
      OR NULLIF(
        pg_catalog.current_setting('app.edge_node_id', true),
        ''
      ) IS NOT NULL
    )
  THEN
    RAISE EXCEPTION 'Receipt allocation requires a Cloud branch scope'
      USING ERRCODE = '42501';
  ELSIF SESSION_USER NOT IN ('aurum_app', 'aurum_support') THEN
    RAISE EXCEPTION 'Receipt allocation is not allowed'
      USING ERRCODE = '42501';
  END IF;

  INSERT INTO public.sync_node (
    tenant_id,
    branch_id,
    node_kind,
    mode,
    status,
    display_name
  ) VALUES (
    p_tenant_id,
    v_branch_id,
    'cloud',
    'cloud_writer',
    'active',
    'Cloud writer'
  )
  ON CONFLICT (tenant_id, branch_id)
    WHERE node_kind = 'cloud'
  DO NOTHING;

  SELECT sync_node.id
  INTO v_writer_node_id
  FROM public.sync_node AS sync_node
  WHERE sync_node.tenant_id = p_tenant_id
    AND sync_node.branch_id = v_branch_id
    AND sync_node.node_kind = 'cloud';

  INSERT INTO public.sync_stream (
    tenant_id,
    branch_id,
    writer_node_id,
    writer_epoch,
    last_sequence,
    current_checksum,
    current_projection_checksum
  ) VALUES (
    p_tenant_id,
    v_branch_id,
    v_writer_node_id,
    1,
    0,
    repeat('0', 64),
    repeat('0', 64)
  )
  ON CONFLICT (tenant_id, branch_id) DO NOTHING;

  SELECT sync_stream.writer_epoch
  INTO v_writer_epoch
  FROM public.sync_stream AS sync_stream
  JOIN public.sync_node AS sync_node
    ON sync_node.id = sync_stream.writer_node_id
   AND sync_node.tenant_id = sync_stream.tenant_id
   AND sync_node.branch_id = sync_stream.branch_id
  WHERE sync_stream.tenant_id = p_tenant_id
    AND sync_stream.branch_id = v_branch_id
    AND sync_node.node_kind = 'cloud'
    AND sync_node.mode = 'cloud_writer'
    AND sync_node.status = 'active'
  FOR UPDATE OF sync_stream;

  IF v_writer_epoch IS NULL THEN
    RAISE EXCEPTION 'Cloud receipt writer is not active'
      USING ERRCODE = '55000';
  END IF;

  INSERT INTO public.register_receipt_counter (
    tenant_id,
    branch_id,
    register_id,
    writer_epoch,
    last_receipt_seq
  ) VALUES (
    p_tenant_id,
    v_branch_id,
    p_register_id,
    v_writer_epoch,
    0
  )
  ON CONFLICT (tenant_id, register_id) DO NOTHING;

  UPDATE public.register_receipt_counter AS counter
  SET
    writer_epoch = v_writer_epoch,
    last_receipt_seq = counter.last_receipt_seq + 1
  WHERE counter.tenant_id = p_tenant_id
    AND counter.branch_id = v_branch_id
    AND counter.register_id = p_register_id
  RETURNING counter.last_receipt_seq INTO v_receipt_seq;

  IF v_receipt_seq IS NULL THEN
    RAISE EXCEPTION 'Receipt counter is unavailable'
      USING ERRCODE = '55000';
  END IF;

  RETURN QUERY
  SELECT v_receipt_seq, pg_catalog.lpad(v_receipt_seq::TEXT, 6, '0');
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


SYNC_STREAM_EPOCH_LEDGER_SQL = f"""
CREATE FUNCTION public.trg_sync_stream_epoch_ledger() RETURNS TRIGGER AS $$
DECLARE
  v_updated INTEGER;
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF NEW.writer_epoch <> 1 OR NOT EXISTS (
      SELECT 1
      FROM public.sync_node AS sync_node
      WHERE sync_node.id = NEW.writer_node_id
        AND sync_node.tenant_id = NEW.tenant_id
        AND sync_node.branch_id = NEW.branch_id
        AND sync_node.node_kind = 'cloud'
        AND sync_node.mode = 'cloud_writer'
        AND sync_node.status = 'active'
    ) THEN
      RAISE EXCEPTION 'Initial sync stream requires an active Cloud writer'
        USING ERRCODE = '23514';
    END IF;

    INSERT INTO public.sync_writer_epoch (
      tenant_id,
      branch_id,
      writer_epoch,
      activation_id,
      writer_node_id,
      capability,
      state,
      root_source_checksum,
      root_projection_checksum,
      last_sequence,
      current_source_checksum,
      current_projection_checksum,
      bootstrap_snapshot_hash,
      activation_manifest_hash,
      receipt_baseline_seq,
      prepared_at,
      activated_at
    ) VALUES (
      NEW.tenant_id,
      NEW.branch_id,
      NEW.writer_epoch,
      public.gen_random_uuid(),
      NEW.writer_node_id,
      'cloud_full',
      'active',
      '{ZERO_CHECKSUM}',
      '{ZERO_CHECKSUM}',
      NEW.last_sequence,
      NEW.current_checksum,
      NEW.current_projection_checksum,
      '{ZERO_CHECKSUM}',
      '{ZERO_CHECKSUM}',
      0,
      NEW.created_at,
      NEW.created_at
    );
  ELSE
    UPDATE public.sync_writer_epoch AS writer_epoch
    SET
      last_sequence = NEW.last_sequence,
      current_source_checksum = NEW.current_checksum,
      current_projection_checksum = NEW.current_projection_checksum
    WHERE writer_epoch.tenant_id = NEW.tenant_id
      AND writer_epoch.branch_id = NEW.branch_id
      AND writer_epoch.writer_epoch = NEW.writer_epoch
      AND writer_epoch.writer_node_id = NEW.writer_node_id;

    GET DIAGNOSTICS v_updated = ROW_COUNT;
    IF v_updated <> 1 THEN
      RAISE EXCEPTION 'Sync stream epoch ledger is unavailable'
        USING ERRCODE = '23514';
    END IF;
  END IF;

  RETURN NULL;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


def _meta_triggers(table: str) -> None:
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


def _meta_and_audit_triggers(table: str) -> None:
    _meta_triggers(table)
    op.execute(f"""
        CREATE TRIGGER trg_audit_{table}
        AFTER INSERT OR UPDATE OR DELETE ON public.{table}
        FOR EACH ROW EXECUTE FUNCTION public.trg_audit_log()
        """)


def _branch_rls(table: str) -> None:
    op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
    op.execute(f"""
        CREATE POLICY tenant_isolation ON public.{table}
        USING (
          tenant_id = public.current_tenant_id()
          AND (
            NULLIF(pg_catalog.current_setting('app.edge_node_id', true), '') IS NULL
            OR branch_id = NULLIF(
              pg_catalog.current_setting('app.branch_id', true),
              ''
            )::UUID
          )
        )
        WITH CHECK (
          tenant_id = public.current_tenant_id()
          AND (
            NULLIF(pg_catalog.current_setting('app.edge_node_id', true), '') IS NULL
            OR branch_id = NULLIF(
              pg_catalog.current_setting('app.branch_id', true),
              ''
            )::UUID
          )
        )
        """)
    op.execute(f"REVOKE ALL PRIVILEGES ON TABLE public.{table} FROM PUBLIC, aurum_app")
    op.execute(f"GRANT SELECT ON TABLE public.{table} TO aurum_app")


def _extend_sync_node() -> None:
    op.execute(
        "ALTER TABLE public.sync_node ADD COLUMN register_id UUID "
        "REFERENCES public.register(id) ON DELETE RESTRICT"
    )
    op.execute("ALTER TABLE public.sync_node DROP CONSTRAINT sync_node_mode_check")
    op.execute("ALTER TABLE public.sync_node DROP CONSTRAINT ck_sync_node_kind_mode_credential")
    op.execute("""
        ALTER TABLE public.sync_node
        ADD CONSTRAINT ck_sync_node_mode
          CHECK (mode IN ('cloud_writer','shadow_readonly','edge_writer')),
        ADD CONSTRAINT ck_sync_node_kind_mode_credential CHECK (
          (
            node_kind = 'cloud'
            AND mode = 'cloud_writer'
            AND register_id IS NULL
            AND credential_kid IS NULL
            AND credential_hash IS NULL
            AND credential_expires_at IS NULL
          )
          OR
          (
            node_kind = 'edge'
            AND mode IN ('shadow_readonly','edge_writer')
            AND (mode <> 'edge_writer' OR register_id IS NOT NULL)
            AND credential_kid IS NOT NULL
            AND credential_hash ~ '^[0-9a-f]{64}$'
            AND credential_expires_at IS NOT NULL
          )
        )
        """)
    op.execute("""
        CREATE UNIQUE INDEX uq_sync_node_active_edge_writer_branch
        ON public.sync_node (tenant_id, branch_id)
        WHERE node_kind = 'edge' AND mode = 'edge_writer' AND status = 'active'
        """)
    op.execute("""
        CREATE UNIQUE INDEX uq_sync_node_active_edge_writer_register
        ON public.sync_node (tenant_id, register_id)
        WHERE node_kind = 'edge' AND mode = 'edge_writer' AND status = 'active'
        """)


def _extend_shadow_report() -> None:
    op.execute("ALTER TABLE public.sync_shadow_report ADD COLUMN source_checksum TEXT")
    op.execute("ALTER TABLE public.sync_shadow_report ADD COLUMN expected_source_checksum TEXT")
    op.execute(
        "ALTER TABLE public.sync_shadow_report "
        "ADD COLUMN source_verified BOOLEAN NOT NULL DEFAULT false"
    )
    op.execute(
        f"UPDATE public.sync_shadow_report SET "
        f"source_checksum = '{ZERO_CHECKSUM}', expected_source_checksum = '{ZERO_CHECKSUM}'"
    )
    op.execute("ALTER TABLE public.sync_shadow_report ALTER COLUMN source_checksum SET NOT NULL")
    op.execute(
        "ALTER TABLE public.sync_shadow_report "
        "ALTER COLUMN expected_source_checksum SET NOT NULL"
    )
    op.execute("""
        ALTER TABLE public.sync_shadow_report
        ADD CONSTRAINT ck_sync_shadow_report_source_checksum
          CHECK (source_checksum ~ '^[0-9a-f]{64}$'),
        ADD CONSTRAINT ck_sync_shadow_report_expected_source_checksum
          CHECK (expected_source_checksum ~ '^[0-9a-f]{64}$')
        """)
    op.execute("""
        GRANT INSERT (source_checksum, expected_source_checksum, source_verified)
        ON TABLE public.sync_shadow_report TO aurum_app
        """)


def _create_writer_epoch() -> None:
    op.execute("""
        CREATE TABLE public.sync_writer_epoch (
          tenant_id                      UUID NOT NULL
                                         REFERENCES public.tenant(id) ON DELETE CASCADE,
          branch_id                      UUID NOT NULL
                                         REFERENCES public.branch(id) ON DELETE RESTRICT,
          writer_epoch                   BIGINT NOT NULL CHECK (writer_epoch > 0),
          activation_id                  UUID NOT NULL UNIQUE,
          writer_node_id                 UUID NOT NULL
                                         REFERENCES public.sync_node(id) ON DELETE RESTRICT,
          allowed_register_id            UUID
                                         REFERENCES public.register(id) ON DELETE RESTRICT,
          capability                     TEXT NOT NULL
                                         CHECK (capability IN ('cloud_full','cash_sale_v1')),
          state                          TEXT NOT NULL
                                         CHECK (state IN ('prepared','active','fenced')),
          root_source_checksum           TEXT NOT NULL
                                         CHECK (root_source_checksum ~ '^[0-9a-f]{64}$'),
          root_projection_checksum       TEXT NOT NULL
                                         CHECK (root_projection_checksum ~ '^[0-9a-f]{64}$'),
          last_sequence                  BIGINT NOT NULL DEFAULT 0
                                         CHECK (last_sequence >= 0),
          current_source_checksum        TEXT NOT NULL
                                         CHECK (current_source_checksum ~ '^[0-9a-f]{64}$'),
          current_projection_checksum    TEXT NOT NULL
                                         CHECK (current_projection_checksum ~ '^[0-9a-f]{64}$'),
          previous_writer_epoch          BIGINT,
          previous_terminal_source_checksum TEXT,
          previous_terminal_projection_checksum TEXT,
          bootstrap_snapshot_hash        TEXT NOT NULL
                                         CHECK (bootstrap_snapshot_hash ~ '^[0-9a-f]{64}$'),
          activation_manifest_hash       TEXT NOT NULL
                                         CHECK (activation_manifest_hash ~ '^[0-9a-f]{64}$'),
          receipt_baseline_seq            BIGINT NOT NULL DEFAULT 0
                                         CHECK (receipt_baseline_seq >= 0),
          prepared_at                    TIMESTAMPTZ NOT NULL,
          activated_at                   TIMESTAMPTZ,
          fenced_at                      TIMESTAMPTZ,
          created_at                     TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_by                     UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
          updated_at                     TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_by                     UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
          PRIMARY KEY (tenant_id, branch_id, writer_epoch),
          CONSTRAINT uq_sync_writer_epoch_activation_scope
            UNIQUE (activation_id, tenant_id, branch_id, writer_epoch),
          CONSTRAINT ck_sync_writer_epoch_writer CHECK (
            (capability = 'cloud_full' AND allowed_register_id IS NULL)
            OR (capability = 'cash_sale_v1' AND allowed_register_id IS NOT NULL)
          ),
          CONSTRAINT ck_sync_writer_epoch_previous CHECK (
            (
              previous_writer_epoch IS NULL
              AND previous_terminal_source_checksum IS NULL
              AND previous_terminal_projection_checksum IS NULL
            )
            OR (
              previous_writer_epoch = writer_epoch - 1
              AND previous_terminal_source_checksum ~ '^[0-9a-f]{64}$'
              AND previous_terminal_projection_checksum ~ '^[0-9a-f]{64}$'
            )
          ),
          CONSTRAINT ck_sync_writer_epoch_state_time CHECK (
            (state = 'prepared' AND activated_at IS NULL AND fenced_at IS NULL)
            OR (state = 'active' AND activated_at IS NOT NULL AND fenced_at IS NULL)
            OR (state = 'fenced' AND activated_at IS NOT NULL AND fenced_at IS NOT NULL)
          ),
          CONSTRAINT ck_sync_writer_epoch_root_position CHECK (
            last_sequence <> 0
            OR (
              current_source_checksum = root_source_checksum
              AND current_projection_checksum = root_projection_checksum
            )
          )
        )
        """)
    op.execute("""
        ALTER TABLE public.sync_writer_epoch
        ADD CONSTRAINT fk_sync_writer_epoch_previous
        FOREIGN KEY (tenant_id, branch_id, previous_writer_epoch)
        REFERENCES public.sync_writer_epoch (tenant_id, branch_id, writer_epoch)
        """)
    op.execute("""
        CREATE UNIQUE INDEX uq_sync_writer_epoch_active_branch
        ON public.sync_writer_epoch (tenant_id, branch_id)
        WHERE state = 'active'
        """)
    op.execute("""
        CREATE UNIQUE INDEX uq_sync_writer_epoch_prepared_branch
        ON public.sync_writer_epoch (tenant_id, branch_id)
        WHERE state = 'prepared'
        """)
    op.execute(f"""
        INSERT INTO public.sync_writer_epoch (
          tenant_id,
          branch_id,
          writer_epoch,
          activation_id,
          writer_node_id,
          allowed_register_id,
          capability,
          state,
          root_source_checksum,
          root_projection_checksum,
          last_sequence,
          current_source_checksum,
          current_projection_checksum,
          bootstrap_snapshot_hash,
          activation_manifest_hash,
          receipt_baseline_seq,
          prepared_at,
          activated_at
        )
        SELECT
          sync_stream.tenant_id,
          sync_stream.branch_id,
          sync_stream.writer_epoch,
          gen_random_uuid(),
          sync_stream.writer_node_id,
          NULL,
          'cloud_full',
          'active',
          '{ZERO_CHECKSUM}',
          '{ZERO_CHECKSUM}',
          sync_stream.last_sequence,
          sync_stream.current_checksum,
          sync_stream.current_projection_checksum,
          '{ZERO_CHECKSUM}',
          '{ZERO_CHECKSUM}',
          0,
          sync_stream.created_at,
          sync_stream.created_at
        FROM public.sync_stream AS sync_stream
        """)
    _meta_and_audit_triggers("sync_writer_epoch")
    _branch_rls("sync_writer_epoch")
    op.execute("""
        ALTER TABLE public.sync_stream
        ADD CONSTRAINT fk_sync_stream_writer_epoch
        FOREIGN KEY (tenant_id, branch_id, writer_epoch)
        REFERENCES public.sync_writer_epoch (tenant_id, branch_id, writer_epoch)
        DEFERRABLE INITIALLY DEFERRED
        """)
    op.execute(SYNC_STREAM_EPOCH_LEDGER_SQL)
    op.execute("ALTER FUNCTION public.trg_sync_stream_epoch_ledger() OWNER TO aurum_support")
    op.execute(
        "REVOKE ALL PRIVILEGES ON FUNCTION public.trg_sync_stream_epoch_ledger() "
        "FROM PUBLIC, aurum_app"
    )
    op.execute("""
        CREATE TRIGGER trg_sync_stream_epoch_ledger
        AFTER INSERT OR UPDATE OF
          writer_node_id,
          writer_epoch,
          last_sequence,
          current_checksum,
          current_projection_checksum
        ON public.sync_stream
        FOR EACH ROW EXECUTE FUNCTION public.trg_sync_stream_epoch_ledger()
        """)


def _create_writer_readiness() -> None:
    op.execute("""
        CREATE TABLE public.sync_writer_readiness (
          activation_id                 UUID PRIMARY KEY,
          tenant_id                     UUID NOT NULL
                                        REFERENCES public.tenant(id) ON DELETE CASCADE,
          branch_id                     UUID NOT NULL
                                        REFERENCES public.branch(id) ON DELETE RESTRICT,
          edge_node_id                  UUID NOT NULL
                                        REFERENCES public.sync_node(id) ON DELETE RESTRICT,
          register_id                   UUID NOT NULL
                                        REFERENCES public.register(id) ON DELETE RESTRICT,
          writer_epoch                  BIGINT NOT NULL CHECK (writer_epoch > 0),
          previous_sequence             BIGINT NOT NULL CHECK (previous_sequence >= 0),
          previous_source_checksum      TEXT NOT NULL
                                        CHECK (previous_source_checksum ~ '^[0-9a-f]{64}$'),
          previous_projection_checksum  TEXT NOT NULL
                                        CHECK (previous_projection_checksum ~ '^[0-9a-f]{64}$'),
          bootstrap_snapshot_hash       TEXT NOT NULL
                                        CHECK (bootstrap_snapshot_hash ~ '^[0-9a-f]{64}$'),
          activation_manifest_hash      TEXT NOT NULL
                                        CHECK (activation_manifest_hash ~ '^[0-9a-f]{64}$'),
          receipt_baseline_seq           BIGINT NOT NULL CHECK (receipt_baseline_seq >= 0),
          request_hash                  TEXT NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
          reported_at                   TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_at                    TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_by                    UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
          updated_at                    TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_by                    UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
          CONSTRAINT uq_sync_writer_readiness_scope
            UNIQUE (tenant_id, branch_id, writer_epoch),
          CONSTRAINT fk_sync_writer_readiness_epoch
            FOREIGN KEY (activation_id, tenant_id, branch_id, writer_epoch)
            REFERENCES public.sync_writer_epoch (
              activation_id,
              tenant_id,
              branch_id,
              writer_epoch
            )
            ON DELETE RESTRICT
        )
        """)
    _meta_and_audit_triggers("sync_writer_readiness")
    _branch_rls("sync_writer_readiness")


def _create_receipt_counter() -> None:
    op.execute("""
        CREATE TABLE public.register_receipt_counter (
          tenant_id        UUID NOT NULL
                           REFERENCES public.tenant(id) ON DELETE CASCADE,
          branch_id        UUID NOT NULL
                           REFERENCES public.branch(id) ON DELETE RESTRICT,
          register_id      UUID NOT NULL
                           REFERENCES public.register(id) ON DELETE RESTRICT,
          writer_epoch     BIGINT NOT NULL CHECK (writer_epoch > 0),
          last_receipt_seq BIGINT NOT NULL DEFAULT 0 CHECK (last_receipt_seq >= 0),
          created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_by       UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
          updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_by       UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
          PRIMARY KEY (tenant_id, register_id),
          CONSTRAINT uq_register_receipt_counter_register UNIQUE (register_id)
        )
        """)
    op.execute("""
        INSERT INTO public.register_receipt_counter (
          tenant_id,
          branch_id,
          register_id,
          writer_epoch,
          last_receipt_seq
        )
        SELECT
          register.tenant_id,
          register.branch_id,
          register.id,
          COALESCE(sync_stream.writer_epoch, 1),
          GREATEST(
            COALESCE(max(sale.receipt_seq), 0),
            COALESCE(
              max(
                CASE
                  WHEN sale.receipt_number ~ '^[0-9]{1,18}$'
                  THEN sale.receipt_number::BIGINT
                  ELSE 0
                END
              ),
              0
            )
          )
        FROM public.register AS register
        LEFT JOIN public.sync_stream AS sync_stream
          ON sync_stream.tenant_id = register.tenant_id
         AND sync_stream.branch_id = register.branch_id
        LEFT JOIN public.sale AS sale ON sale.register_id = register.id
        GROUP BY
          register.tenant_id,
          register.branch_id,
          register.id,
          sync_stream.writer_epoch
        """)
    _meta_triggers("register_receipt_counter")
    _branch_rls("register_receipt_counter")
    op.execute(ALLOCATE_REGISTER_RECEIPT_SQL)
    op.execute(
        "ALTER FUNCTION public.allocate_register_receipt(UUID, UUID) " "OWNER TO aurum_support"
    )
    op.execute(
        "REVOKE ALL PRIVILEGES ON FUNCTION "
        "public.allocate_register_receipt(UUID, UUID) FROM PUBLIC, aurum_app"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION public.allocate_register_receipt(UUID, UUID) "
        "TO aurum_support, aurum_app"
    )


def _make_cursor_epoch_scoped() -> None:
    op.execute("ALTER TABLE public.sync_cursor DROP CONSTRAINT sync_cursor_pkey")
    op.execute("""
        ALTER TABLE public.sync_cursor
        ADD CONSTRAINT sync_cursor_pkey
        PRIMARY KEY (tenant_id, branch_id, origin_node_id, writer_epoch)
        """)


def upgrade() -> None:
    _extend_sync_node()
    _extend_shadow_report()
    _create_writer_epoch()
    _create_writer_readiness()
    _create_receipt_counter()
    _make_cursor_epoch_scoped()


def downgrade() -> None:
    op.execute("ALTER TABLE public.sync_cursor DROP CONSTRAINT sync_cursor_pkey")
    op.execute("""
        DELETE FROM public.sync_cursor AS older
        USING public.sync_cursor AS newer
        WHERE older.tenant_id = newer.tenant_id
          AND older.branch_id = newer.branch_id
          AND older.origin_node_id = newer.origin_node_id
          AND older.writer_epoch < newer.writer_epoch
        """)
    op.execute("""
        ALTER TABLE public.sync_cursor
        ADD CONSTRAINT sync_cursor_pkey
        PRIMARY KEY (tenant_id, branch_id, origin_node_id)
        """)

    op.execute("DROP FUNCTION public.allocate_register_receipt(UUID, UUID)")
    op.execute("DROP TABLE public.register_receipt_counter")
    op.execute("DROP TABLE public.sync_writer_readiness")
    op.execute("DROP TRIGGER trg_sync_stream_epoch_ledger ON public.sync_stream")
    op.execute("DROP FUNCTION public.trg_sync_stream_epoch_ledger()")
    op.execute("ALTER TABLE public.sync_stream DROP CONSTRAINT fk_sync_stream_writer_epoch")
    op.execute("DROP TABLE public.sync_writer_epoch")

    op.execute("""
        ALTER TABLE public.sync_shadow_report
        DROP CONSTRAINT ck_sync_shadow_report_expected_source_checksum,
        DROP CONSTRAINT ck_sync_shadow_report_source_checksum,
        DROP COLUMN source_verified,
        DROP COLUMN expected_source_checksum,
        DROP COLUMN source_checksum
        """)

    op.execute("DROP INDEX public.uq_sync_node_active_edge_writer_register")
    op.execute("DROP INDEX public.uq_sync_node_active_edge_writer_branch")
    op.execute("ALTER TABLE public.sync_node DROP CONSTRAINT ck_sync_node_kind_mode_credential")
    op.execute("ALTER TABLE public.sync_node DROP CONSTRAINT ck_sync_node_mode")
    op.execute("""
        ALTER TABLE public.sync_node
        ADD CONSTRAINT sync_node_mode_check
          CHECK (mode IN ('cloud_writer','shadow_readonly')),
        ADD CONSTRAINT ck_sync_node_kind_mode_credential CHECK (
          (
            node_kind = 'cloud'
            AND mode = 'cloud_writer'
            AND credential_kid IS NULL
            AND credential_hash IS NULL
            AND credential_expires_at IS NULL
          )
          OR
          (
            node_kind = 'edge'
            AND mode = 'shadow_readonly'
            AND credential_kid IS NOT NULL
            AND credential_hash ~ '^[0-9a-f]{64}$'
            AND credential_expires_at IS NOT NULL
          )
        )
        """)
    op.execute("ALTER TABLE public.sync_node DROP COLUMN register_id")
