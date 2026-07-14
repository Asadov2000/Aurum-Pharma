"""sync: bind stream writes to Cloud branch context and outbox

Revision ID: 0043
Revises: 0042
Create Date: 2026-07-14

The application role is shared by Cloud HTTP traffic and read-only Edge machine
sessions. Stream writes are therefore guarded at the table boundary: only a
Cloud request with an explicit matching branch context may mutate a stream, and
the committed checkpoint must correspond to the durable outbox event.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0043"
down_revision: str | Sequence[str] | None = "0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SCOPE_GUARD_SQL = """
CREATE FUNCTION public.trg_guard_sync_stream_scope() RETURNS TRIGGER AS $$
DECLARE
  v_branch_id UUID;
BEGIN
  IF SESSION_USER = 'aurum_app' THEN
    v_branch_id := NULLIF(
      pg_catalog.current_setting('app.branch_id', true),
      ''
    )::UUID;

    IF public.current_tenant_id() IS NULL
      OR NEW.tenant_id IS DISTINCT FROM public.current_tenant_id()
      OR v_branch_id IS NULL
      OR NEW.branch_id IS DISTINCT FROM v_branch_id
      OR NULLIF(
        pg_catalog.current_setting('app.edge_node_id', true),
        ''
      ) IS NOT NULL
    THEN
      RAISE EXCEPTION 'Sync stream writes require a Cloud branch scope'
        USING ERRCODE = '42501';
    END IF;
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


CHECKPOINT_GUARD_SQL = """
CREATE FUNCTION public.trg_validate_sync_stream_checkpoint() RETURNS TRIGGER AS $$
DECLARE
  v_stream public.sync_stream%ROWTYPE;
BEGIN
  SELECT sync_stream.*
  INTO v_stream
  FROM public.sync_stream AS sync_stream
  WHERE sync_stream.id = NEW.id;

  IF NOT FOUND OR v_stream.last_sequence = 0 THEN
    RETURN NULL;
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM public.sync_outbox AS sync_outbox
    WHERE sync_outbox.tenant_id = v_stream.tenant_id
      AND sync_outbox.branch_id = v_stream.branch_id
      AND sync_outbox.origin_node_id = v_stream.writer_node_id
      AND sync_outbox.writer_epoch = v_stream.writer_epoch
      AND sync_outbox.sequence = v_stream.last_sequence
      AND sync_outbox.stream_checksum = v_stream.current_checksum
      AND sync_outbox.projection_hash IS NOT NULL
      AND sync_outbox.projection_checksum = v_stream.current_projection_checksum
  ) THEN
    RAISE EXCEPTION 'Sync stream checkpoint is not backed by its outbox event'
      USING ERRCODE = '23514';
  END IF;

  RETURN NULL;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


def _secure_trigger_function(signature: str) -> None:
    op.execute(f"ALTER FUNCTION {signature} OWNER TO aurum_support")
    op.execute(f"REVOKE ALL PRIVILEGES ON FUNCTION {signature} FROM PUBLIC, aurum_app")


def upgrade() -> None:
    op.execute(SCOPE_GUARD_SQL)
    _secure_trigger_function("public.trg_guard_sync_stream_scope()")
    op.execute("""
        CREATE TRIGGER trg_sync_stream_scope_guard
        BEFORE INSERT OR UPDATE ON public.sync_stream
        FOR EACH ROW EXECUTE FUNCTION public.trg_guard_sync_stream_scope()
        """)

    op.execute(CHECKPOINT_GUARD_SQL)
    _secure_trigger_function("public.trg_validate_sync_stream_checkpoint()")
    op.execute("""
        CREATE CONSTRAINT TRIGGER trg_sync_stream_checkpoint_guard
        AFTER INSERT OR UPDATE ON public.sync_stream
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION public.trg_validate_sync_stream_checkpoint()
        """)


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_sync_stream_checkpoint_guard ON public.sync_stream")
    op.execute("DROP TRIGGER trg_sync_stream_scope_guard ON public.sync_stream")
    op.execute("DROP FUNCTION public.trg_validate_sync_stream_checkpoint()")
    op.execute("DROP FUNCTION public.trg_guard_sync_stream_scope()")
