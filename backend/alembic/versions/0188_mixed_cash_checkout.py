"""Guard atomic mixed legacy/physical cash sales without changing V3 checkout.

Revision ID: 0188
Revises: 0187
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "0188"
down_revision: str | Sequence[str] | None = "0187"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _sql(sql: str) -> None:
    start = 0
    for match in re.finditer(r"\$\$.*?\$\$|'(?:''|[^'])*'|;", sql, flags=re.DOTALL):
        if match.group() == ";":
            statement = sql[start : match.start()].strip()
            if statement:
                op.get_bind().exec_driver_sql(statement)
            start = match.end()
    if sql[start:].strip():
        op.get_bind().exec_driver_sql(sql[start:])


def _replace(signature: str, old: str, new: str) -> None:
    definition = op.get_bind().scalar(
        text("SELECT pg_get_functiondef(CAST(:signature AS regprocedure))"),
        {"signature": "public." + signature},
    )
    if not isinstance(definition, str) or definition.count(old) != 1:
        raise RuntimeError("Mixed checkout historical guard changed")
    # CREATE OR REPLACE preserves each historical function's owner, ACL and mode.
    op.get_bind().exec_driver_sql(definition.replace(old, new, 1))


FUNCTIONS_SQL = """
CREATE FUNCTION public.assert_mixed_checkout(p_tenant UUID,p_sale UUID,p_final BOOLEAN)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE
 m public.pos_mixed_checkout%ROWTYPE; s public.sale%ROWTYPE;
 c public.unit_stock_cart_command_v2%ROWTYPE; i public.pos_unit_cash_intent%ROWTYPE;
 r RECORD; v_line JSONB; v_lines JSONB:='[]'::JSONB; v_receipt_lines JSONB:='[]'::JSONB;
 v_quote JSONB; v_expected JSONB; v_payment JSONB; v_total NUMERIC:=0;
 v_physical_count INTEGER; v_legacy_count INTEGER; v_position INTEGER:=0;
 v_scaled NUMERIC; v_floor NUMERIC; v_gross NUMERIC; v_name TEXT; v_label TEXT;
 v_created TIMESTAMPTZ; v_completed TIMESTAMPTZ; v_ids UUID[]:=ARRAY[]::UUID[];
BEGIN
 SELECT * INTO m FROM public.pos_mixed_checkout WHERE tenant_id=p_tenant AND sale_id=p_sale;
 SELECT * INTO s FROM public.sale WHERE tenant_id=p_tenant AND id=p_sale;
 IF m.id IS NULL OR s.id IS NULL OR s.sale_type<>'sale' OR s.parent_sale_id IS NOT NULL
   OR s.currency<>'TJS' OR s.status NOT IN ('draft','completed') OR s.is_test
   OR (s.branch_id,s.register_id,s.shift_id,s.cashier_user_id)
      IS DISTINCT FROM (m.branch_id,m.register_id,m.shift_id,m.cashier_user_id)
   OR EXISTS(SELECT 1 FROM public.pos_physical_checkout WHERE sale_id=p_sale)
   OR EXISTS(SELECT 1 FROM public.pos_payment_attempt WHERE sale_id=p_sale) THEN
  RAISE EXCEPTION 'mixed_checkout_scope_invalid' USING ERRCODE='23514';
 END IF;
 SELECT * INTO c FROM public.unit_stock_cart_command_v2
   WHERE tenant_id=p_tenant AND id=m.stock_command_id;
 SELECT * INTO i FROM public.pos_unit_cash_intent WHERE tenant_id=p_tenant AND id=m.cash_intent_id;
 IF c.id IS NULL OR c.mixed_checkout_sale_id IS DISTINCT FROM p_sale
   OR c.checkout_sale_id IS NOT NULL OR c.retail_version IS DISTINCT FROM 1
   OR c.currency IS DISTINCT FROM 'TJS'
   OR (c.branch_id,c.actor_id) IS DISTINCT FROM (m.branch_id,m.cashier_user_id)
   OR i.id IS NULL OR (i.sale_id,i.branch_id,i.register_id,i.shift_id,i.cashier_user_id)
      IS DISTINCT FROM (m.sale_id,m.branch_id,m.register_id,m.shift_id,m.cashier_user_id)
   OR i.request_hash IS DISTINCT FROM m.request_hash THEN
  RAISE EXCEPTION 'mixed_checkout_binding_invalid' USING ERRCODE='23514';
 END IF;
 PERFORM public.assert_unit_cart_v2_complete(p_tenant,c.id);
 SELECT count(*) INTO v_legacy_count FROM public.sale_item
   WHERE tenant_id=p_tenant AND sale_id=p_sale;
 SELECT count(*) INTO v_physical_count FROM public.unit_stock_cart_line_v2
   WHERE tenant_id=p_tenant AND command_id=c.id;
 IF v_legacy_count<1 OR v_physical_count<1 OR v_legacy_count+v_physical_count>200
   OR v_physical_count<>(SELECT count(*) FROM public.pos_unit_cart_item
      WHERE tenant_id=p_tenant AND sale_id=p_sale)
   OR jsonb_typeof(m.result_payload->'lines') IS DISTINCT FROM 'array'
   OR jsonb_array_length(m.result_payload->'lines')<>v_legacy_count+v_physical_count THEN
  RAISE EXCEPTION 'mixed_checkout_cart_invalid' USING ERRCODE='23514';
 END IF;
 FOR r IN SELECT * FROM public.sale_item
   WHERE tenant_id=p_tenant AND sale_id=p_sale ORDER BY position,id LOOP
  IF r.parent_sale_item_id IS NOT NULL OR r.currency<>'TJS'
    OR r.qty<=0 OR r.unit_price<0 OR r.discount_amount<0 OR r.id=ANY(v_ids) THEN
   RAISE EXCEPTION 'mixed_checkout_legacy_invalid' USING ERRCODE='23514';
  END IF;
  -- Preserve Python Decimal's ROUND_HALF_EVEN at fractional-pack boundaries.
  v_scaled:=r.unit_price*r.qty*100; v_floor:=trunc(v_scaled);
  IF v_scaled-v_floor>0.5 OR (v_scaled-v_floor=0.5 AND mod(v_floor,2)=1) THEN
   v_floor:=v_floor+1;
  END IF;
  v_gross:=v_floor/100;
  IF v_gross-r.discount_amount IS DISTINCT FROM r.total_price OR r.total_price<0 THEN
   RAISE EXCEPTION 'mixed_checkout_legacy_amounts_invalid' USING ERRCODE='23514';
  END IF;
  v_name:=m.result_payload->'lines'->v_position->>'name';
  IF v_name IS NULL OR length(v_name) NOT BETWEEN 1 AND 500 OR btrim(v_name)=''
    OR v_name ~ '[[:cntrl:]]' THEN
   RAISE EXCEPTION 'mixed_checkout_name_invalid' USING ERRCODE='23514';
  END IF;
  v_position:=v_position+1; v_ids:=array_append(v_ids,r.id);
  v_line:=jsonb_build_object('inventory_kind','legacy_pack','name',v_name,
    'id',r.id,'position',v_position,'catalog_id',r.catalog_id,'batch_id',r.batch_id,
    'quantity',to_char(r.qty,'FM999999999990.000'),
    'original_retail_unit_price',to_char(r.unit_price,'FM999999999990.00'),
    'gross_amount',to_char(v_gross,'FM999999999990.00'),
    'discount_amount',to_char(r.discount_amount,'FM999999999990.00'),
    'net_amount',to_char(r.total_price,'FM999999999990.00'),'money_version',1,'currency','TJS');
  v_lines:=v_lines||jsonb_build_array(v_line); v_total:=v_total+r.total_price;
  v_receipt_lines:=v_receipt_lines||jsonb_build_array(
    (v_line-ARRAY['id','catalog_id','batch_id'])||
      jsonb_build_object('sale_format','pack','unit_label','уп.'));
 END LOOP;
 FOR r IN SELECT l.*,cart.physical_packaging AS cart_packaging,
   cart.sale_format AS cart_format,cart.quantity AS cart_quantity,
   cart.discount_amount AS cart_discount,cart.catalog_id AS cart_catalog,
   cart.branch_id AS cart_branch,cart.ordinal AS cart_ordinal
   FROM public.unit_stock_cart_line_v2 l LEFT JOIN (
     SELECT item.*,row_number() OVER(ORDER BY item.position,item.id) AS ordinal
       FROM public.pos_unit_cart_item item WHERE item.tenant_id=p_tenant AND item.sale_id=p_sale
   ) cart ON cart.tenant_id=l.tenant_id AND cart.sale_id=p_sale AND cart.id=l.line_id
   WHERE l.tenant_id=p_tenant AND l.command_id=c.id ORDER BY l.position LOOP
  IF r.cart_packaging IS NULL OR r.cart_ordinal IS DISTINCT FROM (r.position+1)::BIGINT
    OR r.line_id=ANY(v_ids)
    OR (r.packaging,r.sale_format,r.quantity,r.discount_amount,r.catalog_id,m.branch_id)
       IS DISTINCT FROM
       (r.cart_packaging,r.cart_format,r.cart_quantity,r.cart_discount,r.cart_catalog,r.cart_branch)
  THEN RAISE EXCEPTION 'mixed_checkout_physical_line_invalid' USING ERRCODE='23514'; END IF;
  v_name:=m.result_payload->'lines'->v_position->>'name';
  IF v_name IS NULL OR length(v_name) NOT BETWEEN 1 AND 500 OR btrim(v_name)=''
    OR v_name ~ '[[:cntrl:]]' THEN
   RAISE EXCEPTION 'mixed_checkout_name_invalid' USING ERRCODE='23514';
  END IF;
  v_position:=v_position+1; v_ids:=array_append(v_ids,r.line_id);
  v_line:=jsonb_build_object('inventory_kind','physical_unit','name',v_name,
    'id',r.line_id,'position',v_position,'catalog_id',r.catalog_id,
    'packaging',r.packaging,'sale_format',r.sale_format,'quantity',r.quantity,
    'base_units',r.base_units,'original_retail_unit_price',to_char(
      r.original_retail_unit_price,'FM999999999990.00'),
    'gross_amount',to_char(r.gross_amount,'FM999999999990.00'),
    'discount_amount',to_char(r.discount_amount,'FM999999999990.00'),
    'net_amount',to_char(r.net_amount,'FM999999999990.00'),'money_version',1,'currency','TJS');
  v_lines:=v_lines||jsonb_build_array(v_line); v_total:=v_total+r.net_amount;
  v_label:=CASE r.sale_format WHEN 'pack' THEN 'уп.' WHEN 'blister' THEN 'блист.' ELSE
    CASE r.packaging->>'unit' WHEN 'tablet' THEN 'табл.' WHEN 'capsule' THEN 'капс.'
      WHEN 'ampoule' THEN 'амп.' WHEN 'sachet' THEN 'саше' WHEN 'suppository' THEN 'супп.' END END;
  v_receipt_lines:=v_receipt_lines||jsonb_build_array(
    (v_line-ARRAY['id','catalog_id'])||jsonb_build_object('unit_label',v_label));
 END LOOP;
 IF v_total IS DISTINCT FROM i.expected_total_amount OR v_total<=0 THEN
  RAISE EXCEPTION 'mixed_checkout_total_invalid' USING ERRCODE='23514';
 END IF;
 SELECT jsonb_build_object('schema_version',2,'state','draft_quote','tenant_id',p_tenant,
   'branch_id',m.branch_id,'sale_id',p_sale,'register_id',m.register_id,'shift_id',m.shift_id,
   'cashier_id',m.cashier_user_id,'lines',jsonb_agg(value-'name' ORDER BY ordinality),
   'total_amount',to_char(v_total,'FM999999999990.00'),'currency','TJS') INTO v_quote
   FROM jsonb_array_elements(v_lines) WITH ORDINALITY;
 IF v_quote IS DISTINCT FROM i.quote_payload THEN
  RAISE EXCEPTION 'mixed_checkout_cash_quote_invalid' USING ERRCODE='23514';
 END IF;
 -- Legacy consumption is one immutable, exact operation per selected batch.
 IF EXISTS(SELECT 1 FROM public.sale_item item LEFT JOIN public.batch b
      ON b.tenant_id=item.tenant_id AND b.id=item.batch_id
    WHERE item.sale_id=p_sale AND (item.tenant_id IS DISTINCT FROM p_tenant OR b.id IS NULL
      OR (b.branch_id,b.catalog_id,b.currency) IS DISTINCT FROM
         (m.branch_id,item.catalog_id,'TJS')))
   OR EXISTS(SELECT 1 FROM public.batch_movement movement
     LEFT JOIN (SELECT batch_id,sum(qty) AS quantity FROM public.sale_item
        WHERE tenant_id=p_tenant AND sale_id=p_sale GROUP BY batch_id) expected
       ON expected.batch_id=movement.batch_id
     WHERE movement.source_table='sale' AND movement.source_id=p_sale
       AND (expected.batch_id IS NULL OR movement.tenant_id IS DISTINCT FROM p_tenant
         OR movement.movement_type IS DISTINCT FROM 'sale'
         OR movement.qty_delta IS DISTINCT FROM -expected.quantity
         OR movement.created_by IS DISTINCT FROM m.cashier_user_id
         OR movement.operation_key IS DISTINCT FROM
           'pos:sale:'||p_sale::TEXT||':sale:'||movement.batch_id::TEXT))
   OR EXISTS(SELECT 1 FROM public.sale_item item WHERE item.tenant_id=p_tenant AND item.sale_id=p_sale
     GROUP BY item.batch_id HAVING (SELECT count(*) FROM public.batch_movement movement
       WHERE movement.tenant_id=p_tenant AND movement.batch_id=item.batch_id
         AND movement.source_table='sale' AND movement.source_id=p_sale)<>1) THEN
  RAISE EXCEPTION 'mixed_checkout_legacy_stock_invalid' USING ERRCODE='23514';
 END IF;
 IF jsonb_typeof(m.result_payload->'created_at') IS DISTINCT FROM 'string'
   OR jsonb_typeof(m.result_payload->'completed_at') IS DISTINCT FROM 'string'
   OR coalesce(m.result_payload->>'created_at','') !~ '(Z|[+-][0-9]{2}:[0-9]{2})$'
   OR coalesce(m.result_payload->>'completed_at','') !~ '(Z|[+-][0-9]{2}:[0-9]{2})$'
   OR jsonb_typeof(m.result_payload->'receipt_number') IS DISTINCT FROM 'string'
   OR length(m.result_payload->>'receipt_number') NOT BETWEEN 1 AND 100
   OR btrim(m.result_payload->>'receipt_number')='' OR m.result_payload->>'receipt_number' ~ '[[:cntrl:]]'
   OR jsonb_typeof(m.result_payload->'receipt_sequence') IS DISTINCT FROM 'number'
   OR coalesce(m.result_payload->>'receipt_sequence','') !~ '^[1-9][0-9]{0,18}$'
   OR jsonb_typeof(m.result_payload->'event_id') IS DISTINCT FROM 'string' THEN
  RAISE EXCEPTION 'mixed_checkout_header_invalid' USING ERRCODE='23514';
 END IF;
 PERFORM (m.result_payload->>'event_id')::UUID;
 v_created:=(m.result_payload->>'created_at')::TIMESTAMPTZ;
 v_completed:=(m.result_payload->>'completed_at')::TIMESTAMPTZ;
 IF v_created IS DISTINCT FROM s.created_at OR v_completed<v_created
   OR NOT isfinite(v_created) OR NOT isfinite(v_completed) THEN
  RAISE EXCEPTION 'mixed_checkout_time_invalid' USING ERRCODE='23514';
 END IF;
 v_payment:=jsonb_build_object('id',m.payment_id,'method','cash',
   'amount',to_char(v_total,'FM999999999990.00'),'currency','TJS');
 v_expected:=jsonb_build_object('schema_version',4,'document_kind','mixed_checkout',
   'tenant_id',p_tenant,'branch_id',m.branch_id,'register_id',m.register_id,'shift_id',m.shift_id,
   'cashier_id',m.cashier_user_id,'event_id',m.result_payload->'event_id','sale_id',p_sale,
   'operation_id',m.id,'created_at',m.result_payload->'created_at',
   'completed_at',m.result_payload->'completed_at','receipt_number',m.result_payload->'receipt_number',
   'receipt_sequence',m.result_payload->'receipt_sequence','is_test',false,
   'stock_command_id',c.id,'lines',v_lines,'payments',jsonb_build_array(v_payment),
   'total_amount',to_char(v_total,'FM999999999990.00'),'currency','TJS');
 IF m.result_payload IS DISTINCT FROM v_expected THEN
  RAISE EXCEPTION 'mixed_checkout_result_invalid' USING ERRCODE='23514';
 END IF;
 v_expected:=jsonb_build_object('schema_version',4,'document_kind','mixed_receipt',
   'receipt_number',m.result_payload->'receipt_number','receipt_sequence',m.result_payload->'receipt_sequence',
   'completed_at',m.result_payload->'completed_at','is_test',false,'lines',v_receipt_lines,
   'payments',jsonb_build_array(v_payment-ARRAY['id','currency']),
   'total_amount',to_char(v_total,'FM999999999990.00'),'currency','TJS');
 IF m.receipt_payload IS DISTINCT FROM v_expected THEN
  RAISE EXCEPTION 'mixed_checkout_receipt_invalid' USING ERRCODE='23514';
 END IF;
 IF EXISTS(SELECT 1 FROM public.sale_payment p WHERE p.sale_id=p_sale AND
   ((p.tenant_id,p.id,p.payment_method,p.amount,p.currency,p.operation_id,p.operation_hash)
      IS DISTINCT FROM (p_tenant,m.payment_id,'cash',v_total,'TJS',m.id,m.request_hash)
     OR p.payment_attempt_id IS NOT NULL OR p.metadata IS NOT NULL)) THEN
  RAISE EXCEPTION 'mixed_checkout_payment_invalid' USING ERRCODE='23514';
 END IF;
 IF p_final AND (s.status<>'completed' OR
   (s.receipt_number,s.receipt_seq,s.completed_at,s.total_amount,s.receipt_snapshot)
     IS DISTINCT FROM (m.result_payload->>'receipt_number',
       (m.result_payload->>'receipt_sequence')::BIGINT,v_completed,v_total,m.receipt_payload)
   OR (SELECT count(*) FROM public.sale_payment WHERE sale_id=p_sale)<>1) THEN
  RAISE EXCEPTION 'mixed_checkout_incomplete' USING ERRCODE='23514';
 END IF;
END $$;

CREATE FUNCTION public.trg_mixed_stock_checkout_binding() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS $$
DECLARE s public.sale%ROWTYPE; i public.pos_unit_cash_intent%ROWTYPE; h public.shift%ROWTYPE;
 v_active BOOLEAN;
BEGIN
 IF NEW.mixed_checkout_sale_id IS NULL THEN RETURN NEW; END IF;
 IF current_user<>'aurum_app' OR public.is_support_session()
   OR NEW.tenant_id IS DISTINCT FROM public.current_tenant_id()
   OR NEW.actor_id IS DISTINCT FROM public.current_app_user_id()
   OR NOT coalesce(public.tenant_actor_has_scoped_permission(
     NEW.tenant_id,'pos.sell',NEW.branch_id),false) THEN
  RAISE EXCEPTION 'mixed_checkout_actor_denied' USING ERRCODE='42501';
 END IF;
 SELECT * INTO s FROM public.sale
   WHERE tenant_id=NEW.tenant_id AND id=NEW.mixed_checkout_sale_id FOR UPDATE;
 SELECT * INTO i FROM public.pos_unit_cash_intent WHERE tenant_id=NEW.tenant_id AND id=NEW.id;
 SELECT * INTO h FROM public.shift WHERE tenant_id=NEW.tenant_id AND id=s.shift_id FOR UPDATE;
 SELECT is_active INTO v_active FROM public.register WHERE tenant_id=NEW.tenant_id
   AND id=s.register_id AND branch_id=s.branch_id FOR SHARE;
 IF s.id IS NULL OR s.status<>'draft' OR s.sale_type<>'sale' OR s.currency<>'TJS' OR s.is_test
   OR s.parent_sale_id IS NOT NULL OR s.receipt_number IS NOT NULL OR s.completed_at IS NOT NULL
   OR (s.branch_id,s.cashier_user_id) IS DISTINCT FROM (NEW.branch_id,NEW.actor_id)
   OR i.id IS NULL OR (i.sale_id,i.branch_id,i.register_id,i.shift_id,i.cashier_user_id)
      IS DISTINCT FROM (s.id,s.branch_id,s.register_id,s.shift_id,s.cashier_user_id)
   OR NEW.checkout_sale_id IS NOT NULL OR NEW.retail_version IS DISTINCT FROM 1
   OR NEW.currency IS DISTINCT FROM 'TJS' OR h.id IS NULL OR h.status<>'open' OR h.currency<>'TJS'
   OR (h.branch_id,h.register_id,h.opened_by_user_id)
      IS DISTINCT FROM (s.branch_id,s.register_id,s.cashier_user_id)
   OR v_active IS DISTINCT FROM true
   OR NOT EXISTS(SELECT 1 FROM public.pos_unit_cart_item WHERE tenant_id=NEW.tenant_id AND sale_id=s.id)
   OR NOT EXISTS(SELECT 1 FROM public.sale_item WHERE tenant_id=NEW.tenant_id AND sale_id=s.id)
   OR EXISTS(SELECT 1 FROM public.pos_unit_cart_item
      WHERE tenant_id=NEW.tenant_id AND sale_id=s.id AND physical_packaging IS NULL)
   OR EXISTS(SELECT 1 FROM public.sale_payment WHERE sale_id=s.id)
   OR EXISTS(SELECT 1 FROM public.pos_payment_attempt WHERE sale_id=s.id)
   OR EXISTS(SELECT 1 FROM public.pos_physical_checkout WHERE sale_id=s.id) THEN
  RAISE EXCEPTION 'mixed_checkout_draft_invalid' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION public.trg_mixed_checkout_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS $$
DECLARE r RECORD; v_id UUID; v_timezone TEXT; v_today DATE;
BEGIN
 IF TG_OP<>'INSERT' THEN
  RAISE EXCEPTION 'mixed_checkout_history_immutable' USING ERRCODE='55000';
 END IF;
 IF current_user<>'aurum_app' OR public.is_support_session()
   OR NEW.tenant_id IS DISTINCT FROM public.current_tenant_id()
   OR NEW.cashier_user_id IS DISTINCT FROM public.current_app_user_id()
   OR NOT coalesce(public.tenant_actor_has_scoped_permission(
      NEW.tenant_id,'pos.sell',NEW.branch_id),false) THEN
  RAISE EXCEPTION 'mixed_checkout_actor_denied' USING ERRCODE='42501';
 END IF;
 PERFORM 1 FROM public.sale WHERE tenant_id=NEW.tenant_id AND id=NEW.sale_id
   AND status='draft' AND NOT is_test AND cashier_user_id=NEW.cashier_user_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'mixed_checkout_draft_invalid' USING ERRCODE='23514'; END IF;
 PERFORM 1 FROM public.shift WHERE tenant_id=NEW.tenant_id AND id=NEW.shift_id
   AND status='open' AND currency='TJS' AND branch_id=NEW.branch_id
   AND register_id=NEW.register_id AND opened_by_user_id=NEW.cashier_user_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'mixed_checkout_shift_invalid' USING ERRCODE='23514'; END IF;
 PERFORM 1 FROM public.register WHERE tenant_id=NEW.tenant_id AND id=NEW.register_id
   AND branch_id=NEW.branch_id AND is_active FOR SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION 'mixed_checkout_register_invalid' USING ERRCODE='23514'; END IF;
 FOR v_id IN SELECT catalog_id FROM (
     SELECT catalog_id FROM public.sale_item WHERE tenant_id=NEW.tenant_id AND sale_id=NEW.sale_id
     UNION SELECT catalog_id FROM public.pos_unit_cart_item
       WHERE tenant_id=NEW.tenant_id AND sale_id=NEW.sale_id
   ) catalogs ORDER BY catalog_id LOOP
  PERFORM 1 FROM public.tenant_catalog WHERE tenant_id=NEW.tenant_id AND id=v_id FOR NO KEY UPDATE;
 END LOOP;
 FOR r IN SELECT cart.id,cart.kind,c.dispensing_type,c.is_active,c.deleted_at,c.currency FROM (
     SELECT id,catalog_id,'legacy' AS kind FROM public.sale_item
       WHERE tenant_id=NEW.tenant_id AND sale_id=NEW.sale_id
     UNION ALL SELECT id,catalog_id,'physical' FROM public.pos_unit_cart_item
       WHERE tenant_id=NEW.tenant_id AND sale_id=NEW.sale_id
   ) cart LEFT JOIN public.tenant_catalog c ON c.tenant_id=NEW.tenant_id AND c.id=cart.catalog_id LOOP
  IF r.is_active IS DISTINCT FROM true OR r.deleted_at IS NOT NULL
    OR r.currency IS DISTINCT FROM 'TJS' OR r.dispensing_type='special' THEN
   RAISE EXCEPTION 'mixed_checkout_catalog_invalid' USING ERRCODE='23514';
  END IF;
  IF r.dispensing_type='prescription' AND NOT EXISTS(SELECT 1 FROM public.prescription_log p
    WHERE p.tenant_id=NEW.tenant_id AND p.sale_id=NEW.sale_id
      AND ((r.kind='legacy' AND p.sale_item_id=r.id)
        OR (r.kind='physical' AND p.unit_cart_item_id=r.id)
        OR (p.unit_cart_item_id IS NULL AND p.sale_item_id IS NULL))
      AND regexp_replace(concat_ws('',p.prescription_number,p.doctor_name,p.doctor_license,
        p.patient_name,p.notes),'[[:space:]]','','g')<>'') THEN
   RAISE EXCEPTION 'mixed_checkout_prescription_required' USING ERRCODE='23514';
  END IF;
 END LOOP;
 IF jsonb_typeof(NEW.result_payload->'lines') IS DISTINCT FROM 'array' THEN
  RAISE EXCEPTION 'mixed_checkout_document_invalid' USING ERRCODE='23514';
 END IF;
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.result_payload->'lines') l
   LEFT JOIN public.tenant_catalog c ON c.tenant_id=NEW.tenant_id AND c.id=(l->>'catalog_id')::UUID
   WHERE c.id IS NULL OR l->>'name' IS DISTINCT FROM c.brand_name) THEN
  RAISE EXCEPTION 'mixed_checkout_name_snapshot_invalid' USING ERRCODE='23514';
 END IF;
 SELECT report_timezone INTO v_timezone FROM public.tenant_settings WHERE tenant_id=NEW.tenant_id;
 IF v_timezone IS NULL THEN
  RAISE EXCEPTION 'mixed_checkout_timezone_invalid' USING ERRCODE='23514';
 END IF;
 v_today:=(statement_timestamp() AT TIME ZONE v_timezone)::DATE;
 FOR v_id IN SELECT DISTINCT batch_id FROM public.sale_item
   WHERE tenant_id=NEW.tenant_id AND sale_id=NEW.sale_id ORDER BY batch_id LOOP
  PERFORM 1 FROM public.batch WHERE tenant_id=NEW.tenant_id AND id=v_id FOR UPDATE;
 END LOOP;
 IF EXISTS(SELECT 1 FROM public.sale_item item LEFT JOIN public.batch b
     ON b.tenant_id=item.tenant_id AND b.id=item.batch_id
   WHERE item.tenant_id=NEW.tenant_id AND item.sale_id=NEW.sale_id
     AND (b.id IS NULL OR b.is_blocked OR b.expires_at<=v_today)) THEN
  RAISE EXCEPTION 'mixed_checkout_legacy_batch_invalid' USING ERRCODE='23514';
 END IF;
 NEW.created_at:=statement_timestamp(); NEW.created_by:=public.current_app_user_id();
 RETURN NEW;
END $$;

CREATE FUNCTION public.trg_mixed_checkout_complete() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 IF TG_TABLE_NAME='unit_stock_cart_command_v2' THEN
  IF NEW.mixed_checkout_sale_id IS NOT NULL THEN
   PERFORM public.assert_mixed_checkout(NEW.tenant_id,NEW.mixed_checkout_sale_id,true);
  END IF;
 ELSIF TG_TABLE_NAME='batch_movement' THEN
  IF NEW.source_table='sale' AND EXISTS(SELECT 1 FROM public.unit_stock_cart_command_v2
    WHERE tenant_id=NEW.tenant_id AND mixed_checkout_sale_id=NEW.source_id) THEN
   PERFORM public.assert_mixed_checkout(NEW.tenant_id,NEW.source_id,true);
  END IF;
 ELSE
  PERFORM public.assert_mixed_checkout(NEW.tenant_id,NEW.sale_id,TG_NAME<>'a20_mixed_checkout_check');
 END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION public.trg_mixed_checkout_cart_frozen() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE v_tenant UUID; v_sale UUID;
BEGIN
 IF TG_OP='DELETE' THEN v_tenant:=OLD.tenant_id;v_sale:=OLD.sale_id;
 ELSE v_tenant:=NEW.tenant_id;v_sale:=NEW.sale_id; END IF;
 PERFORM 1 FROM public.sale WHERE tenant_id=v_tenant AND id=v_sale FOR UPDATE;
 IF EXISTS(SELECT 1 FROM public.unit_stock_cart_command_v2
     WHERE tenant_id=v_tenant AND mixed_checkout_sale_id=v_sale)
   OR (TG_OP='UPDATE' AND EXISTS(SELECT 1 FROM public.unit_stock_cart_command_v2
     WHERE tenant_id=OLD.tenant_id AND mixed_checkout_sale_id=OLD.sale_id)) THEN
  RAISE EXCEPTION 'mixed_checkout_cart_frozen' USING ERRCODE='55000';
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;RETURN NEW;
END $$;

CREATE FUNCTION public.trg_mixed_checkout_movement_frozen() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 IF NEW.source_table='sale' THEN
  PERFORM 1 FROM public.sale WHERE tenant_id=NEW.tenant_id AND id=NEW.source_id FOR UPDATE;
  IF EXISTS(SELECT 1 FROM public.unit_stock_cart_command_v2
    WHERE tenant_id=NEW.tenant_id AND mixed_checkout_sale_id=NEW.source_id) THEN
   RAISE EXCEPTION 'mixed_checkout_stock_frozen' USING ERRCODE='55000';
  END IF;
 END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION public.trg_mixed_checkout_return_boundary() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 -- A private definer lets Edge enforce this boundary without app-only table grants.
 IF NEW.parent_sale_id IS NOT NULL THEN
  IF EXISTS(SELECT 1 FROM public.pos_mixed_checkout
    WHERE tenant_id=NEW.tenant_id AND sale_id=NEW.parent_sale_id) THEN
   RAISE EXCEPTION 'mixed_checkout_refund_not_supported' USING ERRCODE='23514';
  END IF;
 END IF;
 RETURN NEW;
END $$;
"""

BOUNDARY_OLD = """ IF EXISTS(SELECT 1 FROM public.unit_stock_cart_command_v2
      WHERE tenant_id=v_tenant AND checkout_sale_id=v_sale_id) THEN"""
BOUNDARY_NEW = """ IF EXISTS(SELECT 1 FROM public.unit_stock_cart_command_v2
      WHERE tenant_id=v_tenant AND mixed_checkout_sale_id=v_sale_id) THEN
  IF session_user<>'aurum_app' OR public.is_support_session()
    OR NOT EXISTS(SELECT 1 FROM public.pos_mixed_checkout m
      WHERE m.tenant_id=v_tenant AND m.sale_id=v_sale_id
        AND m.cashier_user_id=public.current_app_user_id()
        AND v_tenant=public.current_tenant_id()
        AND public.tenant_actor_has_scoped_permission(v_tenant,'pos.sell',m.branch_id)) THEN
   RAISE EXCEPTION 'mixed_checkout_actor_denied' USING ERRCODE='42501';
  END IF;
  IF TG_OP<>'INSERT' AND TG_TABLE_NAME<>'sale' THEN
   RAISE EXCEPTION 'mixed_checkout_payment_immutable' USING ERRCODE='55000';
  END IF;
  IF TG_TABLE_NAME='pos_payment_attempt' OR TG_OP='DELETE' THEN
   RAISE EXCEPTION 'mixed_checkout_cash_only' USING ERRCODE='23514';
  END IF;
  PERFORM public.assert_mixed_checkout(v_tenant,v_sale_id,false);
  IF TG_TABLE_NAME='sale_payment' THEN
   IF NOT EXISTS(SELECT 1 FROM public.pos_mixed_checkout m
     WHERE m.tenant_id=v_tenant AND m.sale_id=v_sale_id
       AND (NEW.id,NEW.payment_method,NEW.amount,NEW.currency,NEW.operation_id,NEW.operation_hash)
         IS NOT DISTINCT FROM (m.payment_id,'cash',
           (m.result_payload->>'total_amount')::NUMERIC,'TJS',m.id,m.request_hash)
       AND NEW.payment_attempt_id IS NULL AND NEW.metadata IS NULL) THEN
    RAISE EXCEPTION 'mixed_checkout_payment_invalid' USING ERRCODE='23514';
   END IF;
  END IF;
 ELSIF EXISTS(SELECT 1 FROM public.unit_stock_cart_command_v2
      WHERE tenant_id=v_tenant AND checkout_sale_id=v_sale_id) THEN"""

DECLARATION_OLD = "  v_physical_refund BOOLEAN := false;"
DECLARATION_NEW = DECLARATION_OLD + "\n  v_mixed_cash BOOLEAN := false;"
PROBE_OLD = """  IF current_user = 'aurum_app' AND TG_OP = 'UPDATE' THEN
    SELECT EXISTS(SELECT 1 FROM public.pos_physical_checkout"""
PROBE_NEW = """  IF current_user = 'aurum_app' AND TG_OP = 'UPDATE' THEN
    SELECT EXISTS(SELECT 1 FROM public.unit_stock_cart_command_v2
      WHERE tenant_id=NEW.tenant_id AND mixed_checkout_sale_id=NEW.id)
      INTO v_mixed_cash;
    SELECT EXISTS(SELECT 1 FROM public.pos_physical_checkout"""
ITEM_OLD = "    IF v_physical_refund THEN"
ITEM_NEW = """    IF v_mixed_cash THEN
      IF NOT EXISTS(SELECT 1 FROM public.pos_mixed_checkout m
        WHERE m.tenant_id=NEW.tenant_id AND m.sale_id=NEW.id
          AND m.cashier_user_id=public.current_app_user_id()
          AND session_user='aurum_app' AND NOT public.is_support_session()
          AND public.tenant_actor_has_scoped_permission(m.tenant_id,'pos.sell',m.branch_id)
          AND (NEW.receipt_number,NEW.receipt_seq,NEW.completed_at,NEW.total_amount,
               NEW.receipt_snapshot,NEW.is_test)
            IS NOT DISTINCT FROM (m.result_payload->>'receipt_number',
              (m.result_payload->>'receipt_sequence')::BIGINT,
              (m.result_payload->>'completed_at')::TIMESTAMPTZ,
              (m.result_payload->>'total_amount')::NUMERIC,m.receipt_payload,false)) THEN
        RAISE EXCEPTION 'mixed_checkout_sale_invalid' USING ERRCODE='23514';
      END IF;
    ELSIF v_physical_refund THEN"""
TOTAL_OLD = "    IF NOT v_physical_cash AND NOT v_physical_refund AND ("
TOTAL_NEW = "    IF NOT v_physical_cash AND NOT v_physical_refund AND NOT v_mixed_cash AND ("


def upgrade() -> None:
    _sql("""
      CREATE TEMP TABLE mixed_checkout_reference_grants AS
        SELECT oid::regclass::TEXT AS relation,
          has_table_privilege('aurum_schema_owner',oid,'REFERENCES') AS granted
        FROM pg_class WHERE oid IN ('public.sale'::regclass,
          'public.pos_unit_cash_intent'::regclass,'public.unit_stock_cart_command_v2'::regclass);
      GRANT REFERENCES ON public.sale,public.pos_unit_cash_intent,
        public.unit_stock_cart_command_v2 TO aurum_schema_owner;
      ALTER TABLE public.unit_stock_cart_command_v2 ADD COLUMN mixed_checkout_sale_id UUID,
        ADD CONSTRAINT fk_unit_cart_v2_mixed_checkout_sale FOREIGN KEY(tenant_id,mixed_checkout_sale_id)
          REFERENCES public.sale(tenant_id,id),
        ADD CONSTRAINT ck_unit_cart_v2_checkout_kind
          CHECK(checkout_sale_id IS NULL OR mixed_checkout_sale_id IS NULL);
      CREATE UNIQUE INDEX uq_unit_cart_v2_mixed_checkout_sale
        ON public.unit_stock_cart_command_v2(tenant_id,mixed_checkout_sale_id)
        WHERE mixed_checkout_sale_id IS NOT NULL;
      CREATE TABLE public.pos_mixed_checkout (
        tenant_id UUID NOT NULL,id UUID NOT NULL,sale_id UUID NOT NULL,
        branch_id UUID NOT NULL,register_id UUID NOT NULL,shift_id UUID NOT NULL,
        cashier_user_id UUID NOT NULL,cash_intent_id UUID NOT NULL,stock_command_id UUID NOT NULL,
        payment_id UUID NOT NULL,request_hash TEXT NOT NULL CHECK(request_hash~'^[0-9a-f]{64}$'),
        result_payload JSONB NOT NULL CHECK(jsonb_typeof(result_payload)='object'),
        receipt_payload JSONB NOT NULL CHECK(jsonb_typeof(receipt_payload)='object'),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),created_by UUID NOT NULL,
        PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,sale_id),UNIQUE(tenant_id,stock_command_id),
        CHECK(id=cash_intent_id AND id=stock_command_id AND id=payment_id),
        FOREIGN KEY(tenant_id,sale_id) REFERENCES public.sale(tenant_id,id),
        FOREIGN KEY(tenant_id,cash_intent_id) REFERENCES public.pos_unit_cash_intent(tenant_id,id),
        FOREIGN KEY(tenant_id,stock_command_id)
          REFERENCES public.unit_stock_cart_command_v2(tenant_id,id)
      );
      DO $$ DECLARE r RECORD; BEGIN
        FOR r IN SELECT * FROM pg_temp.mixed_checkout_reference_grants WHERE NOT granted LOOP
          EXECUTE format('REVOKE REFERENCES ON %s FROM aurum_schema_owner',r.relation);
        END LOOP;
      END $$;
      DROP TABLE pg_temp.mixed_checkout_reference_grants;
      ALTER TABLE public.pos_mixed_checkout OWNER TO aurum_schema_owner;
      ALTER TABLE public.pos_mixed_checkout ENABLE ROW LEVEL SECURITY;
      ALTER TABLE public.pos_mixed_checkout FORCE ROW LEVEL SECURITY;
      REVOKE ALL ON public.pos_mixed_checkout FROM PUBLIC,aurum_app,aurum_support;
      GRANT SELECT,INSERT ON public.pos_mixed_checkout TO aurum_app;
      GRANT SELECT ON public.pos_mixed_checkout TO aurum_support;
      CREATE POLICY schema_owner ON public.pos_mixed_checkout TO aurum_schema_owner
        USING(true) WITH CHECK(true);
      CREATE POLICY support ON public.pos_mixed_checkout FOR SELECT TO aurum_support USING(true);
      CREATE POLICY tenant_actor ON public.pos_mixed_checkout TO aurum_app
        USING(tenant_id=public.current_tenant_id() AND cashier_user_id=public.current_app_user_id()
          AND public.tenant_actor_has_scoped_permission(tenant_id,'pos.sell',branch_id))
        WITH CHECK(tenant_id=public.current_tenant_id() AND cashier_user_id=public.current_app_user_id()
          AND public.tenant_actor_has_scoped_permission(tenant_id,'pos.sell',branch_id));
    """)
    _sql(FUNCTIONS_SQL)
    for signature in (
        "assert_mixed_checkout(uuid,uuid,boolean)",
        "trg_mixed_stock_checkout_binding()",
        "trg_mixed_checkout_guard()",
        "trg_mixed_checkout_complete()",
        "trg_mixed_checkout_cart_frozen()",
        "trg_mixed_checkout_movement_frozen()",
        "trg_mixed_checkout_return_boundary()",
    ):
        _sql(f"""
          ALTER FUNCTION public.{signature} OWNER TO aurum_schema_owner;
          REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC,aurum_app,aurum_support;
        """)
    _replace("trg_unit_cart_payment_boundary()", BOUNDARY_OLD, BOUNDARY_NEW)
    _replace("trg_guard_sale_immutability()", DECLARATION_OLD, DECLARATION_NEW)
    _replace("trg_guard_sale_immutability()", PROBE_OLD, PROBE_NEW)
    _replace("trg_guard_sale_immutability()", ITEM_OLD, ITEM_NEW)
    _replace("trg_guard_sale_immutability()", TOTAL_OLD, TOTAL_NEW)
    _sql("""
      CREATE TRIGGER a07_mixed_checkout_binding BEFORE INSERT ON public.unit_stock_cart_command_v2
        FOR EACH ROW EXECUTE FUNCTION public.trg_mixed_stock_checkout_binding();
      CREATE CONSTRAINT TRIGGER mixed_stock_checkout_complete AFTER INSERT
        ON public.unit_stock_cart_command_v2 DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION public.trg_mixed_checkout_complete();
      CREATE TRIGGER a00_tenant_access BEFORE INSERT OR UPDATE OR DELETE ON public.pos_mixed_checkout
        FOR EACH ROW EXECUTE FUNCTION public.trg_enforce_tenant_access();
      CREATE TRIGGER a10_mixed_checkout_guard BEFORE INSERT OR UPDATE OR DELETE ON public.pos_mixed_checkout
        FOR EACH ROW EXECUTE FUNCTION public.trg_mixed_checkout_guard();
      CREATE TRIGGER a20_mixed_checkout_check AFTER INSERT ON public.pos_mixed_checkout
        FOR EACH ROW EXECUTE FUNCTION public.trg_mixed_checkout_complete();
      CREATE CONSTRAINT TRIGGER mixed_checkout_complete AFTER INSERT ON public.pos_mixed_checkout
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.trg_mixed_checkout_complete();
      CREATE TRIGGER z90_audit AFTER INSERT OR UPDATE OR DELETE ON public.pos_mixed_checkout
        FOR EACH ROW EXECUTE FUNCTION public.trg_audit_log();
      CREATE TRIGGER a05_mixed_checkout_payment_frozen BEFORE UPDATE OR DELETE ON public.sale_payment
        FOR EACH ROW EXECUTE FUNCTION public.trg_mixed_checkout_cart_frozen();
      CREATE TRIGGER a05_mixed_checkout_stock_frozen BEFORE INSERT ON public.batch_movement
        FOR EACH ROW EXECUTE FUNCTION public.trg_mixed_checkout_movement_frozen();
      CREATE CONSTRAINT TRIGGER mixed_legacy_checkout_complete AFTER INSERT ON public.batch_movement
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.trg_mixed_checkout_complete();
      CREATE TRIGGER a05_mixed_checkout_return_boundary BEFORE INSERT OR UPDATE ON public.sale
        FOR EACH ROW EXECUTE FUNCTION public.trg_mixed_checkout_return_boundary();
    """)
    for table in ("pos_unit_cart_item", "sale_item", "prescription_log"):
        _sql(f"""
          CREATE TRIGGER a05_mixed_checkout_cart_frozen BEFORE INSERT OR UPDATE OR DELETE
            ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.trg_mixed_checkout_cart_frozen();
        """)


def downgrade() -> None:
    _sql("""
      LOCK TABLE public.pos_mixed_checkout,public.unit_stock_cart_command_v2,
        public.sale,public.sale_payment,public.batch_movement IN ACCESS EXCLUSIVE MODE;
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.pos_mixed_checkout)
          OR EXISTS(SELECT 1 FROM public.unit_stock_cart_command_v2
            WHERE mixed_checkout_sale_id IS NOT NULL) THEN
          RAISE EXCEPTION 'mixed_checkout_downgrade_loses_history' USING ERRCODE='55000';
        END IF;
      END $$;
    """)
    for table in ("pos_unit_cart_item", "sale_item", "prescription_log"):
        _sql(f"DROP TRIGGER a05_mixed_checkout_cart_frozen ON public.{table};")
    _replace("trg_unit_cart_payment_boundary()", BOUNDARY_NEW, BOUNDARY_OLD)
    _replace("trg_guard_sale_immutability()", TOTAL_NEW, TOTAL_OLD)
    _replace("trg_guard_sale_immutability()", ITEM_NEW, ITEM_OLD)
    _replace("trg_guard_sale_immutability()", PROBE_NEW, PROBE_OLD)
    _replace("trg_guard_sale_immutability()", DECLARATION_NEW, DECLARATION_OLD)
    _sql("""
      DROP TRIGGER a05_mixed_checkout_payment_frozen ON public.sale_payment;
      DROP TRIGGER a05_mixed_checkout_stock_frozen ON public.batch_movement;
      DROP TRIGGER mixed_legacy_checkout_complete ON public.batch_movement;
      DROP TRIGGER a05_mixed_checkout_return_boundary ON public.sale;
      DROP TRIGGER mixed_stock_checkout_complete ON public.unit_stock_cart_command_v2;
      DROP TRIGGER a07_mixed_checkout_binding ON public.unit_stock_cart_command_v2;
      DROP TABLE public.pos_mixed_checkout;
      DROP FUNCTION public.trg_mixed_checkout_return_boundary();
      DROP FUNCTION public.trg_mixed_checkout_movement_frozen();
      DROP FUNCTION public.trg_mixed_checkout_cart_frozen();
      DROP FUNCTION public.trg_mixed_checkout_complete();
      DROP FUNCTION public.trg_mixed_checkout_guard();
      DROP FUNCTION public.trg_mixed_stock_checkout_binding();
      DROP FUNCTION public.assert_mixed_checkout(uuid,uuid,boolean);
      DROP INDEX public.uq_unit_cart_v2_mixed_checkout_sale;
      ALTER TABLE public.unit_stock_cart_command_v2 DROP CONSTRAINT fk_unit_cart_v2_mixed_checkout_sale,
        DROP CONSTRAINT ck_unit_cart_v2_checkout_kind,DROP COLUMN mixed_checkout_sale_id;
    """)
