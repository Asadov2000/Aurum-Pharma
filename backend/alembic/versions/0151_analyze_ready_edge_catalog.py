"""Refresh Edge catalog statistics before activating a new snapshot.

Revision ID: 0151
Revises: 0150
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0151"
down_revision: str | Sequence[str] | None = "0150"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
      CREATE FUNCTION public.analyze_ready_sync_read_catalog(
        p_tenant_id UUID,
        p_branch_id UUID,
        p_node_id UUID,
        p_snapshot_id UUID
      ) RETURNS VOID
      LANGUAGE plpgsql
      SECURITY DEFINER
      SET search_path = pg_catalog, pg_temp
      SET row_security = off
      AS $$
      BEGIN
        IF p_tenant_id IS NULL OR p_branch_id IS NULL
           OR p_node_id IS NULL OR p_snapshot_id IS NULL
           OR p_tenant_id IS DISTINCT FROM public.current_tenant_id()
           OR p_branch_id IS DISTINCT FROM
              NULLIF(current_setting('app.branch_id', true), '')::UUID
           OR p_node_id IS DISTINCT FROM
              NULLIF(current_setting('app.edge_node_id', true), '')::UUID
           OR NOT EXISTS (
             SELECT 1
             FROM public.sync_read_projection AS projection
             JOIN public.sync_read_snapshot AS snapshot
               ON snapshot.snapshot_id = projection.snapshot_id
              AND snapshot.tenant_id = projection.tenant_id
              AND snapshot.branch_id = projection.branch_id
              AND snapshot.node_id = projection.node_id
             WHERE projection.snapshot_id = p_snapshot_id
               AND projection.tenant_id = p_tenant_id
               AND projection.branch_id = p_branch_id
               AND projection.node_id = p_node_id
               AND projection.status = 'ready'
               AND snapshot.status = 'ready'
           ) THEN
          RAISE EXCEPTION 'Edge catalog statistics scope is invalid'
            USING ERRCODE = '42501';
        END IF;

        ANALYZE public.sync_read_catalog_projection;
      END;
      $$
    """)
    op.execute("""
      ALTER FUNCTION public.analyze_ready_sync_read_catalog(UUID, UUID, UUID, UUID)
      OWNER TO aurum_schema_owner
    """)
    op.execute("""
      REVOKE ALL PRIVILEGES ON FUNCTION
        public.analyze_ready_sync_read_catalog(UUID, UUID, UUID, UUID)
      FROM PUBLIC, aurum_support
    """)
    op.execute("""
      GRANT EXECUTE ON FUNCTION
        public.analyze_ready_sync_read_catalog(UUID, UUID, UUID, UUID)
      TO aurum_app
    """)


def downgrade() -> None:
    op.execute(
        "DROP FUNCTION public.analyze_ready_sync_read_catalog(UUID, UUID, UUID, UUID)"
    )
