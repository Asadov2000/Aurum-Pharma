"""Persist immutable training-only checkout without real payment or stock effects.

Revision ID: 0224
Revises: 0223
"""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from sqlalchemy import text

from alembic import op

revision = "0224"
down_revision = "0223"
branch_labels = None
depends_on = None

FUNCTIONS = (
    "assert_training_checkout_stock_snapshot(jsonb,boolean)",
    "assert_training_checkout_document(public.pos_training_checkout)",
    "assert_training_checkout(uuid,uuid,boolean)",
    "trg_training_checkout_guard()",
    "training_checkout_sale_write_allowed(text,text,jsonb,jsonb)",
    "trg_training_checkout_complete()",
    "trg_training_checkout_return_boundary()",
)
COMPLETENESS_TABLES = (
    "pos_training_checkout",
    "sale",
    "sale_payment",
    "pos_payment_attempt",
    "batch_movement",
    "unit_stock_cart_command_v2",
)


def _sql(statement: str) -> None:
    spec = spec_from_file_location(
        "aurum_0224_sql", Path(__file__).with_name("0171_unit_cash_intents.py")
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load historical SQL executor")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    module._sql(statement)


def _patch(signature: str, old: str, new: str, *, upgrade: bool) -> None:
    definition = op.get_bind().scalar(
        text("SELECT pg_get_functiondef(CAST(:signature AS regprocedure))"),
        {"signature": f"public.{signature}"},
    )
    source, target = (old, new) if upgrade else (new, old)
    if not isinstance(definition, str) or definition.count(source) != 1:
        raise RuntimeError("Training checkout migration anchor changed")
    # pg_get_functiondef uses tagged $function$ bodies: this is one statement,
    # not a batch for the historical $$-only SQL splitter.
    updated = definition.replace(source, target, 1)
    op.execute(text(updated.replace(":", r"\:")))


SQL = """
CREATE FUNCTION public.assert_training_checkout_stock_snapshot(b JSONB,physical BOOLEAN)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,pg_temp AS $$
DECLARE common_keys TEXT[]:=ARRAY['id','tenant_id','branch_id','catalog_id',
 'expires_at','created_at','is_blocked']; key_name TEXT; packaging JSONB; pack JSONB;
 fragment JSONB; value JSONB; size BIGINT; step BIGINT; blister_size BIGINT;
 total BIGINT; remainder BIGINT; number BIGINT; seen_ids TEXT[]:='{}'; seen_numbers BIGINT[];
BEGIN
 IF jsonb_typeof(b) IS DISTINCT FROM 'object' OR NOT (b ?& common_keys)
   OR jsonb_typeof(b->'is_blocked') IS DISTINCT FROM 'boolean'
   OR jsonb_typeof(b->'expires_at') IS DISTINCT FROM 'string'
   OR coalesce(b->>'expires_at','') !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
   OR jsonb_typeof(b->'created_at') IS DISTINCT FROM 'string'
   OR coalesce(b->>'created_at','') !~
     '^[0-9]{4}-[0-9]{2}-[0-9]{2}T.*(Z|[+-][0-9]{2}:[0-9]{2})$' THEN
  RAISE EXCEPTION 'training_checkout_stock_shape_invalid' USING ERRCODE='23514';
 END IF;
 IF NOT isfinite((b->>'expires_at')::DATE)
   OR NOT isfinite((b->>'created_at')::TIMESTAMPTZ) THEN
  RAISE EXCEPTION 'training_checkout_stock_date_invalid' USING ERRCODE='23514';
 END IF;
 FOREACH key_name IN ARRAY ARRAY['id','tenant_id','branch_id','catalog_id'] LOOP
  IF jsonb_typeof(b->key_name) IS DISTINCT FROM 'string'
    OR coalesce(b->>key_name,'') !~
      '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN
   RAISE EXCEPTION 'training_checkout_stock_identity_invalid' USING ERRCODE='23514';
  END IF;
 END LOOP;
 IF NOT physical THEN
  IF b-(common_keys||ARRAY['quantity_remaining'])<>'{}'::JSONB
    OR NOT (b ? 'quantity_remaining')
    OR jsonb_typeof(b->'quantity_remaining') IS DISTINCT FROM 'string'
    OR coalesce(b->>'quantity_remaining','') !~ '^(0|[1-9][0-9]{0,10})[.][0-9]{3}$' THEN
   RAISE EXCEPTION 'training_checkout_legacy_stock_shape_invalid' USING ERRCODE='23514';
  END IF;
  RETURN;
 END IF;
 IF b-(common_keys||ARRAY['packaging','closed_packs','opened_packs'])<>'{}'::JSONB
   OR NOT (b ?& ARRAY['packaging','closed_packs','opened_packs'])
   OR jsonb_typeof(b->'closed_packs') IS DISTINCT FROM 'number'
   OR coalesce(b->>'closed_packs','') !~ '^(0|[1-9][0-9]{0,10})$'
   OR jsonb_typeof(b->'opened_packs') IS DISTINCT FROM 'array'
   OR jsonb_array_length(b->'opened_packs')>10000 THEN
  RAISE EXCEPTION 'training_checkout_physical_stock_shape_invalid' USING ERRCODE='23514';
 END IF;
 packaging:=b->'packaging';
 IF jsonb_typeof(packaging) IS DISTINCT FROM 'object'
   OR packaging-ARRAY['version','unit','units_per_pack','units_per_blister',
     'dispense_step']<>'{}'::JSONB
   OR NOT (packaging ?& ARRAY['version','unit','units_per_pack','units_per_blister',
     'dispense_step']) OR packaging->>'version' IS DISTINCT FROM '2'
   OR jsonb_typeof(packaging->'version') IS DISTINCT FROM 'number'
   OR packaging->>'unit' IS NULL
   OR packaging->>'unit' NOT IN ('tablet','capsule','ampoule','sachet','suppository')
   OR jsonb_typeof(packaging->'units_per_pack') IS DISTINCT FROM 'number'
   OR jsonb_typeof(packaging->'dispense_step') IS DISTINCT FROM 'number'
   OR coalesce(packaging->>'units_per_pack','') !~ '^[1-9][0-9]{0,4}$'
   OR coalesce(packaging->>'dispense_step','') !~ '^[1-9][0-9]{0,4}$' THEN
  RAISE EXCEPTION 'training_checkout_packaging_shape_invalid' USING ERRCODE='23514';
 END IF;
 size:=(packaging->>'units_per_pack')::BIGINT;
 step:=(packaging->>'dispense_step')::BIGINT;
 IF size NOT BETWEEN 2 AND 10000 OR step NOT BETWEEN 1 AND 10000 OR size%step<>0 THEN
  RAISE EXCEPTION 'training_checkout_packaging_shape_invalid' USING ERRCODE='23514';
 END IF;
 IF packaging->'units_per_blister'<>'null'::JSONB THEN
  IF jsonb_typeof(packaging->'units_per_blister') IS DISTINCT FROM 'number'
    OR coalesce(packaging->>'units_per_blister','') !~ '^[1-9][0-9]{0,4}$' THEN
   RAISE EXCEPTION 'training_checkout_packaging_shape_invalid' USING ERRCODE='23514';
  END IF;
  blister_size:=(packaging->>'units_per_blister')::BIGINT;
  IF blister_size NOT BETWEEN 2 AND 10000 OR size%blister_size<>0 THEN
   RAISE EXCEPTION 'training_checkout_packaging_shape_invalid' USING ERRCODE='23514';
  END IF;
 END IF;
 total:=(b->>'closed_packs')::BIGINT*size;
 FOR pack IN SELECT item FROM jsonb_array_elements(b->'opened_packs') AS entries(item) LOOP
  IF jsonb_typeof(pack) IS DISTINCT FROM 'object'
    OR pack-ARRAY['id','opened_at','intact_blisters','opened_blisters','loose_units']<>'{}'
    OR NOT (pack ?& ARRAY['id','opened_at','intact_blisters','opened_blisters','loose_units'])
    OR jsonb_typeof(pack->'id') IS DISTINCT FROM 'string'
    OR coalesce(pack->>'id','') !~
      '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    OR pack->>'id'=ANY(seen_ids)
    OR jsonb_typeof(pack->'opened_at') IS DISTINCT FROM 'string'
    OR coalesce(pack->>'opened_at','') !~
      '^[0-9]{4}-[0-9]{2}-[0-9]{2}T.*(Z|[+-][0-9]{2}:[0-9]{2})$'
    OR jsonb_typeof(pack->'loose_units') IS DISTINCT FROM 'number'
    OR coalesce(pack->>'loose_units','') !~ '^(0|[1-9][0-9]{0,4})$'
    OR jsonb_typeof(pack->'intact_blisters') IS DISTINCT FROM 'array'
    OR jsonb_typeof(pack->'opened_blisters') IS DISTINCT FROM 'array'
    OR jsonb_array_length(pack->'intact_blisters')>10000
    OR jsonb_array_length(pack->'opened_blisters')>10000 THEN
   RAISE EXCEPTION 'training_checkout_opened_pack_shape_invalid' USING ERRCODE='23514';
  END IF;
  IF NOT isfinite((pack->>'opened_at')::TIMESTAMPTZ) THEN
   RAISE EXCEPTION 'training_checkout_opened_pack_date_invalid' USING ERRCODE='23514';
  END IF;
  seen_ids:=array_append(seen_ids,pack->>'id'); seen_numbers:='{}';
  remainder:=(pack->>'loose_units')::BIGINT;
  IF blister_size IS NULL THEN
   IF pack->'intact_blisters'<>'[]'::JSONB OR pack->'opened_blisters'<>'[]'::JSONB THEN
    RAISE EXCEPTION 'training_checkout_unknown_blister_invalid' USING ERRCODE='23514';
   END IF;
  ELSE
   IF remainder<>0 THEN
    RAISE EXCEPTION 'training_checkout_known_blister_invalid' USING ERRCODE='23514';
   END IF;
   FOR value IN SELECT item FROM jsonb_array_elements(pack->'intact_blisters') e(item) LOOP
    IF jsonb_typeof(value) IS DISTINCT FROM 'number'
      OR value::TEXT !~ '^[1-9][0-9]{0,4}$' THEN
     RAISE EXCEPTION 'training_checkout_blister_number_invalid' USING ERRCODE='23514';
    END IF;
    number:=(value::TEXT)::BIGINT;
    IF number>size/blister_size OR number=ANY(seen_numbers) THEN
     RAISE EXCEPTION 'training_checkout_blister_number_invalid' USING ERRCODE='23514';
    END IF;
    seen_numbers:=array_append(seen_numbers,number); remainder:=remainder+blister_size;
   END LOOP;
   FOR fragment IN SELECT item FROM jsonb_array_elements(pack->'opened_blisters') e(item) LOOP
    IF jsonb_typeof(fragment) IS DISTINCT FROM 'object'
      OR fragment-ARRAY['number','remaining_units']<>'{}'::JSONB
      OR NOT (fragment ?& ARRAY['number','remaining_units'])
      OR jsonb_typeof(fragment->'number') IS DISTINCT FROM 'number'
      OR coalesce(fragment->>'number','') !~ '^[1-9][0-9]{0,4}$'
      OR jsonb_typeof(fragment->'remaining_units') IS DISTINCT FROM 'number'
      OR coalesce(fragment->>'remaining_units','') !~ '^[1-9][0-9]{0,4}$' THEN
     RAISE EXCEPTION 'training_checkout_opened_blister_shape_invalid' USING ERRCODE='23514';
    END IF;
    number:=(fragment->>'number')::BIGINT;
    IF number>size/blister_size OR number=ANY(seen_numbers)
      OR (fragment->>'remaining_units')::BIGINT>=blister_size THEN
     RAISE EXCEPTION 'training_checkout_opened_blister_shape_invalid' USING ERRCODE='23514';
    END IF;
    seen_numbers:=array_append(seen_numbers,number);
    remainder:=remainder+(fragment->>'remaining_units')::BIGINT;
   END LOOP;
  END IF;
  IF remainder NOT BETWEEN 1 AND size THEN
   RAISE EXCEPTION 'training_checkout_opened_pack_capacity_invalid' USING ERRCODE='23514';
  END IF;
  total:=total+remainder;
 END LOOP;
 IF total>99999999999 THEN
  RAISE EXCEPTION 'training_checkout_stock_capacity_invalid' USING ERRCODE='23514';
 END IF;
END $$;

CREATE FUNCTION public.assert_training_checkout_document(m public.pos_training_checkout)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,pg_temp AS $$
DECLARE r JSONB:=m.result_payload; d JSONB:=m.receipt_payload; q JSONB; request JSONB;
 line JSONB; receipt_line JSONB; payment JSONB; identity TEXT; field_name TEXT;
 batch JSONB; snapshot_key TEXT; stock_ids TEXT[]; before_ids TEXT[];
 scope_keys TEXT[]:=ARRAY['schema_version','is_test','operation_id','sale_id','tenant_id',
   'branch_id','register_id','shift_id','cashier_id','receipt_number','receipt_sequence',
   'completed_at','total_amount','currency']; keys TEXT[];
 positions INTEGER:=0; seen TEXT[]:='{}'; physical_count INTEGER:=0;
 line_total NUMERIC:=0; payment_total NUMERIC:=0; cash NUMERIC:=0;
 gross NUMERIC; discount NUMERIC; net NUMERIC; amount NUMERIC;
BEGIN
 q:=r->'simulation'->'quote'; request:=r->'request';
 keys:=scope_keys||ARRAY['document_kind','request','receipt','simulation'];
 IF r-keys<>'{}'::JSONB OR NOT (r ?& keys) THEN
  RAISE EXCEPTION 'training_checkout_result_shape_invalid' USING ERRCODE='23514';
 END IF;
 keys:=scope_keys||ARRAY['document_kind','lines','simulated_payments',
   'cash_tendered','change_amount'];
 IF d-keys<>'{}'::JSONB OR NOT (d ?& keys) THEN
  RAISE EXCEPTION 'training_checkout_receipt_shape_invalid' USING ERRCODE='23514';
 END IF;
 keys:=ARRAY['schema_version','operation_id','sale_id','expected_revision_hash',
   'expected_total_amount','simulated_payments','cash_tendered','currency'];
 IF request-keys<>'{}'::JSONB OR NOT coalesce(request ?& keys,false) THEN
  RAISE EXCEPTION 'training_checkout_request_shape_invalid' USING ERRCODE='23514';
 END IF;
 keys:=ARRAY['schema_version','state','tenant_id','branch_id','sale_id','register_id',
   'shift_id','cashier_id','lines','total_amount','currency'];
 IF q-keys<>'{}'::JSONB OR NOT coalesce(q ?& keys,false) THEN
  RAISE EXCEPTION 'training_checkout_quote_shape_invalid' USING ERRCODE='23514';
 END IF;
 keys:=ARRAY['schema_version','state','is_test','stock_date','quote','total_amount',
   'physical_before','physical_after','legacy_before','legacy_after'];
 IF jsonb_typeof(r->'simulation') IS DISTINCT FROM 'object'
   OR r->'simulation'-keys<>'{}'::JSONB OR NOT (r->'simulation' ?& keys)
   OR jsonb_typeof(r->'simulation'->'stock_date') IS DISTINCT FROM 'string'
   OR coalesce(r->'simulation'->>'stock_date','') !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' THEN
  RAISE EXCEPTION 'training_checkout_simulation_shape_invalid' USING ERRCODE='23514';
 END IF;
 IF NOT isfinite((r->'simulation'->>'stock_date')::DATE) THEN
  RAISE EXCEPTION 'training_checkout_simulation_date_invalid' USING ERRCODE='23514';
 END IF;
 IF jsonb_typeof(r) IS DISTINCT FROM 'object'
   OR jsonb_typeof(d) IS DISTINCT FROM 'object'
   OR r->'schema_version' IS DISTINCT FROM '1'::JSONB
   OR r->>'schema_version' IS DISTINCT FROM '1'
   OR d->'schema_version' IS DISTINCT FROM '1'::JSONB
   OR d->>'schema_version' IS DISTINCT FROM '1'
   OR r->>'document_kind' IS DISTINCT FROM 'training_checkout'
   OR d->>'document_kind' IS DISTINCT FROM 'training_sale_receipt'
   OR r->'is_test' IS DISTINCT FROM 'true'::JSONB
   OR d->'is_test' IS DISTINCT FROM 'true'::JSONB
   OR r->'receipt' IS DISTINCT FROM d
   OR r->'simulation'->'schema_version' IS DISTINCT FROM '1'::JSONB
   OR r->'simulation'->>'schema_version' IS DISTINCT FROM '1'
   OR r->'simulation'->'is_test' IS DISTINCT FROM 'true'::JSONB
   OR r->'simulation'->>'state' IS DISTINCT FROM 'simulated_training_sale'
   OR q->'schema_version' IS DISTINCT FROM '2'::JSONB
   OR q->>'schema_version' IS DISTINCT FROM '2'
   OR q->>'state' IS DISTINCT FROM 'draft_quote'
   OR jsonb_typeof(request) IS DISTINCT FROM 'object'
   OR request->'schema_version' IS DISTINCT FROM '1'::JSONB
   OR request->>'schema_version' IS DISTINCT FROM '1'
   OR request->>'operation_id' IS DISTINCT FROM m.id::TEXT
   OR request->>'sale_id' IS DISTINCT FROM m.sale_id::TEXT
   OR request->>'expected_revision_hash' IS DISTINCT FROM m.quote_revision
   OR request->>'currency' IS DISTINCT FROM 'TJS'
   OR (r->>'receipt_number',d->>'receipt_number') IS DISTINCT FROM
     (m.receipt_number,m.receipt_number)
   OR (r->'receipt_sequence',d->'receipt_sequence') IS DISTINCT FROM
     (to_jsonb(m.receipt_seq),to_jsonb(m.receipt_seq))
   OR r->>'receipt_sequence' IS DISTINCT FROM m.receipt_seq::TEXT
   OR d->>'receipt_sequence' IS DISTINCT FROM m.receipt_seq::TEXT
   OR r->>'operation_id' IS DISTINCT FROM m.id::TEXT
   OR d->>'operation_id' IS DISTINCT FROM m.id::TEXT
   OR jsonb_typeof(r->'completed_at') IS DISTINCT FROM 'string'
   OR r->'completed_at' IS DISTINCT FROM d->'completed_at'
   OR coalesce(r->>'completed_at','') !~ '(Z|[+-][0-9]{2}:[0-9]{2})$'
   OR jsonb_typeof(q->'lines') IS DISTINCT FROM 'array'
   OR jsonb_array_length(q->'lines') NOT BETWEEN 1 AND 200
   OR jsonb_typeof(d->'lines') IS DISTINCT FROM 'array'
   OR jsonb_array_length(d->'lines') NOT BETWEEN 1 AND 200
   OR jsonb_typeof(d->'simulated_payments') IS DISTINCT FROM 'array'
   OR jsonb_array_length(d->'simulated_payments') NOT BETWEEN 1 AND 3
   OR request->'simulated_payments' IS DISTINCT FROM d->'simulated_payments'
   OR request->'cash_tendered' IS DISTINCT FROM d->'cash_tendered' THEN
  RAISE EXCEPTION 'training_checkout_document_invalid' USING ERRCODE='23514';
 END IF;
 IF encode(sha256(convert_to(public.edge_canonical_jsonb(request),'UTF8')),'hex')
     IS DISTINCT FROM m.request_hash
   OR encode(sha256(convert_to(public.edge_canonical_jsonb(q),'UTF8')),'hex')
     IS DISTINCT FROM m.quote_revision THEN
  RAISE EXCEPTION 'training_checkout_hash_invalid' USING ERRCODE='23514';
 END IF;
 FOREACH field_name IN ARRAY ARRAY['tenant_id','branch_id','register_id','shift_id',
   'cashier_id','sale_id'] LOOP
  identity:=CASE field_name WHEN 'cashier_id' THEN m.cashier_user_id::TEXT
    ELSE to_jsonb(m)->>field_name END;
  IF r->>field_name IS DISTINCT FROM identity
    OR d->>field_name IS DISTINCT FROM identity
    OR q->>field_name IS DISTINCT FROM identity THEN
   RAISE EXCEPTION 'training_checkout_scope_invalid' USING ERRCODE='23514';
  END IF;
 END LOOP;
 FOREACH snapshot_key IN ARRAY ARRAY['physical_before','physical_after',
   'legacy_before','legacy_after'] LOOP
  IF jsonb_typeof(r->'simulation'->snapshot_key) IS DISTINCT FROM 'array'
    OR jsonb_array_length(r->'simulation'->snapshot_key)>10000 THEN
   RAISE EXCEPTION 'training_checkout_stock_snapshot_invalid' USING ERRCODE='23514';
  END IF;
  stock_ids:='{}';
  FOR batch IN SELECT value FROM jsonb_array_elements(r->'simulation'->snapshot_key) LOOP
   PERFORM public.assert_training_checkout_stock_snapshot(batch,snapshot_key LIKE 'physical_%');
   IF batch->>'tenant_id' IS DISTINCT FROM m.tenant_id::TEXT
     OR batch->>'branch_id' IS DISTINCT FROM m.branch_id::TEXT
     OR batch->>'id' IS NULL OR (batch->>'id')=ANY(stock_ids)
     OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(q->'lines') item
       WHERE item->>'catalog_id'=batch->>'catalog_id'
         AND item->>'inventory_kind'=CASE WHEN snapshot_key LIKE 'physical_%'
           THEN 'physical_unit' ELSE 'legacy_pack' END) THEN
    RAISE EXCEPTION 'training_checkout_stock_scope_invalid' USING ERRCODE='23514';
   END IF;
   stock_ids:=array_append(stock_ids,batch->>'id');
  END LOOP;
  IF snapshot_key LIKE '%_before' THEN before_ids:=stock_ids;
  ELSIF NOT (stock_ids @> before_ids AND before_ids @> stock_ids) THEN
   RAISE EXCEPTION 'training_checkout_stock_identity_invalid' USING ERRCODE='23514';
  END IF;
 END LOOP;
 IF r->>'currency' IS DISTINCT FROM 'TJS' OR d->>'currency' IS DISTINCT FROM 'TJS'
   OR q->>'currency' IS DISTINCT FROM 'TJS'
   OR r->'total_amount' IS DISTINCT FROM d->'total_amount'
   OR r->'total_amount' IS DISTINCT FROM q->'total_amount'
   OR r->'total_amount' IS DISTINCT FROM r->'simulation'->'total_amount'
   OR r->'total_amount' IS DISTINCT FROM request->'expected_total_amount'
   OR jsonb_typeof(r->'total_amount') IS DISTINCT FROM 'string'
   OR coalesce(r->>'total_amount','') !~ '^(0|[1-9][0-9]{0,11})[.][0-9]{2}$'
   OR (r->>'total_amount')::NUMERIC IS DISTINCT FROM m.total_amount
   OR jsonb_array_length(q->'lines') NOT BETWEEN 1 AND 200
   OR jsonb_array_length(q->'lines')<>jsonb_array_length(d->'lines') THEN
  RAISE EXCEPTION 'training_checkout_total_invalid' USING ERRCODE='23514';
 END IF;
 FOR line IN SELECT value FROM jsonb_array_elements(q->'lines') LOOP
  positions:=positions+1; receipt_line:=d->'lines'->(positions-1);
  keys:=ARRAY['inventory_kind','id','position','catalog_id','quantity',
    'original_retail_unit_price','gross_amount','discount_amount','net_amount',
    'money_version','currency'];
  IF line->>'inventory_kind'='physical_unit' THEN
   keys:=keys||ARRAY['packaging','sale_format','base_units'];
  ELSE keys:=keys||ARRAY['batch_id']; END IF;
  IF jsonb_typeof(line) IS DISTINCT FROM 'object' OR line-keys<>'{}'::JSONB
    OR NOT (line ?& keys) THEN
   RAISE EXCEPTION 'training_checkout_quote_line_shape_invalid' USING ERRCODE='23514';
  END IF;
  keys:=ARRAY['inventory_kind','position','name','sale_format','quantity','unit_label',
    'original_retail_unit_price','gross_amount','discount_amount','net_amount',
    'money_version','currency'];
  IF line->>'inventory_kind'='physical_unit' THEN
   keys:=keys||ARRAY['packaging','base_units'];
  END IF;
  IF jsonb_typeof(receipt_line) IS DISTINCT FROM 'object'
    OR receipt_line-keys<>'{}'::JSONB OR NOT (receipt_line ?& keys) THEN
   RAISE EXCEPTION 'training_checkout_receipt_line_shape_invalid' USING ERRCODE='23514';
  END IF;
  IF line->'position' IS DISTINCT FROM to_jsonb(positions)
    OR line->>'position' IS DISTINCT FROM positions::TEXT
    OR receipt_line->>'position' IS DISTINCT FROM positions::TEXT
    OR receipt_line->'position' IS DISTINCT FROM line->'position'
    OR line->>'inventory_kind' NOT IN ('legacy_pack','physical_unit')
    OR line->>'inventory_kind' IS NULL
    OR receipt_line->'inventory_kind' IS DISTINCT FROM line->'inventory_kind'
    OR receipt_line->'quantity' IS DISTINCT FROM line->'quantity'
    OR receipt_line->>'currency' IS DISTINCT FROM 'TJS'
    OR line->>'currency' IS DISTINCT FROM 'TJS'
    OR line->'money_version' IS DISTINCT FROM '1'::JSONB
    OR line->>'money_version' IS DISTINCT FROM '1'
    OR receipt_line->'money_version' IS DISTINCT FROM '1'::JSONB
    OR receipt_line->>'money_version' IS DISTINCT FROM '1'
    OR jsonb_typeof(receipt_line->'name') IS DISTINCT FROM 'string'
    OR length(btrim(coalesce(receipt_line->>'name',''))) NOT BETWEEN 1 AND 500
    OR receipt_line->>'name' ~ '[[:cntrl:]]'
    OR jsonb_typeof(receipt_line->'unit_label') IS DISTINCT FROM 'string'
    OR coalesce(receipt_line->>'unit_label','')=''
    OR line->>'id' IS NULL OR (line->>'id')=ANY(seen) THEN
   RAISE EXCEPTION 'training_checkout_line_invalid' USING ERRCODE='23514';
  END IF;
  seen:=array_append(seen,line->>'id');
  FOREACH field_name IN ARRAY ARRAY['original_retail_unit_price','gross_amount',
    'discount_amount','net_amount'] LOOP
   IF line->field_name IS DISTINCT FROM receipt_line->field_name
     OR jsonb_typeof(line->field_name) IS DISTINCT FROM 'string'
     OR coalesce(line->>field_name,'') !~ '^(0|[1-9][0-9]{0,11})[.][0-9]{2}$' THEN
    RAISE EXCEPTION 'training_checkout_line_money_invalid' USING ERRCODE='23514';
   END IF;
  END LOOP;
  gross:=(line->>'gross_amount')::NUMERIC;
  discount:=(line->>'discount_amount')::NUMERIC;
  net:=(line->>'net_amount')::NUMERIC;
  IF gross<>net+discount OR net<0
    OR (line->>'original_retail_unit_price')::NUMERIC<=0 THEN
   RAISE EXCEPTION 'training_checkout_line_money_invalid' USING ERRCODE='23514';
  END IF;
  line_total:=line_total+net;
  IF NOT EXISTS(SELECT 1 FROM public.tenant_catalog catalog
    WHERE catalog.tenant_id=m.tenant_id AND catalog.id::TEXT=line->>'catalog_id'
      AND catalog.brand_name=receipt_line->>'name') THEN
   RAISE EXCEPTION 'training_checkout_name_snapshot_invalid' USING ERRCODE='23514';
  END IF;
  IF line->>'inventory_kind'='physical_unit' THEN
   physical_count:=physical_count+1;
   IF jsonb_typeof(line->'quantity') IS DISTINCT FROM 'number'
     OR coalesce(line->>'quantity','') !~ '^[1-9][0-9]{0,10}$'
     OR jsonb_typeof(line->'base_units') IS DISTINCT FROM 'number'
     OR coalesce(line->>'base_units','') !~ '^[1-9][0-9]{0,10}$'
     OR receipt_line->>'quantity' IS DISTINCT FROM line->>'quantity'
     OR receipt_line->>'base_units' IS DISTINCT FROM line->>'base_units'
     OR receipt_line->>'unit_label' IS DISTINCT FROM (CASE line->>'sale_format'
       WHEN 'pack' THEN 'уп.' WHEN 'blister' THEN 'блист.'
       ELSE CASE line->'packaging'->>'unit'
         WHEN 'tablet' THEN 'табл.' WHEN 'capsule' THEN 'капс.' WHEN 'ampoule' THEN 'амп.'
         WHEN 'sachet' THEN 'саше' WHEN 'suppository' THEN 'супп.' END END)
     OR net<=0
     OR (receipt_line->'packaging')::TEXT IS DISTINCT FROM (line->'packaging')::TEXT
     OR receipt_line->'base_units' IS DISTINCT FROM line->'base_units'
     OR receipt_line->'sale_format' IS DISTINCT FROM line->'sale_format'
     OR NOT EXISTS(SELECT 1 FROM public.pos_unit_cart_item item
       JOIN public.tenant_catalog catalog ON catalog.tenant_id=item.tenant_id
         AND catalog.id=item.catalog_id
       WHERE item.tenant_id=m.tenant_id AND item.sale_id=m.sale_id
         AND item.id::TEXT=line->>'id' AND item.catalog_id::TEXT=line->>'catalog_id'
         AND item.physical_packaging IS NOT NULL
         AND item.physical_packaging::TEXT=(line->'packaging')::TEXT
         AND item.sale_format=line->>'sale_format'
         AND to_jsonb(item.quantity)=line->'quantity'
         AND item.discount_amount=discount
         AND catalog.is_active AND catalog.deleted_at IS NULL AND catalog.currency='TJS'
         AND catalog.unit_sale_preparation IS NOT NULL
         AND catalog.unit_sale_preparation->'unit'=item.physical_packaging->'unit'
         AND catalog.unit_sale_preparation->'units_per_pack'
           =item.physical_packaging->'units_per_pack'
         AND coalesce(catalog.unit_sale_preparation->'units_per_blister','null'::JSONB)
           =item.physical_packaging->'units_per_blister'
         AND catalog.unit_sale_preparation->'dispense_step'
           =item.physical_packaging->'dispense_step'
         AND (line->>'original_retail_unit_price')::NUMERIC=CASE item.sale_format
           WHEN 'pack' THEN catalog.base_price
           WHEN 'blister' THEN (catalog.unit_sale_preparation->>'unit_price')::NUMERIC
             *(item.physical_packaging->>'units_per_blister')::NUMERIC
           ELSE (catalog.unit_sale_preparation->>'unit_price')::NUMERIC END
         AND gross=(line->>'original_retail_unit_price')::NUMERIC*item.quantity
         AND (line->>'base_units')::NUMERIC=item.quantity*CASE item.sale_format
           WHEN 'pack' THEN (item.physical_packaging->>'units_per_pack')::NUMERIC
           WHEN 'blister' THEN (item.physical_packaging->>'units_per_blister')::NUMERIC
           ELSE 1 END) THEN
    RAISE EXCEPTION 'training_checkout_physical_source_invalid' USING ERRCODE='23514';
   END IF;
  ELSIF jsonb_typeof(line->'quantity') IS DISTINCT FROM 'string'
    OR coalesce(line->>'quantity','') !~ '^(0|[1-9][0-9]{0,10})[.][0-9]{3}$'
    OR (line->>'quantity')::NUMERIC<=0
    OR receipt_line->>'sale_format' IS DISTINCT FROM 'pack'
    OR receipt_line->>'unit_label' IS DISTINCT FROM 'уп.'
    OR NOT EXISTS(SELECT 1 FROM public.sale_item item
    WHERE item.tenant_id=m.tenant_id AND item.sale_id=m.sale_id
      AND item.id::TEXT=line->>'id' AND item.catalog_id::TEXT=line->>'catalog_id'
      AND item.batch_id::TEXT=line->>'batch_id'
      AND item.qty=(line->>'quantity')::NUMERIC
      AND item.unit_price=(line->>'original_retail_unit_price')::NUMERIC
      AND item.total_price=net AND item.discount_amount=discount
      AND item.parent_sale_item_id IS NULL AND item.currency='TJS') THEN
   RAISE EXCEPTION 'training_checkout_legacy_source_invalid' USING ERRCODE='23514';
  END IF;
 END LOOP;
 IF physical_count=0 OR line_total<>m.total_amount
   OR positions<>(SELECT count(*) FROM public.pos_unit_cart_item
     WHERE tenant_id=m.tenant_id AND sale_id=m.sale_id)
     +(SELECT count(*) FROM public.sale_item
       WHERE tenant_id=m.tenant_id AND sale_id=m.sale_id) THEN
  RAISE EXCEPTION 'training_checkout_cart_incomplete' USING ERRCODE='23514';
 END IF;
 seen:='{}';
 IF jsonb_array_length(d->'simulated_payments') NOT BETWEEN 1 AND 3 THEN
  RAISE EXCEPTION 'training_checkout_simulated_payments_invalid' USING ERRCODE='23514';
 END IF;
 FOR payment IN SELECT value FROM jsonb_array_elements(d->'simulated_payments') LOOP
  IF payment-ARRAY['method','amount']<>'{}'::JSONB
    OR payment->>'method' NOT IN ('cash','card','qr') OR payment->>'method' IS NULL
    OR (payment->>'method')=ANY(seen)
    OR jsonb_typeof(payment->'amount') IS DISTINCT FROM 'string'
    OR coalesce(payment->>'amount','') !~ '^(0|[1-9][0-9]{0,11})[.][0-9]{2}$' THEN
   RAISE EXCEPTION 'training_checkout_simulated_payments_invalid' USING ERRCODE='23514';
  END IF;
  amount:=(payment->>'amount')::NUMERIC;
  IF amount<=0 THEN
   RAISE EXCEPTION 'training_checkout_simulated_payments_invalid' USING ERRCODE='23514';
  END IF;
  seen:=array_append(seen,payment->>'method'); payment_total:=payment_total+amount;
  IF payment->>'method'='cash' THEN cash:=amount; END IF;
 END LOOP;
 IF payment_total<>m.total_amount
   OR jsonb_typeof(d->'change_amount') IS DISTINCT FROM 'string'
   OR coalesce(d->>'change_amount','') !~ '^(0|[1-9][0-9]{0,11})[.][0-9]{2}$' THEN
  RAISE EXCEPTION 'training_checkout_simulated_payments_invalid' USING ERRCODE='23514';
 END IF;
 IF cash=0 THEN
  IF d->'cash_tendered' IS DISTINCT FROM '"0.00"'::JSONB
    OR (d->>'change_amount')::NUMERIC<>0 THEN
   RAISE EXCEPTION 'training_checkout_simulated_cash_invalid' USING ERRCODE='23514';
  END IF;
 ELSIF jsonb_typeof(d->'cash_tendered') IS DISTINCT FROM 'string'
   OR coalesce(d->>'cash_tendered','') !~ '^(0|[1-9][0-9]{0,11})[.][0-9]{2}$'
   OR (d->>'cash_tendered')::NUMERIC<cash
   OR (d->>'change_amount')::NUMERIC<>(d->>'cash_tendered')::NUMERIC-cash THEN
  RAISE EXCEPTION 'training_checkout_simulated_cash_invalid' USING ERRCODE='23514';
 END IF;
END $$;

CREATE FUNCTION public.assert_training_checkout(p_tenant UUID,p_sale UUID,p_final BOOLEAN)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,pg_temp AS $$
DECLARE m public.pos_training_checkout%ROWTYPE; s public.sale%ROWTYPE;
BEGIN
 SELECT * INTO m FROM public.pos_training_checkout
  WHERE tenant_id=p_tenant AND sale_id=p_sale;
 SELECT * INTO s FROM public.sale WHERE tenant_id=p_tenant AND id=p_sale;
 IF m.id IS NULL OR s.id IS NULL OR NOT s.is_test OR s.sale_type<>'sale'
   OR s.parent_sale_id IS NOT NULL OR s.refund_attempt_id IS NOT NULL
   OR s.currency<>'TJS' OR s.status NOT IN ('draft','completed')
   OR (s.branch_id,s.register_id,s.shift_id,s.cashier_user_id) IS DISTINCT FROM
     (m.branch_id,m.register_id,m.shift_id,m.cashier_user_id) THEN
  RAISE EXCEPTION 'training_checkout_sale_invalid' USING ERRCODE='23514';
 END IF;
 PERFORM public.assert_training_checkout_document(m);
 IF EXISTS(SELECT 1 FROM public.sale_payment WHERE tenant_id=p_tenant AND sale_id=p_sale)
   OR EXISTS(SELECT 1 FROM public.pos_payment_attempt
     WHERE tenant_id=p_tenant AND sale_id=p_sale)
   OR EXISTS(SELECT 1 FROM public.unit_stock_cart_command_v2
     WHERE tenant_id=p_tenant AND (checkout_sale_id=p_sale OR mixed_checkout_sale_id=p_sale))
   OR EXISTS(SELECT 1 FROM public.batch_movement
     WHERE tenant_id=p_tenant AND source_table='sale' AND source_id=p_sale)
   OR EXISTS(SELECT 1 FROM public.pos_physical_checkout
     WHERE tenant_id=p_tenant AND sale_id=p_sale)
   OR EXISTS(SELECT 1 FROM public.pos_mixed_checkout
     WHERE tenant_id=p_tenant AND sale_id=p_sale) THEN
  RAISE EXCEPTION 'training_checkout_real_effect_forbidden' USING ERRCODE='23514';
 END IF;
 IF p_final AND (s.status<>'completed'
   OR (s.total_amount,s.receipt_number,s.receipt_seq,
     s.receipt_snapshot,s.completed_at) IS DISTINCT FROM
     (m.total_amount,m.receipt_number,m.receipt_seq,m.receipt_payload,
       (m.result_payload->>'completed_at')::TIMESTAMPTZ)) THEN
  RAISE EXCEPTION 'training_checkout_incomplete' USING ERRCODE='23514';
 END IF;
END $$;

CREATE FUNCTION public.trg_training_checkout_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE s public.sale%ROWTYPE; work_shift public.shift%ROWTYPE; actor UUID;
 completed TIMESTAMPTZ;
BEGIN
 IF TG_OP<>'INSERT' THEN
  RAISE EXCEPTION 'training_checkout_immutable' USING ERRCODE='55000';
 END IF;
 actor:=public.current_app_user_id();
 IF session_user<>'aurum_app' OR public.is_support_session()
   OR NEW.tenant_id IS DISTINCT FROM public.current_tenant_id()
   OR NEW.cashier_user_id IS DISTINCT FROM actor OR actor IS NULL
   OR NOT coalesce(public.tenant_actor_has_scoped_permission(
     NEW.tenant_id,'pos.sell',NEW.branch_id),false)
   OR NOT EXISTS(SELECT 1 FROM public.app_user WHERE id=actor AND status='active')
   OR current_setting('transaction_isolation')<>'read committed' THEN
  RAISE EXCEPTION 'training_checkout_actor_denied' USING ERRCODE='42501';
 END IF;
 SELECT * INTO s FROM public.sale WHERE tenant_id=NEW.tenant_id AND id=NEW.sale_id
  FOR UPDATE;
 IF s.id IS NULL OR s.status<>'draft' OR NOT s.is_test OR s.sale_type<>'sale'
   OR s.parent_sale_id IS NOT NULL OR s.refund_attempt_id IS NOT NULL
   OR s.currency<>'TJS' OR s.receipt_number IS NOT NULL OR s.receipt_seq IS NOT NULL
   OR s.receipt_snapshot IS NOT NULL OR s.completed_at IS NOT NULL
   OR (s.branch_id,s.register_id,s.shift_id,s.cashier_user_id) IS DISTINCT FROM
     (NEW.branch_id,NEW.register_id,NEW.shift_id,actor)
   OR public.unit_payment_binding_active(NEW.tenant_id,NEW.sale_id) THEN
  RAISE EXCEPTION 'training_checkout_source_invalid' USING ERRCODE='23514';
 END IF;
 SELECT * INTO work_shift FROM public.shift
  WHERE tenant_id=NEW.tenant_id AND id=NEW.shift_id FOR UPDATE;
 IF work_shift.id IS NULL OR work_shift.status<>'open'
   OR (work_shift.branch_id,work_shift.register_id,work_shift.opened_by_user_id,
     work_shift.currency) IS DISTINCT FROM (NEW.branch_id,NEW.register_id,actor,'TJS')
   OR NOT EXISTS(SELECT 1 FROM public.register WHERE tenant_id=NEW.tenant_id
     AND id=NEW.register_id AND branch_id=NEW.branch_id AND is_active) THEN
  RAISE EXCEPTION 'training_checkout_shift_invalid' USING ERRCODE='23514';
 END IF;
 PERFORM public.assert_training_checkout_document(NEW);
 completed:=(NEW.result_payload->>'completed_at')::TIMESTAMPTZ;
 IF NOT isfinite(completed) OR completed<s.created_at OR completed>clock_timestamp() THEN
  RAISE EXCEPTION 'training_checkout_completed_at_invalid' USING ERRCODE='23514';
 END IF;
 NEW.created_at:=statement_timestamp(); NEW.created_by:=actor;
 RETURN NEW;
END $$;

CREATE FUNCTION public.training_checkout_sale_write_allowed(
 p_table TEXT,p_action TEXT,p_new JSONB,p_old JSONB) RETURNS BOOLEAN
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE m public.pos_training_checkout%ROWTYPE;
BEGIN
 IF p_table<>'sale' OR p_action<>'UPDATE' OR session_user<>'aurum_app'
   OR public.is_support_session() OR p_old->>'status' IS DISTINCT FROM 'draft'
   OR p_new->>'status' IS DISTINCT FROM 'completed'
   OR p_old->'is_test' IS DISTINCT FROM 'true'::JSONB
   OR p_new->'is_test' IS DISTINCT FROM 'true'::JSONB
   OR p_new->>'tenant_id' IS DISTINCT FROM public.current_tenant_id()::TEXT THEN
  RETURN false;
 END IF;
 SELECT * INTO m FROM public.pos_training_checkout
  WHERE tenant_id=(p_new->>'tenant_id')::UUID AND sale_id=(p_new->>'id')::UUID;
 IF m.id IS NULL OR m.cashier_user_id IS DISTINCT FROM public.current_app_user_id()
   OR NOT coalesce(public.tenant_actor_has_scoped_permission(
     m.tenant_id,'pos.sell',m.branch_id),false)
   OR NOT EXISTS(SELECT 1 FROM public.app_user WHERE id=m.cashier_user_id AND status='active')
   OR (p_new-ARRAY['status','total_amount',
     'completed_at','receipt_snapshot','receipt_number','receipt_seq','updated_at','updated_by'])
     IS DISTINCT FROM
     (p_old-ARRAY['status','total_amount',
       'completed_at','receipt_snapshot','receipt_number','receipt_seq',
       'updated_at','updated_by'])
   OR p_new->>'receipt_number' IS DISTINCT FROM m.receipt_number
   OR (p_new->>'total_amount')::NUMERIC IS DISTINCT FROM m.total_amount
   OR (p_new->>'receipt_seq')::BIGINT IS DISTINCT FROM m.receipt_seq
   OR p_new->'receipt_snapshot' IS DISTINCT FROM m.receipt_payload
   OR (p_new->>'completed_at')::TIMESTAMPTZ IS DISTINCT FROM
     (m.result_payload->>'completed_at')::TIMESTAMPTZ THEN RETURN false; END IF;
 PERFORM public.assert_training_checkout(m.tenant_id,m.sale_id,false);
 RETURN true;
END $$;

CREATE FUNCTION public.trg_training_checkout_complete() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE target UUID;
BEGIN
 IF TG_TABLE_NAME='sale' THEN target:=NEW.id;
 ELSIF TG_TABLE_NAME='batch_movement' THEN
  IF NEW.source_table='sale' THEN target:=NEW.source_id; END IF;
 ELSIF TG_TABLE_NAME='unit_stock_cart_command_v2' THEN
  target:=coalesce(NEW.checkout_sale_id,NEW.mixed_checkout_sale_id);
 ELSE target:=NEW.sale_id; END IF;
 IF target IS NOT NULL AND EXISTS(SELECT 1 FROM public.pos_training_checkout
   WHERE tenant_id=NEW.tenant_id AND sale_id=target) THEN
  PERFORM public.assert_training_checkout(NEW.tenant_id,target,true);
 END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION public.trg_training_checkout_return_boundary() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 IF session_user='aurum_app'
   AND NEW.tenant_id IS DISTINCT FROM public.current_tenant_id() THEN
  RAISE EXCEPTION 'training_checkout_context_denied' USING ERRCODE='42501';
 END IF;
 IF NEW.parent_sale_id IS NOT NULL AND EXISTS(SELECT 1 FROM public.pos_training_checkout
   WHERE tenant_id=NEW.tenant_id AND sale_id=NEW.parent_sale_id) THEN
  RAISE EXCEPTION 'training_checkout_refund_not_supported' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
"""

ALLOW = """ IF coalesce(public.training_checkout_sale_write_allowed(
  TG_TABLE_NAME,TG_OP,CASE WHEN TG_OP='DELETE' THEN NULL ELSE to_jsonb(NEW) END,
  CASE WHEN TG_OP='INSERT' THEN NULL ELSE to_jsonb(OLD) END),false) THEN RETURN NEW; END IF;
"""
AUDIT_ANCHOR = "IF v_result IS NULL THEN RETURN NULL; END IF;"
PATCHES = [
    (signature, "BEGIN\n", "BEGIN\n" + ALLOW)
    for signature in ("trg_guard_sale_immutability()", "trg_unit_cart_payment_boundary()")
] + [
    (
        "audit_redact_table_snapshot(text,jsonb)",
        AUDIT_ANCHOR,
        AUDIT_ANCHOR + """
      IF p_table_name='pos_training_checkout' THEN
        RETURN jsonb_build_object('created_at',v_result->'created_at',
          'created_by',v_result->'created_by');
      END IF;""",
    )
]


def upgrade() -> None:
    _sql("""
      CREATE TEMP TABLE training_checkout_reference_grants AS
       SELECT oid::regclass::TEXT AS relation,
        has_table_privilege('aurum_schema_owner',oid,'REFERENCES') AS granted
       FROM pg_class WHERE oid IN ('public.sale'::regclass,'public.app_user'::regclass);
      GRANT REFERENCES ON public.sale,public.app_user TO aurum_schema_owner;
      CREATE TABLE public.pos_training_checkout (
       tenant_id UUID NOT NULL,id UUID NOT NULL,
       sale_id UUID NOT NULL,branch_id UUID NOT NULL,register_id UUID NOT NULL,
       shift_id UUID NOT NULL,cashier_user_id UUID NOT NULL REFERENCES public.app_user(id),
       request_hash TEXT NOT NULL CHECK(request_hash~'^[0-9a-f]{64}$'),
       quote_revision TEXT NOT NULL CHECK(quote_revision~'^[0-9a-f]{64}$'),
       total_amount NUMERIC(14,2) NOT NULL CHECK(total_amount>0),
       receipt_number TEXT NOT NULL CHECK(length(btrim(receipt_number)) BETWEEN 1 AND 100
         AND receipt_number LIKE 'TEST-%' AND receipt_number !~ '[[:cntrl:]]'),
       receipt_seq BIGINT NOT NULL CHECK(receipt_seq>0),
       result_payload JSONB NOT NULL CHECK(jsonb_typeof(result_payload)='object'),
       receipt_payload JSONB NOT NULL CHECK(jsonb_typeof(receipt_payload)='object'),
       created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
       created_by UUID NOT NULL REFERENCES public.app_user(id),
       PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,sale_id),
       CHECK(substr(id::TEXT,15,1)='4' AND substr(id::TEXT,20,1) IN ('8','9','a','b')),
       CHECK(created_by=cashier_user_id),
       FOREIGN KEY(tenant_id,sale_id) REFERENCES public.sale(tenant_id,id));
      DO $$ DECLARE r RECORD; BEGIN
       FOR r IN SELECT * FROM pg_temp.training_checkout_reference_grants WHERE NOT granted LOOP
        EXECUTE format('REVOKE REFERENCES ON %s FROM aurum_schema_owner',r.relation);
       END LOOP;
      END $$;
      DROP TABLE pg_temp.training_checkout_reference_grants;
      ALTER TABLE public.pos_training_checkout OWNER TO aurum_schema_owner;
      ALTER TABLE public.pos_training_checkout ENABLE ROW LEVEL SECURITY;
      ALTER TABLE public.pos_training_checkout FORCE ROW LEVEL SECURITY;
      REVOKE ALL ON public.pos_training_checkout FROM PUBLIC,aurum_app,aurum_support;
      GRANT SELECT,INSERT ON public.pos_training_checkout TO aurum_app;
      GRANT SELECT ON public.pos_training_checkout TO aurum_support;
      CREATE POLICY schema_owner ON public.pos_training_checkout TO aurum_schema_owner
       USING(true) WITH CHECK(true);
      CREATE POLICY support ON public.pos_training_checkout FOR SELECT TO aurum_support
       USING(true);
      CREATE POLICY tenant_actor ON public.pos_training_checkout TO aurum_app
       USING(session_user='aurum_app' AND NOT public.is_support_session()
        AND tenant_id=public.current_tenant_id()
        AND cashier_user_id=public.current_app_user_id()
        AND EXISTS(SELECT 1 FROM public.app_user
          WHERE id=public.current_app_user_id() AND status='active')
        AND public.tenant_actor_has_scoped_permission(tenant_id,'pos.sell',branch_id))
       WITH CHECK(tenant_id=public.current_tenant_id()
        AND cashier_user_id=public.current_app_user_id()
        AND public.tenant_actor_has_scoped_permission(tenant_id,'pos.sell',branch_id));
    """)
    _sql(SQL)
    for signature in FUNCTIONS:
        _sql(f"""
         ALTER FUNCTION public.{signature} OWNER TO aurum_schema_owner;
         REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC,aurum_app,aurum_support;
        """)
    # The old sale immutability trigger is SECURITY INVOKER. Its existing
    # app/support/Edge writers must evaluate this read-only predicate; other
    # helpers stay private, and the predicate rejects non-app sessions early.
    _sql("""
      GRANT EXECUTE ON FUNCTION public.training_checkout_sale_write_allowed(text,text,jsonb,jsonb)
       TO aurum_app,aurum_support,aurum_edge_cash_owner;
    """)
    for signature, old, new in PATCHES:
        _patch(signature, old, new, upgrade=True)
    _sql("""
      CREATE TRIGGER a00_tenant_access BEFORE INSERT OR UPDATE OR DELETE
       ON public.pos_training_checkout FOR EACH ROW
       EXECUTE FUNCTION public.trg_enforce_tenant_access('pos');
      CREATE TRIGGER a10_training_checkout_guard BEFORE INSERT OR UPDATE OR DELETE
       ON public.pos_training_checkout FOR EACH ROW
       EXECUTE FUNCTION public.trg_training_checkout_guard();
      CREATE TRIGGER z90_training_checkout_audit AFTER INSERT OR UPDATE OR DELETE
       ON public.pos_training_checkout FOR EACH ROW EXECUTE FUNCTION public.trg_audit_log();
      CREATE TRIGGER a02_training_checkout_return_boundary BEFORE INSERT OR UPDATE
       ON public.sale FOR EACH ROW
       EXECUTE FUNCTION public.trg_training_checkout_return_boundary();
    """)
    for table in COMPLETENESS_TABLES:
        _sql(f"""
         CREATE CONSTRAINT TRIGGER training_checkout_complete AFTER INSERT OR UPDATE
          ON public.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION public.trg_training_checkout_complete();
        """)


def downgrade() -> None:
    _sql("""
      LOCK TABLE public.pos_training_checkout IN ACCESS EXCLUSIVE MODE;
      DO $$ BEGIN
       IF EXISTS(SELECT 1 FROM public.pos_training_checkout) THEN
        RAISE EXCEPTION 'training_checkout_downgrade_requires_empty_history'
         USING ERRCODE='55000';
       END IF;
      END $$;
    """)
    for signature, old, new in reversed(PATCHES):
        _patch(signature, old, new, upgrade=False)
    for table in reversed(COMPLETENESS_TABLES):
        _sql(f"DROP TRIGGER training_checkout_complete ON public.{table};")
    _sql("DROP TRIGGER a10_training_checkout_guard ON public.pos_training_checkout;")
    _sql("DROP TRIGGER a02_training_checkout_return_boundary ON public.sale;")
    for signature in reversed(FUNCTIONS):
        _sql(f"DROP FUNCTION public.{signature};")
    _sql("DROP TABLE public.pos_training_checkout;")
