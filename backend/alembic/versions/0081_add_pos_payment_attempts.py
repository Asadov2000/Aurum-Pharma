"""add server-trusted POS payment attempts

Revision ID: 0081
Revises: 0080
Create Date: 2026-08-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0081"
down_revision: str | Sequence[str] | None = "0080"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_sale_tenant_id_id",
        "sale",
        ["tenant_id", "id"],
    )
    op.execute("""
        CREATE TABLE public.pos_payment_attempt (
          id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id           UUID NOT NULL REFERENCES public.tenant(id) ON DELETE CASCADE,
          sale_id             UUID NOT NULL,
          cashier_user_id     UUID NOT NULL REFERENCES public.app_user(id) ON DELETE RESTRICT,
          operation_id        UUID NOT NULL,
          operation_hash      TEXT NOT NULL,
          payment_method      TEXT NOT NULL,
          amount              NUMERIC(14,2) NOT NULL,
          currency            TEXT NOT NULL DEFAULT 'TJS',
          status              TEXT NOT NULL DEFAULT 'pending',
          external_reference  TEXT,
          void_reason         TEXT,
          void_note           TEXT,
          created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          confirmed_at        TIMESTAMPTZ,
          consumed_at         TIMESTAMPTZ,
          voided_at           TIMESTAMPTZ,
          updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_by          UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
          updated_by          UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
          CONSTRAINT uq_pos_payment_attempt_tenant_operation
            UNIQUE (tenant_id, operation_id),
          CONSTRAINT uq_pos_payment_attempt_tenant_id_id
            UNIQUE (tenant_id, id),
          CONSTRAINT fk_pos_payment_attempt_tenant_sale
            FOREIGN KEY (tenant_id, sale_id)
            REFERENCES public.sale(tenant_id, id)
            ON DELETE CASCADE,
          CONSTRAINT ck_pos_payment_attempt_operation_hash
            CHECK (operation_hash ~ '^[0-9a-f]{64}$'),
          CONSTRAINT ck_pos_payment_attempt_method
            CHECK (payment_method IN ('card','qr')),
          CONSTRAINT ck_pos_payment_attempt_amount
            CHECK (amount > 0),
          CONSTRAINT ck_pos_payment_attempt_currency
            CHECK (currency = 'TJS'),
          CONSTRAINT ck_pos_payment_attempt_status
            CHECK (status IN ('pending','confirmed','consumed','voided')),
          CONSTRAINT ck_pos_payment_attempt_external_reference
            CHECK (external_reference IS NULL OR char_length(external_reference) <= 128),
          CONSTRAINT ck_pos_payment_attempt_void_reason
            CHECK (
              void_reason IS NULL OR void_reason IN (
                'cashier_cancelled',
                'customer_cancelled',
                'terminal_declined',
                'timeout',
                'duplicate',
                'checkout_failed',
                'manager_override'
              )
            ),
          CONSTRAINT ck_pos_payment_attempt_void_note
            CHECK (void_note IS NULL OR char_length(void_note) <= 160),
          CONSTRAINT ck_pos_payment_attempt_state_timestamps
            CHECK (
              (status = 'pending'
                AND confirmed_at IS NULL AND consumed_at IS NULL AND voided_at IS NULL
                AND void_reason IS NULL AND void_note IS NULL)
              OR (status = 'confirmed'
                AND confirmed_at IS NOT NULL AND consumed_at IS NULL AND voided_at IS NULL
                AND void_reason IS NULL AND void_note IS NULL)
              OR (status = 'consumed'
                AND confirmed_at IS NOT NULL AND consumed_at IS NOT NULL AND voided_at IS NULL
                AND void_reason IS NULL AND void_note IS NULL)
              OR (status = 'voided'
                AND consumed_at IS NULL AND voided_at IS NOT NULL AND void_reason IS NOT NULL)
            )
        )
        """)
    op.execute(
        "CREATE INDEX ix_pos_payment_attempt_sale_status "
        "ON public.pos_payment_attempt (tenant_id, sale_id, status, created_at DESC)"
    )

    op.execute("ALTER TABLE public.pos_payment_attempt ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.pos_payment_attempt FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY pos_payment_attempt_tenant_access ON public.pos_payment_attempt
          FOR ALL
          USING (tenant_id = public.current_tenant_id())
          WITH CHECK (tenant_id = public.current_tenant_id())
        """)

    op.execute("""
        CREATE FUNCTION public.trg_guard_pos_payment_attempt_transition()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
          IF ROW(
            NEW.id,
            NEW.tenant_id,
            NEW.sale_id,
            NEW.cashier_user_id,
            NEW.operation_id,
            NEW.operation_hash,
            NEW.payment_method,
            NEW.amount,
            NEW.currency,
            NEW.created_at,
            NEW.created_by
          ) IS DISTINCT FROM ROW(
            OLD.id,
            OLD.tenant_id,
            OLD.sale_id,
            OLD.cashier_user_id,
            OLD.operation_id,
            OLD.operation_hash,
            OLD.payment_method,
            OLD.amount,
            OLD.currency,
            OLD.created_at,
            OLD.created_by
          ) THEN
            RAISE EXCEPTION 'Payment attempt identity is immutable';
          END IF;

          IF OLD.status = 'pending' AND NEW.status NOT IN ('pending','confirmed','voided') THEN
            RAISE EXCEPTION 'Invalid payment attempt transition';
          ELSIF OLD.status = 'confirmed'
             AND NEW.status NOT IN ('confirmed','consumed','voided') THEN
            RAISE EXCEPTION 'Invalid payment attempt transition';
          ELSIF OLD.status IN ('consumed','voided') AND NEW.status <> OLD.status THEN
            RAISE EXCEPTION 'Final payment attempt is immutable';
          END IF;

          IF NEW.external_reference IS DISTINCT FROM OLD.external_reference
             AND NOT (OLD.status = 'pending' AND NEW.status = 'confirmed') THEN
            RAISE EXCEPTION 'External payment reference is immutable';
          END IF;
          IF NEW.confirmed_at IS DISTINCT FROM OLD.confirmed_at
             AND NOT (OLD.status = 'pending' AND NEW.status = 'confirmed') THEN
            RAISE EXCEPTION 'Payment confirmation timestamp is immutable';
          END IF;
          IF NEW.consumed_at IS DISTINCT FROM OLD.consumed_at
             AND NOT (OLD.status = 'confirmed' AND NEW.status = 'consumed') THEN
            RAISE EXCEPTION 'Payment consumption timestamp is immutable';
          END IF;
          IF NEW.voided_at IS DISTINCT FROM OLD.voided_at
             AND NOT (OLD.status IN ('pending','confirmed') AND NEW.status = 'voided') THEN
            RAISE EXCEPTION 'Payment void timestamp is immutable';
          END IF;
          IF ROW(NEW.void_reason, NEW.void_note) IS DISTINCT FROM
             ROW(OLD.void_reason, OLD.void_note)
             AND NOT (OLD.status IN ('pending','confirmed') AND NEW.status = 'voided') THEN
            RAISE EXCEPTION 'Void details require a voided payment attempt';
          END IF;
          RETURN NEW;
        END;
        $function$
        """)
    op.execute(
        "REVOKE ALL ON FUNCTION public.trg_guard_pos_payment_attempt_transition() "
        "FROM PUBLIC, aurum_app, aurum_support"
    )
    op.execute("""
        CREATE TRIGGER trg_pos_payment_attempt_transition
          BEFORE UPDATE ON public.pos_payment_attempt
          FOR EACH ROW EXECUTE FUNCTION public.trg_guard_pos_payment_attempt_transition()
        """)
    op.execute("""
        CREATE TRIGGER trg_pos_payment_attempt_created_meta
          BEFORE INSERT ON public.pos_payment_attempt
          FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_meta()
        """)
    op.execute("""
        CREATE TRIGGER trg_pos_payment_attempt_updated_meta
          BEFORE UPDATE ON public.pos_payment_attempt
          FOR EACH ROW EXECUTE FUNCTION public.trg_set_updated_meta()
        """)
    op.execute("""
        CREATE TRIGGER trg_audit_pos_payment_attempt
          AFTER INSERT OR UPDATE OR DELETE ON public.pos_payment_attempt
          FOR EACH ROW EXECUTE FUNCTION public.trg_audit_log()
        """)

    op.add_column(
        "sale_payment",
        sa.Column(
            "payment_attempt_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_sale_payment_tenant_payment_attempt",
        "sale_payment",
        "pos_payment_attempt",
        ["tenant_id", "payment_attempt_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "uq_sale_payment_tenant_attempt",
        "sale_payment",
        ["tenant_id", "payment_attempt_id"],
        unique=True,
        postgresql_where=sa.text("payment_attempt_id IS NOT NULL"),
    )

    op.execute(
        "REVOKE ALL PRIVILEGES ON TABLE public.pos_payment_attempt "
        "FROM PUBLIC, aurum_app, aurum_support"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON TABLE public.pos_payment_attempt "
        "TO aurum_app, aurum_support"
    )


def downgrade() -> None:
    op.drop_index("uq_sale_payment_tenant_attempt", table_name="sale_payment")
    op.drop_constraint(
        "fk_sale_payment_tenant_payment_attempt",
        "sale_payment",
        type_="foreignkey",
    )
    op.drop_column("sale_payment", "payment_attempt_id")
    op.execute("DROP TABLE public.pos_payment_attempt")
    op.execute("DROP FUNCTION public.trg_guard_pos_payment_attempt_transition()")
    op.drop_constraint("uq_sale_tenant_id_id", "sale", type_="unique")
