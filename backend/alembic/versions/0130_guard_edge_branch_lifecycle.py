"""guard Edge authentication with branch lifecycle

Revision ID: 0130
Revises: 0129
Create Date: 2026-09-01
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0130"
down_revision: str | Sequence[str] | None = "0129"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SIGNATURE = "UUID, TEXT"
UNCHECKED_NAME = "public.authenticate_edge_node_credential_unchecked"

COUNT_ACTIVE_EDGE_NODES_SQL = r"""
CREATE FUNCTION public.count_active_edge_nodes_for_branch(
  p_tenant_id UUID,
  p_branch_id UUID
) RETURNS BIGINT AS $$
DECLARE
  v_count BIGINT;
BEGIN
  IF p_tenant_id IS NULL
    OR p_branch_id IS NULL
    OR (
      session_user <> 'aurum_support'
      AND p_tenant_id IS DISTINCT FROM public.current_tenant_id()
    )
  THEN
    RAISE EXCEPTION 'Edge node count is unavailable'
      USING ERRCODE = '42501';
  END IF;

  SELECT pg_catalog.count(*)
  INTO v_count
  FROM public.sync_node AS sync_node
  WHERE sync_node.tenant_id = p_tenant_id
    AND sync_node.branch_id = p_branch_id
    AND sync_node.node_kind = 'edge'
    AND sync_node.status = 'active';

  RETURN v_count;
END;
$$ LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""

AUTHENTICATE_EDGE_NODE_SQL = r"""
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
DECLARE
  v_node_id UUID;
  v_now TIMESTAMPTZ := pg_catalog.statement_timestamp();
BEGIN
  IF p_credential_kid IS NULL OR p_credential_hash !~ '^[0-9a-f]{64}$' THEN
    RETURN;
  END IF;

  -- The branch lock is held until the request transaction finishes. Branch
  -- or register deactivation therefore waits for an authenticated Edge request,
  -- while a later request sees the new inactive state and is rejected.
  SELECT sync_node.id
  INTO v_node_id
  FROM public.sync_node AS sync_node
  JOIN public.branch AS branch
    ON branch.id = sync_node.branch_id
   AND branch.tenant_id = sync_node.tenant_id
   AND branch.is_active
  LEFT JOIN public.register AS register
    ON register.id = sync_node.register_id
   AND register.tenant_id = sync_node.tenant_id
   AND register.branch_id = sync_node.branch_id
  WHERE sync_node.node_kind = 'edge'
    AND sync_node.status = 'active'
    AND (
      sync_node.register_id IS NULL
      OR (register.id IS NOT NULL AND register.is_active)
    )
    AND (
      (
        sync_node.credential_kid = p_credential_kid
        AND sync_node.credential_hash = p_credential_hash
        AND sync_node.credential_issued_at IS NOT NULL
        AND sync_node.credential_expires_at > v_now
      )
      OR EXISTS (
        SELECT 1
        FROM public.sync_node_credential_rotation AS rotation
        WHERE rotation.node_id = sync_node.id
          AND rotation.status IN ('pending', 'verified')
          AND rotation.activate_before > v_now
          AND rotation.credential_expires_at > v_now
          AND rotation.credential_kid = p_credential_kid
          AND rotation.credential_hash = p_credential_hash
      )
    )
  ORDER BY sync_node.id
  LIMIT 1
  FOR KEY SHARE OF branch;

  IF v_node_id IS NULL THEN
    RETURN;
  END IF;

  RETURN QUERY
  SELECT authenticated.*
  FROM public.authenticate_edge_node_credential_unchecked(
    p_credential_kid,
    p_credential_hash
  ) AS authenticated
  WHERE authenticated.node_id = v_node_id;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
"""


def _secure_function(name: str, *, grant_to: str) -> None:
    op.execute(f"REVOKE ALL ON FUNCTION {name} FROM PUBLIC, aurum_app, aurum_support")
    op.execute(f"GRANT EXECUTE ON FUNCTION {name} TO {grant_to}")


def upgrade() -> None:
    op.execute(
        "ALTER FUNCTION public.authenticate_edge_node(UUID, TEXT) "
        "RENAME TO authenticate_edge_node_credential_unchecked"
    )
    op.execute(f"REVOKE ALL ON FUNCTION {UNCHECKED_NAME}({SIGNATURE}) FROM PUBLIC")
    op.execute(f"REVOKE ALL ON FUNCTION {UNCHECKED_NAME}({SIGNATURE}) FROM aurum_app")
    op.execute(f"REVOKE ALL ON FUNCTION {UNCHECKED_NAME}({SIGNATURE}) FROM aurum_support")
    op.execute(AUTHENTICATE_EDGE_NODE_SQL)
    _secure_function(
        "public.authenticate_edge_node(UUID, TEXT)",
        grant_to="aurum_app, aurum_support",
    )
    op.execute(COUNT_ACTIVE_EDGE_NODES_SQL)
    _secure_function(
        "public.count_active_edge_nodes_for_branch(UUID, UUID)",
        grant_to="aurum_app, aurum_support",
    )


def downgrade() -> None:
    op.execute(
        "DROP FUNCTION IF EXISTS public.count_active_edge_nodes_for_branch(UUID, UUID)"
    )
    op.execute("DROP FUNCTION public.authenticate_edge_node(UUID, TEXT)")
    op.execute(
        "ALTER FUNCTION public.authenticate_edge_node_credential_unchecked(UUID, TEXT) "
        "RENAME TO authenticate_edge_node"
    )
    _secure_function(
        "public.authenticate_edge_node(UUID, TEXT)",
        grant_to="aurum_app, aurum_support",
    )
