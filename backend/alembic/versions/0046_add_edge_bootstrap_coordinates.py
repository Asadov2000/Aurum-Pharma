"""sync: bind Edge enrollment to an immutable bootstrap checkpoint

Revision ID: 0046
Revises: 0045
Create Date: 2026-07-15

The enrollment checkpoint now records the exact writer identity and epoch.
Machine authentication returns that immutable scope together with the epoch
roots needed to verify a chunked bootstrap from the first event.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0046"
down_revision: str | Sequence[str] | None = "0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


AUTHENTICATE_EDGE_NODE_SQL = """
CREATE FUNCTION public.authenticate_edge_node(
  p_credential_kid UUID,
  p_credential_hash TEXT
) RETURNS TABLE(
  node_id UUID,
  tenant_id UUID,
  branch_id UUID,
  credential_issued_at TIMESTAMPTZ,
  credential_expires_at TIMESTAMPTZ,
  shadow_start_origin_node_id UUID,
  shadow_start_writer_epoch BIGINT,
  shadow_root_source_checksum TEXT,
  shadow_root_projection_checksum TEXT,
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
    sync_node.credential_issued_at,
    sync_node.credential_expires_at,
    sync_node.shadow_start_origin_node_id,
    sync_node.shadow_start_writer_epoch,
    writer_epoch.root_source_checksum,
    writer_epoch.root_projection_checksum,
    sync_node.shadow_start_sequence,
    sync_node.shadow_start_checksum,
    sync_node.shadow_start_projection_checksum
  FROM public.sync_node AS sync_node
  JOIN public.sync_writer_epoch AS writer_epoch
    ON writer_epoch.tenant_id = sync_node.tenant_id
   AND writer_epoch.branch_id = sync_node.branch_id
   AND writer_epoch.writer_epoch = sync_node.shadow_start_writer_epoch
   AND writer_epoch.writer_node_id = sync_node.shadow_start_origin_node_id
  WHERE sync_node.node_kind = 'edge'
    AND sync_node.mode IN ('shadow_readonly', 'edge_writer')
    AND sync_node.status = 'active'
    AND sync_node.credential_kid = p_credential_kid
    AND sync_node.credential_hash = p_credential_hash
    AND sync_node.credential_issued_at IS NOT NULL
    AND sync_node.credential_expires_at > pg_catalog.now();

  UPDATE public.sync_node AS sync_node
  SET last_seen_at = pg_catalog.now()
  WHERE sync_node.node_kind = 'edge'
    AND sync_node.mode IN ('shadow_readonly', 'edge_writer')
    AND sync_node.status = 'active'
    AND sync_node.credential_kid = p_credential_kid
    AND sync_node.credential_hash = p_credential_hash
    AND sync_node.credential_issued_at IS NOT NULL
    AND sync_node.credential_expires_at > pg_catalog.now();
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


LEGACY_AUTHENTICATE_EDGE_NODE_SQL = """
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
    AND sync_node.mode IN ('shadow_readonly', 'edge_writer')
    AND sync_node.status = 'active'
    AND sync_node.credential_kid = p_credential_kid
    AND sync_node.credential_hash = p_credential_hash
    AND sync_node.credential_expires_at > pg_catalog.now();

  UPDATE public.sync_node AS sync_node
  SET last_seen_at = pg_catalog.now()
  WHERE sync_node.node_kind = 'edge'
    AND sync_node.mode IN ('shadow_readonly', 'edge_writer')
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


def _secure_authenticate_function() -> None:
    signature = "public.authenticate_edge_node(UUID, TEXT)"
    op.execute(f"ALTER FUNCTION {signature} OWNER TO aurum_support")
    op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO aurum_app")


def upgrade() -> None:
    op.execute("""
        ALTER TABLE public.sync_node
        ADD COLUMN credential_issued_at TIMESTAMPTZ,
        ADD COLUMN shadow_start_origin_node_id UUID,
        ADD COLUMN shadow_start_writer_epoch BIGINT
        """)
    op.execute("""
        WITH enrollment_epoch AS (
          SELECT DISTINCT ON (sync_node.id)
            sync_node.id AS edge_node_id,
            writer_epoch.writer_node_id,
            writer_epoch.writer_epoch
          FROM public.sync_node AS sync_node
          JOIN public.sync_writer_epoch AS writer_epoch
            ON writer_epoch.tenant_id = sync_node.tenant_id
           AND writer_epoch.branch_id = sync_node.branch_id
           AND writer_epoch.activated_at IS NOT NULL
           AND writer_epoch.activated_at <= sync_node.created_at
           AND (
             writer_epoch.fenced_at IS NULL
             OR writer_epoch.fenced_at >= sync_node.created_at
           )
          LEFT JOIN public.sync_outbox AS checkpoint
            ON checkpoint.tenant_id = sync_node.tenant_id
           AND checkpoint.branch_id = sync_node.branch_id
           AND checkpoint.origin_node_id = writer_epoch.writer_node_id
           AND checkpoint.writer_epoch = writer_epoch.writer_epoch
           AND checkpoint.sequence = sync_node.shadow_start_sequence
          WHERE sync_node.node_kind = 'edge'
            AND (
              (
                sync_node.shadow_start_sequence = 0
                AND writer_epoch.root_source_checksum = sync_node.shadow_start_checksum
                AND writer_epoch.root_projection_checksum
                    = sync_node.shadow_start_projection_checksum
              )
              OR (
                sync_node.shadow_start_sequence > 0
                AND checkpoint.stream_checksum = sync_node.shadow_start_checksum
                AND checkpoint.projection_checksum
                    = sync_node.shadow_start_projection_checksum
              )
            )
          ORDER BY sync_node.id, writer_epoch.writer_epoch DESC
        )
        UPDATE public.sync_node AS sync_node
        SET
          credential_issued_at = pg_catalog.clock_timestamp(),
          shadow_start_origin_node_id = enrollment_epoch.writer_node_id,
          shadow_start_writer_epoch = enrollment_epoch.writer_epoch
        FROM enrollment_epoch
        WHERE sync_node.id = enrollment_epoch.edge_node_id
        """)
    op.execute("""
        ALTER TABLE public.sync_node
        ADD CONSTRAINT ck_sync_node_shadow_writer_epoch CHECK (
          shadow_start_writer_epoch IS NULL OR shadow_start_writer_epoch > 0
        ),
        ADD CONSTRAINT ck_sync_node_edge_bootstrap_coordinates CHECK (
          node_kind <> 'edge'
          OR (
            credential_issued_at IS NOT NULL
            AND shadow_start_origin_node_id IS NOT NULL
            AND shadow_start_writer_epoch IS NOT NULL
          )
        ),
        ADD CONSTRAINT fk_sync_node_shadow_writer_epoch
          FOREIGN KEY (
            tenant_id,
            branch_id,
            shadow_start_writer_epoch,
            shadow_start_origin_node_id
          )
          REFERENCES public.sync_writer_epoch (
            tenant_id,
            branch_id,
            writer_epoch,
            writer_node_id
          )
          ON DELETE RESTRICT
        """)

    op.execute("DROP FUNCTION public.authenticate_edge_node(UUID, TEXT)")
    op.execute(AUTHENTICATE_EDGE_NODE_SQL)
    _secure_authenticate_function()


def downgrade() -> None:
    op.execute("DROP FUNCTION public.authenticate_edge_node(UUID, TEXT)")
    op.execute(LEGACY_AUTHENTICATE_EDGE_NODE_SQL)
    _secure_authenticate_function()

    op.execute("""
        ALTER TABLE public.sync_node
        DROP CONSTRAINT fk_sync_node_shadow_writer_epoch,
        DROP CONSTRAINT ck_sync_node_edge_bootstrap_coordinates,
        DROP CONSTRAINT ck_sync_node_shadow_writer_epoch,
        DROP COLUMN shadow_start_writer_epoch,
        DROP COLUMN shadow_start_origin_node_id,
        DROP COLUMN credential_issued_at
        """)
