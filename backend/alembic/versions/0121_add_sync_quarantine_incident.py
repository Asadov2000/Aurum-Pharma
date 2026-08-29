"""add immutable Edge quarantine incident journal

Revision ID: 0121
Revises: 0120
Create Date: 2026-08-29
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0121"
down_revision: str | Sequence[str] | None = "0120"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


RECORD_INCIDENT_SQL = r"""
CREATE FUNCTION public.record_sync_quarantine_incident(
  p_incident_id UUID,
  p_tenant_id UUID,
  p_branch_id UUID,
  p_edge_node_id UUID,
  p_origin_node_id UUID,
  p_writer_epoch BIGINT,
  p_cursor_status TEXT,
  p_reason_code TEXT,
  p_last_applied_sequence BIGINT,
  p_blocked_sequence BIGINT,
  p_blocked_event_id UUID,
  p_blocked_operation_id UUID,
  p_source_checksum TEXT,
  p_projection_checksum TEXT,
  p_event_type TEXT,
  p_schema_version INTEGER,
  p_observed_at TIMESTAMPTZ,
  p_evidence_hash TEXT,
  p_request_hash TEXT
)
RETURNS TABLE (
  incident_id UUID,
  tenant_id UUID,
  branch_id UUID,
  edge_node_id UUID,
  origin_node_id UUID,
  writer_epoch BIGINT,
  cursor_status TEXT,
  reason_code TEXT,
  last_applied_sequence BIGINT,
  blocked_sequence BIGINT,
  blocked_event_id UUID,
  blocked_operation_id UUID,
  source_checksum TEXT,
  projection_checksum TEXT,
  event_type TEXT,
  schema_version INTEGER,
  observed_at TIMESTAMPTZ,
  evidence_hash TEXT,
  received_at TIMESTAMPTZ,
  replayed BOOLEAN
) AS $$
DECLARE
  v_existing public.sync_quarantine_incident%ROWTYPE;
  v_record public.sync_quarantine_incident%ROWTYPE;
BEGIN
  IF SESSION_USER <> 'aurum_support' AND (
     p_tenant_id IS DISTINCT FROM public.current_tenant_id()
     OR p_branch_id::TEXT IS DISTINCT FROM
        NULLIF(pg_catalog.current_setting('app.branch_id', true), '')
     OR p_edge_node_id::TEXT IS DISTINCT FROM
        NULLIF(pg_catalog.current_setting('app.edge_node_id', true), '')
  ) THEN
    RAISE EXCEPTION 'Edge incident scope is invalid' USING ERRCODE = '42501';
  END IF;

  PERFORM 1
  FROM public.sync_node AS node
  WHERE node.id = p_edge_node_id
    AND node.tenant_id = p_tenant_id
    AND node.branch_id = p_branch_id
    AND node.node_kind = 'edge'
    AND node.status = 'active';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Active Edge node is unavailable' USING ERRCODE = '42501';
  END IF;

  IF p_cursor_status NOT IN ('gap', 'quarantined', 'mismatch')
     OR p_reason_code !~ '^[a-z][a-z0-9_]{2,63}$'
     OR p_writer_epoch <= 0
     OR p_last_applied_sequence < 0
     OR p_source_checksum !~ '^[0-9a-f]{64}$'
     OR p_projection_checksum !~ '^[0-9a-f]{64}$'
     OR p_evidence_hash !~ '^[0-9a-f]{64}$'
     OR p_request_hash !~ '^[0-9a-f]{64}$'
     OR p_observed_at IS NULL
     OR p_observed_at > pg_catalog.statement_timestamp() + INTERVAL '5 minutes'
     OR p_observed_at < pg_catalog.statement_timestamp() - INTERVAL '30 days' THEN
    RAISE EXCEPTION 'Edge incident evidence is invalid' USING ERRCODE = '22023';
  END IF;

  IF (
    p_blocked_event_id IS NULL
    AND (
      p_blocked_operation_id IS NOT NULL OR p_blocked_sequence IS NOT NULL
      OR p_event_type IS NOT NULL OR p_schema_version IS NOT NULL
    )
  ) OR (
    p_blocked_event_id IS NOT NULL
    AND (
      p_blocked_operation_id IS NULL OR p_blocked_sequence IS NULL
      OR p_event_type IS NULL OR p_schema_version IS NULL
    )
  ) THEN
    RAISE EXCEPTION 'Blocked event evidence is incomplete' USING ERRCODE = '22023';
  END IF;

  IF p_blocked_event_id IS NOT NULL THEN
    PERFORM 1
    WHERE EXISTS (
      SELECT 1
      FROM public.sync_outbox AS event
      WHERE event.event_id = p_blocked_event_id
        AND event.operation_id = p_blocked_operation_id
        AND event.tenant_id = p_tenant_id
        AND event.branch_id = p_branch_id
        AND event.origin_node_id = p_origin_node_id
        AND event.writer_epoch = p_writer_epoch
        AND event.sequence = p_blocked_sequence
        AND event.event_type IS NOT DISTINCT FROM p_event_type
        AND event.schema_version IS NOT DISTINCT FROM p_schema_version
    ) OR EXISTS (
      SELECT 1
      FROM public.sync_inbox AS event
      WHERE event.event_id = p_blocked_event_id
        AND event.operation_id = p_blocked_operation_id
        AND event.tenant_id = p_tenant_id
        AND event.branch_id = p_branch_id
        AND event.origin_node_id = p_origin_node_id
        AND event.writer_epoch = p_writer_epoch
        AND event.sequence = p_blocked_sequence
        AND event.event_type IS NOT DISTINCT FROM p_event_type
        AND event.schema_version IS NOT DISTINCT FROM p_schema_version
    );
  ELSE
    PERFORM 1
    WHERE EXISTS (
      SELECT 1
      FROM public.sync_cursor AS cursor
      WHERE cursor.tenant_id = p_tenant_id
        AND cursor.branch_id = p_branch_id
        AND cursor.origin_node_id = p_origin_node_id
        AND cursor.writer_epoch = p_writer_epoch
        AND cursor.status = p_cursor_status
        AND cursor.last_sequence = p_last_applied_sequence
    ) OR EXISTS (
      SELECT 1
      FROM public.sync_stream AS stream
      WHERE stream.tenant_id = p_tenant_id
        AND stream.branch_id = p_branch_id
        AND stream.writer_node_id = p_origin_node_id
        AND stream.writer_epoch = p_writer_epoch
        AND stream.last_sequence >= p_last_applied_sequence
    );
  END IF;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Edge incident is not backed by the sync ledger'
      USING ERRCODE = '23514';
  END IF;

  SELECT * INTO v_existing
  FROM public.sync_quarantine_incident AS incident
  WHERE incident.incident_id = p_incident_id;
  IF FOUND THEN
    IF v_existing.request_hash IS DISTINCT FROM p_request_hash
       OR v_existing.edge_node_id IS DISTINCT FROM p_edge_node_id THEN
      RAISE EXCEPTION 'Incident ID was already used for different evidence'
        USING ERRCODE = '23505';
    END IF;
    RETURN QUERY SELECT
      v_existing.incident_id, v_existing.tenant_id, v_existing.branch_id,
      v_existing.edge_node_id, v_existing.origin_node_id, v_existing.writer_epoch,
      v_existing.cursor_status, v_existing.reason_code,
      v_existing.last_applied_sequence, v_existing.blocked_sequence,
      v_existing.blocked_event_id, v_existing.blocked_operation_id,
      v_existing.source_checksum, v_existing.projection_checksum,
      v_existing.event_type, v_existing.schema_version, v_existing.observed_at,
      v_existing.evidence_hash, v_existing.received_at, true;
    RETURN;
  END IF;

  IF EXISTS (
    SELECT 1 FROM public.sync_quarantine_incident AS incident
    WHERE incident.edge_node_id = p_edge_node_id
      AND incident.evidence_hash = p_evidence_hash
  ) THEN
    RAISE EXCEPTION 'Incident evidence already has another ID' USING ERRCODE = '23505';
  END IF;

  INSERT INTO public.sync_quarantine_incident (
    incident_id, tenant_id, branch_id, edge_node_id, origin_node_id,
    writer_epoch, cursor_status, reason_code, last_applied_sequence,
    blocked_sequence, blocked_event_id, blocked_operation_id, source_checksum,
    projection_checksum, event_type, schema_version, observed_at,
    evidence_hash, request_hash
  ) VALUES (
    p_incident_id, p_tenant_id, p_branch_id, p_edge_node_id, p_origin_node_id,
    p_writer_epoch, p_cursor_status, p_reason_code, p_last_applied_sequence,
    p_blocked_sequence, p_blocked_event_id, p_blocked_operation_id,
    p_source_checksum, p_projection_checksum, p_event_type, p_schema_version,
    p_observed_at, p_evidence_hash, p_request_hash
  ) RETURNING * INTO v_record;

  RETURN QUERY SELECT
    v_record.incident_id, v_record.tenant_id, v_record.branch_id,
    v_record.edge_node_id, v_record.origin_node_id, v_record.writer_epoch,
    v_record.cursor_status, v_record.reason_code, v_record.last_applied_sequence,
    v_record.blocked_sequence, v_record.blocked_event_id,
    v_record.blocked_operation_id, v_record.source_checksum,
    v_record.projection_checksum, v_record.event_type, v_record.schema_version,
    v_record.observed_at, v_record.evidence_hash, v_record.received_at, false;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
"""


def upgrade() -> None:
    op.execute("""
        CREATE TABLE public.sync_quarantine_incident (
          incident_id UUID PRIMARY KEY,
          tenant_id UUID NOT NULL REFERENCES public.tenant(id) ON DELETE RESTRICT,
          branch_id UUID NOT NULL REFERENCES public.branch(id) ON DELETE RESTRICT,
          edge_node_id UUID NOT NULL REFERENCES public.sync_node(id) ON DELETE RESTRICT,
          origin_node_id UUID NOT NULL,
          writer_epoch BIGINT NOT NULL CHECK (writer_epoch > 0),
          cursor_status TEXT NOT NULL CHECK (
            cursor_status IN ('gap', 'quarantined', 'mismatch')
          ),
          reason_code TEXT NOT NULL CHECK (
            reason_code ~ '^[a-z][a-z0-9_]{2,63}$'
          ),
          last_applied_sequence BIGINT NOT NULL CHECK (last_applied_sequence >= 0),
          blocked_sequence BIGINT CHECK (blocked_sequence > 0),
          blocked_event_id UUID,
          blocked_operation_id UUID,
          source_checksum TEXT NOT NULL CHECK (source_checksum ~ '^[0-9a-f]{64}$'),
          projection_checksum TEXT NOT NULL CHECK (
            projection_checksum ~ '^[0-9a-f]{64}$'
          ),
          event_type TEXT CHECK (char_length(event_type) <= 120),
          schema_version INTEGER CHECK (schema_version >= 1),
          observed_at TIMESTAMPTZ NOT NULL,
          evidence_hash TEXT NOT NULL CHECK (evidence_hash ~ '^[0-9a-f]{64}$'),
          request_hash TEXT NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
          received_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          created_by UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
          updated_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          updated_by UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
          CHECK (
            (
              blocked_event_id IS NULL
              AND blocked_operation_id IS NULL
              AND blocked_sequence IS NULL
              AND event_type IS NULL
              AND schema_version IS NULL
            ) OR (
              blocked_event_id IS NOT NULL
              AND blocked_operation_id IS NOT NULL
              AND blocked_sequence IS NOT NULL
              AND event_type IS NOT NULL
              AND schema_version IS NOT NULL
            )
          )
        )
        """)
    op.execute("""
        CREATE UNIQUE INDEX uq_sync_quarantine_node_evidence
        ON public.sync_quarantine_incident(edge_node_id, evidence_hash)
        """)
    op.execute("""
        CREATE INDEX ix_sync_quarantine_node_received
        ON public.sync_quarantine_incident(edge_node_id, received_at DESC, incident_id)
        """)
    op.execute("ALTER TABLE public.sync_quarantine_incident ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation ON public.sync_quarantine_incident
        USING (
          tenant_id = public.current_tenant_id() OR SESSION_USER = 'aurum_support'
        )
        WITH CHECK (
          tenant_id = public.current_tenant_id() OR SESSION_USER = 'aurum_support'
        )
        """)
    op.execute("""
        CREATE FUNCTION public.trg_reject_sync_quarantine_incident_mutation()
        RETURNS TRIGGER AS $$
        BEGIN
          RAISE EXCEPTION 'Sync quarantine incidents are immutable'
            USING ERRCODE = '42501';
        END;
        $$ LANGUAGE plpgsql VOLATILE SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        """)
    op.execute("""
        REVOKE ALL PRIVILEGES ON FUNCTION
          public.trg_reject_sync_quarantine_incident_mutation()
        FROM PUBLIC, aurum_app, aurum_support
        """)
    op.execute("""
        CREATE TRIGGER trg_immutable_sync_quarantine_incident
        BEFORE UPDATE OR DELETE ON public.sync_quarantine_incident
        FOR EACH ROW EXECUTE FUNCTION
          public.trg_reject_sync_quarantine_incident_mutation()
        """)
    op.execute("""
        CREATE FUNCTION public.trg_audit_sync_quarantine_incident()
        RETURNS TRIGGER AS $$
        BEGIN
          INSERT INTO public.audit_log (
            tenant_id, user_id, action, table_name, record_id, metadata, created_at
          ) VALUES (
            NEW.tenant_id, NULL, 'INSERT', 'sync_quarantine_incident',
            NEW.incident_id,
            jsonb_build_object(
              'edge_node_id', NEW.edge_node_id,
              'cursor_status', NEW.cursor_status,
              'reason_code', NEW.reason_code,
              'last_applied_sequence', NEW.last_applied_sequence
            ),
            NEW.received_at
          );
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql VOLATILE SECURITY DEFINER
        SET search_path = pg_catalog, public, pg_temp
        """)
    op.execute("""
        REVOKE ALL PRIVILEGES ON FUNCTION public.trg_audit_sync_quarantine_incident()
        FROM PUBLIC, aurum_app, aurum_support
        """)
    op.execute("""
        CREATE TRIGGER trg_audit_sync_quarantine_incident
        AFTER INSERT ON public.sync_quarantine_incident
        FOR EACH ROW EXECUTE FUNCTION public.trg_audit_sync_quarantine_incident()
        """)
    op.execute(
        "REVOKE ALL PRIVILEGES ON TABLE public.sync_quarantine_incident "
        "FROM PUBLIC, aurum_app, aurum_support"
    )
    op.execute(
        "GRANT SELECT ON TABLE public.sync_quarantine_incident TO aurum_app, aurum_support"
    )
    op.execute(RECORD_INCIDENT_SQL)
    signature = (
        "public.record_sync_quarantine_incident("
        "UUID, UUID, UUID, UUID, UUID, BIGINT, TEXT, TEXT, BIGINT, BIGINT, "
        "UUID, UUID, TEXT, TEXT, TEXT, INTEGER, TIMESTAMPTZ, TEXT, TEXT)"
    )
    op.execute(
        f"REVOKE ALL PRIVILEGES ON FUNCTION {signature} "
        "FROM PUBLIC, aurum_app, aurum_support"
    )
    op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO aurum_app, aurum_support")


def downgrade() -> None:
    op.execute("""
        DROP FUNCTION public.record_sync_quarantine_incident(
          UUID, UUID, UUID, UUID, UUID, BIGINT, TEXT, TEXT, BIGINT, BIGINT,
          UUID, UUID, TEXT, TEXT, TEXT, INTEGER, TIMESTAMPTZ, TEXT, TEXT
        )
        """)
    op.execute(
        "DROP TRIGGER trg_audit_sync_quarantine_incident "
        "ON public.sync_quarantine_incident"
    )
    op.execute("DROP FUNCTION public.trg_audit_sync_quarantine_incident()")
    op.execute(
        "DROP TRIGGER trg_immutable_sync_quarantine_incident "
        "ON public.sync_quarantine_incident"
    )
    op.execute("DROP FUNCTION public.trg_reject_sync_quarantine_incident_mutation()")
    op.execute("DROP TABLE public.sync_quarantine_incident")
