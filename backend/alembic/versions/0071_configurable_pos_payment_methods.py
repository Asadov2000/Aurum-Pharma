"""pos: configurable payment methods and QR support

Revision ID: 0071
Revises: 0070
Create Date: 2026-07-30
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0071"
down_revision: str | Sequence[str] | None = "0070"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE tenant_settings
          ADD COLUMN pos_payment_methods JSONB NOT NULL
            DEFAULT '["cash","card","qr"]'::jsonb,
          ADD COLUMN pos_mixed_payment_enabled BOOLEAN NOT NULL DEFAULT true,
          ADD CONSTRAINT ck_tenant_settings_pos_payment_methods CHECK (
            jsonb_typeof(pos_payment_methods) = 'array'
            AND jsonb_array_length(pos_payment_methods) BETWEEN 1 AND 3
            AND pos_payment_methods <@ '["cash","card","qr"]'::jsonb
            AND jsonb_array_length(pos_payment_methods) = (
              CASE WHEN pos_payment_methods ? 'cash' THEN 1 ELSE 0 END
              + CASE WHEN pos_payment_methods ? 'card' THEN 1 ELSE 0 END
              + CASE WHEN pos_payment_methods ? 'qr' THEN 1 ELSE 0 END
            )
          )
        """
    )
    op.execute(
        """
        ALTER TABLE sale_payment
          DROP CONSTRAINT IF EXISTS ck_sp_method,
          DROP CONSTRAINT IF EXISTS sale_payment_payment_method_check
        """
    )
    op.execute(
        """
        ALTER TABLE sale_payment
          ADD CONSTRAINT sale_payment_payment_method_check
          CHECK (payment_method IN ('cash','card','qr','bank_transfer'))
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM sale_payment WHERE payment_method = 'qr'
          ) THEN
            RAISE EXCEPTION
              'Cannot downgrade 0071 while QR payment history exists';
          END IF;
        END
        $$
        """
    )
    op.execute(
        """
        ALTER TABLE sale_payment
          DROP CONSTRAINT IF EXISTS ck_sp_method,
          DROP CONSTRAINT IF EXISTS sale_payment_payment_method_check
        """
    )
    op.execute(
        """
        ALTER TABLE sale_payment
          ADD CONSTRAINT sale_payment_payment_method_check
          CHECK (payment_method IN ('cash','card','bank_transfer'))
        """
    )
    op.execute(
        """
        ALTER TABLE tenant_settings
          DROP CONSTRAINT ck_tenant_settings_pos_payment_methods,
          DROP COLUMN pos_mixed_payment_enabled,
          DROP COLUMN pos_payment_methods
        """
    )
