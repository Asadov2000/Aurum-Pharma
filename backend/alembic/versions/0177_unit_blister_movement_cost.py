"""Freeze exact cost of new physical movements; legacy cost remains unknown.

Revision ID: 0177
Revises: 0176
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType

from alembic import op

revision: str = "0177"
down_revision: str | Sequence[str] | None = "0176"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COST_FIELDS = (
    "cost_pack_ordinal_start",
    "cost_pack_count",
    "cost_unit_offset",
    "cost_base_units",
    "cost_original_pack_cost",
    "cost_amount",
    "cost_version",
)


def _sql(sql: str) -> None:
    start = 0
    for match in re.finditer(r"\$\$.*?\$\$|'(?:''|[^'])*'|;", sql, flags=re.DOTALL):
        if match.group() == ";":
            statement = sql[start : match.start()].strip()
            if statement:
                op.execute(statement.replace(":", r"\:"))
            start = match.end()
    if sql[start:].strip():
        op.execute(sql[start:].replace(":", r"\:"))


def _history(filename: str) -> ModuleType:
    path = Path(__file__).with_name(filename)
    spec = spec_from_file_location(f"aurum_0177_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load historical migration {filename}")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


COST_SQL = """
CREATE FUNCTION public.trg_unit_movement_v2_cost_snapshot() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE
 l public.unit_stock_lot_v2%ROWTYPE;
 p public.unit_stock_opened_pack_v2%ROWTYPE;
 v_start BIGINT;
 v_count BIGINT;
 v_offset BIGINT;
 v_units BIGINT;
 v_p INTEGER;
 v_cents NUMERIC;
 v_amount NUMERIC;
BEGIN
 IF NEW.kind='pack_open' THEN
   IF num_nonnulls(NEW.cost_pack_ordinal_start,NEW.cost_pack_count,NEW.cost_unit_offset,
     NEW.cost_base_units,NEW.cost_original_pack_cost,NEW.cost_amount,NEW.cost_version)<>0 THEN
     RAISE EXCEPTION 'unit_movement_v2_open_has_cost' USING ERRCODE='23514';
   END IF;
   RETURN NEW;
 END IF;
 -- Retain catalog -> lot -> opened-pack lock order, including direct SQL callers.
 PERFORM 1 FROM public.tenant_catalog WHERE tenant_id=NEW.tenant_id AND id=NEW.catalog_id
   FOR NO KEY UPDATE;
 SELECT * INTO l FROM public.unit_stock_lot_v2
   WHERE tenant_id=NEW.tenant_id AND id=NEW.lot_id
     AND branch_id=NEW.branch_id AND catalog_id=NEW.catalog_id FOR NO KEY UPDATE;
 IF l.id IS NULL OR NOT (l.purchase_cost_per_pack BETWEEN 0 AND 999999999999.99)
   OR l.cost_version<>1 THEN
   RAISE EXCEPTION 'unit_movement_v2_cost_source_invalid' USING ERRCODE='23514';
 END IF;
 v_p:=(l.packaging->>'units_per_pack')::INTEGER;
 v_units:=NEW.units_taken;
 IF NEW.kind='pack_take' THEN
   v_start:=NEW.sealed_start;v_count:=NEW.sealed_count;v_offset:=0;
   IF v_start IS NULL OR v_count IS NULL OR v_start<1 OR v_count<1
     OR v_start::NUMERIC+v_count-1>l.initial_closed_packs
     OR v_units::NUMERIC<>v_count::NUMERIC*v_p THEN
     RAISE EXCEPTION 'unit_movement_v2_cost_sealed_range' USING ERRCODE='23514';
   END IF;
   v_amount:=v_count::NUMERIC*l.purchase_cost_per_pack;
 ELSIF NEW.kind IN ('unit_take','blister_take') THEN
   SELECT * INTO p FROM public.unit_stock_opened_pack_v2
     WHERE tenant_id=NEW.tenant_id AND id=NEW.pack_id AND lot_id=l.id
       AND branch_id=NEW.branch_id AND catalog_id=NEW.catalog_id FOR NO KEY UPDATE;
   IF p.id IS NULL OR NEW.before_state IS DISTINCT FROM p.state
     OR p.remaining_units<>public.unit_pack_v2_remaining(p.state,l.packaging) THEN
     RAISE EXCEPTION 'unit_movement_v2_cost_opened_source' USING ERRCODE='23514';
   END IF;
   v_start:=p.pack_ordinal;v_count:=1;v_offset:=v_p-p.remaining_units;
   IF v_start<1 OR v_start>l.initial_closed_packs OR v_offset<0
     OR v_units<=0 OR v_offset::NUMERIC+v_units>v_p THEN
     RAISE EXCEPTION 'unit_movement_v2_cost_opened_range' USING ERRCODE='23514';
   END IF;
   v_cents:=l.purchase_cost_per_pack*100;
   -- NUMERIC div avoids floating-point error and overflow of intermediate cents.
   v_amount:=(div(v_cents*(v_offset+v_units),v_p)-div(v_cents*v_offset,v_p))/100;
 ELSE
   RAISE EXCEPTION 'unit_movement_v2_cost_kind' USING ERRCODE='23514';
 END IF;
 IF v_units<=0 OR v_units>99999999999 OR NOT(v_amount BETWEEN 0 AND 999999999999.99)
   OR v_amount+(SELECT coalesce(sum(cost_amount),0) FROM public.unit_stock_movement_v2
     WHERE tenant_id=NEW.tenant_id AND command_id=NEW.command_id)>999999999999.99 THEN
   RAISE EXCEPTION 'unit_movement_v2_cost_amount_limit' USING ERRCODE='23514';
 END IF;
 IF (NEW.cost_pack_ordinal_start IS NOT NULL AND NEW.cost_pack_ordinal_start<>v_start)
   OR (NEW.cost_pack_count IS NOT NULL AND NEW.cost_pack_count<>v_count)
   OR (NEW.cost_unit_offset IS NOT NULL AND NEW.cost_unit_offset<>v_offset)
   OR (NEW.cost_base_units IS NOT NULL AND NEW.cost_base_units<>v_units)
   OR (NEW.cost_original_pack_cost IS NOT NULL
     AND NEW.cost_original_pack_cost IS DISTINCT FROM l.purchase_cost_per_pack)
   OR (NEW.cost_amount IS NOT NULL AND NEW.cost_amount IS DISTINCT FROM v_amount)
   OR (NEW.cost_version IS NOT NULL AND NEW.cost_version<>1) THEN
   RAISE EXCEPTION 'unit_movement_v2_cost_snapshot_mismatch' USING ERRCODE='23514';
 END IF;
 NEW.cost_pack_ordinal_start:=v_start;NEW.cost_pack_count:=v_count;
 NEW.cost_unit_offset:=v_offset;NEW.cost_base_units:=v_units;
 NEW.cost_original_pack_cost:=l.purchase_cost_per_pack;NEW.cost_amount:=v_amount;NEW.cost_version:=1;
 RETURN NEW;
END $$;
"""

SNAPSHOT_CHECK = """
num_nonnulls(cost_pack_ordinal_start,cost_pack_count,cost_unit_offset,cost_base_units,
  cost_original_pack_cost,cost_amount,cost_version)=0 OR COALESCE(
  num_nonnulls(cost_pack_ordinal_start,cost_pack_count,cost_unit_offset,cost_base_units,
    cost_original_pack_cost,cost_amount,cost_version)=7
  AND kind IN ('pack_take','unit_take','blister_take') AND cost_version=1
  AND cost_pack_ordinal_start BETWEEN 1 AND 99999999999
  AND cost_pack_count BETWEEN 1 AND 99999999999
  AND cost_pack_ordinal_start::NUMERIC+cost_pack_count-1<=99999999999
  AND cost_unit_offset BETWEEN 0 AND 9999
  AND cost_base_units=units_taken AND cost_base_units BETWEEN 1 AND 99999999999
  AND cost_original_pack_cost BETWEEN 0 AND 999999999999.99
  AND cost_amount BETWEEN 0 AND 999999999999.99
  AND ((kind='pack_take' AND cost_unit_offset=0
      AND cost_pack_ordinal_start=sealed_start AND cost_pack_count=sealed_count
      AND cost_amount=cost_pack_count::NUMERIC*cost_original_pack_cost)
    OR (kind IN ('unit_take','blister_take') AND cost_pack_count=1)),false)
"""


def _audit(*, costs: bool) -> None:
    history = _history("0175_received_blister_initialization.py")
    sql = history._history("0169_prepared_unit_return_quarantine.py")._audit_redactor(returns=True)
    tables = "'unit_stock_lot_v2','unit_stock_entry_v2'"
    if costs:
        tables += ",'unit_stock_movement_v2'"
    sql = history._replace(
        sql,
        "'unit_stock_lot','unit_stock_entry','pos_unit_sale_line'",
        "'unit_stock_lot','unit_stock_entry','pos_unit_sale_line'," + tables,
    )
    _sql(sql)


def _complete(*, costs: bool) -> None:
    history = _history("0176_unit_blister_physical_dispensing.py")
    sql = history.COMPLETE_SQL
    if costs:
        sql = sql.replace(
            "   OR v_total>99999999999",
            """   OR v_total>99999999999
   OR (SELECT coalesce(sum(cost_amount),0) FROM public.unit_stock_movement_v2
     WHERE tenant_id=p_tenant AND command_id=p_command)>999999999999.99""",
            1,
        )
        if "sum(cost_amount)" not in sql:
            raise RuntimeError("Historical cart guard changed; review its cost boundary")
    _sql(sql.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION"))


def upgrade() -> None:
    _sql("""
      ALTER TABLE public.unit_stock_movement_v2
        ADD COLUMN cost_pack_ordinal_start BIGINT,ADD COLUMN cost_pack_count BIGINT,
        ADD COLUMN cost_unit_offset BIGINT,ADD COLUMN cost_base_units BIGINT,
        ADD COLUMN cost_original_pack_cost NUMERIC(14,2),ADD COLUMN cost_amount NUMERIC(14,2),
        ADD COLUMN cost_version INTEGER;
    """)
    op.create_check_constraint(
        "ck_unit_movement_v2_cost_snapshot", "unit_stock_movement_v2", SNAPSHOT_CHECK
    )
    _sql(COST_SQL)
    _sql("""
      ALTER FUNCTION public.trg_unit_movement_v2_cost_snapshot() OWNER TO aurum_schema_owner;
      REVOKE ALL ON FUNCTION public.trg_unit_movement_v2_cost_snapshot()
        FROM PUBLIC,aurum_app,aurum_support;
      CREATE TRIGGER a11_unit_movement_v2_cost_snapshot BEFORE INSERT
        ON public.unit_stock_movement_v2 FOR EACH ROW
        EXECUTE FUNCTION public.trg_unit_movement_v2_cost_snapshot();
    """)
    _complete(costs=True)
    _audit(costs=True)


def downgrade() -> None:
    _sql("""
      LOCK TABLE public.unit_stock_movement_v2 IN ACCESS EXCLUSIVE MODE;
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.unit_stock_movement_v2 WHERE
          num_nonnulls(cost_pack_ordinal_start,cost_pack_count,cost_unit_offset,cost_base_units,
            cost_original_pack_cost,cost_amount,cost_version)>0) THEN
          RAISE EXCEPTION 'unit_movement_v2_cost_downgrade_loses_history' USING ERRCODE='55000';
        END IF;
      END $$;
      DROP TRIGGER a11_unit_movement_v2_cost_snapshot ON public.unit_stock_movement_v2;
      DROP FUNCTION public.trg_unit_movement_v2_cost_snapshot();
    """)
    _complete(costs=False)
    _audit(costs=False)
    op.drop_constraint("ck_unit_movement_v2_cost_snapshot", "unit_stock_movement_v2", type_="check")
    for field in reversed(COST_FIELDS):
        op.drop_column("unit_stock_movement_v2", field)
