"""Atomically post an evidenced mixed electronic refund.

Revision ID: 0223
Revises: 0222
"""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0223"
down_revision = "0222"
branch_labels = None
depends_on = None
TABLE = "pos_mixed_electronic_refund_posting"
FUNCTIONS = (
    "mixed_electronic_refund_posting_write_allowed(text,text,jsonb,jsonb)",
    "assert_mixed_electronic_refund_posting(uuid,uuid)",
    "trg_mixed_electronic_refund_posting_guard()",
    "trg_mixed_electronic_refund_posting_complete()",
)


def _sql(sql: str) -> None:
    spec = spec_from_file_location(
        "aurum_0223_sql", Path(__file__).with_name("0171_unit_cash_intents.py")
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load historical SQL executor")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    module._sql(sql)


def _patch(signature: str, old: str, new: str, *, upgrade: bool) -> None:
    definition = op.get_bind().scalar(
        text("SELECT pg_get_functiondef(CAST(:signature AS regprocedure))"),
        {"signature": "public." + signature},
    )
    before, after = (old, new) if upgrade else (new, old)
    if not isinstance(definition, str) or definition.count(before) != 1:
        raise RuntimeError(f"Electronic refund historical guard changed: {signature}")
    op.get_bind().exec_driver_sql(definition.replace(before, after, 1))


SQL = """
CREATE FUNCTION public.mixed_electronic_refund_posting_write_allowed(
 p_table TEXT,p_op TEXT,p_new JSONB,p_old JSONB) RETURNS BOOLEAN
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE m public.pos_mixed_electronic_refund_posting%ROWTYPE; v_sale UUID;
BEGIN
 IF p_new IS NULL OR p_op='DELETE' OR session_user<>'aurum_app'
   OR public.is_support_session() OR current_setting('transaction_isolation')<>'read committed'
   OR (p_new->>'tenant_id')::UUID IS DISTINCT FROM public.current_tenant_id() THEN
  RETURN false;
 END IF;
 SELECT * INTO m FROM public.pos_mixed_electronic_refund_posting
  WHERE tenant_id=(p_new->>'tenant_id')::UUID AND CASE
   WHEN p_table='pos_mixed_return_command' THEN id=(p_new->>'id')::UUID
   WHEN p_table='sale' THEN sale_id=(p_new->>'id')::UUID
   ELSE sale_id=(p_new->>'sale_id')::UUID END;
 IF m.id IS NULL OR m.actor_id IS DISTINCT FROM public.current_app_user_id()
  OR NOT EXISTS(SELECT 1 FROM public.app_user WHERE id=m.actor_id AND status='active')
  OR NOT coalesce(public.tenant_actor_has_scoped_permission(
    m.tenant_id,'pos.refund',m.branch_id),false) THEN RETURN false; END IF;
 IF p_table='pos_mixed_return_command' THEN
  RETURN p_op='INSERT' AND (p_new->>'parent_sale_id')::UUID=m.parent_sale_id
    AND (p_new->>'actor_id')::UUID=m.actor_id AND (p_new->>'branch_id')::UUID=m.branch_id;
 ELSIF p_table='sale' THEN
  RETURN p_op IN ('INSERT','UPDATE') AND p_new->>'sale_type'='return'
    AND (p_new->>'parent_sale_id')::UUID=m.parent_sale_id
    AND (p_new->>'branch_id')::UUID=m.branch_id
    AND (p_new->>'register_id')::UUID=m.register_id
    AND (p_new->>'shift_id')::UUID=m.shift_id
    AND (p_new->>'cashier_user_id')::UUID=m.actor_id
    AND (p_new->>'operation_id')::UUID=m.id
    AND p_new->>'currency'='TJS' AND p_new->>'is_test'='false'
    AND p_new->>'refund_attempt_id' IS NULL
    AND p_new->>'fiscal_data' IS NULL AND p_new->>'marking_codes' IS NULL
    AND p_new->>'voided_at' IS NULL AND p_new->>'voided_by_sale_id' IS NULL
    AND ((p_op='INSERT' AND p_new->>'status'='draft') OR
      (p_op='UPDATE' AND p_old->>'status'='draft' AND p_new->>'status'='completed'
       AND (p_new-ARRAY['status','completed_at','receipt_snapshot','receipt_number','receipt_seq',
         'updated_at','updated_by'])=(p_old-ARRAY['status','completed_at','receipt_snapshot',
         'receipt_number','receipt_seq','updated_at','updated_by'])));
 ELSIF p_table='sale_payment' THEN
  RETURN p_op='INSERT' AND p_new->>'payment_attempt_id' IS NULL
    AND p_new->>'operation_id' IS NULL AND p_new->>'operation_hash' IS NULL
    AND p_new->>'currency'='TJS' AND EXISTS(
     SELECT 1 FROM public.pos_mixed_electronic_refund_intent i,
       jsonb_array_elements(i.result_payload->'payments') part
     WHERE i.tenant_id=m.tenant_id AND i.id=m.id
       AND part->>'method'=p_new->>'payment_method'
       AND (part->>'amount')::NUMERIC=(p_new->>'amount')::NUMERIC);
 END IF;
 RETURN false;
END $$;
CREATE FUNCTION public.trg_mixed_electronic_refund_posting_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE a public.pos_mixed_electronic_refund_activation%ROWTYPE;
 i public.pos_mixed_electronic_refund_intent%ROWTYPE; h public.shift%ROWTYPE;
 p public.sale%ROWTYPE; reason_mode TEXT;
BEGIN
 IF TG_OP<>'INSERT' THEN
  RAISE EXCEPTION 'mixed_electronic_refund_posting_immutable' USING ERRCODE='55000';
 END IF;
 IF current_setting('transaction_isolation')<>'read committed' THEN
  RAISE EXCEPTION 'mixed_electronic_refund_posting_isolation_required' USING ERRCODE='23514';
 END IF;
 IF session_user<>'aurum_app' OR public.is_support_session()
  OR NEW.tenant_id IS DISTINCT FROM public.current_tenant_id()
  OR NEW.actor_id IS DISTINCT FROM public.current_app_user_id() THEN
  RAISE EXCEPTION 'mixed_electronic_refund_posting_actor_denied' USING ERRCODE='42501';
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(
  'mixed-electronic-refund-review:'||NEW.tenant_id||':'||NEW.id,0));
 PERFORM pg_advisory_xact_lock(hashtextextended(
  'aurum-mixed-return:'||NEW.tenant_id||':'||NEW.id,0));
 SELECT * INTO p FROM public.sale
  WHERE tenant_id=NEW.tenant_id AND id=NEW.parent_sale_id FOR UPDATE;
 SELECT * INTO a FROM public.pos_mixed_electronic_refund_activation
  WHERE tenant_id=NEW.tenant_id AND id=NEW.id;
 SELECT * INTO i FROM public.pos_mixed_electronic_refund_intent
  WHERE tenant_id=NEW.tenant_id AND id=NEW.id;
 SELECT * INTO h FROM public.shift
  WHERE tenant_id=NEW.tenant_id AND id=NEW.shift_id FOR UPDATE;
 PERFORM 1 FROM public.register WHERE tenant_id=NEW.tenant_id
  AND branch_id=NEW.branch_id AND id=NEW.register_id AND is_active FOR SHARE;
 IF NOT FOUND OR p.id IS NULL OR a.id IS NULL OR i.id IS NULL OR h.id IS NULL
  OR (a.parent_sale_id,a.branch_id,a.register_id,a.actor_id) IS DISTINCT FROM
    (NEW.parent_sale_id,NEW.branch_id,NEW.register_id,NEW.actor_id)
  OR (p.status,p.sale_type,p.currency,p.is_test,p.branch_id,p.register_id) IS DISTINCT FROM
    ('completed','sale','TJS',false,NEW.branch_id,NEW.register_id)
  OR p.parent_sale_id IS NOT NULL OR NOT EXISTS(
    SELECT 1 FROM public.pos_mixed_checkout checkout WHERE checkout.tenant_id=NEW.tenant_id
      AND checkout.sale_id=NEW.parent_sale_id AND checkout.payment_journal_version=3)
  OR (h.status,h.branch_id,h.register_id,h.opened_by_user_id,h.currency) IS DISTINCT FROM
    ('open',NEW.branch_id,NEW.register_id,NEW.actor_id,'TJS') OR h.closed_at IS NOT NULL
  OR NOT EXISTS(SELECT 1 FROM public.pos_mixed_electronic_refund_binding b
    WHERE b.tenant_id=NEW.tenant_id AND b.id=NEW.id)
  OR EXISTS(SELECT 1 FROM public.pos_mixed_electronic_refund_binding_cancel c
    WHERE c.tenant_id=NEW.tenant_id AND c.id=NEW.id)
  OR EXISTS(SELECT 1 FROM jsonb_array_elements(i.result_payload->'payments') part
    WHERE part->>'method'<>'cash' AND NOT EXISTS(
     SELECT 1 FROM public.pos_mixed_electronic_refund_outcome o
     WHERE o.tenant_id=NEW.tenant_id AND o.operation_id=NEW.id
       AND o.original_payment_id=(part->>'original_payment_id')::UUID
       AND o.method=part->>'method' AND o.amount=(part->>'amount')::NUMERIC)) THEN
  RAISE EXCEPTION 'mixed_electronic_refund_posting_source_invalid' USING ERRCODE='23514';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM public.app_user WHERE id=NEW.actor_id AND status='active')
  OR NOT coalesce(public.tenant_actor_has_scoped_permission(
    NEW.tenant_id,'pos.refund',NEW.branch_id),false) THEN
  RAISE EXCEPTION 'mixed_electronic_refund_posting_actor_denied' USING ERRCODE='42501';
 END IF;
 SELECT refund_reason_mode INTO reason_mode FROM public.tenant_settings
  WHERE tenant_id=NEW.tenant_id FOR SHARE;
 IF reason_mode IS NULL OR reason_mode NOT IN ('off','optional','required','required_with_text')
  OR (NEW.reason_code IS NOT NULL AND NEW.reason_code NOT IN (
    'dispensing_error','duplicate_sale','pricing_error','quality_issue',
    'damaged_package','customer_cancelled','other'))
  OR (NEW.comment IS NOT NULL AND (length(NEW.comment) NOT BETWEEN 1 AND 500
    OR NEW.comment IS DISTINCT FROM btrim(NEW.comment,E' \t\r\n')))
  OR (reason_mode='off' AND (NEW.reason_code IS NOT NULL OR NEW.comment IS NOT NULL))
  OR (reason_mode IN ('required','required_with_text') AND NEW.reason_code IS NULL)
  OR (reason_mode='required_with_text' AND NEW.comment IS NULL) THEN
  RAISE EXCEPTION 'mixed_electronic_refund_posting_reason_invalid' USING ERRCODE='23514';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM public.app_user WHERE id=NEW.actor_id AND status='active')
   OR NOT coalesce(public.tenant_actor_has_scoped_permission(
     NEW.tenant_id,'pos.refund',NEW.branch_id),false) THEN
  RAISE EXCEPTION 'mixed_electronic_refund_posting_actor_denied' USING ERRCODE='42501';
 END IF;
 NEW.created_by:=NEW.actor_id; NEW.created_at:=greatest(clock_timestamp(),a.created_at);
 RETURN NEW;
END $$;
CREATE FUNCTION public.assert_mixed_electronic_refund_posting(p_tenant UUID,p_sale UUID)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE m public.pos_mixed_electronic_refund_posting%ROWTYPE; s public.sale%ROWTYPE;
 c public.pos_mixed_return_command%ROWTYPE; i public.pos_mixed_electronic_refund_intent%ROWTYPE;
 original public.pos_mixed_checkout%ROWTYPE; r RECORD; part JSONB; source JSONB;
 expected_lines JSONB:='[]'; expected_payments JSONB:='[]'; expected JSONB;
 line_position INTEGER:=0; quote JSONB;
 refunded NUMERIC; expected_amount NUMERIC; component RECORD; payout public.sale_payment%ROWTYPE;
 evidence public.pos_mixed_electronic_refund_outcome%ROWTYPE;
BEGIN
 IF current_setting('transaction_isolation')<>'read committed' THEN
  RAISE EXCEPTION 'mixed_electronic_refund_posting_isolation_required' USING ERRCODE='23514';
 END IF;
 SELECT * INTO m FROM public.pos_mixed_electronic_refund_posting
  WHERE tenant_id=p_tenant AND sale_id=p_sale;
 SELECT * INTO s FROM public.sale WHERE tenant_id=p_tenant AND id=p_sale;
 SELECT * INTO i FROM public.pos_mixed_electronic_refund_intent
  WHERE tenant_id=p_tenant AND id=m.id;
 SELECT * INTO c FROM public.pos_mixed_return_command WHERE tenant_id=p_tenant AND id=m.id;
 SELECT * INTO original FROM public.pos_mixed_checkout
  WHERE tenant_id=p_tenant AND sale_id=m.parent_sale_id;
 PERFORM 1 FROM public.sale WHERE tenant_id=p_tenant AND id=m.parent_sale_id FOR UPDATE;
 IF session_user<>'aurum_app' OR public.is_support_session()
  OR m.tenant_id IS DISTINCT FROM public.current_tenant_id()
  OR m.actor_id IS DISTINCT FROM public.current_app_user_id()
  OR NOT EXISTS(SELECT 1 FROM public.app_user WHERE id=m.actor_id AND status='active')
  OR NOT coalesce(public.tenant_actor_has_scoped_permission(
    m.tenant_id,'pos.refund',m.branch_id),false) THEN
  RAISE EXCEPTION 'mixed_electronic_refund_posting_actor_denied' USING ERRCODE='42501';
 END IF;
 IF m.id IS NULL OR s.id IS NULL OR i.id IS NULL OR c.id IS NULL OR original.id IS NULL
  OR (s.status,s.sale_type,s.parent_sale_id,s.branch_id,s.register_id,s.shift_id,
      s.cashier_user_id,s.currency,s.is_test,s.operation_id,s.total_amount)
    IS DISTINCT FROM ('completed','return',m.parent_sale_id,m.branch_id,m.register_id,
      m.shift_id,m.actor_id,'TJS',false,m.id,c.total_amount)
  OR (c.parent_sale_id,c.branch_id,c.actor_id,c.state) IS DISTINCT FROM
    (m.parent_sale_id,m.branch_id,m.actor_id,'prepared_quarantine')
  OR s.operation_hash IS DISTINCT FROM i.request_payload->>'expected_revision_hash'
  OR s.refund_attempt_id IS NOT NULL OR s.fiscal_data IS NOT NULL
  OR s.marking_codes IS NOT NULL OR s.voided_at IS NOT NULL OR s.voided_by_sale_id IS NOT NULL
  OR EXISTS(SELECT 1 FROM public.pos_unit_cart_item WHERE sale_id=p_sale)
  OR EXISTS(SELECT 1 FROM public.prescription_log WHERE sale_id=p_sale)
  OR EXISTS(SELECT 1 FROM public.batch_movement WHERE source_table='sale' AND source_id=p_sale)
  OR EXISTS(SELECT 1 FROM public.unit_stock_cart_command_v2
    WHERE tenant_id=p_tenant AND (id=m.id OR checkout_sale_id=p_sale
      OR mixed_checkout_sale_id=p_sale))
  OR EXISTS(SELECT 1 FROM public.sale_item WHERE sale_id=p_sale)
  OR EXISTS(SELECT 1 FROM public.pos_payment_attempt WHERE sale_id=p_sale)
  OR c.total_amount IS DISTINCT FROM (i.result_payload->>'total_amount')::NUMERIC
  OR (SELECT count(*) FROM public.sale_payment WHERE sale_id=p_sale)
    <>jsonb_array_length(i.result_payload->'payments')
  OR (SELECT count(*) FROM public.pos_unit_refund_payment_component
      WHERE tenant_id=p_tenant AND sale_id=p_sale)
    <>jsonb_array_length(i.result_payload->'payments') THEN
  RAISE EXCEPTION 'mixed_electronic_refund_posting_incomplete' USING ERRCODE='23514';
 END IF;
 FOR part IN SELECT value FROM jsonb_array_elements(i.result_payload->'payments')
   ORDER BY value->>'original_payment_id' LOOP
  SELECT * INTO component FROM public.pos_unit_refund_payment_component
   WHERE tenant_id=p_tenant AND mixed_electronic_refund_id=m.id
     AND original_payment_id=(part->>'original_payment_id')::UUID;
  SELECT * INTO payout FROM public.sale_payment
   WHERE tenant_id=p_tenant AND id=component.id AND sale_id=p_sale;
  SELECT * INTO evidence FROM public.pos_mixed_electronic_refund_outcome
   WHERE tenant_id=p_tenant AND operation_id=m.id
     AND original_payment_id=(part->>'original_payment_id')::UUID;
  expected_amount:=(part->>'amount')::NUMERIC;
  IF component.id IS NULL OR payout.id IS NULL OR
    (component.parent_sale_id,component.sale_id,component.method,component.amount,
     component.currency,component.created_by) IS DISTINCT FROM
      (m.parent_sale_id,p_sale,part->>'method',expected_amount,'TJS',m.actor_id)
    OR component.physical_refund_id IS NOT NULL OR component.mixed_refund_id IS NOT NULL
    OR component.electronic_refund_id IS NOT NULL
    OR (payout.payment_method,payout.amount,payout.currency) IS DISTINCT FROM
      (part->>'method',expected_amount,'TJS') OR payout.payment_attempt_id IS NOT NULL
    OR payout.operation_id IS NOT NULL OR payout.operation_hash IS NOT NULL
    OR NOT EXISTS(SELECT 1 FROM public.sale_payment p
       WHERE p.tenant_id=p_tenant AND p.sale_id=m.parent_sale_id
         AND p.id=component.original_payment_id AND p.payment_method=component.method
         AND p.currency='TJS' AND p.amount>=expected_amount)
    OR (CASE WHEN part->>'method'='cash' THEN payout.metadata IS NOT NULL ELSE
      evidence.id IS NULL OR payout.metadata IS DISTINCT FROM jsonb_build_object(
        'terminal_id',evidence.terminal_id,'document_number',evidence.document_number)
      OR (evidence.method,evidence.amount,evidence.currency) IS DISTINCT FROM
        (component.method,expected_amount,'TJS') END) THEN
   RAISE EXCEPTION 'mixed_electronic_refund_posting_payment_invalid' USING ERRCODE='23514';
  END IF;
  SELECT coalesce(sum(x.amount),0) INTO refunded
   FROM public.pos_unit_refund_payment_component x JOIN public.sale refunded_sale
    ON refunded_sale.tenant_id=x.tenant_id AND refunded_sale.id=x.sale_id
   WHERE x.tenant_id=p_tenant AND x.original_payment_id=component.original_payment_id
     AND refunded_sale.status='completed';
  IF refunded>(SELECT p.amount FROM public.sale_payment p
    WHERE p.tenant_id=p_tenant AND p.id=component.original_payment_id) THEN
   RAISE EXCEPTION 'mixed_electronic_refund_posting_amount_exceeded' USING ERRCODE='23514';
  END IF;
  expected_payments:=expected_payments||jsonb_build_array(jsonb_build_object(
   'id',component.id,'original_payment_id',component.original_payment_id,
   'method',component.method,'amount',to_char(expected_amount,'FM999999999990.00'),'currency','TJS',
   'terminal_id',CASE WHEN component.method='cash' THEN NULL ELSE evidence.terminal_id END,
   'document_number',CASE WHEN component.method='cash' THEN NULL
     ELSE evidence.document_number END));
 END LOOP;
 FOR r IN SELECT * FROM public.pos_mixed_return_line
  WHERE tenant_id=p_tenant AND command_id=m.id ORDER BY position LOOP
  SELECT value INTO quote FROM jsonb_array_elements(i.result_payload->'lines')
    WHERE value->>'line_id'=r.line_id::TEXT;
  IF quote IS NULL OR quote->>'inventory_kind' IS DISTINCT FROM r.inventory_kind
    OR (quote->>'quantity')::NUMERIC IS DISTINCT FROM
      (CASE WHEN r.inventory_kind='legacy_pack' THEN r.legacy_quantity ELSE r.quantity END)
    OR (quote->>'offset')::NUMERIC IS DISTINCT FROM
      (CASE WHEN r.inventory_kind='legacy_pack' THEN r.legacy_offset ELSE r."offset" END)
    OR (quote->>'net_amount')::NUMERIC IS DISTINCT FROM r.net_amount
    OR (quote->>'gross_amount')::NUMERIC IS DISTINCT FROM r.gross_amount
    OR (quote->>'discount_amount')::NUMERIC IS DISTINCT FROM r.discount_amount
    OR (r.inventory_kind='physical_unit' AND
      ((quote->>'sale_format') IS DISTINCT FROM r.sale_format
        OR (quote->>'base_units')::INTEGER IS DISTINCT FROM r.base_units)) THEN
   RAISE EXCEPTION 'mixed_electronic_refund_posting_selection_invalid' USING ERRCODE='23514';
  END IF;
  SELECT value INTO source FROM jsonb_array_elements(original.result_payload->'lines')
   WHERE value->>'id'=r.line_id::TEXT;
  IF source IS NULL OR source->>'inventory_kind' IS DISTINCT FROM r.inventory_kind THEN
   RAISE EXCEPTION 'mixed_electronic_refund_posting_source_invalid' USING ERRCODE='23514';
  END IF;
  line_position:=line_position+1;
  expected_lines:=expected_lines||jsonb_build_array(
    jsonb_build_object(
      'inventory_kind',r.inventory_kind,'original_line_id',r.line_id,
      'position',line_position,'original_position',r.position,'name',source->'name',
      'original_retail_unit_price',source->'original_retail_unit_price',
      'quantity',CASE WHEN r.inventory_kind='legacy_pack'
        THEN to_jsonb(to_char(r.legacy_quantity,'FM999999999990.000'))
        ELSE to_jsonb(r.quantity::INTEGER) END,
      'offset',CASE WHEN r.inventory_kind='legacy_pack'
        THEN to_jsonb(to_char(r.legacy_offset,'FM999999999990.000'))
        ELSE to_jsonb(r."offset"::INTEGER) END,
      'gross_amount',to_char(r.gross_amount,'FM999999999990.00'),
      'discount_amount',to_char(r.discount_amount,'FM999999999990.00'),
      'net_amount',to_char(r.net_amount,'FM999999999990.00'))
    ||CASE WHEN r.inventory_kind='physical_unit' THEN jsonb_build_object(
      'packaging',source->'packaging','original_sale_format',source->'sale_format',
      'sale_format',r.sale_format,'base_units',r.base_units) ELSE '{}'::JSONB END);
 END LOOP;
 expected:=jsonb_build_object('schema_version',4,
  'document_kind','mixed_electronic_refund_receipt','parent_sale_id',m.parent_sale_id,
  'parent_receipt_number',original.result_payload->'receipt_number',
  'receipt_number',s.receipt_number,'receipt_sequence',s.receipt_seq,
  'completed_at',s.receipt_snapshot->'completed_at','is_test',false,
  'lines',expected_lines,'payments',expected_payments,
  'total_amount',to_char(c.total_amount,'FM999999999990.00'),'currency','TJS');
 IF s.receipt_snapshot IS DISTINCT FROM expected OR s.completed_at IS NULL
   OR s.completed_at IS DISTINCT FROM (s.receipt_snapshot->>'completed_at')::TIMESTAMPTZ
   OR NOT isfinite(s.completed_at) OR s.completed_at<m.created_at
   OR s.completed_at>clock_timestamp() OR s.receipt_seq IS NULL OR s.receipt_seq<=0
   OR s.receipt_number IS NULL OR length(btrim(s.receipt_number)) NOT BETWEEN 1 AND 100
   OR s.receipt_number ~ '[[:cntrl:]]'
   OR jsonb_array_length(expected_lines) NOT BETWEEN 1 AND 200
   OR jsonb_array_length(expected_lines)<>jsonb_array_length(i.result_payload->'lines') THEN
  RAISE EXCEPTION 'mixed_electronic_refund_posting_receipt_invalid' USING ERRCODE='23514';
 END IF;
END $$;
CREATE FUNCTION public.trg_mixed_electronic_refund_posting_complete() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE target_sale UUID;
BEGIN
 IF TG_TABLE_NAME='sale' THEN
  target_sale:=NEW.id;
 ELSE
  target_sale:=NEW.sale_id;
 END IF;
 IF EXISTS(SELECT 1 FROM public.pos_mixed_electronic_refund_posting
   WHERE tenant_id=NEW.tenant_id AND sale_id=target_sale) THEN
  PERFORM public.assert_mixed_electronic_refund_posting(NEW.tenant_id,target_sale);
 END IF;
 RETURN NEW;
END $$;
"""

ALLOW = """ IF coalesce(public.mixed_electronic_refund_posting_write_allowed(
  TG_TABLE_NAME,TG_OP,CASE WHEN TG_OP='DELETE' THEN NULL ELSE to_jsonb(NEW) END,
  CASE WHEN TG_OP='INSERT' THEN NULL ELSE to_jsonb(OLD) END),false) THEN RETURN NEW; END IF;
"""
PATCHES = [
    (signature, "BEGIN\n", "BEGIN\n" + ALLOW)
    for signature in (
        "trg_mixed_electronic_refund_binding_fence()",
        "trg_unit_payment_binding_fence()",
        "trg_unit_cart_payment_boundary()",
        "trg_mixed_checkout_return_boundary()",
    )
]
SALE_ALLOW = """ IF TG_OP='UPDATE' AND session_user='aurum_app'
  AND NOT public.is_support_session() AND OLD.status='draft' AND NEW.status='completed'
  AND NEW.sale_type='return' AND NEW.currency='TJS' AND NOT NEW.is_test
  AND NEW.refund_attempt_id IS NULL AND NEW.tenant_id=public.current_tenant_id()
  AND (to_jsonb(NEW)-ARRAY['status','completed_at','receipt_snapshot','receipt_number',
    'receipt_seq','updated_at','updated_by'])=
   (to_jsonb(OLD)-ARRAY['status','completed_at','receipt_snapshot','receipt_number',
    'receipt_seq','updated_at','updated_by'])
  AND EXISTS(SELECT 1 FROM public.pos_mixed_electronic_refund_posting m
    WHERE m.tenant_id=NEW.tenant_id AND m.sale_id=NEW.id AND m.id=NEW.operation_id
      AND m.parent_sale_id=NEW.parent_sale_id AND m.actor_id=NEW.cashier_user_id
      AND m.actor_id=public.current_app_user_id() AND m.branch_id=NEW.branch_id
      AND m.register_id=NEW.register_id AND m.shift_id=NEW.shift_id
      AND public.tenant_actor_has_scoped_permission(m.tenant_id,'pos.refund',m.branch_id)
      AND EXISTS(SELECT 1 FROM public.app_user WHERE id=m.actor_id AND status='active'))
  THEN RETURN NEW; END IF;
"""
PATCHES.append(("trg_guard_sale_immutability()", "BEGIN\n", "BEGIN\n" + SALE_ALLOW))
ACTIVE_OLD = "WHERE b.tenant_id=p_tenant AND b.parent_sale_id=p_parent"
ACTIVE_NEW = ACTIVE_OLD + """
     AND NOT EXISTS(SELECT 1 FROM public.pos_mixed_electronic_refund_posting posting
       JOIN public.sale s ON s.tenant_id=posting.tenant_id AND s.id=posting.sale_id
       WHERE posting.tenant_id=b.tenant_id AND posting.id=b.id AND s.status='completed')"""
PATCHES.append(("mixed_electronic_refund_binding_active(uuid,uuid)", ACTIVE_OLD, ACTIVE_NEW))
COMPONENT_DISPATCH = """ IF NEW.mixed_electronic_refund_id IS NOT NULL THEN
  IF TG_OP<>'INSERT' OR current_setting('transaction_isolation')<>'read committed'
    OR NEW.physical_refund_id IS NOT NULL OR NEW.mixed_refund_id IS NOT NULL
    OR NEW.electronic_refund_id IS NOT NULL
    OR NOT EXISTS(SELECT 1 FROM public.pos_mixed_electronic_refund_posting posting_marker
      JOIN public.sale posting_sale ON posting_sale.tenant_id=posting_marker.tenant_id
        AND posting_sale.id=posting_marker.sale_id
      WHERE posting_marker.tenant_id=NEW.tenant_id
        AND posting_marker.id=NEW.mixed_electronic_refund_id
        AND posting_marker.sale_id=NEW.sale_id AND posting_marker.parent_sale_id=NEW.parent_sale_id
        AND posting_marker.actor_id=public.current_app_user_id() AND session_user='aurum_app'
        AND NOT public.is_support_session() AND posting_sale.status='draft'
        AND posting_marker.tenant_id=public.current_tenant_id()
        AND public.tenant_actor_has_scoped_permission(
          posting_marker.tenant_id,'pos.refund',posting_marker.branch_id)) THEN
   RAISE EXCEPTION 'mixed_electronic_refund_component_invalid' USING ERRCODE='23514';
  END IF;
  NEW.created_at:=clock_timestamp(); NEW.created_by:=public.current_app_user_id();
  RETURN NEW;
 END IF;
"""
PATCHES.append(
    ("trg_unit_refund_payment_component_guard()", "BEGIN\n", "BEGIN\n" + COMPONENT_DISPATCH)
)
COMPLETE_DISPATCH = """ IF EXISTS(SELECT 1 FROM public.pos_mixed_electronic_refund_posting
   WHERE tenant_id=NEW.tenant_id AND sale_id=NEW.sale_id) THEN
  PERFORM public.assert_mixed_electronic_refund_posting(NEW.tenant_id,NEW.sale_id);
  RETURN NEW;
 END IF;
"""
PATCHES.append(
    ("trg_unit_refund_payment_component_complete()", "BEGIN\n", "BEGIN\n" + COMPLETE_DISPATCH)
)
AUDIT_OLD = "IF v_result IS NULL THEN RETURN NULL; END IF;"
AUDIT_NEW = AUDIT_OLD + """
      IF p_table_name='pos_mixed_electronic_refund_posting' THEN
        RETURN jsonb_build_object('created_at',v_result->'created_at',
          'created_by',v_result->'created_by');
      END IF;
      IF p_table_name='sale_payment' AND jsonb_typeof(v_result->'metadata')='object' THEN
        RETURN jsonb_set(v_result,'{metadata}',
          (v_result->'metadata')-ARRAY['terminal_id','document_number']);
      END IF;"""
PATCHES.append(("audit_redact_table_snapshot(text,jsonb)", AUDIT_OLD, AUDIT_NEW))


def upgrade() -> None:
    _sql("""
      CREATE TEMP TABLE electronic_posting_reference_grants AS
       SELECT oid::regclass::TEXT AS relation,
        has_table_privilege('aurum_schema_owner',oid,'REFERENCES') AS granted
       FROM pg_class WHERE oid IN ('public.pos_mixed_electronic_refund_activation'::regclass,
        'public.sale'::regclass,'public.app_user'::regclass);
      GRANT REFERENCES ON public.pos_mixed_electronic_refund_activation,public.sale,public.app_user
       TO aurum_schema_owner;
      CREATE TABLE public.pos_mixed_electronic_refund_posting (
       tenant_id UUID NOT NULL,id UUID NOT NULL,parent_sale_id UUID NOT NULL,
       sale_id UUID NOT NULL,branch_id UUID NOT NULL,register_id UUID NOT NULL,
       shift_id UUID NOT NULL,actor_id UUID NOT NULL REFERENCES public.app_user(id),
       reason_code TEXT,comment TEXT,
       created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
       created_by UUID NOT NULL REFERENCES public.app_user(id),
       PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,sale_id),
       FOREIGN KEY(tenant_id,id)
        REFERENCES public.pos_mixed_electronic_refund_activation(tenant_id,id),
       FOREIGN KEY(tenant_id,sale_id) REFERENCES public.sale(tenant_id,id)
        DEFERRABLE INITIALLY DEFERRED);
      DO $$ DECLARE r RECORD; BEGIN
       FOR r IN SELECT * FROM pg_temp.electronic_posting_reference_grants WHERE NOT granted LOOP
        EXECUTE format('REVOKE REFERENCES ON %s FROM aurum_schema_owner',r.relation);
       END LOOP;
      END $$;
      DROP TABLE pg_temp.electronic_posting_reference_grants;
      ALTER TABLE public.pos_mixed_electronic_refund_posting OWNER TO aurum_schema_owner;
      ALTER TABLE public.pos_mixed_electronic_refund_posting ENABLE ROW LEVEL SECURITY;
      ALTER TABLE public.pos_mixed_electronic_refund_posting FORCE ROW LEVEL SECURITY;
      REVOKE ALL ON public.pos_mixed_electronic_refund_posting FROM PUBLIC,aurum_app,aurum_support;
      GRANT SELECT,INSERT ON public.pos_mixed_electronic_refund_posting TO aurum_app;
      GRANT SELECT ON public.pos_mixed_electronic_refund_posting TO aurum_support;
      CREATE POLICY schema_owner ON public.pos_mixed_electronic_refund_posting
       TO aurum_schema_owner USING(true) WITH CHECK(true);
      CREATE POLICY support ON public.pos_mixed_electronic_refund_posting
       FOR SELECT TO aurum_support USING(true);
      CREATE POLICY tenant_actor ON public.pos_mixed_electronic_refund_posting TO aurum_app
       USING(current_user='aurum_app' AND session_user='aurum_app'
        AND NOT public.is_tenant_support_session()
        AND EXISTS(SELECT 1 FROM public.app_user
          WHERE id=public.current_app_user_id() AND status='active')
        AND tenant_id=public.current_tenant_id() AND actor_id=public.current_app_user_id()
        AND public.tenant_actor_has_scoped_permission(tenant_id,'pos.refund',branch_id))
       WITH CHECK(tenant_id=public.current_tenant_id() AND actor_id=public.current_app_user_id()
        AND public.tenant_actor_has_scoped_permission(tenant_id,'pos.refund',branch_id));
      ALTER TABLE public.pos_unit_refund_payment_component
       ADD COLUMN mixed_electronic_refund_id UUID,
       DROP CONSTRAINT ck_unit_refund_component_parent,
       ADD CONSTRAINT ck_unit_refund_component_parent CHECK(
        num_nonnulls(physical_refund_id,mixed_refund_id,electronic_refund_id,
          mixed_electronic_refund_id)=1),
       ADD CONSTRAINT fk_unit_refund_component_mixed_electronic
        FOREIGN KEY(tenant_id,mixed_electronic_refund_id)
        REFERENCES public.pos_mixed_electronic_refund_posting(tenant_id,id)
        DEFERRABLE INITIALLY DEFERRED;
      CREATE UNIQUE INDEX ix_unit_refund_component_mixed_electronic_source
       ON public.pos_unit_refund_payment_component
        (tenant_id,mixed_electronic_refund_id,original_payment_id)
       WHERE mixed_electronic_refund_id IS NOT NULL;
    """)
    _sql(SQL)
    for signature in FUNCTIONS:
        _sql(f"""
         ALTER FUNCTION public.{signature} OWNER TO aurum_schema_owner;
         REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC,aurum_app,aurum_support;
        """)
    for signature, old, new in PATCHES:
        _patch(signature, old, new, upgrade=True)
    _sql("""
      CREATE TRIGGER a00_tenant_access BEFORE INSERT OR UPDATE OR DELETE
       ON public.pos_mixed_electronic_refund_posting FOR EACH ROW
       EXECUTE FUNCTION public.trg_enforce_tenant_access('pos');
      CREATE TRIGGER a10_mixed_electronic_refund_posting_guard BEFORE INSERT OR UPDATE OR DELETE
       ON public.pos_mixed_electronic_refund_posting FOR EACH ROW
       EXECUTE FUNCTION public.trg_mixed_electronic_refund_posting_guard();
      CREATE CONSTRAINT TRIGGER mixed_electronic_refund_posting_complete
       AFTER INSERT ON public.pos_mixed_electronic_refund_posting
       DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
       EXECUTE FUNCTION public.trg_mixed_electronic_refund_posting_complete();
      CREATE CONSTRAINT TRIGGER mixed_electronic_refund_posting_complete
       AFTER INSERT OR UPDATE ON public.sale
       DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
       EXECUTE FUNCTION public.trg_mixed_electronic_refund_posting_complete();
      CREATE CONSTRAINT TRIGGER mixed_electronic_refund_posting_complete
       AFTER INSERT ON public.sale_payment
       DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
       EXECUTE FUNCTION public.trg_mixed_electronic_refund_posting_complete();
      CREATE CONSTRAINT TRIGGER mixed_electronic_refund_posting_complete
       AFTER INSERT ON public.pos_unit_refund_payment_component
       DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
       EXECUTE FUNCTION public.trg_mixed_electronic_refund_posting_complete();
      CREATE TRIGGER z90_mixed_electronic_refund_posting_audit AFTER INSERT OR UPDATE OR DELETE
       ON public.pos_mixed_electronic_refund_posting FOR EACH ROW
       EXECUTE FUNCTION public.trg_audit_log();
    """)


def downgrade() -> None:
    _sql("""
      LOCK TABLE public.pos_mixed_electronic_refund_posting,
       public.pos_unit_refund_payment_component IN ACCESS EXCLUSIVE MODE;
      DO $$ BEGIN
       IF EXISTS(SELECT 1 FROM public.pos_mixed_electronic_refund_posting) THEN
        RAISE EXCEPTION 'mixed_electronic_refund_posting_downgrade_requires_empty_history'
         USING ERRCODE='55000';
       END IF;
      END $$;
    """)
    for signature, old, new in reversed(PATCHES):
        _patch(signature, old, new, upgrade=False)
    _sql("""
      DROP TRIGGER mixed_electronic_refund_posting_complete ON public.sale;
      DROP TRIGGER mixed_electronic_refund_posting_complete ON public.sale_payment;
      DROP TRIGGER mixed_electronic_refund_posting_complete
        ON public.pos_unit_refund_payment_component;
      ALTER TABLE public.pos_unit_refund_payment_component
       DROP CONSTRAINT fk_unit_refund_component_mixed_electronic,
       DROP CONSTRAINT ck_unit_refund_component_parent,
       ADD CONSTRAINT ck_unit_refund_component_parent
        CHECK(num_nonnulls(physical_refund_id,mixed_refund_id,electronic_refund_id)=1),
       DROP COLUMN mixed_electronic_refund_id;
      DROP TABLE public.pos_mixed_electronic_refund_posting;
    """)
    for signature in reversed(FUNCTIONS):
        _sql(f"DROP FUNCTION public.{signature};")
