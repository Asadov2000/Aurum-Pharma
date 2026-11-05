"""Prepare physical-format rows in unpaid POS drafts without enabling checkout.

Revision ID: 0179
Revises: 0178
"""

from __future__ import annotations

from collections.abc import Sequence
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType

revision: str = "0179"
down_revision: str | Sequence[str] | None = "0178"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _history(filename: str) -> ModuleType:
    path = Path(__file__).with_name(filename)
    spec = spec_from_file_location(f"aurum_0179_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load historical migration {filename}")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sql(sql: str) -> None:
    _history("0170_unit_cart_intents.py")._sql(sql)


def _replace(sql: str, old: str, new: str) -> str:
    if sql.count(old) != 1:
        raise RuntimeError("Historical unit cart guard differs from the expected definition")
    return sql.replace(old, new, 1)


PACKAGING_CHECK = """
physical_packaging IS NULL OR COALESCE(CASE WHEN
 jsonb_typeof(physical_packaging)='object'
 AND physical_packaging ?&
     ARRAY['version','unit','units_per_pack','units_per_blister','dispense_step']
 AND physical_packaging-
     ARRAY['version','unit','units_per_pack','units_per_blister','dispense_step']='{}'::JSONB
 AND jsonb_typeof(physical_packaging->'version')='number'
 AND physical_packaging->>'version'='2'
 AND jsonb_typeof(physical_packaging->'unit')='string'
 AND physical_packaging->>'unit' IN ('tablet','capsule','ampoule','sachet','suppository')
 AND jsonb_typeof(physical_packaging->'units_per_pack')='number'
 AND physical_packaging->>'units_per_pack' ~ '^[0-9]{1,5}$'
 AND jsonb_typeof(physical_packaging->'dispense_step')='number'
 AND physical_packaging->>'dispense_step' ~ '^[0-9]{1,5}$'
 AND (physical_packaging->'units_per_blister'='null'::JSONB OR
      (jsonb_typeof(physical_packaging->'units_per_blister')='number'
       AND physical_packaging->>'units_per_blister' ~ '^[0-9]{1,5}$'))
 THEN CASE WHEN
   (physical_packaging->>'units_per_pack')::INTEGER BETWEEN 2 AND 10000
   AND (physical_packaging->>'dispense_step')::INTEGER BETWEEN 1 AND 10000
   AND (physical_packaging->>'units_per_blister' IS NULL OR
        (physical_packaging->>'units_per_blister')::INTEGER BETWEEN 2 AND 10000)
   THEN (physical_packaging->>'units_per_pack')::INTEGER %
        (physical_packaging->>'dispense_step')::INTEGER=0
    AND (physical_packaging->>'units_per_blister' IS NULL OR
         (physical_packaging->>'units_per_pack')::INTEGER %
         (physical_packaging->>'units_per_blister')::INTEGER=0)
   ELSE false END
 ELSE false END,false)
"""


def _cart_guard() -> str:
    sql: str = _history("0170_unit_cart_intents.py").FUNCTIONS.split(
        "CREATE FUNCTION public.trg_unit_cart_payment_boundary()"
    )[0]
    sql = _replace(
        sql,
        "CREATE FUNCTION public.trg_unit_cart_guard()",
        "CREATE OR REPLACE FUNCTION public.trg_unit_cart_guard()",
    )
    sql = _replace(
        sql,
        "v_register_active BOOLEAN;",
        """v_register_active BOOLEAN;
 v_blister BIGINT; v_base_units NUMERIC; v_expected JSONB;""",
    )
    sql = _replace(
        sql,
        " v_actor:=public.current_app_user_id();",
        """
 IF TG_OP='UPDATE' AND NEW.physical_packaging IS DISTINCT FROM OLD.physical_packaging THEN
  RAISE EXCEPTION 'unit_cart_physical_snapshot_immutable' USING ERRCODE='55000';
 END IF;
 v_actor:=public.current_app_user_id();""",
    )
    sql = _replace(
        sql,
        " IF TG_OP='DELETE' THEN RETURN OLD; END IF;",
        """
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 IF EXISTS(SELECT 1 FROM public.pos_unit_cart_item other
   WHERE other.tenant_id=NEW.tenant_id AND other.sale_id=NEW.sale_id AND other.id<>NEW.id
   AND (other.physical_packaging IS NULL)<>(NEW.physical_packaging IS NULL)) THEN
  RAISE EXCEPTION 'unit_cart_accounting_modes_mixed' USING ERRCODE='23514';
 END IF;""",
    )
    sql = _replace(
        sql,
        " IF NEW.sale_format='pack' THEN v_price:=v_catalog.base_price;",
        """
 IF NEW.physical_packaging IS NOT NULL THEN
  v_blister:=(v_catalog.unit_sale_preparation->>'units_per_blister')::BIGINT;
  v_expected:=jsonb_build_object('version',2,
    'unit',v_catalog.unit_sale_preparation->>'unit','units_per_pack',v_factor,
    'units_per_blister',v_blister,'dispense_step',v_step);
  IF NEW.physical_packaging IS DISTINCT FROM v_expected THEN
   RAISE EXCEPTION 'unit_cart_physical_composition_stale' USING ERRCODE='23514';
  END IF;
 END IF;
 IF NEW.sale_format='pack' THEN v_price:=v_catalog.base_price;""",
    )
    sql = _replace(
        sql,
        " v_gross:=v_price*NEW.quantity;",
        """
 IF NEW.physical_packaging IS NOT NULL THEN
  v_base_units:=NEW.quantity;
  IF NEW.sale_format='pack' THEN v_base_units:=NEW.quantity::NUMERIC*v_factor;
  ELSIF NEW.sale_format='blister' THEN
   IF v_blister IS NULL THEN
    RAISE EXCEPTION 'unit_cart_blister_unknown' USING ERRCODE='23514';
   END IF;
   v_base_units:=NEW.quantity::NUMERIC*v_blister; v_price:=v_price*v_blister;
  END IF;
  IF v_base_units NOT BETWEEN 1 AND 99999999999 OR mod(v_base_units,v_step)<>0
     OR v_price::TEXT IN ('NaN','Infinity','-Infinity')
     OR v_price>999999999999.99 THEN
   RAISE EXCEPTION 'unit_cart_quantity_invalid' USING ERRCODE='23514';
  END IF;
 END IF;
 v_gross:=v_price*NEW.quantity;""",
    )
    return sql


def _cash_guard() -> str:
    sql: str = _history("0171_unit_cash_intents.py").FUNCTIONS
    sql = _replace(
        sql,
        "CREATE FUNCTION public.trg_unit_cash_intent_guard()",
        "CREATE OR REPLACE FUNCTION public.trg_unit_cash_intent_guard()",
    )
    sql = _replace(
        sql,
        " v_base_units NUMERIC;",
        """ v_base_units NUMERIC;
 v_blister BIGINT; v_packaging JSONB; v_kind TEXT; v_physical BOOLEAN;""",
    )
    sql = _replace(
        sql,
        "  v_preparation:=v_catalog.unit_sale_preparation;",
        """
  IF v_physical IS NOT NULL AND v_physical<>(v_item.physical_packaging IS NOT NULL) THEN
   RAISE EXCEPTION 'unit_cart_accounting_modes_mixed' USING ERRCODE='23514';
  END IF;
  v_physical:=v_item.physical_packaging IS NOT NULL;
  v_preparation:=v_catalog.unit_sale_preparation;""",
    )
    sql = _replace(
        sql,
        "  v_gross:=v_price*v_item.quantity;",
        """
  v_kind:='prepared_unit';
  v_packaging:=jsonb_build_object('version',1,'unit',v_preparation->>'unit',
    'units_per_pack',v_factor,'dispense_step',v_step);
  IF v_physical THEN
   v_kind:='physical_unit';
   v_blister:=(v_preparation->>'units_per_blister')::BIGINT;
   v_packaging:=jsonb_build_object('version',2,'unit',v_preparation->>'unit',
     'units_per_pack',v_factor,'units_per_blister',v_blister,'dispense_step',v_step);
   IF v_item.physical_packaging IS DISTINCT FROM v_packaging THEN
    RAISE EXCEPTION 'unit_cash_intent_physical_composition_stale' USING ERRCODE='23514';
   END IF;
   IF v_item.sale_format='blister' THEN
    IF v_blister IS NULL THEN
     RAISE EXCEPTION 'unit_cart_blister_unknown' USING ERRCODE='23514';
    END IF;
    v_price:=v_unit_price*v_blister;
    v_base_units:=v_item.quantity::NUMERIC*v_blister;
   END IF;
   IF mod(v_base_units,v_step)<>0 THEN
    RAISE EXCEPTION 'unit_cash_intent_unit_amounts' USING ERRCODE='23514';
   END IF;
  END IF;
  v_gross:=v_price*v_item.quantity;""",
    )
    sql = _replace(
        sql,
        "'inventory_kind','prepared_unit','id',v_item.id",
        "'inventory_kind',v_kind,'id',v_item.id",
    )
    sql = _replace(
        sql,
        """'catalog_id',v_item.catalog_id,'packaging',jsonb_build_object(
      'version',1,'unit',v_preparation->>'unit','units_per_pack',v_factor,
      'dispense_step',v_step),""",
        "'catalog_id',v_item.catalog_id,'packaging',v_packaging,",
    )
    return sql


def _restore_guards() -> None:
    cart: str = _history("0170_unit_cart_intents.py").FUNCTIONS.split(
        "CREATE FUNCTION public.trg_unit_cart_payment_boundary()"
    )[0]
    _sql(cart.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))
    cash: str = _history("0171_unit_cash_intents.py").FUNCTIONS
    _sql(cash.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))


def upgrade() -> None:
    _sql(f"""
      ALTER TABLE public.pos_unit_cart_item ADD COLUMN physical_packaging JSONB;
      ALTER TABLE public.pos_unit_cart_item DROP CONSTRAINT pos_unit_cart_item_sale_format_check;
      ALTER TABLE public.pos_unit_cart_item ADD CONSTRAINT ck_unit_cart_format_physical CHECK(
        sale_format IN ('pack','unit') OR
        (sale_format='blister' AND physical_packaging IS NOT NULL
         AND physical_packaging->>'units_per_blister' IS NOT NULL));
      ALTER TABLE public.pos_unit_cart_item ADD CONSTRAINT ck_unit_cart_physical_packaging
        CHECK({PACKAGING_CHECK});
    """)
    _sql(_cart_guard())
    _sql(_cash_guard())
    # CREATE OR REPLACE retains the existing owner, function ACL, RLS and audit.
    # The three 0170 financial boundary triggers are intentionally unchanged.


def downgrade() -> None:
    _sql("""
      LOCK TABLE public.pos_unit_cart_item,public.pos_unit_cash_intent IN ACCESS EXCLUSIVE MODE;
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.pos_unit_cart_item WHERE physical_packaging IS NOT NULL)
          OR EXISTS(SELECT 1 FROM public.pos_unit_cash_intent intent,
                    LATERAL jsonb_array_elements(intent.quote_payload->'lines') line
                    WHERE line->>'inventory_kind'='physical_unit') THEN
          RAISE EXCEPTION 'unit_physical_cart_nonempty_downgrade_forbidden' USING ERRCODE='55000';
        END IF;
      END $$;
    """)
    _restore_guards()
    _sql("""
      ALTER TABLE public.pos_unit_cart_item DROP CONSTRAINT ck_unit_cart_physical_packaging;
      ALTER TABLE public.pos_unit_cart_item DROP CONSTRAINT ck_unit_cart_format_physical;
      ALTER TABLE public.pos_unit_cart_item DROP COLUMN physical_packaging;
      ALTER TABLE public.pos_unit_cart_item ADD CONSTRAINT pos_unit_cart_item_sale_format_check
        CHECK(sale_format IN ('pack','unit'));
    """)
