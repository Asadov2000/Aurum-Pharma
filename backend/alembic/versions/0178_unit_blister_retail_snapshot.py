"""Persist internal retail snapshots atomically with physical cart history.

Revision ID: 0178
Revises: 0177
"""

from __future__ import annotations

from collections.abc import Sequence
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType

from alembic import op

revision: str = "0178"
down_revision: str | Sequence[str] | None = "0177"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RETAIL_FIELDS = ("original_retail_unit_price", "gross_amount", "discount_amount", "net_amount")


def _history(filename: str) -> ModuleType:
    path = Path(__file__).with_name(filename)
    spec = spec_from_file_location(f"aurum_0178_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load historical migration {filename}")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sql(sql: str) -> None:
    _history("0177_unit_blister_movement_cost.py")._sql(sql)


HASH_SQL = """
CREATE OR REPLACE FUNCTION public.unit_cart_v2_fingerprint(p_tenant UUID,p_command UUID)
RETURNS TEXT LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE v_branch UUID; v_version INTEGER; v_lines TEXT; v_payload TEXT;
BEGIN
 SELECT branch_id,retail_version INTO v_branch,v_version
   FROM public.unit_stock_cart_command_v2 WHERE tenant_id=p_tenant AND id=p_command;
 IF v_version IS NULL THEN
   SELECT '['||string_agg(replace(json_build_array(
     line_id::TEXT,catalog_id::TEXT,(packaging->>'version')::INTEGER,
     packaging->>'unit',(packaging->>'units_per_pack')::INTEGER,
     (packaging->>'units_per_blister')::INTEGER,(packaging->>'dispense_step')::INTEGER,
     sale_format,quantity)::TEXT,' ',''),',' ORDER BY position)||']'
     INTO v_lines FROM public.unit_stock_cart_line_v2
     WHERE tenant_id=p_tenant AND command_id=p_command;
   v_payload:='["aurum-unit-cart-v2:1",';
 ELSE
   SELECT '['||string_agg(replace(json_build_array(
     line_id::TEXT,catalog_id::TEXT,(packaging->>'version')::INTEGER,
     packaging->>'unit',(packaging->>'units_per_pack')::INTEGER,
     (packaging->>'units_per_blister')::INTEGER,(packaging->>'dispense_step')::INTEGER,
     sale_format,quantity,to_char(discount_amount,'FM999999999990.00'))::TEXT,' ',''),
     ',' ORDER BY position)||']' INTO v_lines FROM public.unit_stock_cart_line_v2
     WHERE tenant_id=p_tenant AND command_id=p_command;
   v_payload:='["aurum-unit-cart-v2:retail:1",';
 END IF;
 v_payload:=v_payload||to_json(p_tenant::TEXT)::TEXT||','||
   to_json(v_branch::TEXT)::TEXT||','||v_lines||']';
 RETURN encode(public.digest(convert_to(v_payload,'UTF8'),'sha256'),'hex');
END $$;
"""

RETAIL_SQL = """
CREATE FUNCTION public.trg_unit_cart_v2_retail_snapshot() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE
 c public.unit_stock_cart_command_v2%ROWTYPE;
 item public.tenant_catalog%ROWTYPE;
 prep JSONB; v_price NUMERIC; v_gross NUMERIC; v_net NUMERIC;
BEGIN
 SELECT * INTO c FROM public.unit_stock_cart_command_v2
   WHERE tenant_id=NEW.tenant_id AND id=NEW.command_id;
 IF c.retail_version IS NULL THEN
   IF num_nonnulls(NEW.original_retail_unit_price,NEW.gross_amount,
     NEW.discount_amount,NEW.net_amount)<>0 THEN
     RAISE EXCEPTION 'unit_cart_v2_unpriced_has_retail' USING ERRCODE='23514';
   END IF;
   RETURN NEW;
 END IF;
 SELECT * INTO item FROM public.tenant_catalog
   WHERE tenant_id=NEW.tenant_id AND id=NEW.catalog_id FOR NO KEY UPDATE;
 prep:=item.unit_sale_preparation;
 IF item.id IS NULL OR NOT item.is_active OR item.deleted_at IS NOT NULL
   OR item.currency IS DISTINCT FROM 'TJS' OR prep IS NULL
   OR prep->>'version' IS DISTINCT FROM '1' OR prep->>'state' IS DISTINCT FROM 'prepared'
   OR prep->>'unit' IS DISTINCT FROM NEW.packaging->>'unit'
   OR prep->>'units_per_pack' IS DISTINCT FROM NEW.packaging->>'units_per_pack'
   OR prep->>'units_per_blister' IS DISTINCT FROM NEW.packaging->>'units_per_blister'
   OR prep->>'dispense_step' IS DISTINCT FROM NEW.packaging->>'dispense_step' THEN
   RAISE EXCEPTION 'unit_cart_v2_retail_source_invalid' USING ERRCODE='23514';
 END IF;
 IF NEW.sale_format='pack' THEN v_price:=item.base_price;
 ELSE
   v_price:=(prep->>'unit_price')::NUMERIC;
   IF NEW.sale_format='blister' THEN
     v_price:=v_price*(NEW.packaging->>'units_per_blister')::INTEGER;
   END IF;
 END IF;
 v_gross:=v_price*NEW.quantity;
 v_net:=v_gross-NEW.discount_amount;
 IF v_price IS NULL OR NOT(v_price>0 AND v_price<=999999999999.99)
   OR v_price<>trunc(v_price,2) OR v_gross IS NULL
   OR NOT(v_gross>0 AND v_gross<=999999999999.99)
   OR NEW.discount_amount IS NULL
   OR NOT(NEW.discount_amount>=0 AND NEW.discount_amount<v_gross)
   OR NOT(v_net>0 AND v_net<=999999999999.99) THEN
   RAISE EXCEPTION 'unit_cart_v2_retail_amount_invalid' USING ERRCODE='23514';
 END IF;
 IF (NEW.original_retail_unit_price IS NOT NULL
       AND NEW.original_retail_unit_price IS DISTINCT FROM v_price)
   OR (NEW.gross_amount IS NOT NULL AND NEW.gross_amount IS DISTINCT FROM v_gross)
   OR (NEW.net_amount IS NOT NULL AND NEW.net_amount IS DISTINCT FROM v_net) THEN
   RAISE EXCEPTION 'unit_cart_v2_retail_snapshot_mismatch' USING ERRCODE='23514';
 END IF;
 NEW.original_retail_unit_price:=v_price;NEW.gross_amount:=v_gross;NEW.net_amount:=v_net;
 RETURN NEW;
END $$;
"""

SNAPSHOT_CHECK = """
num_nonnulls(original_retail_unit_price,gross_amount,discount_amount,net_amount)=0
OR COALESCE(num_nonnulls(original_retail_unit_price,gross_amount,discount_amount,net_amount)=4
  AND original_retail_unit_price>0 AND original_retail_unit_price<=999999999999.99
  AND gross_amount>0 AND gross_amount<=999999999999.99
  AND discount_amount>=0 AND discount_amount<gross_amount
  AND net_amount>0 AND net_amount<=999999999999.99
  AND gross_amount=original_retail_unit_price*quantity
  AND net_amount=gross_amount-discount_amount,false)
"""


def _complete() -> None:
    history = _history("0176_unit_blister_physical_dispensing.py")
    sql = history.COMPLETE_SQL.replace(
        "   OR v_total>99999999999",
        """   OR v_total>99999999999
   OR (SELECT coalesce(sum(cost_amount),0) FROM public.unit_stock_movement_v2
     WHERE tenant_id=p_tenant AND command_id=p_command)>999999999999.99
   OR EXISTS(SELECT 1 FROM public.unit_stock_cart_line_v2 r
     WHERE r.tenant_id=p_tenant AND r.command_id=p_command
       AND ((c.retail_version IS NULL AND num_nonnulls(r.original_retail_unit_price,
         r.gross_amount,r.discount_amount,r.net_amount)<>0)
       OR (c.retail_version IS NOT NULL AND num_nonnulls(r.original_retail_unit_price,
         r.gross_amount,r.discount_amount,r.net_amount)<>4)))
   OR (c.retail_version IS NOT NULL AND (
     (SELECT coalesce(sum(gross_amount),0) FROM public.unit_stock_cart_line_v2
       WHERE tenant_id=p_tenant AND command_id=p_command)>999999999999.99
     OR (SELECT coalesce(sum(discount_amount),0) FROM public.unit_stock_cart_line_v2
       WHERE tenant_id=p_tenant AND command_id=p_command)>999999999999.99
     OR (SELECT coalesce(sum(net_amount),0) FROM public.unit_stock_cart_line_v2
       WHERE tenant_id=p_tenant AND command_id=p_command)>999999999999.99
     OR EXISTS(SELECT 1 FROM public.unit_stock_movement_v2 m
       WHERE m.tenant_id=p_tenant AND m.command_id=p_command AND m.units_taken>0
         AND (m.cost_version IS DISTINCT FROM 1 OR m.cost_amount IS NULL))))""",
        1,
    )
    if "sum(net_amount)" not in sql:
        raise RuntimeError("Historical completeness guard changed")
    _sql(sql.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION"))


def _audit() -> None:
    history = _history("0175_received_blister_initialization.py")
    sql = history._history("0169_prepared_unit_return_quarantine.py")._audit_redactor(returns=True)
    sql = history._replace(
        sql,
        "'unit_stock_lot','unit_stock_entry','pos_unit_sale_line'",
        "'unit_stock_lot','unit_stock_entry','pos_unit_sale_line',"
        "'unit_stock_lot_v2','unit_stock_entry_v2','unit_stock_movement_v2',"
        "'unit_stock_cart_line_v2'",
    )
    sql = history._replace(
        sql,
        "'purchase_cost_per_pack','cost_original_pack_cost','original_cost_amount'",
        "'purchase_cost_per_pack','cost_original_pack_cost','original_cost_amount',"
        "'original_retail_unit_price','gross_amount','discount_amount','net_amount'",
    )
    _sql(sql)


def upgrade() -> None:
    _sql("""
      ALTER TABLE public.unit_stock_cart_command_v2 ADD COLUMN retail_version INTEGER,
        ADD COLUMN currency TEXT;
      ALTER TABLE public.unit_stock_cart_line_v2
        ADD COLUMN original_retail_unit_price NUMERIC(14,2),
        ADD COLUMN gross_amount NUMERIC(14,2),ADD COLUMN discount_amount NUMERIC(14,2),
        ADD COLUMN net_amount NUMERIC(14,2);
    """)
    op.create_check_constraint(
        "ck_unit_cart_v2_retail_mode",
        "unit_stock_cart_command_v2",
        "(retail_version IS NULL AND currency IS NULL) OR "
        "COALESCE(retail_version=1 AND currency='TJS',false)",
    )
    op.create_check_constraint(
        "ck_unit_cart_v2_retail_snapshot", "unit_stock_cart_line_v2", SNAPSHOT_CHECK
    )
    _sql(HASH_SQL)
    _sql(RETAIL_SQL)
    _sql("""
      ALTER FUNCTION public.trg_unit_cart_v2_retail_snapshot() OWNER TO aurum_schema_owner;
      REVOKE ALL ON FUNCTION public.trg_unit_cart_v2_retail_snapshot()
        FROM PUBLIC,aurum_app,aurum_support;
      CREATE TRIGGER a11_unit_cart_v2_retail_snapshot BEFORE INSERT
        ON public.unit_stock_cart_line_v2 FOR EACH ROW
        EXECUTE FUNCTION public.trg_unit_cart_v2_retail_snapshot();
    """)
    _complete()
    _audit()


def downgrade() -> None:
    _sql("""
      LOCK TABLE public.unit_stock_cart_command_v2,public.unit_stock_cart_line_v2
        IN ACCESS EXCLUSIVE MODE;
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.unit_stock_cart_command_v2 WHERE retail_version IS NOT NULL)
          OR EXISTS(SELECT 1 FROM public.unit_stock_cart_line_v2 WHERE
            num_nonnulls(original_retail_unit_price,gross_amount,discount_amount,net_amount)>0) THEN
          RAISE EXCEPTION 'unit_cart_v2_retail_downgrade_loses_history' USING ERRCODE='55000';
        END IF;
      END $$;
      DROP TRIGGER a11_unit_cart_v2_retail_snapshot ON public.unit_stock_cart_line_v2;
      DROP FUNCTION public.trg_unit_cart_v2_retail_snapshot();
    """)
    _sql(
        _history("0176_unit_blister_physical_dispensing.py").HASH_SQL.replace(
            "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION"
        )
    )
    prior = _history("0177_unit_blister_movement_cost.py")
    prior._complete(costs=True)
    prior._audit(costs=True)
    op.drop_constraint("ck_unit_cart_v2_retail_snapshot", "unit_stock_cart_line_v2", type_="check")
    op.drop_constraint("ck_unit_cart_v2_retail_mode", "unit_stock_cart_command_v2", type_="check")
    for field in reversed(RETAIL_FIELDS):
        op.drop_column("unit_stock_cart_line_v2", field)
    op.drop_column("unit_stock_cart_command_v2", "currency")
    op.drop_column("unit_stock_cart_command_v2", "retail_version")
