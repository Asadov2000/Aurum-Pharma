"""Prevent closing shifts that contain unfinished unit-cart sales.

Revision ID: 0199
Revises: 0198
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0199"
down_revision: str | Sequence[str] | None = "0198"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GUARD = """
CREATE FUNCTION public.trg_shift_unfinished_unit_cart() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 IF OLD.status='open' AND NEW.status='closed' AND EXISTS(
   SELECT 1 FROM public.sale s
   JOIN public.pos_unit_cart_item item
     ON item.tenant_id=s.tenant_id AND item.sale_id=s.id AND item.branch_id=s.branch_id
   WHERE s.tenant_id=OLD.tenant_id AND s.branch_id=OLD.branch_id AND s.shift_id=OLD.id
     AND s.sale_type='sale' AND s.status='draft'
 ) THEN
   RAISE EXCEPTION 'Cannot close a shift while unfinished sales contain items or payments'
     USING ERRCODE='23514',CONSTRAINT='ck_shift_unfinished_unit_cart';
 END IF;
 RETURN NEW;
END $$;
"""


def upgrade() -> None:
    # Unit-cart edits lock Sale then Shift. The shift UPDATE already owns its row
    # lock: a plain fresh read avoids taking Sale locks in the reverse order.
    op.execute(GUARD)
    op.execute(
        "ALTER FUNCTION public.trg_shift_unfinished_unit_cart() OWNER TO aurum_schema_owner"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.trg_shift_unfinished_unit_cart() "
        "FROM PUBLIC,aurum_app,aurum_support"
    )
    op.execute("""
        CREATE TRIGGER shift_unfinished_unit_cart_guard
        BEFORE UPDATE OF status ON public.shift
        FOR EACH ROW EXECUTE FUNCTION public.trg_shift_unfinished_unit_cart()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER shift_unfinished_unit_cart_guard ON public.shift")
    op.execute("DROP FUNCTION public.trg_shift_unfinished_unit_cart()")
