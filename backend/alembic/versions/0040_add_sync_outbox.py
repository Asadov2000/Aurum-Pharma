"""sync: add transactional outbox for durable sale events

Revision ID: 0040
Revises: 0039
Create Date: 2026-07-14
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0040"
down_revision: str | Sequence[str] | None = "0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE public.sync_outbox (
          event_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id        UUID NOT NULL REFERENCES public.tenant(id) ON DELETE CASCADE,
          branch_id        UUID NOT NULL REFERENCES public.branch(id) ON DELETE CASCADE,
          operation_id     UUID NOT NULL,
          aggregate_type   TEXT NOT NULL CHECK (btrim(aggregate_type) <> ''),
          aggregate_id     UUID NOT NULL REFERENCES public.sale(id) ON DELETE CASCADE,
          event_type       TEXT NOT NULL CHECK (btrim(event_type) <> ''),
          schema_version   INTEGER NOT NULL DEFAULT 1 CHECK (schema_version > 0),
          payload          JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
          payload_hash     TEXT NOT NULL
                           CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
          delivery_status TEXT NOT NULL DEFAULT 'pending'
                           CHECK (delivery_status IN ('pending','published','quarantined')),
          attempts         INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
          available_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
          published_at     TIMESTAMPTZ,
          created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_by       UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
          updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_by       UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
          CONSTRAINT uq_sync_outbox_tenant_operation UNIQUE (tenant_id, operation_id)
        )
        """)
    op.execute("""
        CREATE INDEX ix_sync_outbox_pending
        ON public.sync_outbox (available_at, created_at)
        WHERE delivery_status = 'pending'
        """)
    op.execute("""
        CREATE INDEX ix_sync_outbox_aggregate
        ON public.sync_outbox (tenant_id, aggregate_type, aggregate_id)
        """)
    op.execute("""
        CREATE TRIGGER trg_sync_outbox_created
        BEFORE INSERT ON public.sync_outbox
        FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_meta()
        """)
    op.execute("""
        CREATE TRIGGER trg_sync_outbox_updated
        BEFORE UPDATE ON public.sync_outbox
        FOR EACH ROW EXECUTE FUNCTION public.trg_set_updated_meta()
        """)

    op.execute("ALTER TABLE public.sync_outbox ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation ON public.sync_outbox
        USING (tenant_id = public.current_tenant_id())
        WITH CHECK (tenant_id = public.current_tenant_id())
        """)

    op.execute("REVOKE ALL PRIVILEGES ON TABLE public.sync_outbox FROM PUBLIC, aurum_app")
    op.execute("GRANT SELECT ON TABLE public.sync_outbox TO aurum_app")
    op.execute("""
        GRANT INSERT (
          event_id,
          tenant_id,
          branch_id,
          operation_id,
          aggregate_type,
          aggregate_id,
          event_type,
          schema_version,
          payload,
          payload_hash
        ) ON TABLE public.sync_outbox TO aurum_app
        """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.sync_outbox CASCADE")
