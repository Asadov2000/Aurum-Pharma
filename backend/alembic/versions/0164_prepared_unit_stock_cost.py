"""Immutable physical pack cost snapshots for newly priced prepared stock.

Revision ID: 0164
Revises: 0163
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from alembic import op

revision: str = "0164"
down_revision: str | Sequence[str] | None = "0163"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _sql(sql: str) -> None:
    start = 0
    for match in re.finditer(r"\$\$.*?\$\$|'(?:''|[^'])*'|;", sql, flags=re.DOTALL):
        if match.group() == ";":
            statement = sql[start : match.start()].strip()
            if statement:
                op.execute(statement)
            start = match.end()
    if sql[start:].strip():
        op.execute(sql[start:])


COST_SQL = r"""
CREATE FUNCTION public.trg_unit_stock_cost_identity() RETURNS TRIGGER
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
DECLARE v_lot public.unit_stock_lot%ROWTYPE;
BEGIN
 IF TG_TABLE_NAME='unit_stock_lot' THEN
   IF NOT ((NEW.initial_closed_packs IS NULL AND NEW.purchase_cost_per_pack IS NULL
       AND NEW.cost_version IS NULL)
     OR (NEW.initial_closed_packs IS NOT NULL AND NEW.initial_closed_packs>0
       AND NEW.initial_closed_packs::NUMERIC*NEW.units_per_pack<=99999999999
       AND NEW.purchase_cost_per_pack IS NOT NULL
       AND NEW.purchase_cost_per_pack>=0 AND NEW.purchase_cost_per_pack<=999999999999.99
       AND NEW.cost_version IS NOT NULL AND NEW.cost_version=1)) THEN
     RAISE EXCEPTION 'unit_stock_cost_basis_invalid' USING ERRCODE='23514';
   END IF;
 ELSE
   SELECT * INTO v_lot FROM public.unit_stock_lot
     WHERE tenant_id=NEW.tenant_id AND id=NEW.lot_id;
   IF (v_lot.cost_version IS NULL AND NEW.pack_ordinal IS NOT NULL)
     OR (v_lot.cost_version IS NOT NULL AND (NEW.pack_ordinal IS NULL
       OR NEW.pack_ordinal<=v_lot.initial_closed_packs-v_lot.closed_packs
       OR NEW.pack_ordinal>v_lot.initial_closed_packs)) THEN
     RAISE EXCEPTION 'unit_stock_cost_pack_ordinal' USING ERRCODE='23514';
   END IF;
 END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION public.trg_unit_stock_cost_snapshot() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE
 v_lot public.unit_stock_lot%ROWTYPE;
 v_pack public.unit_stock_opened_pack%ROWTYPE;
 v_start BIGINT; v_count BIGINT; v_offset BIGINT; v_units BIGINT;
 v_cents NUMERIC; v_amount NUMERIC; v_whole NUMERIC; v_tail NUMERIC;
BEGIN
 -- Preserve the existing lock order and derive from the protected stock projection.
 PERFORM 1 FROM public.tenant_catalog
   WHERE tenant_id=NEW.tenant_id AND id=NEW.catalog_id FOR NO KEY UPDATE;
 SELECT * INTO v_lot FROM public.unit_stock_lot
   WHERE tenant_id=NEW.tenant_id AND id=NEW.lot_id FOR UPDATE;
 IF NEW.kind='initialize' THEN
   IF v_lot.cost_version IS NOT NULL AND NEW.closed_delta<>v_lot.initial_closed_packs THEN
     RAISE EXCEPTION 'unit_stock_cost_initial_quantity' USING ERRCODE='23514';
   END IF;
 END IF;
 IF v_lot.cost_version IS NULL OR NEW.kind='initialize' THEN
   IF NEW.cost_pack_ordinal_start IS NOT NULL OR NEW.cost_pack_count IS NOT NULL
      OR NEW.cost_unit_offset IS NOT NULL OR NEW.cost_base_units IS NOT NULL
      OR NEW.cost_original_pack_cost IS NOT NULL OR NEW.cost_amount IS NOT NULL
      OR NEW.cost_version IS NOT NULL THEN
     RAISE EXCEPTION 'unit_stock_unknown_cost_snapshot' USING ERRCODE='23514';
   END IF;
   RETURN NEW;
 END IF;
 IF NEW.entry_order<>(SELECT count(*) FROM public.unit_stock_entry
   WHERE tenant_id=NEW.tenant_id AND operation_id=NEW.operation_id) THEN
   RAISE EXCEPTION 'unit_stock_cost_entry_order' USING ERRCODE='23514';
 END IF;
 v_units:=-NEW.units_delta;
 IF NEW.kind='unit_take' THEN
   SELECT * INTO v_pack FROM public.unit_stock_opened_pack
     WHERE tenant_id=NEW.tenant_id AND id=NEW.pack_id AND lot_id=NEW.lot_id FOR UPDATE;
   IF v_pack.pack_ordinal IS NULL OR v_pack.remaining_units<=0 THEN
     RAISE EXCEPTION 'unit_stock_cost_pack_origin' USING ERRCODE='23514';
   END IF;
   v_start:=v_pack.pack_ordinal; v_count:=1;
   v_offset:=v_lot.units_per_pack-v_pack.remaining_units;
   IF v_units<=0 OR v_offset+v_units>v_lot.units_per_pack THEN
     RAISE EXCEPTION 'unit_stock_cost_unit_range' USING ERRCODE='23514';
   END IF;
 ELSE
   v_start:=v_lot.initial_closed_packs-v_lot.closed_packs+1;
   v_count:=-NEW.closed_delta; v_offset:=0;
   IF v_count<=0 OR v_start<=0 OR v_start+v_count-1>v_lot.initial_closed_packs
      OR v_units<=0 OR v_units>(v_count::NUMERIC*v_lot.units_per_pack)
      OR v_units<=(v_count::NUMERIC-1)*v_lot.units_per_pack THEN
     RAISE EXCEPTION 'unit_stock_cost_closed_range' USING ERRCODE='23514';
   END IF;
   IF NEW.pack_id IS NOT NULL THEN
     SELECT * INTO v_pack FROM public.unit_stock_opened_pack
       WHERE tenant_id=NEW.tenant_id AND id=NEW.pack_id AND lot_id=NEW.lot_id FOR UPDATE;
     IF v_pack.pack_ordinal IS DISTINCT FROM v_start+v_count-1 THEN
       RAISE EXCEPTION 'unit_stock_cost_pack_origin' USING ERRCODE='23514';
     END IF;
   END IF;
 END IF;
 v_cents:=v_lot.purchase_cost_per_pack*100;
 -- NUMERIC div gives exact integer cents even beyond BIGINT multiplication.
 IF NEW.kind='unit_take' THEN
   v_amount:=(div(v_cents*(v_offset+v_units),v_lot.units_per_pack)
       -div(v_cents*v_offset,v_lot.units_per_pack))/100;
 ELSE
   v_whole:=div(v_units::NUMERIC,v_lot.units_per_pack);
   v_tail:=mod(v_units::NUMERIC,v_lot.units_per_pack);
   v_amount:=(v_whole*v_cents+div(v_cents*v_tail,v_lot.units_per_pack))/100;
 END IF;
 IF v_amount+coalesce((SELECT sum(cost_amount) FROM public.unit_stock_entry
     WHERE tenant_id=NEW.tenant_id AND operation_id=NEW.operation_id),0)>999999999999.99 THEN
   RAISE EXCEPTION 'unit_stock_cost_amount_limit' USING ERRCODE='23514';
 END IF;
 IF (NEW.cost_pack_ordinal_start IS NOT NULL AND NEW.cost_pack_ordinal_start<>v_start)
   OR (NEW.cost_pack_count IS NOT NULL AND NEW.cost_pack_count<>v_count)
   OR (NEW.cost_unit_offset IS NOT NULL AND NEW.cost_unit_offset<>v_offset)
   OR (NEW.cost_base_units IS NOT NULL AND NEW.cost_base_units<>v_units)
   OR (NEW.cost_original_pack_cost IS NOT NULL
       AND NEW.cost_original_pack_cost<>v_lot.purchase_cost_per_pack)
   OR (NEW.cost_amount IS NOT NULL AND NEW.cost_amount<>v_amount)
   OR (NEW.cost_version IS NOT NULL AND NEW.cost_version<>1) THEN
   RAISE EXCEPTION 'unit_stock_cost_snapshot_mismatch' USING ERRCODE='23514';
 END IF;
 NEW.cost_pack_ordinal_start:=v_start; NEW.cost_pack_count:=v_count;
 NEW.cost_unit_offset:=v_offset; NEW.cost_base_units:=v_units;
 NEW.cost_original_pack_cost:=v_lot.purchase_cost_per_pack;
 NEW.cost_amount:=v_amount; NEW.cost_version:=1;
 RETURN NEW;
END $$;
"""


def upgrade() -> None:
    _sql("""
      ALTER TABLE public.unit_stock_lot ADD COLUMN initial_closed_packs BIGINT,
        ADD COLUMN purchase_cost_per_pack NUMERIC(14,2), ADD COLUMN cost_version INTEGER;
      ALTER TABLE public.unit_stock_opened_pack ADD COLUMN pack_ordinal BIGINT;
      ALTER TABLE public.unit_stock_entry ADD COLUMN cost_pack_ordinal_start BIGINT,
        ADD COLUMN cost_pack_count BIGINT, ADD COLUMN cost_unit_offset BIGINT,
        ADD COLUMN cost_base_units BIGINT, ADD COLUMN cost_original_pack_cost NUMERIC(14,2),
        ADD COLUMN cost_amount NUMERIC(14,2), ADD COLUMN cost_version INTEGER;
      CREATE UNIQUE INDEX uq_unit_stock_pack_ordinal
        ON public.unit_stock_opened_pack(tenant_id,lot_id,pack_ordinal)
        WHERE pack_ordinal IS NOT NULL;
    """)
    _sql(COST_SQL)
    for function in ("trg_unit_stock_cost_identity", "trg_unit_stock_cost_snapshot"):
        _sql(f"""
          ALTER FUNCTION public.{function}() OWNER TO aurum_schema_owner;
          REVOKE ALL ON FUNCTION public.{function}() FROM PUBLIC,aurum_app,aurum_support;
        """)
    _sql("""
      CREATE TRIGGER a20_unit_stock_cost_identity BEFORE INSERT ON public.unit_stock_lot
        FOR EACH ROW EXECUTE FUNCTION public.trg_unit_stock_cost_identity();
      CREATE TRIGGER a20_unit_stock_cost_identity BEFORE INSERT ON public.unit_stock_opened_pack
        FOR EACH ROW EXECUTE FUNCTION public.trg_unit_stock_cost_identity();
      CREATE TRIGGER a20_unit_stock_cost_snapshot BEFORE INSERT ON public.unit_stock_entry
        FOR EACH ROW EXECUTE FUNCTION public.trg_unit_stock_cost_snapshot();
    """)


def downgrade() -> None:
    _sql("""
      LOCK TABLE public.unit_stock_lot,public.unit_stock_opened_pack,public.unit_stock_entry
        IN ACCESS EXCLUSIVE MODE;
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.unit_stock_lot WHERE cost_version IS NOT NULL)
          OR EXISTS(SELECT 1 FROM public.unit_stock_entry WHERE cost_version IS NOT NULL) THEN
          RAISE EXCEPTION 'unit_stock_priced_downgrade_forbidden' USING ERRCODE='55000';
        END IF;
      END $$;
      DROP TRIGGER a20_unit_stock_cost_snapshot ON public.unit_stock_entry;
      DROP TRIGGER a20_unit_stock_cost_identity ON public.unit_stock_opened_pack;
      DROP TRIGGER a20_unit_stock_cost_identity ON public.unit_stock_lot;
      DROP FUNCTION public.trg_unit_stock_cost_snapshot();
      DROP FUNCTION public.trg_unit_stock_cost_identity();
      DROP INDEX public.uq_unit_stock_pack_ordinal;
      ALTER TABLE public.unit_stock_entry DROP COLUMN cost_pack_ordinal_start,
        DROP COLUMN cost_pack_count, DROP COLUMN cost_unit_offset, DROP COLUMN cost_base_units,
        DROP COLUMN cost_original_pack_cost, DROP COLUMN cost_amount, DROP COLUMN cost_version;
      ALTER TABLE public.unit_stock_opened_pack DROP COLUMN pack_ordinal;
      ALTER TABLE public.unit_stock_lot DROP COLUMN initial_closed_packs,
        DROP COLUMN purchase_cost_per_pack, DROP COLUMN cost_version;
    """)
