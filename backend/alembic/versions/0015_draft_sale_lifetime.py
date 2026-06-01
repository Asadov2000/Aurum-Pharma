"""tenant_settings.draft_sale_lifetime_min — configurable POS draft TTL.

The POS register auto-saves the in-progress sale to the cashier's device and
restores it on reload; a draft idle longer than this many minutes is dropped
instead of being silently reopened. Default 30, bounded 5..240.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0015"
down_revision: str | Sequence[str] | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE tenant_settings "
        "ADD COLUMN draft_sale_lifetime_min INT NOT NULL DEFAULT 30"
    )
    op.execute(
        "ALTER TABLE tenant_settings "
        "ADD CONSTRAINT ck_tenant_settings_draft_ttl "
        "CHECK (draft_sale_lifetime_min BETWEEN 5 AND 240)"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE tenant_settings DROP CONSTRAINT IF EXISTS ck_tenant_settings_draft_ttl"
    )
    op.execute("ALTER TABLE tenant_settings DROP COLUMN IF EXISTS draft_sale_lifetime_min")
