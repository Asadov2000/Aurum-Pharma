"""billing: plans, subscriptions, invoices, payments + seed default plan

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-20

Tables:
- subscription_plan   (global catalogue; seeded with one row `aurum_pharma`)
- tenant_subscription (per-tenant; RLS on; lifecycle trial → active → grace
                       → suspended | cancelled | archived)
- invoice             (per-tenant; RLS on; unique invoice_number)
- payment             (per-tenant; RLS on; method default 'bank_transfer'
                       — the only one supported in phase 1)

View `v_active_subscription` joins the subscription with its plan and
filters out cancelled/archived rows so the API gets a denormalised
record for `GET /api/v1/billing/subscription`.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from decimal import Decimal
from typing import Union

from alembic import op
from sqlalchemy import text as sa_text

revision: str = "0009"
down_revision: Union[str, Sequence[str], None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- subscription_plan --------------------------------------------------
    op.execute(
        """
        CREATE TABLE subscription_plan (
          id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          code                TEXT NOT NULL UNIQUE,
          name                TEXT NOT NULL,
          description         TEXT,
          price_per_branch    NUMERIC(14, 2) NOT NULL,
          currency            TEXT NOT NULL DEFAULT 'TJS' CHECK (currency IN ('TJS')),
          billing_period      TEXT NOT NULL DEFAULT 'monthly'
                                CHECK (billing_period IN ('monthly','yearly')),
          annual_discount_pct NUMERIC(5, 2) NOT NULL DEFAULT 0,
          features            JSONB,
          is_active           BOOLEAN NOT NULL DEFAULT true,
          created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    # ---- tenant_subscription ------------------------------------------------
    op.execute(
        """
        CREATE TABLE tenant_subscription (
          id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id           UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
          plan_id             UUID NOT NULL REFERENCES subscription_plan(id),
          status              TEXT NOT NULL DEFAULT 'trial'
                                CHECK (status IN ('trial','active','grace_period',
                                                  'suspended','cancelled','archived')),
          billing_period      TEXT NOT NULL DEFAULT 'monthly'
                                CHECK (billing_period IN ('monthly','yearly')),
          period_start        TIMESTAMPTZ NOT NULL DEFAULT now(),
          period_end          TIMESTAMPTZ NOT NULL,
          branches_count      INT NOT NULL,
          amount              NUMERIC(14, 2) NOT NULL,
          currency            TEXT NOT NULL DEFAULT 'TJS' CHECK (currency IN ('TJS')),
          created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          cancelled_at        TIMESTAMPTZ
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_ts_tenant ON tenant_subscription (tenant_id) "
        "WHERE status NOT IN ('cancelled','archived')"
    )
    op.execute(
        "CREATE INDEX ix_ts_status ON tenant_subscription (status, period_end)"
    )
    op.execute(
        """
        CREATE TRIGGER trg_ts_updated BEFORE UPDATE ON tenant_subscription
          FOR EACH ROW EXECUTE FUNCTION trg_set_updated_meta()
        """
    )
    op.execute("ALTER TABLE tenant_subscription ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON tenant_subscription
          USING (tenant_id = current_tenant_id() OR is_support_session())
        """
    )

    # ---- invoice ------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE invoice (
          id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id           UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
          subscription_id     UUID NOT NULL REFERENCES tenant_subscription(id),
          invoice_number      TEXT NOT NULL,
          issued_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
          due_at              TIMESTAMPTZ NOT NULL,
          amount              NUMERIC(14, 2) NOT NULL,
          currency            TEXT NOT NULL DEFAULT 'TJS' CHECK (currency IN ('TJS')),
          discount_amount     NUMERIC(14, 2) NOT NULL DEFAULT 0,
          discount_reason     TEXT,
          status              TEXT NOT NULL DEFAULT 'pending'
                                CHECK (status IN ('pending','paid','overdue','cancelled')),
          paid_at             TIMESTAMPTZ,
          notes               TEXT,
          pdf_path            TEXT,
          created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX ux_invoice_number ON invoice (invoice_number)")
    op.execute("CREATE INDEX ix_invoice_tenant ON invoice (tenant_id, issued_at DESC)")
    op.execute("CREATE INDEX ix_invoice_status ON invoice (status, due_at)")
    op.execute(
        """
        CREATE TRIGGER trg_invoice_updated BEFORE UPDATE ON invoice
          FOR EACH ROW EXECUTE FUNCTION trg_set_updated_meta()
        """
    )
    op.execute("ALTER TABLE invoice ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON invoice
          USING (tenant_id = current_tenant_id() OR is_support_session())
        """
    )

    # ---- payment ------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE payment (
          id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id           UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
          invoice_id          UUID NOT NULL REFERENCES invoice(id),
          amount              NUMERIC(14, 2) NOT NULL CHECK (amount > 0),
          currency            TEXT NOT NULL DEFAULT 'TJS' CHECK (currency IN ('TJS')),
          method              TEXT NOT NULL DEFAULT 'bank_transfer'
                                CHECK (method IN ('bank_transfer','card','cash')),
          reference           TEXT,
          paid_at             TIMESTAMPTZ NOT NULL,
          recorded_by         UUID REFERENCES app_user(id),
          notes               TEXT,
          created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_payment_invoice ON payment (invoice_id)")
    op.execute("CREATE INDEX ix_payment_tenant ON payment (tenant_id, paid_at DESC)")
    op.execute("ALTER TABLE payment ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON payment
          USING (tenant_id = current_tenant_id() OR is_support_session())
        """
    )

    # ---- v_active_subscription ---------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE VIEW v_active_subscription AS
        SELECT
          ts.*,
          sp.name AS plan_name,
          sp.code AS plan_code,
          sp.features AS plan_features
        FROM tenant_subscription ts
        JOIN subscription_plan sp ON sp.id = ts.plan_id
        WHERE ts.status NOT IN ('cancelled','archived')
        """
    )

    # ---- seed default plan --------------------------------------------------
    # DEFAULT_PRICE_TJS comes from the env; fall back to 550 (~$50) if unset.
    default_price = Decimal(os.environ.get("DEFAULT_PRICE_TJS", "550"))
    conn = op.get_bind()
    conn.execute(
        sa_text(
            "INSERT INTO subscription_plan (code, name, description, "
            "price_per_branch, billing_period, annual_discount_pct, features) "
            "VALUES (:code, :name, :desc, :price, 'monthly', 10, "
            "jsonb_build_object('all_features', true))"
        ),
        {
            "code": "aurum_pharma",
            "name": "Aurum Pharma",
            "desc": "Базовый тариф Aurum Pharma — все функции, оплата за каждую точку",
            "price": default_price,
        },
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_active_subscription")
    op.execute("DROP TABLE IF EXISTS payment CASCADE")
    op.execute("DROP TABLE IF EXISTS invoice CASCADE")
    op.execute("DROP TABLE IF EXISTS tenant_subscription CASCADE")
    op.execute("DROP TABLE IF EXISTS subscription_plan CASCADE")
