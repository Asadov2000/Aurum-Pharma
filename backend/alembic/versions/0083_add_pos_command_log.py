"""add tenant-scoped idempotency ledger for POS draft commands

Revision ID: 0083
Revises: 0082
Create Date: 2026-08-09
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0083"
down_revision: str | Sequence[str] | None = "0082"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE public.pos_command (
          id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id     UUID NOT NULL REFERENCES public.tenant(id) ON DELETE CASCADE,
          operation_id  UUID NOT NULL,
          actor_user_id UUID NOT NULL REFERENCES public.app_user(id) ON DELETE RESTRICT,
          sale_id       UUID,
          command_type  TEXT NOT NULL,
          request_hash  TEXT NOT NULL,
          result_payload JSONB NOT NULL,
          created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_by    UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
          CONSTRAINT uq_pos_command_tenant_operation
            UNIQUE (tenant_id, operation_id),
          CONSTRAINT uq_pos_command_tenant_id_id
            UNIQUE (tenant_id, id),
          CONSTRAINT fk_pos_command_tenant_sale
            FOREIGN KEY (tenant_id, sale_id)
            REFERENCES public.sale(tenant_id, id) ON DELETE CASCADE,
          CONSTRAINT ck_pos_command_operation_uuid4 CHECK (
            (get_byte(uuid_send(operation_id), 6) >> 4) = 4
            AND (get_byte(uuid_send(operation_id), 8) & 192) = 128
          ),
          CONSTRAINT ck_pos_command_type CHECK (
            command_type IN (
              'sale.create', 'item.add', 'item.update', 'item.delete'
            )
          ),
          CONSTRAINT ck_pos_command_sale_reference CHECK (
            command_type = 'sale.create' OR sale_id IS NOT NULL
          ),
          CONSTRAINT ck_pos_command_request_hash CHECK (
            request_hash ~ '^[0-9a-f]{64}$'
          ),
          CONSTRAINT ck_pos_command_result_payload CHECK (
            jsonb_typeof(result_payload) = 'object'
            AND octet_length(result_payload::text) BETWEEN 2 AND 65536
            AND result_payload ->> 'command_type' = command_type
          )
        )
        """)
    op.execute(
        "CREATE INDEX ix_pos_command_actor_created "
        "ON public.pos_command (tenant_id, actor_user_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX ix_pos_command_sale_created "
        "ON public.pos_command (tenant_id, sale_id, created_at DESC) "
        "WHERE sale_id IS NOT NULL"
    )

    op.execute("ALTER TABLE public.pos_command ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.pos_command FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY pos_command_tenant_select ON public.pos_command
          FOR SELECT
          USING (tenant_id = public.current_tenant_id())
        """)
    op.execute("""
        CREATE POLICY pos_command_actor_insert ON public.pos_command
          FOR INSERT
          WITH CHECK (
            tenant_id = public.current_tenant_id()
            AND actor_user_id = public.current_app_user_id()
          )
        """)

    op.execute("""
        CREATE FUNCTION public.trg_guard_pos_command_immutable()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
          RAISE EXCEPTION 'POS command ledger is immutable';
        END;
        $function$
        """)
    op.execute(
        "REVOKE ALL ON FUNCTION public.trg_guard_pos_command_immutable() "
        "FROM PUBLIC, aurum_app, aurum_support"
    )
    op.execute("""
        CREATE TRIGGER trg_pos_command_immutable
          BEFORE UPDATE OR DELETE ON public.pos_command
          FOR EACH ROW EXECUTE FUNCTION public.trg_guard_pos_command_immutable()
        """)
    op.execute("""
        CREATE TRIGGER trg_pos_command_created_meta
          BEFORE INSERT ON public.pos_command
          FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_meta()
        """)
    op.execute("""
        CREATE TRIGGER trg_audit_pos_command
          AFTER INSERT OR UPDATE OR DELETE ON public.pos_command
          FOR EACH ROW EXECUTE FUNCTION public.trg_audit_log()
        """)

    op.execute(
        "REVOKE ALL PRIVILEGES ON TABLE public.pos_command " "FROM PUBLIC, aurum_app, aurum_support"
    )
    op.execute("GRANT SELECT, INSERT ON TABLE public.pos_command " "TO aurum_app, aurum_support")


def downgrade() -> None:
    op.execute("DROP TABLE public.pos_command")
    op.execute("DROP FUNCTION public.trg_guard_pos_command_immutable()")
