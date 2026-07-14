"""sync: add sequenced shadow replication foundation

Revision ID: 0042
Revises: 0041
Create Date: 2026-07-14

The Cloud remains the only writer.  This revision adds a rollback-safe stream
sequence, read-only Edge identities, durable inbox/cursor/projection tables and
branch-scoped RLS for machine sync sessions.  Existing outbox rows become the
immutable pre-shadow history; newly enrolled Edge nodes start at the current
stream watermark.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0042"
down_revision: str | Sequence[str] | None = "0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ZERO_CHECKSUM = "0" * 64

BRANCH_SCOPED_POLICY = """
tenant_id = public.current_tenant_id()
AND (
  NULLIF(pg_catalog.current_setting('app.edge_node_id', true), '') IS NULL
  OR branch_id = NULLIF(
    pg_catalog.current_setting('app.branch_id', true),
    ''
  )::UUID
)
"""

RESERVE_SYNC_EVENT_POSITION_SQL = f"""
CREATE FUNCTION public.reserve_sync_event_position(
  p_tenant_id UUID,
  p_branch_id UUID
) RETURNS TABLE(
  stream_id UUID,
  origin_node_id UUID,
  writer_epoch BIGINT,
  sequence BIGINT,
  previous_checksum TEXT,
  previous_projection_checksum TEXT
) AS $$
DECLARE
  v_stream_id UUID;
  v_origin_node_id UUID;
  v_writer_epoch BIGINT;
  v_previous_checksum TEXT;
  v_previous_projection_checksum TEXT;
  v_sequence BIGINT;
BEGIN
  IF p_tenant_id IS NULL
    OR p_branch_id IS NULL
    OR (
      p_tenant_id IS DISTINCT FROM public.current_tenant_id()
      AND SESSION_USER <> 'aurum_support'
    )
  THEN
    RAISE EXCEPTION 'Invalid sync stream scope'
      USING ERRCODE = '42501';
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM public.branch AS branch
    WHERE branch.id = p_branch_id
      AND branch.tenant_id = p_tenant_id
  ) THEN
    RAISE EXCEPTION 'Sync stream branch not found'
      USING ERRCODE = '22023';
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
    p_branch_id,
    'cloud',
    'cloud_writer',
    'active',
    'Cloud writer'
  )
  ON CONFLICT (tenant_id, branch_id)
    WHERE node_kind = 'cloud'
  DO NOTHING;

  SELECT sync_node.id
  INTO v_origin_node_id
  FROM public.sync_node AS sync_node
  WHERE sync_node.tenant_id = p_tenant_id
    AND sync_node.branch_id = p_branch_id
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
    p_branch_id,
    v_origin_node_id,
    1,
    0,
    '{ZERO_CHECKSUM}',
    '{ZERO_CHECKSUM}'
  )
  ON CONFLICT (tenant_id, branch_id) DO NOTHING;

  SELECT
    sync_stream.id,
    sync_stream.writer_node_id,
    sync_stream.writer_epoch,
    sync_stream.current_checksum,
    sync_stream.current_projection_checksum
  INTO
    v_stream_id,
    v_origin_node_id,
    v_writer_epoch,
    v_previous_checksum,
    v_previous_projection_checksum
  FROM public.sync_stream AS sync_stream
  WHERE sync_stream.tenant_id = p_tenant_id
    AND sync_stream.branch_id = p_branch_id
  FOR UPDATE;

  IF v_stream_id IS NULL THEN
    RAISE EXCEPTION 'Sync stream is unavailable'
      USING ERRCODE = '55000';
  END IF;

  UPDATE public.sync_stream AS sync_stream
  SET last_sequence = sync_stream.last_sequence + 1
  WHERE sync_stream.id = v_stream_id
  RETURNING sync_stream.last_sequence INTO v_sequence;

  RETURN QUERY
  SELECT
    v_stream_id,
    v_origin_node_id,
    v_writer_epoch,
    v_sequence,
    v_previous_checksum,
    v_previous_projection_checksum;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""

FINALIZE_SYNC_EVENT_POSITION_SQL = """
CREATE FUNCTION public.finalize_sync_event_position(
  p_stream_id UUID,
  p_sequence BIGINT,
  p_checksum TEXT,
  p_projection_checksum TEXT
) RETURNS VOID AS $$
DECLARE
  v_updated INTEGER;
BEGIN
  IF p_stream_id IS NULL
    OR p_sequence <= 0
    OR p_checksum !~ '^[0-9a-f]{64}$'
    OR p_projection_checksum !~ '^[0-9a-f]{64}$'
  THEN
    RAISE EXCEPTION 'Invalid sync stream checkpoint'
      USING ERRCODE = '22023';
  END IF;

  UPDATE public.sync_stream AS sync_stream
  SET
    current_checksum = p_checksum,
    current_projection_checksum = p_projection_checksum
  WHERE sync_stream.id = p_stream_id
    AND (
      sync_stream.tenant_id = public.current_tenant_id()
      OR SESSION_USER = 'aurum_support'
    )
    AND sync_stream.last_sequence = p_sequence;

  GET DIAGNOSTICS v_updated = ROW_COUNT;
  IF v_updated <> 1 THEN
    RAISE EXCEPTION 'Sync stream checkpoint conflict'
      USING ERRCODE = '40001';
  END IF;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""

AUTHENTICATE_EDGE_NODE_SQL = """
CREATE FUNCTION public.authenticate_edge_node(
  p_credential_kid UUID,
  p_credential_hash TEXT
) RETURNS TABLE(
  node_id UUID,
  tenant_id UUID,
  branch_id UUID,
  shadow_start_sequence BIGINT,
  shadow_start_checksum TEXT,
  shadow_start_projection_checksum TEXT
) AS $$
BEGIN
  IF p_credential_kid IS NULL
    OR p_credential_hash !~ '^[0-9a-f]{64}$'
  THEN
    RETURN;
  END IF;

  RETURN QUERY
  SELECT
    sync_node.id,
    sync_node.tenant_id,
    sync_node.branch_id,
    sync_node.shadow_start_sequence,
    sync_node.shadow_start_checksum,
    sync_node.shadow_start_projection_checksum
  FROM public.sync_node AS sync_node
  WHERE sync_node.node_kind = 'edge'
    AND sync_node.mode = 'shadow_readonly'
    AND sync_node.status = 'active'
    AND sync_node.credential_kid = p_credential_kid
    AND sync_node.credential_hash = p_credential_hash
    AND sync_node.credential_expires_at > pg_catalog.now();

  UPDATE public.sync_node AS sync_node
  SET last_seen_at = pg_catalog.now()
  WHERE sync_node.node_kind = 'edge'
    AND sync_node.mode = 'shadow_readonly'
    AND sync_node.status = 'active'
    AND sync_node.credential_kid = p_credential_kid
    AND sync_node.credential_hash = p_credential_hash
    AND sync_node.credential_expires_at > pg_catalog.now();
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


def _create_meta_triggers(table: str) -> None:
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


def _enable_branch_rls(table: str) -> None:
    op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"""
        CREATE POLICY tenant_isolation ON public.{table}
        USING ({BRANCH_SCOPED_POLICY})
        WITH CHECK ({BRANCH_SCOPED_POLICY})
        """)


def _configure_function(function: str) -> None:
    op.execute(f"ALTER FUNCTION {function} OWNER TO aurum_support")
    op.execute(f"REVOKE ALL PRIVILEGES ON FUNCTION {function} FROM PUBLIC, aurum_app")
    op.execute(f"GRANT EXECUTE ON FUNCTION {function} TO aurum_support, aurum_app")


def _create_nodes_and_streams() -> None:
    op.execute(f"""
        CREATE TABLE public.sync_node (
          id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id               UUID NOT NULL
                                  REFERENCES public.tenant(id) ON DELETE CASCADE,
          branch_id               UUID NOT NULL
                                  REFERENCES public.branch(id) ON DELETE CASCADE,
          node_kind               TEXT NOT NULL
                                  CHECK (node_kind IN ('cloud','edge')),
          mode                    TEXT NOT NULL
                                  CHECK (mode IN ('cloud_writer','shadow_readonly')),
          status                  TEXT NOT NULL DEFAULT 'active'
                                  CHECK (status IN ('active','revoked')),
          display_name            TEXT NOT NULL CHECK (btrim(display_name) <> ''),
          credential_kid          UUID,
          credential_hash         TEXT,
          credential_expires_at   TIMESTAMPTZ,
          shadow_start_sequence   BIGINT NOT NULL DEFAULT 0
                                  CHECK (shadow_start_sequence >= 0),
          shadow_start_checksum   TEXT NOT NULL DEFAULT '{ZERO_CHECKSUM}'
                                  CHECK (shadow_start_checksum ~ '^[0-9a-f]{{64}}$'),
          shadow_start_projection_checksum TEXT NOT NULL DEFAULT '{ZERO_CHECKSUM}'
                                  CHECK (
                                    shadow_start_projection_checksum
                                    ~ '^[0-9a-f]{{64}}$'
                                  ),
          last_seen_at            TIMESTAMPTZ,
          created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_by              UUID
                                  REFERENCES public.app_user(id) ON DELETE SET NULL,
          updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_by              UUID
                                  REFERENCES public.app_user(id) ON DELETE SET NULL,
          CONSTRAINT ck_sync_node_kind_mode_credential CHECK (
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
              AND credential_hash ~ '^[0-9a-f]{{64}}$'
              AND credential_expires_at IS NOT NULL
            )
          )
        )
        """)
    op.execute("""
        CREATE UNIQUE INDEX uq_sync_node_cloud_branch
        ON public.sync_node (tenant_id, branch_id)
        WHERE node_kind = 'cloud'
        """)
    op.execute("""
        CREATE UNIQUE INDEX uq_sync_node_credential_kid
        ON public.sync_node (credential_kid)
        WHERE credential_kid IS NOT NULL
        """)
    op.execute("""
        CREATE UNIQUE INDEX uq_sync_node_credential_hash
        ON public.sync_node (credential_hash)
        WHERE credential_hash IS NOT NULL
        """)
    _create_meta_triggers("sync_node")
    _enable_branch_rls("sync_node")
    op.execute("REVOKE ALL PRIVILEGES ON TABLE public.sync_node FROM PUBLIC, aurum_app")

    op.execute("""
        INSERT INTO public.sync_node (
          tenant_id,
          branch_id,
          node_kind,
          mode,
          status,
          display_name
        )
        SELECT
          branch.tenant_id,
          branch.id,
          'cloud',
          'cloud_writer',
          'active',
          'Cloud writer'
        FROM public.branch AS branch
        ON CONFLICT (tenant_id, branch_id)
          WHERE node_kind = 'cloud'
        DO NOTHING
        """)

    op.execute(f"""
        CREATE TABLE public.sync_stream (
          id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id         UUID NOT NULL
                            REFERENCES public.tenant(id) ON DELETE CASCADE,
          branch_id         UUID NOT NULL
                            REFERENCES public.branch(id) ON DELETE CASCADE,
          writer_node_id    UUID NOT NULL
                            REFERENCES public.sync_node(id) ON DELETE RESTRICT,
          writer_epoch      BIGINT NOT NULL DEFAULT 1 CHECK (writer_epoch > 0),
          last_sequence     BIGINT NOT NULL DEFAULT 0 CHECK (last_sequence >= 0),
          current_checksum  TEXT NOT NULL DEFAULT '{ZERO_CHECKSUM}'
                            CHECK (current_checksum ~ '^[0-9a-f]{{64}}$'),
          current_projection_checksum TEXT NOT NULL DEFAULT '{ZERO_CHECKSUM}'
                            CHECK (current_projection_checksum ~ '^[0-9a-f]{{64}}$'),
          created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_by        UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
          updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_by        UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
          CONSTRAINT uq_sync_stream_branch UNIQUE (tenant_id, branch_id)
        )
        """)
    _create_meta_triggers("sync_stream")
    _enable_branch_rls("sync_stream")
    op.execute("REVOKE ALL PRIVILEGES ON TABLE public.sync_stream FROM PUBLIC, aurum_app")
    op.execute("GRANT SELECT ON TABLE public.sync_stream TO aurum_app")
    op.execute(f"""
        INSERT INTO public.sync_stream (
          tenant_id,
          branch_id,
          writer_node_id,
          writer_epoch,
          last_sequence,
          current_checksum,
          current_projection_checksum
        )
        SELECT
          sync_node.tenant_id,
          sync_node.branch_id,
          sync_node.id,
          1,
          0,
          '{ZERO_CHECKSUM}',
          '{ZERO_CHECKSUM}'
        FROM public.sync_node AS sync_node
        WHERE sync_node.node_kind = 'cloud'
        ON CONFLICT (tenant_id, branch_id) DO NOTHING
        """)


def _extend_outbox() -> None:
    op.execute("ALTER TABLE public.sync_outbox ADD COLUMN origin_node_id UUID")
    op.execute(
        "ALTER TABLE public.sync_outbox " "ADD COLUMN writer_epoch BIGINT NOT NULL DEFAULT 1"
    )
    op.execute("ALTER TABLE public.sync_outbox ADD COLUMN sequence BIGINT")
    op.execute("ALTER TABLE public.sync_outbox ADD COLUMN occurred_at TIMESTAMPTZ")
    op.execute("ALTER TABLE public.sync_outbox ADD COLUMN stream_checksum TEXT")
    op.execute("ALTER TABLE public.sync_outbox ADD COLUMN projection_hash TEXT")
    op.execute("ALTER TABLE public.sync_outbox ADD COLUMN projection_checksum TEXT")
    op.execute("""
        WITH ranked AS (
          SELECT
            sync_outbox.event_id,
            row_number() OVER (
              PARTITION BY sync_outbox.tenant_id, sync_outbox.branch_id
              ORDER BY sync_outbox.created_at, sync_outbox.event_id
            ) AS sequence
          FROM public.sync_outbox
        )
        UPDATE public.sync_outbox AS sync_outbox
        SET
          origin_node_id = sync_node.id,
          writer_epoch = 1,
          sequence = ranked.sequence,
          occurred_at = sync_outbox.created_at
        FROM ranked, public.sync_node AS sync_node
        WHERE ranked.event_id = sync_outbox.event_id
          AND sync_node.tenant_id = sync_outbox.tenant_id
          AND sync_node.branch_id = sync_outbox.branch_id
          AND sync_node.node_kind = 'cloud'
        """)
    op.execute("""
        UPDATE public.sync_stream AS sync_stream
        SET last_sequence = counts.last_sequence
        FROM (
          SELECT
            sync_outbox.tenant_id,
            sync_outbox.branch_id,
            max(sync_outbox.sequence) AS last_sequence
          FROM public.sync_outbox
          GROUP BY sync_outbox.tenant_id, sync_outbox.branch_id
        ) AS counts
        WHERE counts.tenant_id = sync_stream.tenant_id
          AND counts.branch_id = sync_stream.branch_id
        """)
    op.execute("ALTER TABLE public.sync_outbox ALTER COLUMN origin_node_id SET NOT NULL")
    op.execute("ALTER TABLE public.sync_outbox ALTER COLUMN sequence SET NOT NULL")
    op.execute("ALTER TABLE public.sync_outbox ALTER COLUMN occurred_at SET NOT NULL")
    op.execute("""
        ALTER TABLE public.sync_outbox
        ADD CONSTRAINT fk_sync_outbox_origin_node
        FOREIGN KEY (origin_node_id)
        REFERENCES public.sync_node(id)
        ON DELETE RESTRICT
        """)
    op.execute("""
        ALTER TABLE public.sync_outbox
        ADD CONSTRAINT ck_sync_outbox_writer_epoch CHECK (writer_epoch > 0),
        ADD CONSTRAINT ck_sync_outbox_sequence CHECK (sequence > 0),
        ADD CONSTRAINT ck_sync_outbox_stream_checksum CHECK (
          stream_checksum IS NULL OR stream_checksum ~ '^[0-9a-f]{64}$'
        ),
        ADD CONSTRAINT ck_sync_outbox_projection_hash CHECK (
          projection_hash IS NULL OR projection_hash ~ '^[0-9a-f]{64}$'
        ),
        ADD CONSTRAINT ck_sync_outbox_projection_checksum CHECK (
          projection_checksum IS NULL OR projection_checksum ~ '^[0-9a-f]{64}$'
        )
        """)
    op.execute("""
        ALTER TABLE public.sync_outbox
        DROP CONSTRAINT sync_outbox_aggregate_id_fkey
        """)
    op.execute("""
        CREATE UNIQUE INDEX uq_sync_outbox_stream_sequence
        ON public.sync_outbox (origin_node_id, writer_epoch, sequence)
        """)
    op.execute("""
        CREATE INDEX ix_sync_outbox_branch_pull
        ON public.sync_outbox (
          tenant_id,
          branch_id,
          origin_node_id,
          writer_epoch,
          sequence
        )
        """)
    op.execute("DROP POLICY tenant_isolation ON public.sync_outbox")
    op.execute(f"""
        CREATE POLICY tenant_isolation ON public.sync_outbox
        USING ({BRANCH_SCOPED_POLICY})
        WITH CHECK ({BRANCH_SCOPED_POLICY})
        """)
    op.execute("""
        GRANT INSERT (
          origin_node_id,
          writer_epoch,
          sequence,
          occurred_at,
          stream_checksum,
          projection_hash,
          projection_checksum
        ) ON TABLE public.sync_outbox TO aurum_app
        """)


def _create_inbox_and_cursor() -> None:
    op.execute("""
        CREATE TABLE public.sync_inbox (
          event_id          UUID PRIMARY KEY,
          tenant_id         UUID NOT NULL,
          branch_id         UUID NOT NULL,
          origin_node_id    UUID NOT NULL,
          writer_epoch      BIGINT NOT NULL CHECK (writer_epoch > 0),
          sequence          BIGINT NOT NULL CHECK (sequence > 0),
          event_type        TEXT NOT NULL CHECK (btrim(event_type) <> ''),
          schema_version    INTEGER NOT NULL CHECK (schema_version > 0),
          operation_id      UUID NOT NULL,
          aggregate_type    TEXT NOT NULL CHECK (btrim(aggregate_type) <> ''),
          aggregate_id      UUID NOT NULL,
          occurred_at       TIMESTAMPTZ NOT NULL,
          payload           JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
          payload_hash      TEXT NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
          stream_checksum   TEXT NOT NULL
                            CHECK (stream_checksum ~ '^[0-9a-f]{64}$'),
          projection_hash   TEXT NOT NULL
                            CHECK (projection_hash ~ '^[0-9a-f]{64}$'),
          projection_checksum TEXT NOT NULL
                            CHECK (projection_checksum ~ '^[0-9a-f]{64}$'),
          status            TEXT NOT NULL
                            CHECK (status IN ('received','applied','quarantined')),
          reason_code       TEXT,
          received_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
          applied_at        TIMESTAMPTZ,
          created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_by        UUID,
          updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_by        UUID,
          CONSTRAINT uq_sync_inbox_stream_sequence
            UNIQUE (origin_node_id, writer_epoch, sequence),
          CONSTRAINT ck_sync_inbox_status_reason CHECK (
            (status = 'quarantined' AND reason_code IS NOT NULL)
            OR (status <> 'quarantined' AND reason_code IS NULL)
          )
        )
        """)
    _create_meta_triggers("sync_inbox")
    _enable_branch_rls("sync_inbox")
    op.execute("REVOKE ALL PRIVILEGES ON TABLE public.sync_inbox FROM PUBLIC, aurum_app")
    op.execute("GRANT SELECT ON TABLE public.sync_inbox TO aurum_app")
    op.execute("""
        GRANT INSERT (
          event_id,
          tenant_id,
          branch_id,
          origin_node_id,
          writer_epoch,
          sequence,
          event_type,
          schema_version,
          operation_id,
          aggregate_type,
          aggregate_id,
          occurred_at,
          payload,
          payload_hash,
          stream_checksum,
          projection_hash,
          projection_checksum,
          status,
          reason_code,
          received_at,
          applied_at
        ) ON TABLE public.sync_inbox TO aurum_app
        """)
    op.execute("""
        GRANT UPDATE (status, reason_code, applied_at)
        ON TABLE public.sync_inbox TO aurum_app
        """)

    op.execute(f"""
        CREATE TABLE public.sync_cursor (
          tenant_id           UUID NOT NULL,
          branch_id           UUID NOT NULL,
          origin_node_id      UUID NOT NULL,
          writer_epoch        BIGINT NOT NULL CHECK (writer_epoch > 0),
          start_sequence      BIGINT NOT NULL CHECK (start_sequence >= 0),
          start_source_checksum TEXT NOT NULL
                              CHECK (start_source_checksum ~ '^[0-9a-f]{{64}}$'),
          start_projection_checksum TEXT NOT NULL
                              CHECK (start_projection_checksum ~ '^[0-9a-f]{{64}}$'),
          last_sequence       BIGINT NOT NULL CHECK (last_sequence >= 0),
          last_event_id       UUID,
          source_checksum     TEXT NOT NULL DEFAULT '{ZERO_CHECKSUM}'
                              CHECK (source_checksum ~ '^[0-9a-f]{{64}}$'),
          projection_checksum TEXT NOT NULL DEFAULT '{ZERO_CHECKSUM}'
                              CHECK (projection_checksum ~ '^[0-9a-f]{{64}}$'),
          status              TEXT NOT NULL DEFAULT 'synced'
                              CHECK (status IN ('synced','gap','quarantined','mismatch')),
          created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_by          UUID,
          updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_by          UUID,
          PRIMARY KEY (tenant_id, branch_id, origin_node_id)
        )
        """)
    _create_meta_triggers("sync_cursor")
    _enable_branch_rls("sync_cursor")
    op.execute("REVOKE ALL PRIVILEGES ON TABLE public.sync_cursor FROM PUBLIC, aurum_app")
    op.execute("GRANT SELECT ON TABLE public.sync_cursor TO aurum_app")
    op.execute("""
        GRANT INSERT (
          tenant_id,
          branch_id,
          origin_node_id,
          writer_epoch,
          start_sequence,
          start_source_checksum,
          start_projection_checksum,
          last_sequence,
          last_event_id,
          source_checksum,
          projection_checksum,
          status
        ) ON TABLE public.sync_cursor TO aurum_app
        """)
    op.execute("""
        GRANT UPDATE (
          writer_epoch,
          last_sequence,
          last_event_id,
          source_checksum,
          projection_checksum,
          status
        ) ON TABLE public.sync_cursor TO aurum_app
        """)


def _create_sale_projection() -> None:
    op.execute("""
        CREATE TABLE public.sync_sale_projection (
          sale_id              UUID PRIMARY KEY,
          tenant_id            UUID NOT NULL,
          branch_id            UUID NOT NULL,
          origin_node_id       UUID NOT NULL,
          writer_epoch         BIGINT NOT NULL CHECK (writer_epoch > 0),
          sequence             BIGINT NOT NULL CHECK (sequence > 0),
          source_event_id      UUID NOT NULL UNIQUE,
          operation_id         UUID NOT NULL,
          register_id          UUID NOT NULL,
          shift_id             UUID NOT NULL,
          cashier_user_id      UUID NOT NULL,
          receipt_number       TEXT NOT NULL,
          receipt_seq          BIGINT NOT NULL CHECK (receipt_seq > 0),
          sale_created_at      TIMESTAMPTZ NOT NULL,
          completed_at         TIMESTAMPTZ NOT NULL,
          total_amount         NUMERIC(14,2) NOT NULL CHECK (total_amount >= 0),
          currency             TEXT NOT NULL DEFAULT 'TJS' CHECK (currency = 'TJS'),
          is_test              BOOLEAN NOT NULL DEFAULT false,
          items                JSONB NOT NULL CHECK (jsonb_typeof(items) = 'array'),
          payments             JSONB NOT NULL CHECK (jsonb_typeof(payments) = 'array'),
          source_payload_hash  TEXT NOT NULL
                               CHECK (source_payload_hash ~ '^[0-9a-f]{64}$'),
          projection_hash      TEXT NOT NULL
                               CHECK (projection_hash ~ '^[0-9a-f]{64}$'),
          created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_by           UUID,
          updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_by           UUID,
          CONSTRAINT uq_sync_sale_projection_operation
            UNIQUE (tenant_id, operation_id),
          CONSTRAINT uq_sync_sale_projection_sequence
            UNIQUE (origin_node_id, writer_epoch, sequence)
        )
        """)
    op.execute("""
        CREATE INDEX ix_sync_sale_projection_completed
        ON public.sync_sale_projection (tenant_id, branch_id, completed_at)
        """)
    _create_meta_triggers("sync_sale_projection")
    _enable_branch_rls("sync_sale_projection")
    op.execute("REVOKE ALL PRIVILEGES ON TABLE public.sync_sale_projection FROM PUBLIC, aurum_app")
    op.execute("GRANT SELECT ON TABLE public.sync_sale_projection TO aurum_app")
    op.execute("""
        GRANT INSERT (
          sale_id,
          tenant_id,
          branch_id,
          origin_node_id,
          writer_epoch,
          sequence,
          source_event_id,
          operation_id,
          register_id,
          shift_id,
          cashier_user_id,
          receipt_number,
          receipt_seq,
          sale_created_at,
          completed_at,
          total_amount,
          currency,
          is_test,
          items,
          payments,
          source_payload_hash,
          projection_hash
        ) ON TABLE public.sync_sale_projection TO aurum_app
        """)


def _create_shadow_report() -> None:
    op.execute("""
        CREATE TABLE public.sync_shadow_report (
          report_id            UUID PRIMARY KEY,
          tenant_id            UUID NOT NULL,
          branch_id            UUID NOT NULL,
          edge_node_id         UUID NOT NULL
                               REFERENCES public.sync_node(id) ON DELETE RESTRICT,
          origin_node_id       UUID NOT NULL
                               REFERENCES public.sync_node(id) ON DELETE RESTRICT,
          writer_epoch         BIGINT NOT NULL CHECK (writer_epoch > 0),
          last_sequence        BIGINT NOT NULL CHECK (last_sequence >= 0),
          projection_checksum  TEXT NOT NULL
                               CHECK (projection_checksum ~ '^[0-9a-f]{64}$'),
          expected_checksum    TEXT NOT NULL
                               CHECK (expected_checksum ~ '^[0-9a-f]{64}$'),
          request_hash         TEXT NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
          status               TEXT NOT NULL CHECK (status IN ('matched','mismatch')),
          created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_by           UUID,
          updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_by           UUID
        )
        """)
    op.execute("""
        CREATE INDEX ix_sync_shadow_report_node_created
        ON public.sync_shadow_report (tenant_id, branch_id, edge_node_id, created_at)
        """)
    _create_meta_triggers("sync_shadow_report")
    _enable_branch_rls("sync_shadow_report")
    op.execute("REVOKE ALL PRIVILEGES ON TABLE public.sync_shadow_report FROM PUBLIC, aurum_app")
    op.execute("GRANT SELECT ON TABLE public.sync_shadow_report TO aurum_app")
    op.execute("""
        GRANT INSERT (
          report_id,
          tenant_id,
          branch_id,
          edge_node_id,
          origin_node_id,
          writer_epoch,
          last_sequence,
          projection_checksum,
          expected_checksum,
          request_hash,
          status
        ) ON TABLE public.sync_shadow_report TO aurum_app
        """)


def _create_sync_functions() -> None:
    op.execute(RESERVE_SYNC_EVENT_POSITION_SQL)
    op.execute(FINALIZE_SYNC_EVENT_POSITION_SQL)
    op.execute(AUTHENTICATE_EDGE_NODE_SQL)
    _configure_function("public.reserve_sync_event_position(UUID, UUID)")
    _configure_function("public.finalize_sync_event_position(UUID, BIGINT, TEXT, TEXT)")
    _configure_function("public.authenticate_edge_node(UUID, TEXT)")


def upgrade() -> None:
    _create_nodes_and_streams()
    _extend_outbox()
    _create_inbox_and_cursor()
    _create_sale_projection()
    _create_shadow_report()
    _create_sync_functions()


def downgrade() -> None:
    op.execute("DROP FUNCTION public.authenticate_edge_node(UUID, TEXT)")
    op.execute("DROP FUNCTION public.finalize_sync_event_position(UUID, BIGINT, TEXT, TEXT)")
    op.execute("DROP FUNCTION public.reserve_sync_event_position(UUID, UUID)")

    op.execute("DROP TABLE public.sync_shadow_report")
    op.execute("DROP TABLE public.sync_sale_projection")
    op.execute("DROP TABLE public.sync_cursor")
    op.execute("DROP TABLE public.sync_inbox")

    op.execute("DROP POLICY tenant_isolation ON public.sync_outbox")
    op.execute("""
        CREATE POLICY tenant_isolation ON public.sync_outbox
        USING (tenant_id = public.current_tenant_id())
        WITH CHECK (tenant_id = public.current_tenant_id())
        """)
    op.execute("DROP INDEX public.ix_sync_outbox_branch_pull")
    op.execute("DROP INDEX public.uq_sync_outbox_stream_sequence")
    op.execute("""
        ALTER TABLE public.sync_outbox
        DROP CONSTRAINT ck_sync_outbox_stream_checksum,
        DROP CONSTRAINT ck_sync_outbox_projection_checksum,
        DROP CONSTRAINT ck_sync_outbox_projection_hash,
        DROP CONSTRAINT ck_sync_outbox_sequence,
        DROP CONSTRAINT ck_sync_outbox_writer_epoch,
        DROP CONSTRAINT fk_sync_outbox_origin_node
        """)
    op.execute("ALTER TABLE public.sync_outbox DROP COLUMN stream_checksum")
    op.execute("ALTER TABLE public.sync_outbox DROP COLUMN projection_checksum")
    op.execute("ALTER TABLE public.sync_outbox DROP COLUMN projection_hash")
    op.execute("ALTER TABLE public.sync_outbox DROP COLUMN occurred_at")
    op.execute("ALTER TABLE public.sync_outbox DROP COLUMN sequence")
    op.execute("ALTER TABLE public.sync_outbox DROP COLUMN writer_epoch")
    op.execute("ALTER TABLE public.sync_outbox DROP COLUMN origin_node_id")
    op.execute("""
        ALTER TABLE public.sync_outbox
        ADD CONSTRAINT sync_outbox_aggregate_id_fkey
        FOREIGN KEY (aggregate_id)
        REFERENCES public.sale(id)
        ON DELETE CASCADE
        """)

    op.execute("DROP TABLE public.sync_stream")
    op.execute("DROP TABLE public.sync_node")
