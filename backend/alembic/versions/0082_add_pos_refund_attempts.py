"""add server-controlled electronic refund attempts

Revision ID: 0082
Revises: 0081
Create Date: 2026-08-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0082"
down_revision: str | Sequence[str] | None = "0081"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PERMISSION = "pos.refund_external_confirm"


def upgrade() -> None:
    op.execute(f"""
        INSERT INTO public.permission (
          code, group_code, name, description, min_level_required,
          is_dangerous, is_active, scope_type, target_role_type, risk_level,
          developer_grantable, administrator_grantable, owner_grantable,
          developer_delegable, administrator_delegable, owner_delegable,
          requires_step_up, requires_confirmation
        )
        VALUES (
          '{_PERMISSION}', 'pos', 'Подтверждение электронного возврата',
          'Подтверждение возврата по карте, QR или банковскому переводу по документу терминала.',
          4, true, true, 'BRANCH_SET', 'tenant', 'critical',
          true, true, true, true, true, true, false, true
        )
        ON CONFLICT (code) DO NOTHING
        """)
    op.execute(f"""
        INSERT INTO public.role_permission (role_id, permission_code)
        SELECT role.id, '{_PERMISSION}'
        FROM public.role
        WHERE role.is_system = true AND role.level <= 3
        ON CONFLICT (role_id, permission_code) DO NOTHING
        """)
    op.execute(f"""
        INSERT INTO public.role_template_permission (template_id, permission_code)
        SELECT template.id, '{_PERMISSION}'
        FROM public.role_template AS template
        WHERE template.slug = 'owner' AND template.is_active
        ON CONFLICT (template_id, permission_code) DO NOTHING
        """)
    op.execute(f"""
        INSERT INTO public.role_permission (role_id, permission_code)
        SELECT role.id, '{_PERMISSION}'
        FROM public.role
        WHERE role.is_protected = true
          AND role.protected_kind = 'tenant_owner'
          AND role.is_active = true
        ON CONFLICT (role_id, permission_code) DO NOTHING
        """)

    op.execute("""
        CREATE TABLE public.pos_refund_attempt (
          id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id                 UUID NOT NULL REFERENCES public.tenant(id) ON DELETE CASCADE,
          parent_sale_id            UUID NOT NULL,
          register_id               UUID NOT NULL REFERENCES public.register(id) ON DELETE RESTRICT,
          requested_by_user_id      UUID NOT NULL REFERENCES public.app_user(id) ON DELETE RESTRICT,
          confirmed_by_user_id      UUID REFERENCES public.app_user(id) ON DELETE RESTRICT,
          operation_id              UUID NOT NULL,
          operation_hash            TEXT NOT NULL,
          items_json                JSONB NOT NULL,
          external_allocations_json JSONB NOT NULL,
          total_amount              NUMERIC(14,2) NOT NULL,
          external_amount           NUMERIC(14,2) NOT NULL,
          currency                  TEXT NOT NULL DEFAULT 'TJS',
          status                    TEXT NOT NULL DEFAULT 'pending',
          void_reason               TEXT,
          void_note                 TEXT,
          created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
          confirmed_at              TIMESTAMPTZ,
          consumed_at               TIMESTAMPTZ,
          voided_at                 TIMESTAMPTZ,
          updated_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_by                UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
          updated_by                UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
          CONSTRAINT uq_pos_refund_attempt_tenant_operation UNIQUE (tenant_id, operation_id),
          CONSTRAINT uq_pos_refund_attempt_tenant_id_id UNIQUE (tenant_id, id),
          CONSTRAINT fk_pos_refund_attempt_tenant_sale
            FOREIGN KEY (tenant_id, parent_sale_id)
            REFERENCES public.sale(tenant_id, id) ON DELETE RESTRICT,
          CONSTRAINT ck_pos_refund_attempt_operation_hash
            CHECK (operation_hash ~ '^[0-9a-f]{64}$'),
          CONSTRAINT ck_pos_refund_attempt_items
            CHECK (
              jsonb_typeof(items_json) = 'array'
              AND jsonb_array_length(items_json) BETWEEN 1 AND 200
            ),
          CONSTRAINT ck_pos_refund_attempt_allocations
            CHECK (
              jsonb_typeof(external_allocations_json) = 'array'
              AND jsonb_array_length(external_allocations_json) BETWEEN 1 AND 3
            ),
          CONSTRAINT ck_pos_refund_attempt_total CHECK (total_amount > 0),
          CONSTRAINT ck_pos_refund_attempt_external_total CHECK (external_amount > 0),
          CONSTRAINT ck_pos_refund_attempt_amounts CHECK (external_amount <= total_amount),
          CONSTRAINT ck_pos_refund_attempt_currency CHECK (currency = 'TJS'),
          CONSTRAINT ck_pos_refund_attempt_status
            CHECK (status IN ('pending','confirmed','consumed','voided')),
          CONSTRAINT ck_pos_refund_attempt_void_reason
            CHECK (
              void_reason IS NULL OR void_reason IN (
                'cashier_cancelled', 'customer_cancelled', 'terminal_declined',
                'timeout', 'duplicate', 'refund_failed', 'manager_override'
              )
            ),
          CONSTRAINT ck_pos_refund_attempt_void_note
            CHECK (void_note IS NULL OR char_length(void_note) <= 160),
          CONSTRAINT ck_pos_refund_attempt_state
            CHECK (
              (status = 'pending' AND confirmed_by_user_id IS NULL
                AND confirmed_at IS NULL AND consumed_at IS NULL AND voided_at IS NULL
                AND void_reason IS NULL AND void_note IS NULL)
              OR (status = 'confirmed' AND confirmed_by_user_id IS NOT NULL
                AND confirmed_at IS NOT NULL AND consumed_at IS NULL AND voided_at IS NULL
                AND void_reason IS NULL AND void_note IS NULL)
              OR (status = 'consumed' AND confirmed_by_user_id IS NOT NULL
                AND confirmed_at IS NOT NULL AND consumed_at IS NOT NULL AND voided_at IS NULL
                AND void_reason IS NULL AND void_note IS NULL)
              OR (status = 'voided' AND confirmed_by_user_id IS NULL
                AND confirmed_at IS NULL AND consumed_at IS NULL
                AND voided_at IS NOT NULL AND void_reason IS NOT NULL)
            )
        )
        """)
    op.execute(
        "CREATE UNIQUE INDEX uq_pos_refund_attempt_active_sale "
        "ON public.pos_refund_attempt (tenant_id, parent_sale_id) "
        "WHERE status IN ('pending','confirmed')"
    )
    op.execute(
        "CREATE INDEX ix_pos_refund_attempt_register_status "
        "ON public.pos_refund_attempt (tenant_id, register_id, status, created_at DESC)"
    )

    op.execute("""
        CREATE TABLE public.pos_refund_reference (
          id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id            UUID NOT NULL REFERENCES public.tenant(id) ON DELETE CASCADE,
          refund_attempt_id    UUID NOT NULL,
          payment_method       TEXT NOT NULL,
          amount               NUMERIC(14,2) NOT NULL,
          terminal_id          TEXT NOT NULL,
          document_number      TEXT NOT NULL,
          confirmed_by_user_id UUID NOT NULL REFERENCES public.app_user(id) ON DELETE RESTRICT,
          confirmed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_by           UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
          CONSTRAINT fk_pos_refund_reference_tenant_attempt
            FOREIGN KEY (tenant_id, refund_attempt_id)
            REFERENCES public.pos_refund_attempt(tenant_id, id) ON DELETE RESTRICT,
          CONSTRAINT uq_pos_refund_reference_attempt_method
            UNIQUE (tenant_id, refund_attempt_id, payment_method),
          CONSTRAINT uq_pos_refund_reference_terminal_document
            UNIQUE (tenant_id, terminal_id, document_number),
          CONSTRAINT ck_pos_refund_reference_method
            CHECK (payment_method IN ('card','qr','bank_transfer')),
          CONSTRAINT ck_pos_refund_reference_amount CHECK (amount > 0),
          CONSTRAINT ck_pos_refund_reference_terminal
            CHECK (char_length(terminal_id) BETWEEN 1 AND 64),
          CONSTRAINT ck_pos_refund_reference_document
            CHECK (char_length(document_number) BETWEEN 1 AND 128)
        )
        """)

    for table in ("pos_refund_attempt", "pos_refund_reference"):
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY {table}_tenant_access ON public.{table}
              FOR ALL
              USING (tenant_id = public.current_tenant_id())
              WITH CHECK (tenant_id = public.current_tenant_id())
            """)

    op.execute("""
        CREATE FUNCTION public.trg_guard_pos_refund_attempt_transition()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
          IF ROW(
            NEW.id, NEW.tenant_id, NEW.parent_sale_id, NEW.register_id,
            NEW.requested_by_user_id, NEW.operation_id, NEW.operation_hash,
            NEW.items_json, NEW.external_allocations_json, NEW.total_amount,
            NEW.external_amount, NEW.currency, NEW.created_at, NEW.created_by
          ) IS DISTINCT FROM ROW(
            OLD.id, OLD.tenant_id, OLD.parent_sale_id, OLD.register_id,
            OLD.requested_by_user_id, OLD.operation_id, OLD.operation_hash,
            OLD.items_json, OLD.external_allocations_json, OLD.total_amount,
            OLD.external_amount, OLD.currency, OLD.created_at, OLD.created_by
          ) THEN
            RAISE EXCEPTION 'Refund attempt identity is immutable';
          END IF;
          IF OLD.status = 'pending' AND NEW.status NOT IN ('pending','confirmed','voided') THEN
            RAISE EXCEPTION 'Invalid refund attempt transition';
          ELSIF OLD.status = 'confirmed'
             AND NEW.status NOT IN ('confirmed','consumed') THEN
            RAISE EXCEPTION 'Invalid refund attempt transition';
          ELSIF OLD.status IN ('consumed','voided') AND NEW.status <> OLD.status THEN
            RAISE EXCEPTION 'Final refund attempt is immutable';
          END IF;
          IF ROW(NEW.confirmed_by_user_id, NEW.confirmed_at) IS DISTINCT FROM
             ROW(OLD.confirmed_by_user_id, OLD.confirmed_at)
             AND NOT (OLD.status = 'pending' AND NEW.status = 'confirmed') THEN
            RAISE EXCEPTION 'Refund confirmation is immutable';
          END IF;
          IF NEW.consumed_at IS DISTINCT FROM OLD.consumed_at
             AND NOT (OLD.status = 'confirmed' AND NEW.status = 'consumed') THEN
            RAISE EXCEPTION 'Refund consumption timestamp is immutable';
          END IF;
          IF ROW(NEW.void_reason, NEW.void_note, NEW.voided_at) IS DISTINCT FROM
             ROW(OLD.void_reason, OLD.void_note, OLD.voided_at)
             AND NOT (OLD.status = 'pending' AND NEW.status = 'voided') THEN
            RAISE EXCEPTION 'Refund void details are immutable';
          END IF;
          RETURN NEW;
        END;
        $function$
        """)
    op.execute(
        "REVOKE ALL ON FUNCTION public.trg_guard_pos_refund_attempt_transition() "
        "FROM PUBLIC, aurum_app, aurum_support"
    )
    op.execute("""
        CREATE TRIGGER trg_pos_refund_attempt_transition
          BEFORE UPDATE ON public.pos_refund_attempt
          FOR EACH ROW EXECUTE FUNCTION public.trg_guard_pos_refund_attempt_transition()
        """)
    op.execute("""
        CREATE TRIGGER trg_pos_refund_attempt_created_meta
          BEFORE INSERT ON public.pos_refund_attempt
          FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_meta()
        """)
    op.execute("""
        CREATE TRIGGER trg_pos_refund_attempt_updated_meta
          BEFORE UPDATE ON public.pos_refund_attempt
          FOR EACH ROW EXECUTE FUNCTION public.trg_set_updated_meta()
        """)
    op.execute("""
        CREATE TRIGGER trg_pos_refund_reference_created_meta
          BEFORE INSERT ON public.pos_refund_reference
          FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_meta()
        """)
    for table in ("pos_refund_attempt", "pos_refund_reference"):
        op.execute(f"""
            CREATE TRIGGER trg_audit_{table}
              AFTER INSERT OR UPDATE OR DELETE ON public.{table}
              FOR EACH ROW EXECUTE FUNCTION public.trg_audit_log()
            """)

    op.add_column(
        "sale",
        sa.Column("refund_attempt_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_sale_tenant_refund_attempt",
        "sale",
        "pos_refund_attempt",
        ["tenant_id", "refund_attempt_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_sale_refund_attempt_return_only",
        "sale",
        "refund_attempt_id IS NULL OR sale_type = 'return'",
    )
    op.create_index(
        "uq_sale_tenant_refund_attempt",
        "sale",
        ["tenant_id", "refund_attempt_id"],
        unique=True,
        postgresql_where=sa.text("refund_attempt_id IS NOT NULL"),
    )

    op.execute(
        "REVOKE ALL PRIVILEGES ON TABLE public.pos_refund_attempt, "
        "public.pos_refund_reference FROM PUBLIC, aurum_app, aurum_support"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON TABLE public.pos_refund_attempt "
        "TO aurum_app, aurum_support"
    )
    op.execute(
        "GRANT SELECT, INSERT ON TABLE public.pos_refund_reference " "TO aurum_app, aurum_support"
    )


def downgrade() -> None:
    op.drop_index("uq_sale_tenant_refund_attempt", table_name="sale")
    op.drop_constraint("ck_sale_refund_attempt_return_only", "sale", type_="check")
    op.drop_constraint("fk_sale_tenant_refund_attempt", "sale", type_="foreignkey")
    op.drop_column("sale", "refund_attempt_id")
    op.execute("DROP TABLE public.pos_refund_reference")
    op.execute("DROP TABLE public.pos_refund_attempt")
    op.execute("DROP FUNCTION public.trg_guard_pos_refund_attempt_transition()")
    op.execute(f"DELETE FROM public.role_permission WHERE permission_code = '{_PERMISSION}'")
    op.execute(
        f"DELETE FROM public.role_template_permission WHERE permission_code = '{_PERMISSION}'"
    )
    op.execute(f"DELETE FROM public.permission WHERE code = '{_PERMISSION}'")
