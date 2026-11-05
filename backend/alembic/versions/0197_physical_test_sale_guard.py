"""Reject real physical cash posting for training sales without altering history.

Revision ID: 0197
Revises: 0196
"""

from alembic import op
from sqlalchemy import text

revision = "0197"
down_revision = "0196"
branch_labels = None
depends_on = None

GUARDS = (
    (
        "public.assert_physical_checkout(uuid,uuid,boolean)",
        "   OR s.currency<>'TJS' OR s.status NOT IN ('draft','completed')",
    ),
    (
        "public.trg_physical_stock_checkout_binding()",
        " IF s.id IS NULL OR s.status<>'draft' OR s.sale_type<>'sale' OR s.currency<>'TJS'",
    ),
)


def _replace(signature: str, old: str, new: str) -> None:
    definition = op.get_bind().scalar(
        text("SELECT pg_get_functiondef(CAST(:signature AS regprocedure))"),
        {"signature": signature},
    )
    if not isinstance(definition, str) or definition.count(old) != 1:
        raise RuntimeError("Physical test sale guard changed")
    op.get_bind().exec_driver_sql(definition.replace(old, new, 1))


def upgrade() -> None:
    for signature, original in GUARDS:
        _replace(signature, original, original + " OR s.is_test")


def downgrade() -> None:
    for signature, original in reversed(GUARDS):
        _replace(signature, original + " OR s.is_test", original)
