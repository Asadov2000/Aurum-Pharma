"""Immutable cash confirmations, without payment acceptance or stock posting.

Revision ID: 0171
Revises: 0170
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from alembic import op

revision: str = "0171"
down_revision: str | Sequence[str] | None = "0170"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


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


FUNCTIONS = """
CREATE FUNCTION public.trg_unit_cash_intent_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE
 v_sale public.sale%ROWTYPE; v_shift public.shift%ROWTYPE;
 v_catalog public.tenant_catalog%ROWTYPE; v_legacy public.sale_item%ROWTYPE;
 v_item public.pos_unit_cart_item%ROWTYPE;
 v_actor UUID; v_active BOOLEAN; v_catalog_id UUID;
 v_lines JSONB := '[]'::JSONB; v_payload JSONB; v_preparation JSONB;
 v_position INTEGER := 0; v_total NUMERIC := 0; v_gross NUMERIC; v_net NUMERIC;
 v_scaled NUMERIC; v_floor NUMERIC; v_price NUMERIC; v_unit_price NUMERIC;
 v_base_units NUMERIC;
 v_factor BIGINT; v_step BIGINT; v_ids UUID[] := ARRAY[]::UUID[];
BEGIN
 IF TG_OP<>'INSERT' THEN
  RAISE EXCEPTION 'unit_cash_intent_immutable' USING ERRCODE='55000';
 END IF;
 v_actor:=public.current_app_user_id();
 IF session_user<>'aurum_app' OR public.is_support_session() OR v_actor IS NULL
   OR NEW.tenant_id IS DISTINCT FROM public.current_tenant_id()
   OR NEW.cashier_user_id IS DISTINCT FROM v_actor
   OR NOT public.tenant_actor_has_scoped_permission(NEW.tenant_id,'pos.sell',NEW.branch_id)
 THEN RAISE EXCEPTION 'unit_cash_intent_actor_denied' USING ERRCODE='42501'; END IF;
 SELECT * INTO v_sale FROM public.sale
   WHERE tenant_id=NEW.tenant_id AND id=NEW.sale_id FOR UPDATE;
 IF NOT FOUND OR v_sale.status<>'draft' OR v_sale.sale_type<>'sale'
   OR v_sale.currency<>'TJS'
   OR (v_sale.branch_id,v_sale.register_id,v_sale.shift_id,v_sale.cashier_user_id)
      IS DISTINCT FROM (NEW.branch_id,NEW.register_id,NEW.shift_id,NEW.cashier_user_id)
 THEN RAISE EXCEPTION 'unit_cash_intent_sale_invalid' USING ERRCODE='23514'; END IF;
 SELECT * INTO v_shift FROM public.shift
   WHERE tenant_id=NEW.tenant_id AND id=NEW.shift_id FOR UPDATE;
 IF NOT FOUND OR v_shift.status<>'open' OR v_shift.currency<>'TJS'
   OR (v_shift.branch_id,v_shift.register_id,v_shift.opened_by_user_id)
      IS DISTINCT FROM (NEW.branch_id,NEW.register_id,v_actor)
 THEN RAISE EXCEPTION 'unit_cash_intent_shift_invalid' USING ERRCODE='23514'; END IF;
 SELECT r.is_active INTO v_active FROM public.register r
   WHERE r.tenant_id=NEW.tenant_id AND r.id=NEW.register_id
     AND r.branch_id=NEW.branch_id FOR SHARE;
 IF v_active IS DISTINCT FROM true THEN
  RAISE EXCEPTION 'unit_cash_intent_register_invalid' USING ERRCODE='23514';
 END IF;
 IF EXISTS(SELECT 1 FROM public.sale_payment
      WHERE tenant_id=NEW.tenant_id AND sale_id=NEW.sale_id)
   OR EXISTS(SELECT 1 FROM public.pos_payment_attempt
      WHERE tenant_id=NEW.tenant_id AND sale_id=NEW.sale_id) THEN
  RAISE EXCEPTION 'unit_cash_intent_payment_started' USING ERRCODE='23514';
 END IF;
 -- The parent UPDATE lock also excludes legacy/unit cart edits and inserts.
 -- Acquire all prepared catalog locks in the quote service's stable order.
 FOR v_catalog_id IN SELECT DISTINCT catalog_id FROM public.pos_unit_cart_item
   WHERE tenant_id=NEW.tenant_id AND sale_id=NEW.sale_id ORDER BY catalog_id LOOP
  PERFORM 1 FROM public.tenant_catalog
    WHERE tenant_id=NEW.tenant_id AND id=v_catalog_id FOR SHARE;
 END LOOP;
 FOR v_legacy IN SELECT * FROM public.sale_item
   WHERE tenant_id=NEW.tenant_id AND sale_id=NEW.sale_id ORDER BY position,id LOOP
  IF v_legacy.parent_sale_item_id IS NOT NULL OR v_legacy.currency<>'TJS'
    OR v_legacy.qty<=0 OR v_legacy.qty>99999999999.999
    OR v_legacy.unit_price<=0 OR v_legacy.unit_price>999999999999.99
    OR v_legacy.discount_amount<0 OR v_legacy.discount_amount>999999999999.99
    OR v_legacy.id=ANY(v_ids) THEN
   RAISE EXCEPTION 'unit_cash_intent_legacy_invalid' USING ERRCODE='23514';
  END IF;
  -- Decimal.quantize(..., ROUND_HALF_EVEN), rather than PostgreSQL round().
  v_scaled:=v_legacy.unit_price*v_legacy.qty*100;
  v_floor:=trunc(v_scaled);
  IF v_scaled-v_floor>0.5 OR (v_scaled-v_floor=0.5 AND mod(v_floor,2)=1) THEN
   v_floor:=v_floor+1;
  END IF;
  v_gross:=v_floor/100;
  v_net:=v_gross-v_legacy.discount_amount;
  IF v_gross>999999999999.99 OR v_net<0
    OR v_net IS DISTINCT FROM v_legacy.total_price THEN
   RAISE EXCEPTION 'unit_cash_intent_legacy_amounts' USING ERRCODE='23514';
  END IF;
  v_position:=v_position+1; v_ids:=array_append(v_ids,v_legacy.id);
  v_total:=v_total+v_net;
  v_lines:=v_lines||jsonb_build_array(jsonb_build_object(
    'inventory_kind','legacy_pack','id',v_legacy.id,'position',v_position,
    'catalog_id',v_legacy.catalog_id,'batch_id',v_legacy.batch_id,
    'quantity',to_char(v_legacy.qty,'FM999999999990.000'),
    'original_retail_unit_price',to_char(v_legacy.unit_price,'FM999999999990.00'),
    'gross_amount',to_char(v_gross,'FM999999999990.00'),
    'discount_amount',to_char(v_legacy.discount_amount,'FM999999999990.00'),
    'net_amount',to_char(v_net,'FM999999999990.00'),'money_version',1,'currency','TJS'));
 END LOOP;
 FOR v_item IN SELECT * FROM public.pos_unit_cart_item
   WHERE tenant_id=NEW.tenant_id AND sale_id=NEW.sale_id ORDER BY position,id LOOP
  SELECT * INTO v_catalog FROM public.tenant_catalog
    WHERE tenant_id=NEW.tenant_id AND id=v_item.catalog_id;
  IF NOT FOUND OR NOT v_catalog.is_active OR v_catalog.deleted_at IS NOT NULL
    OR v_catalog.currency<>'TJS' OR v_catalog.unit_sale_preparation IS NULL
    OR v_item.branch_id IS DISTINCT FROM NEW.branch_id OR v_item.id=ANY(v_ids) THEN
   RAISE EXCEPTION 'unit_cash_intent_catalog_invalid' USING ERRCODE='23514';
  END IF;
  v_preparation:=v_catalog.unit_sale_preparation;
  v_factor:=(v_preparation->>'units_per_pack')::BIGINT;
  v_step:=(v_preparation->>'dispense_step')::BIGINT;
  v_unit_price:=(v_preparation->>'unit_price')::NUMERIC;
  IF (v_preparation->>'version')::INTEGER IS DISTINCT FROM 1
    OR v_preparation->>'unit' IS NULL
    OR v_preparation->>'unit' NOT IN ('tablet','capsule','ampoule','sachet','suppository')
    OR v_factor IS NULL OR v_factor NOT BETWEEN 2 AND 10000
    OR v_step IS NULL OR v_step NOT BETWEEN 1 AND 10000
    OR v_unit_price IS NULL OR v_unit_price<=0 OR v_unit_price>999999999999.99
    OR v_unit_price<>trunc(v_unit_price,2)
    OR v_preparation->>'state' IS DISTINCT FROM 'prepared' THEN
   RAISE EXCEPTION 'unit_cash_intent_packaging_invalid' USING ERRCODE='23514';
  END IF;
  IF v_factor%v_step<>0 THEN
   RAISE EXCEPTION 'unit_cash_intent_packaging_invalid' USING ERRCODE='23514';
  END IF;
  IF v_item.sale_format='pack' THEN
   v_price:=v_catalog.base_price;
   v_base_units:=v_item.quantity::NUMERIC*v_factor;
  ELSE
   v_price:=v_unit_price;
   v_base_units:=v_item.quantity;
  END IF;
  v_gross:=v_price*v_item.quantity;
  v_net:=v_gross-v_item.discount_amount;
  IF v_price IS NULL OR v_price<=0 OR v_price>999999999999.99
    OR v_price<>trunc(v_price,2) OR v_base_units NOT BETWEEN 1 AND 99999999999
    OR (v_item.sale_format='unit' AND v_item.quantity%v_step<>0)
    OR v_gross>999999999999.99 OR v_item.discount_amount<0
    OR v_item.discount_amount>999999999999.99 OR v_net<=0 THEN
   RAISE EXCEPTION 'unit_cash_intent_unit_amounts' USING ERRCODE='23514';
  END IF;
  v_position:=v_position+1; v_ids:=array_append(v_ids,v_item.id);
  v_total:=v_total+v_net;
  v_lines:=v_lines||jsonb_build_array(jsonb_build_object(
    'inventory_kind','prepared_unit','id',v_item.id,'position',v_position,
    'catalog_id',v_item.catalog_id,'packaging',jsonb_build_object(
      'version',1,'unit',v_preparation->>'unit','units_per_pack',v_factor,
      'dispense_step',v_step),
    'sale_format',v_item.sale_format,'quantity',v_item.quantity,'base_units',v_base_units,
    'original_retail_unit_price',to_char(v_price,'FM999999999990.00'),
    'gross_amount',to_char(v_gross,'FM999999999990.00'),
    'discount_amount',to_char(v_item.discount_amount,'FM999999999990.00'),
    'net_amount',to_char(v_net,'FM999999999990.00'),'money_version',1,'currency','TJS'));
 END LOOP;
 IF v_position NOT BETWEEN 1 AND 200 OR v_total<=0 OR v_total>999999999999.99
   OR NEW.expected_total_amount IS DISTINCT FROM v_total
   OR NEW.cash_tendered<v_total
   OR NEW.change_amount IS DISTINCT FROM NEW.cash_tendered-v_total THEN
  RAISE EXCEPTION 'unit_cash_intent_total_invalid' USING ERRCODE='23514';
 END IF;
 v_payload:=jsonb_build_object('schema_version',2,'state','draft_quote',
   'tenant_id',NEW.tenant_id,'branch_id',NEW.branch_id,'sale_id',NEW.sale_id,
   'register_id',NEW.register_id,'shift_id',NEW.shift_id,'cashier_id',NEW.cashier_user_id,
   'lines',v_lines,'total_amount',to_char(v_total,'FM999999999990.00'),'currency','TJS');
 IF NEW.quote_payload IS DISTINCT FROM v_payload THEN
  RAISE EXCEPTION 'unit_cash_intent_quote_mismatch' USING ERRCODE='23514';
 END IF;
 -- Python fingerprints are opaque here; JSONB equality proves the retail facts.
 NEW.created_at:=statement_timestamp(); NEW.created_by:=v_actor;
 RETURN NEW;
END $$;
"""


def upgrade() -> None:
    _sql("""
      CREATE TEMP TABLE unit_cash_reference_grants AS
        SELECT oid::regclass::TEXT AS relation,
          has_table_privilege('aurum_schema_owner',oid,'REFERENCES') AS granted
        FROM pg_class WHERE oid IN ('public.tenant'::regclass,'public.branch'::regclass,
          'public.app_user'::regclass,'public.sale'::regclass);
      GRANT REFERENCES ON public.tenant,public.branch,public.app_user,public.sale
        TO aurum_schema_owner;
      CREATE TABLE public.pos_unit_cash_intent (
        tenant_id UUID NOT NULL REFERENCES public.tenant(id), id UUID NOT NULL,
        branch_id UUID NOT NULL, sale_id UUID NOT NULL, register_id UUID NOT NULL,
        shift_id UUID NOT NULL, cashier_user_id UUID NOT NULL REFERENCES public.app_user(id),
        request_hash TEXT NOT NULL CHECK(request_hash ~ '^[0-9a-f]{64}$'),
        revision_hash TEXT NOT NULL CHECK(revision_hash ~ '^[0-9a-f]{64}$'),
        expected_total_amount NUMERIC(14,2) NOT NULL CHECK(
          expected_total_amount>0 AND expected_total_amount<=999999999999.99),
        cash_tendered NUMERIC(14,2) NOT NULL CHECK(
          cash_tendered>=expected_total_amount AND cash_tendered<=999999999999.99),
        change_amount NUMERIC(14,2) NOT NULL CHECK(
          change_amount>=0 AND change_amount=cash_tendered-expected_total_amount),
        currency TEXT NOT NULL DEFAULT 'TJS' CHECK(currency='TJS'),
        quote_payload JSONB NOT NULL CHECK(jsonb_typeof(quote_payload)='object'),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        created_by UUID NOT NULL REFERENCES public.app_user(id),
        PRIMARY KEY(tenant_id,id),
        CHECK(substring(id::TEXT,15,1)='4' AND substring(id::TEXT,20,1) IN ('8','9','a','b')),
        FOREIGN KEY(tenant_id,branch_id) REFERENCES public.branch(tenant_id,id),
        FOREIGN KEY(tenant_id,sale_id) REFERENCES public.sale(tenant_id,id) ON DELETE RESTRICT
      );
      DO $$ DECLARE r RECORD; BEGIN
        FOR r IN SELECT * FROM pg_temp.unit_cash_reference_grants WHERE NOT granted LOOP
          EXECUTE format('REVOKE REFERENCES ON %s FROM aurum_schema_owner',r.relation);
        END LOOP;
      END $$;
      DROP TABLE pg_temp.unit_cash_reference_grants;
      ALTER TABLE public.pos_unit_cash_intent OWNER TO aurum_schema_owner;
      ALTER TABLE public.pos_unit_cash_intent ENABLE ROW LEVEL SECURITY;
      ALTER TABLE public.pos_unit_cash_intent FORCE ROW LEVEL SECURITY;
      REVOKE ALL ON public.pos_unit_cash_intent FROM PUBLIC,aurum_app,aurum_support;
      GRANT SELECT,INSERT ON public.pos_unit_cash_intent TO aurum_app;
      GRANT SELECT ON public.pos_unit_cash_intent TO aurum_support;
      CREATE POLICY schema_owner ON public.pos_unit_cash_intent TO aurum_schema_owner
        USING(true) WITH CHECK(true);
      CREATE POLICY support ON public.pos_unit_cash_intent FOR SELECT TO aurum_support USING(true);
      CREATE POLICY tenant_actor ON public.pos_unit_cash_intent TO aurum_app USING(
        tenant_id=public.current_tenant_id() AND cashier_user_id=public.current_app_user_id()
        AND public.tenant_actor_has_scoped_permission(tenant_id,'pos.sell',branch_id))
        WITH CHECK(tenant_id=public.current_tenant_id()
        AND cashier_user_id=public.current_app_user_id()
        AND public.tenant_actor_has_scoped_permission(tenant_id,'pos.sell',branch_id));
    """)
    _sql(FUNCTIONS)
    _sql("""
      ALTER FUNCTION public.trg_unit_cash_intent_guard() OWNER TO aurum_schema_owner;
      REVOKE ALL ON FUNCTION public.trg_unit_cash_intent_guard()
        FROM PUBLIC,aurum_app,aurum_support;
      CREATE TRIGGER a00_tenant_access BEFORE INSERT OR UPDATE OR DELETE
        ON public.pos_unit_cash_intent FOR EACH ROW
        EXECUTE FUNCTION public.trg_enforce_tenant_access();
      CREATE TRIGGER a10_unit_cash_intent_guard BEFORE INSERT OR UPDATE OR DELETE
        ON public.pos_unit_cash_intent FOR EACH ROW
        EXECUTE FUNCTION public.trg_unit_cash_intent_guard();
      CREATE TRIGGER z90_unit_cash_intent_audit AFTER INSERT OR UPDATE OR DELETE
        ON public.pos_unit_cash_intent FOR EACH ROW EXECUTE FUNCTION public.trg_audit_log();
    """)


def downgrade() -> None:
    _sql("""
      LOCK TABLE public.pos_unit_cash_intent IN ACCESS EXCLUSIVE MODE;
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.pos_unit_cash_intent) THEN
          RAISE EXCEPTION 'unit_cash_intent_nonempty_downgrade_forbidden' USING ERRCODE='55000';
        END IF;
      END $$;
      DROP TABLE public.pos_unit_cash_intent;
      DROP FUNCTION public.trg_unit_cash_intent_guard();
    """)
