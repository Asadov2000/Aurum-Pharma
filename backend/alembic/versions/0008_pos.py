"""pos: shift, sale, sale_item, sale_payment, prescription_log

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-19

Tables (all tenant-scoped, RLS enforced):
- shift             (cashier session at a register; one open shift per
                     register; closes with cash/expected/diff + totals JSONB)
- sale              (receipt; sale_type=sale|return; status=draft|completed|voided;
                     completed sales are IMMUTABLE — enforced in the service layer)
- sale_item         (one row per (catalog, batch) pair; FEFO produces multiple
                     items if a single line spans batches)
- sale_payment      (cash/card/bank_transfer)
- prescription_log  (one row per prescription-fact; required for items with
                     dispensing_type='prescription' before complete)
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op

revision: str = "0008"
down_revision: Union[str, Sequence[str], None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- shift --------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE shift (
          id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id             UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
          branch_id             UUID NOT NULL REFERENCES branch(id),
          register_id           UUID NOT NULL REFERENCES register(id),
          opened_by_user_id     UUID NOT NULL REFERENCES app_user(id),
          closed_by_user_id     UUID REFERENCES app_user(id),
          opened_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
          closed_at             TIMESTAMPTZ,
          status                TEXT NOT NULL DEFAULT 'open'
                                  CHECK (status IN ('open','closed','suspended')),
          opening_cash          NUMERIC(14, 2) NOT NULL DEFAULT 0,
          closing_cash_actual   NUMERIC(14, 2),
          closing_cash_expected NUMERIC(14, 2),
          closing_difference    NUMERIC(14, 2),
          totals                JSONB,
          currency              TEXT NOT NULL DEFAULT 'TJS' CHECK (currency IN ('TJS')),
          notes                 TEXT
        )
        """
    )
    op.execute("CREATE INDEX ix_shift_register ON shift (register_id, opened_at DESC)")
    op.execute("CREATE INDEX ix_shift_branch ON shift (branch_id, opened_at DESC)")
    op.execute(
        "CREATE UNIQUE INDEX ix_shift_open ON shift (register_id) WHERE status = 'open'"
    )
    op.execute("ALTER TABLE shift ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON shift
          USING (tenant_id = current_tenant_id() OR is_support_session())
        """
    )

    # ---- sale ---------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE sale (
          id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id           UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
          branch_id           UUID NOT NULL REFERENCES branch(id),
          register_id         UUID NOT NULL REFERENCES register(id),
          shift_id            UUID NOT NULL REFERENCES shift(id),
          sale_type           TEXT NOT NULL DEFAULT 'sale'
                                CHECK (sale_type IN ('sale','return')),
          parent_sale_id      UUID REFERENCES sale(id),
          status              TEXT NOT NULL DEFAULT 'draft'
                                CHECK (status IN ('draft','completed','voided')),
          receipt_number      TEXT,
          is_test             BOOLEAN NOT NULL DEFAULT false,
          total_amount        NUMERIC(14, 2) NOT NULL DEFAULT 0,
          currency            TEXT NOT NULL DEFAULT 'TJS' CHECK (currency IN ('TJS')),
          voided_at           TIMESTAMPTZ,
          voided_by_sale_id   UUID REFERENCES sale(id),
          cashier_user_id     UUID NOT NULL REFERENCES app_user(id),
          created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          completed_at        TIMESTAMPTZ,
          fiscal_data         JSONB,
          marking_codes       JSONB
        )
        """
    )
    op.execute("CREATE INDEX ix_sale_shift ON sale (shift_id, created_at DESC)")
    op.execute(
        "CREATE INDEX ix_sale_tenant ON sale (tenant_id, completed_at DESC) "
        "WHERE status = 'completed'"
    )
    op.execute(
        "CREATE INDEX ix_sale_parent ON sale (parent_sale_id) "
        "WHERE parent_sale_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_sale_register_receipt ON sale (register_id, receipt_number) "
        "WHERE status = 'completed' AND receipt_number IS NOT NULL"
    )
    op.execute("ALTER TABLE sale ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON sale
          USING (tenant_id = current_tenant_id() OR is_support_session())
        """
    )

    # ---- sale_item ----------------------------------------------------------
    op.execute(
        """
        CREATE TABLE sale_item (
          id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id           UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
          sale_id             UUID NOT NULL REFERENCES sale(id) ON DELETE CASCADE,
          catalog_id          UUID NOT NULL REFERENCES tenant_catalog(id),
          batch_id            UUID NOT NULL REFERENCES batch(id),
          qty                 NUMERIC(14, 3) NOT NULL CHECK (qty > 0),
          unit_price          NUMERIC(14, 2) NOT NULL CHECK (unit_price >= 0),
          total_price         NUMERIC(14, 2) NOT NULL CHECK (total_price >= 0),
          currency            TEXT NOT NULL DEFAULT 'TJS' CHECK (currency IN ('TJS')),
          discount_amount     NUMERIC(14, 2) NOT NULL DEFAULT 0,
          position            INT NOT NULL,
          created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_si_sale ON sale_item (sale_id, position)")
    op.execute("CREATE INDEX ix_si_batch ON sale_item (batch_id)")
    op.execute("CREATE INDEX ix_si_catalog ON sale_item (catalog_id)")
    op.execute("ALTER TABLE sale_item ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON sale_item
          USING (tenant_id = current_tenant_id() OR is_support_session())
        """
    )

    # ---- sale_payment -------------------------------------------------------
    op.execute(
        """
        CREATE TABLE sale_payment (
          id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id           UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
          sale_id             UUID NOT NULL REFERENCES sale(id) ON DELETE CASCADE,
          payment_method      TEXT NOT NULL
                                CHECK (payment_method IN ('cash','card','bank_transfer')),
          amount              NUMERIC(14, 2) NOT NULL CHECK (amount > 0),
          currency            TEXT NOT NULL DEFAULT 'TJS' CHECK (currency IN ('TJS')),
          metadata            JSONB,
          created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_sp_sale ON sale_payment (sale_id)")
    op.execute("ALTER TABLE sale_payment ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON sale_payment
          USING (tenant_id = current_tenant_id() OR is_support_session())
        """
    )

    # ---- prescription_log ---------------------------------------------------
    op.execute(
        """
        CREATE TABLE prescription_log (
          id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id           UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
          sale_id             UUID NOT NULL REFERENCES sale(id) ON DELETE CASCADE,
          sale_item_id        UUID REFERENCES sale_item(id),
          prescription_number TEXT,
          doctor_name         TEXT,
          doctor_license      TEXT,
          patient_name        TEXT,
          notes               TEXT,
          created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_by          UUID REFERENCES app_user(id)
        )
        """
    )
    op.execute("CREATE INDEX ix_pl_sale ON prescription_log (sale_id)")
    op.execute("CREATE INDEX ix_pl_tenant ON prescription_log (tenant_id, created_at DESC)")
    op.execute("ALTER TABLE prescription_log ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON prescription_log
          USING (tenant_id = current_tenant_id() OR is_support_session())
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS prescription_log CASCADE")
    op.execute("DROP TABLE IF EXISTS sale_payment CASCADE")
    op.execute("DROP TABLE IF EXISTS sale_item CASCADE")
    op.execute("DROP TABLE IF EXISTS sale CASCADE")
    op.execute("DROP TABLE IF EXISTS shift CASCADE")
