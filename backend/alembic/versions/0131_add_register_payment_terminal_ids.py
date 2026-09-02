"""add payment terminal identifiers to registers

Revision ID: 0131
Revises: 0130
Create Date: 2026-09-02
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0131"
down_revision: str | Sequence[str] | None = "0130"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE register ADD COLUMN card_terminal_id TEXT")
    op.execute("ALTER TABLE register ADD COLUMN qr_terminal_id TEXT")
    op.execute(
        """
        ALTER TABLE register
          ADD CONSTRAINT ck_register_card_terminal_id
          CHECK (
            card_terminal_id IS NULL
            OR card_terminal_id ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,63}$'
          )
        """
    )
    op.execute(
        """
        ALTER TABLE register
          ADD CONSTRAINT ck_register_qr_terminal_id
          CHECK (
            qr_terminal_id IS NULL
            OR qr_terminal_id ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,63}$'
          )
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE register DROP CONSTRAINT IF EXISTS ck_register_qr_terminal_id")
    op.execute("ALTER TABLE register DROP CONSTRAINT IF EXISTS ck_register_card_terminal_id")
    op.execute("ALTER TABLE register DROP COLUMN IF EXISTS qr_terminal_id")
    op.execute("ALTER TABLE register DROP COLUMN IF EXISTS card_terminal_id")
