"""enforce unconditional expired medicine sale blocking

Revision ID: 0105
Revises: 0104
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0105"
down_revision: str | None = "0104"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


LEGACY_CONSTRAINT = "tenant_settings_expired_sale_mode_check"
STRICT_CONSTRAINT = "ck_tenant_settings_expired_sale_mode_strict"


def upgrade() -> None:
    op.execute("LOCK TABLE public.tenant_settings IN ACCESS EXCLUSIVE MODE")
    op.execute("UPDATE public.tenant_settings SET expired_sale_mode = 'strict'")
    op.drop_constraint(LEGACY_CONSTRAINT, "tenant_settings", type_="check")
    op.create_check_constraint(
        STRICT_CONSTRAINT,
        "tenant_settings",
        "expired_sale_mode = 'strict'",
    )


def downgrade() -> None:
    op.drop_constraint(STRICT_CONSTRAINT, "tenant_settings", type_="check")
    op.create_check_constraint(
        LEGACY_CONSTRAINT,
        "tenant_settings",
        "expired_sale_mode IN ('strict', 'warning', 'off')",
    )
