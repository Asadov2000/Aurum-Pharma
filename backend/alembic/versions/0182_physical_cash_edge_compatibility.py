"""Keep the legacy Edge writer outside app-only physical checkout queries.

Revision ID: 0182
Revises: 0181
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "0182"
down_revision: str | Sequence[str] | None = "0181"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DECLARATION_OLD = """  v_parent_status TEXT;
BEGIN"""
DECLARATION_NEW = """  v_parent_status TEXT;
  v_physical_cash BOOLEAN := false;
BEGIN
  -- Separate statements avoid planning an app-only relation under Edge's
  -- intentionally limited writer role. All other writers retain legacy checks.
  IF current_user = 'aurum_app' AND TG_OP = 'UPDATE' THEN
    SELECT EXISTS(SELECT 1 FROM public.unit_stock_cart_command_v2
      WHERE tenant_id=NEW.tenant_id AND checkout_sale_id=NEW.id)
      INTO v_physical_cash;
  END IF;"""
ITEM_OPEN_OLD = """    IF EXISTS(SELECT 1 FROM public.unit_stock_cart_command_v2
      WHERE tenant_id=NEW.tenant_id AND checkout_sale_id=NEW.id) THEN"""
ITEM_OPEN_NEW = "    IF v_physical_cash THEN"
ITEM_TOTAL_OLD = """    IF NOT EXISTS(SELECT 1 FROM public.unit_stock_cart_command_v2
      WHERE tenant_id=NEW.tenant_id AND checkout_sale_id=NEW.id) AND ("""
ITEM_TOTAL_NEW = "    IF NOT v_physical_cash AND ("


def _replace(old: str, new: str) -> None:
    definition = op.get_bind().scalar(
        text("SELECT pg_get_functiondef(" "'public.trg_guard_sale_immutability()'::regprocedure)")
    )
    if not isinstance(definition, str) or definition.count(old) != 1:
        raise RuntimeError("Physical checkout sale guard differs from expected definition")
    # CREATE OR REPLACE retains invoker semantics, owner and the existing private ACL.
    op.get_bind().exec_driver_sql(definition.replace(old, new, 1))


def upgrade() -> None:
    _replace(DECLARATION_OLD, DECLARATION_NEW)
    _replace(ITEM_OPEN_OLD, ITEM_OPEN_NEW)
    _replace(ITEM_TOTAL_OLD, ITEM_TOTAL_NEW)


def downgrade() -> None:
    _replace(ITEM_OPEN_NEW, ITEM_OPEN_OLD)
    _replace(ITEM_TOTAL_NEW, ITEM_TOTAL_OLD)
    _replace(DECLARATION_NEW, DECLARATION_OLD)
