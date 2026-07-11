"""pos: add transaction guards for receipts, refunds, and movements

Revision ID: 0025
Revises: 0024
Create Date: 2026-07-12
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0025"
down_revision: str | Sequence[str] | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE sale ADD COLUMN receipt_seq BIGINT")
    op.execute("ALTER TABLE sale ADD COLUMN operation_id UUID")
    op.execute("ALTER TABLE sale ADD COLUMN operation_hash TEXT")
    op.execute(
        """
        WITH ranked AS (
          SELECT id,
                 row_number() OVER (
                   PARTITION BY register_id
                   ORDER BY completed_at NULLS LAST, created_at, id
                 )::BIGINT AS seq
          FROM sale
          WHERE receipt_number IS NOT NULL
        )
        UPDATE sale AS s
        SET receipt_seq = ranked.seq
        FROM ranked
        WHERE s.id = ranked.id
        """
    )
    op.execute(
        "ALTER TABLE sale ADD CONSTRAINT ck_sale_receipt_seq "
        "CHECK (receipt_seq IS NULL OR receipt_seq > 0)"
    )
    op.execute(
        "ALTER TABLE sale ADD CONSTRAINT ck_sale_operation_pair "
        "CHECK ((operation_id IS NULL) = (operation_hash IS NULL))"
    )
    op.execute(
        "ALTER TABLE sale ADD CONSTRAINT ck_sale_operation_hash "
        "CHECK (operation_hash IS NULL OR operation_hash ~ '^[0-9a-f]{64}$')"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_sale_register_receipt_seq "
        "ON sale (register_id, receipt_seq) WHERE receipt_seq IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_sale_tenant_operation "
        "ON sale (tenant_id, operation_id) WHERE operation_id IS NOT NULL"
    )

    op.execute("ALTER TABLE sale_item ADD COLUMN parent_sale_item_id UUID")
    op.execute(
        """
        WITH matches AS (
          SELECT return_item.id AS return_item_id,
                 min(parent_item.id::text)::UUID AS parent_item_id,
                 count(*) AS match_count
          FROM sale_item AS return_item
          JOIN sale AS return_sale
            ON return_sale.id = return_item.sale_id
           AND return_sale.sale_type = 'return'
          JOIN sale_item AS parent_item
            ON parent_item.sale_id = return_sale.parent_sale_id
           AND parent_item.catalog_id = return_item.catalog_id
           AND parent_item.batch_id = return_item.batch_id
          GROUP BY return_item.id
        )
        UPDATE sale_item AS return_item
        SET parent_sale_item_id = matches.parent_item_id
        FROM matches
        WHERE return_item.id = matches.return_item_id
          AND matches.match_count = 1
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM sale_item AS return_item
            JOIN sale AS return_sale ON return_sale.id = return_item.sale_id
            WHERE return_sale.sale_type = 'return'
              AND return_item.parent_sale_item_id IS NULL
          ) THEN
            RAISE EXCEPTION
              'Cannot map historical return items to a unique parent sale item';
          END IF;
        END
        $$
        """
    )
    op.execute(
        "ALTER TABLE sale_item ADD CONSTRAINT fk_sale_item_parent_item "
        "FOREIGN KEY (parent_sale_item_id) REFERENCES sale_item(id) "
        "DEFERRABLE INITIALLY DEFERRED"
    )
    op.execute(
        "ALTER TABLE sale_item ADD CONSTRAINT ck_si_parent_not_self "
        "CHECK (parent_sale_item_id IS NULL OR parent_sale_item_id <> id)"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_sale_item_return_parent "
        "ON sale_item (sale_id, parent_sale_item_id) "
        "WHERE parent_sale_item_id IS NOT NULL"
    )

    op.execute("ALTER TABLE batch_movement ADD COLUMN operation_key TEXT")
    op.execute(
        "CREATE UNIQUE INDEX uq_batch_movement_operation_key "
        "ON batch_movement (tenant_id, operation_key) "
        "WHERE operation_key IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_batch_movement_operation_key")
    op.execute("ALTER TABLE batch_movement DROP COLUMN operation_key")

    op.execute("DROP INDEX IF EXISTS uq_sale_item_return_parent")
    op.execute("ALTER TABLE sale_item DROP CONSTRAINT IF EXISTS ck_si_parent_not_self")
    op.execute("ALTER TABLE sale_item DROP CONSTRAINT IF EXISTS fk_sale_item_parent_item")
    op.execute("ALTER TABLE sale_item DROP COLUMN parent_sale_item_id")

    op.execute("DROP INDEX IF EXISTS uq_sale_tenant_operation")
    op.execute("DROP INDEX IF EXISTS uq_sale_register_receipt_seq")
    op.execute("ALTER TABLE sale DROP CONSTRAINT IF EXISTS ck_sale_operation_hash")
    op.execute("ALTER TABLE sale DROP CONSTRAINT IF EXISTS ck_sale_operation_pair")
    op.execute("ALTER TABLE sale DROP CONSTRAINT IF EXISTS ck_sale_receipt_seq")
    op.execute("ALTER TABLE sale DROP COLUMN operation_hash")
    op.execute("ALTER TABLE sale DROP COLUMN operation_id")
    op.execute("ALTER TABLE sale DROP COLUMN receipt_seq")
