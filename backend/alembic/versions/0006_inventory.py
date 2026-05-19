"""inventory: batch, batch_movement, write_off + qty trigger + expiry view

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-19

Tables:
- batch          (lots/parties of a tenant_catalog item; denormalised
                  qty_remaining updated by trigger)
- batch_movement (every +/- delta on a batch; INSERT-only ledger)
- write_off     (write-off acts; the service also inserts a matching
                  batch_movement of type 'write_off' so qty decreases)

Trigger trg_bm_update_qty re-applies qty_delta onto batch.qty_remaining
AFTER each batch_movement insert, and raises if the column would go
negative — that's the canonical "no overdraw" guard.

View v_batch_with_expiry_status — joins batches with a colour bucket
based on `expires_at`; used by the list endpoint's `expiry_status`
filter without exposing this logic in Python.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op

revision: str = "0006"
down_revision: Union[str, Sequence[str], None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # batch
    # -------------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE batch (
          id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id           UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
          branch_id           UUID NOT NULL REFERENCES branch(id),
          catalog_id          UUID NOT NULL REFERENCES tenant_catalog(id),
          batch_number        TEXT,
          manufactured_at     DATE,
          expires_at          DATE NOT NULL,
          purchase_price      NUMERIC(14, 2) NOT NULL CHECK (purchase_price >= 0),
          sale_price          NUMERIC(14, 2) NOT NULL CHECK (sale_price >= 0),
          currency            TEXT NOT NULL DEFAULT 'TJS' CHECK (currency IN ('TJS')),
          qty_initial         NUMERIC(14, 3) NOT NULL CHECK (qty_initial > 0),
          qty_remaining       NUMERIC(14, 3) NOT NULL CHECK (qty_remaining >= 0),
          is_blocked          BOOLEAN NOT NULL DEFAULT false,
          block_reason        TEXT,
          blocked_at          TIMESTAMPTZ,
          created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_by          UUID REFERENCES app_user(id),
          updated_by          UUID REFERENCES app_user(id)
        )
        """
    )
    op.execute("CREATE INDEX ix_batch_tenant ON batch (tenant_id)")
    op.execute(
        "CREATE INDEX ix_batch_branch_catalog ON batch (branch_id, catalog_id) "
        "WHERE qty_remaining > 0"
    )
    op.execute(
        "CREATE INDEX ix_batch_expiry ON batch (tenant_id, expires_at) "
        "WHERE qty_remaining > 0"
    )
    op.execute(
        """
        CREATE TRIGGER trg_batch_updated BEFORE UPDATE ON batch
          FOR EACH ROW EXECUTE FUNCTION trg_set_updated_meta()
        """
    )
    op.execute("ALTER TABLE batch ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON batch
          USING (tenant_id = current_tenant_id() OR is_support_session())
        """
    )

    # -------------------------------------------------------------------------
    # batch_movement
    # -------------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE batch_movement (
          id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id           UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
          batch_id            UUID NOT NULL REFERENCES batch(id) ON DELETE CASCADE,
          movement_type       TEXT NOT NULL
                                CHECK (movement_type IN
                                  ('incoming','sale','sale_return','write_off',
                                   'supplier_return','correction','transfer_in','transfer_out')),
          qty_delta           NUMERIC(14, 3) NOT NULL,
          source_table        TEXT,
          source_id           UUID,
          notes               TEXT,
          created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_by          UUID REFERENCES app_user(id)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_bm_batch ON batch_movement (batch_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX ix_bm_tenant ON batch_movement (tenant_id, created_at DESC)"
    )
    op.execute("CREATE INDEX ix_bm_source ON batch_movement (source_table, source_id)")
    op.execute("ALTER TABLE batch_movement ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON batch_movement
          USING (tenant_id = current_tenant_id() OR is_support_session())
        """
    )

    # -------------------------------------------------------------------------
    # qty trigger: bump batch.qty_remaining and refuse to go negative
    # -------------------------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION trg_update_batch_qty() RETURNS TRIGGER AS $$
        BEGIN
          UPDATE batch
            SET qty_remaining = qty_remaining + NEW.qty_delta
            WHERE id = NEW.batch_id;
          IF (SELECT qty_remaining FROM batch WHERE id = NEW.batch_id) < 0 THEN
            RAISE EXCEPTION 'Batch qty_remaining cannot be negative';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_bm_update_qty AFTER INSERT ON batch_movement
          FOR EACH ROW EXECUTE FUNCTION trg_update_batch_qty()
        """
    )

    # -------------------------------------------------------------------------
    # write_off
    # -------------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE write_off (
          id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id           UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
          branch_id           UUID NOT NULL REFERENCES branch(id),
          batch_id            UUID NOT NULL REFERENCES batch(id),
          qty                 NUMERIC(14, 3) NOT NULL CHECK (qty > 0),
          reason              TEXT NOT NULL
                                CHECK (reason IN ('expired','damaged','spoiled','theft','other')),
          comment             TEXT,
          amount              NUMERIC(14, 2) NOT NULL,
          currency            TEXT NOT NULL DEFAULT 'TJS' CHECK (currency IN ('TJS')),
          created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_by          UUID REFERENCES app_user(id)
        )
        """
    )
    op.execute("CREATE INDEX ix_wo_tenant ON write_off (tenant_id, created_at DESC)")
    op.execute("CREATE INDEX ix_wo_branch ON write_off (branch_id, created_at DESC)")
    op.execute("ALTER TABLE write_off ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON write_off
          USING (tenant_id = current_tenant_id() OR is_support_session())
        """
    )

    # -------------------------------------------------------------------------
    # v_batch_with_expiry_status — colour bucket + days_to_expiry
    # -------------------------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE VIEW v_batch_with_expiry_status AS
        SELECT
          b.*,
          CASE
            WHEN b.expires_at <= CURRENT_DATE THEN 'expired'
            WHEN b.expires_at <= CURRENT_DATE + INTERVAL '1 month' THEN 'red'
            WHEN b.expires_at <= CURRENT_DATE + INTERVAL '3 months' THEN 'orange'
            WHEN b.expires_at <= CURRENT_DATE + INTERVAL '6 months' THEN 'yellow'
            ELSE 'normal'
          END AS expiry_status,
          (b.expires_at - CURRENT_DATE) AS days_to_expiry
        FROM batch b
        WHERE b.qty_remaining > 0
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_batch_with_expiry_status")
    op.execute("DROP TABLE IF EXISTS write_off CASCADE")
    op.execute("DROP TRIGGER IF EXISTS trg_bm_update_qty ON batch_movement")
    op.execute("DROP FUNCTION IF EXISTS trg_update_batch_qty() CASCADE")
    op.execute("DROP TABLE IF EXISTS batch_movement CASCADE")
    op.execute("DROP TABLE IF EXISTS batch CASCADE")
