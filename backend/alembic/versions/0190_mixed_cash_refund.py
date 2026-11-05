"""Atomic mixed cash refund bound to append-only mixed quarantine.

Revision ID: 0190
Revises: 0189
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "0190"
down_revision: str | Sequence[str] | None = "0189"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COMPONENT_TABLES = (
    "sale_item",
    "sale_payment",
    "prescription_log",
    "pos_unit_cart_item",
    "pos_payment_attempt",
    "batch_movement",
    "unit_stock_cart_command_v2",
)
FUNCTIONS = (
    "trg_mixed_cash_refund_guard()",
    "trg_mixed_cash_refund_complete()",
    "trg_mixed_cash_refund_components()",
)


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
        raise RuntimeError("Mixed cash refund historical guard changed")
    # Preserve historical owners, ACL, search paths and security modes.
    op.get_bind().exec_driver_sql(definition.replace(old, new, 1))


DECLARATION_OLD = "  v_mixed_cash BOOLEAN := false;"
DECLARATION_NEW = DECLARATION_OLD + "\n  v_mixed_refund BOOLEAN := false;"
PROBE_OLD = """  IF current_user = 'aurum_app' AND TG_OP = 'UPDATE' THEN
    SELECT EXISTS(SELECT 1 FROM public.unit_stock_cart_command_v2"""
PROBE_NEW = """  IF current_user = 'aurum_app' AND TG_OP = 'UPDATE' THEN
    SELECT EXISTS(SELECT 1 FROM public.pos_mixed_checkout
      WHERE tenant_id=NEW.tenant_id AND sale_id=NEW.parent_sale_id)
      INTO v_mixed_refund;
    SELECT EXISTS(SELECT 1 FROM public.unit_stock_cart_command_v2"""
ITEM_OLD = "    IF v_mixed_cash THEN"
ITEM_NEW = """    IF v_mixed_refund THEN
      IF NOT EXISTS(SELECT 1 FROM public.pos_mixed_cash_refund m
        WHERE m.tenant_id=NEW.tenant_id AND m.sale_id=NEW.id
          AND m.actor_id=public.current_app_user_id()
          AND session_user='aurum_app' AND NOT public.is_support_session()
          AND public.tenant_actor_has_scoped_permission(m.tenant_id,'pos.refund',m.branch_id)
          AND (NEW.sale_type,NEW.parent_sale_id,NEW.branch_id,NEW.register_id,NEW.shift_id,
            NEW.cashier_user_id,NEW.operation_id,NEW.operation_hash,NEW.total_amount,NEW.currency,
            NEW.is_test,NEW.receipt_snapshot,NEW.receipt_number,NEW.receipt_seq,NEW.completed_at)
          IS NOT DISTINCT FROM ('return',m.parent_sale_id,m.branch_id,m.register_id,m.shift_id,
            m.actor_id,m.id,m.request_hash,(m.result_payload->>'total_amount')::NUMERIC,'TJS',
            false,m.receipt_payload,m.receipt_payload->>'receipt_number',
            (m.receipt_payload->>'receipt_sequence')::BIGINT,
            (m.receipt_payload->>'completed_at')::TIMESTAMPTZ)) THEN
        RAISE EXCEPTION 'mixed_cash_refund_sale_invalid' USING ERRCODE='23514';
      END IF;
    ELSIF v_mixed_cash THEN"""
TOTAL_OLD = "    IF NOT v_physical_cash AND NOT v_physical_refund AND NOT v_mixed_cash AND ("
TOTAL_NEW = (
    "    IF NOT v_physical_cash AND NOT v_physical_refund AND NOT v_mixed_cash "
    "AND NOT v_mixed_refund AND ("
)
BOUNDARY_OLD = """   RAISE EXCEPTION 'mixed_checkout_refund_not_supported' USING ERRCODE='23514';"""
BOUNDARY_NEW = """   IF session_user<>'aurum_app' OR public.is_support_session()
     OR NEW.tenant_id IS DISTINCT FROM public.current_tenant_id()
     OR NOT EXISTS(SELECT 1 FROM public.app_user
       WHERE id=public.current_app_user_id() AND status='active')
     OR NOT EXISTS(SELECT 1 FROM public.pos_mixed_cash_refund m
       JOIN public.pos_mixed_return_command c ON c.tenant_id=m.tenant_id AND c.id=m.id
       JOIN public.pos_mixed_checkout source
         ON source.tenant_id=c.tenant_id AND source.id=c.parent_checkout_id
       WHERE m.tenant_id=NEW.tenant_id AND m.sale_id=NEW.id
         AND m.parent_sale_id=NEW.parent_sale_id AND source.sale_id=m.parent_sale_id
         AND c.parent_sale_id=m.parent_sale_id AND c.actor_id=m.actor_id
         AND c.branch_id=m.branch_id AND c.total_amount>0
         AND m.actor_id=public.current_app_user_id()
         AND public.tenant_actor_has_scoped_permission(m.tenant_id,'pos.refund',m.branch_id)
         AND (NEW.sale_type,NEW.branch_id,NEW.register_id,NEW.shift_id,NEW.cashier_user_id,
           NEW.operation_id,NEW.operation_hash,NEW.total_amount,NEW.currency,NEW.is_test)
         IS NOT DISTINCT FROM ('return',m.branch_id,m.register_id,m.shift_id,m.actor_id,
           m.id,m.request_hash,c.total_amount,'TJS',false)
         AND NEW.status IN ('draft','completed')) THEN
    RAISE EXCEPTION 'mixed_checkout_refund_not_supported' USING ERRCODE='23514';
   END IF;"""
AUDIT_OLD = "IF p_table_name = 'pos_mixed_return_command' THEN"
AUDIT_NEW = """IF p_table_name = 'pos_mixed_cash_refund' THEN
        v_result := jsonb_set(v_result,ARRAY['result_payload'],to_jsonb('***'::TEXT),false);
        v_result := jsonb_set(v_result,ARRAY['receipt_payload'],to_jsonb('***'::TEXT),false);
        v_result := jsonb_set(v_result,ARRAY['comment'],to_jsonb('***'::TEXT),false);
      END IF;
      """ + AUDIT_OLD
JOURNAL_OLD = """ IF TG_TABLE_NAME='pos_mixed_return_command' THEN
  PERFORM pg_advisory_xact_lock"""
JOURNAL_NEW = """ IF TG_TABLE_NAME='pos_mixed_return_command' THEN
  -- Stamp the top transaction, including INSERTs performed inside savepoints.
  -- Caller supplied values never prove ownership of a previous journal.
  NEW.cash_refund_writer_xid:=pg_current_xact_id();
  PERFORM pg_advisory_xact_lock"""

FUNCTIONS_SQL = """
CREATE FUNCTION public.trg_mixed_cash_refund_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS $$
DECLARE c public.pos_mixed_return_command%ROWTYPE; p public.sale%ROWTYPE;
 h public.shift%ROWTYPE; m public.pos_mixed_checkout%ROWTYPE; r RECORD;
 original JSONB; v_line JSONB; v_lines JSONB:='[]'::JSONB; v_expected JSONB;
 v_payload TEXT; v_completed TIMESTAMPTZ; v_position INTEGER:=0; v_reason_mode TEXT;
 v_cursors TEXT:=''; v_selections TEXT:=''; v_review_payload TEXT; v_prior NUMERIC;
 v_trim TEXT:=chr(9)||chr(10)||chr(11)||chr(12)||chr(13)||chr(28)||chr(29)
   ||chr(30)||chr(31)||chr(32)||chr(133)||chr(160)||chr(5760)
   ||chr(8192)||chr(8193)||chr(8194)||chr(8195)||chr(8196)||chr(8197)
   ||chr(8198)||chr(8199)||chr(8200)||chr(8201)||chr(8202)
   ||chr(8232)||chr(8233)||chr(8239)||chr(8287)||chr(12288);
BEGIN
 IF TG_OP<>'INSERT' THEN
  RAISE EXCEPTION 'mixed_cash_refund_immutable' USING ERRCODE='55000';
 END IF;
 IF current_setting('transaction_isolation')<>'read committed' THEN
  RAISE EXCEPTION 'mixed_cash_refund_isolation_invalid' USING ERRCODE='25000';
 END IF;
 IF current_user<>'aurum_app' OR session_user<>'aurum_app' OR public.is_support_session()
   OR NEW.tenant_id IS DISTINCT FROM public.current_tenant_id()
   OR NEW.actor_id IS DISTINCT FROM public.current_app_user_id()
   OR NOT EXISTS(SELECT 1 FROM public.app_user WHERE id=NEW.actor_id AND status='active')
   OR NOT coalesce(public.tenant_actor_has_scoped_permission(
      NEW.tenant_id,'pos.refund',NEW.branch_id),false) THEN
  RAISE EXCEPTION 'mixed_cash_refund_actor_denied' USING ERRCODE='42501';
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(
   'aurum-mixed-return:'||NEW.tenant_id||':'||NEW.id,0));
 SELECT * INTO p FROM public.sale
   WHERE tenant_id=NEW.tenant_id AND id=NEW.parent_sale_id FOR UPDATE;
 SELECT * INTO c FROM public.pos_mixed_return_command
   WHERE tenant_id=NEW.tenant_id AND id=NEW.id;
 SELECT * INTO m FROM public.pos_mixed_checkout
   WHERE tenant_id=NEW.tenant_id AND sale_id=NEW.parent_sale_id;
 SELECT * INTO h FROM public.shift WHERE tenant_id=NEW.tenant_id AND id=NEW.shift_id FOR UPDATE;
 PERFORM 1 FROM public.register WHERE tenant_id=NEW.tenant_id AND branch_id=NEW.branch_id
   AND id=NEW.register_id AND is_active FOR SHARE;
 IF NOT FOUND THEN
  RAISE EXCEPTION 'mixed_cash_refund_register_inactive' USING ERRCODE='23514';
 END IF;
 IF c.id IS NULL OR m.id IS NULL OR p.id IS NULL OR h.id IS NULL
   OR c.parent_sale_id<>p.id OR c.parent_checkout_id<>m.id OR c.actor_id<>NEW.actor_id
   OR c.stock_command_id<>m.stock_command_id OR c.branch_id<>NEW.branch_id
   OR c.state<>'prepared_quarantine' OR c.total_amount<=0
   OR c.cash_refund_writer_xid IS DISTINCT FROM pg_current_xact_id()
   OR (p.status,p.sale_type,p.parent_sale_id,p.currency,p.is_test,p.register_id,p.branch_id)
     IS DISTINCT FROM ('completed','sale',NULL::UUID,'TJS',false,NEW.register_id,NEW.branch_id)
   OR (m.branch_id,m.register_id,m.result_payload->>'schema_version')
     IS DISTINCT FROM (NEW.branch_id,NEW.register_id,'4')
   OR (h.status,h.branch_id,h.register_id,h.opened_by_user_id,h.currency) IS DISTINCT FROM
      ('open',NEW.branch_id,NEW.register_id,NEW.actor_id,'TJS')
   OR NEW.payment_id<>NEW.id OR NEW.sale_id=NEW.parent_sale_id
   OR EXISTS(SELECT 1 FROM public.sale WHERE id=NEW.sale_id)
   OR EXISTS(SELECT 1 FROM public.sale_payment WHERE id=NEW.payment_id) THEN
  RAISE EXCEPTION 'mixed_cash_refund_scope_invalid' USING ERRCODE='23514';
 END IF;
 FOR r IN SELECT * FROM public.pos_mixed_return_line
   WHERE tenant_id=NEW.tenant_id AND command_id=NEW.id ORDER BY position LOOP
  SELECT value INTO original FROM jsonb_array_elements(m.result_payload->'lines')
    WHERE value->>'id'=r.line_id::TEXT;
  IF original IS NULL OR original->>'inventory_kind' IS DISTINCT FROM r.inventory_kind
     OR (original->>'position')::INTEGER IS DISTINCT FROM r.position THEN
   RAISE EXCEPTION 'mixed_cash_refund_source_invalid' USING ERRCODE='23514';
  END IF;
  v_position:=v_position+1;
  v_line:=jsonb_build_object('inventory_kind',r.inventory_kind,
    'original_line_id',r.line_id,'position',v_position,'original_position',r.position,
    'name',original->'name','original_retail_unit_price',original->'original_retail_unit_price',
    'gross_amount',to_char(r.gross_amount,'FM999999999990.00'),
    'discount_amount',to_char(r.discount_amount,'FM999999999990.00'),
    'net_amount',to_char(r.net_amount,'FM999999999990.00'));
  IF r.inventory_kind='legacy_pack' THEN
   v_line:=v_line||jsonb_build_object(
     'quantity',to_char(r.legacy_quantity,'FM999999999990.000'),
     'offset',to_char(r.legacy_offset,'FM999999999990.000'));
  ELSE
   v_line:=v_line||jsonb_build_object('packaging',original->'packaging',
     'original_sale_format',original->'sale_format','sale_format',r.sale_format,
     'quantity',r.quantity,'base_units',r.base_units,'offset',r."offset");
  END IF;
  v_lines:=v_lines||jsonb_build_array(v_line);
 END LOOP;
 IF jsonb_array_length(v_lines) NOT BETWEEN 1 AND 200
   OR jsonb_typeof(NEW.receipt_payload->'receipt_number') IS DISTINCT FROM 'string'
   OR length(NEW.receipt_payload->>'receipt_number') NOT BETWEEN 1 AND 100
   OR btrim(NEW.receipt_payload->>'receipt_number')=''
   OR NEW.receipt_payload->>'receipt_number' ~ '[[:cntrl:]]'
   OR jsonb_typeof(NEW.receipt_payload->'receipt_sequence') IS DISTINCT FROM 'number'
   OR coalesce(NEW.receipt_payload->>'receipt_sequence','') !~ '^[1-9][0-9]{0,18}$'
   OR jsonb_typeof(NEW.receipt_payload->'completed_at') IS DISTINCT FROM 'string'
   OR coalesce(NEW.receipt_payload->>'completed_at','') !~ '(Z|[+-][0-9]{2}:[0-9]{2})$' THEN
  RAISE EXCEPTION 'mixed_cash_refund_receipt_invalid' USING ERRCODE='23514';
 END IF;
 v_completed:=(NEW.receipt_payload->>'completed_at')::TIMESTAMPTZ;
 IF NOT isfinite(v_completed) OR v_completed<p.completed_at OR v_completed>clock_timestamp() THEN
  RAISE EXCEPTION 'mixed_cash_refund_time_invalid' USING ERRCODE='23514';
 END IF;
 v_expected:=jsonb_build_object('schema_version',4,'document_kind','mixed_cash_refund_receipt',
   'parent_sale_id',p.id,'parent_receipt_number',m.result_payload->'receipt_number',
   'receipt_number',NEW.receipt_payload->'receipt_number',
   'receipt_sequence',NEW.receipt_payload->'receipt_sequence',
   'completed_at',NEW.receipt_payload->'completed_at','is_test',false,
   'lines',v_lines,'payment_method','cash','total_amount',to_char(c.total_amount,'FM999999999990.00'),
   'currency','TJS');
 IF NEW.receipt_payload IS DISTINCT FROM v_expected THEN
  RAISE EXCEPTION 'mixed_cash_refund_receipt_invalid' USING ERRCODE='23514';
 END IF;
 v_expected:=jsonb_build_object('schema_version',4,'document_kind','mixed_cash_refund',
   'operation_id',NEW.id,'parent_sale_id',p.id,'sale_id',NEW.sale_id,'tenant_id',NEW.tenant_id,
   'branch_id',NEW.branch_id,'register_id',NEW.register_id,'shift_id',NEW.shift_id,
   'actor_id',NEW.actor_id,'payment_id',NEW.id,
   'total_amount',to_char(c.total_amount,'FM999999999990.00'),
   'physical_cost_amount',to_char(c.physical_cost_amount,'FM999999999990.00'),
   'receipt',NEW.receipt_payload);
 SELECT refund_reason_mode INTO v_reason_mode FROM public.tenant_settings
   WHERE tenant_id=NEW.tenant_id FOR SHARE;
 IF v_reason_mode IS NULL
   OR v_reason_mode NOT IN ('off','optional','required','required_with_text')
   OR (NEW.reason_code IS NOT NULL AND NEW.reason_code NOT IN (
     'dispensing_error','duplicate_sale','pricing_error','quality_issue',
     'damaged_package','customer_cancelled','other'))
   OR (NEW.comment IS NOT NULL AND (char_length(NEW.comment) NOT BETWEEN 1 AND 500
     OR NEW.comment IS DISTINCT FROM btrim(NEW.comment,v_trim)))
   OR (NEW.review_hash IS NOT NULL AND NEW.review_hash !~ '^[0-9a-f]{64}$')
   OR (v_reason_mode='off' AND (NEW.reason_code IS NOT NULL OR NEW.comment IS NOT NULL))
   OR (v_reason_mode IN ('required','required_with_text') AND NEW.reason_code IS NULL)
   OR (v_reason_mode='required_with_text' AND NEW.comment IS NULL) THEN
  RAISE EXCEPTION 'mixed_cash_refund_reason_invalid' USING ERRCODE='23514';
 END IF;
 IF NEW.review_hash IS NOT NULL THEN
  -- All original lines participate, even if this refund selects only a subset.
  -- Exclude this transaction's newly received command from the reviewed cursor.
  FOR original IN SELECT value FROM jsonb_array_elements(m.result_payload->'lines')
    ORDER BY (value->>'id') COLLATE "C" LOOP
   SELECT coalesce(sum(CASE WHEN l.inventory_kind='legacy_pack' THEN l.legacy_quantity
     ELSE l.base_units::NUMERIC END),0) INTO v_prior
     FROM public.pos_mixed_return_line l JOIN public.pos_mixed_return_command command
       ON command.tenant_id=l.tenant_id AND command.id=l.command_id
     WHERE l.tenant_id=NEW.tenant_id AND command.parent_sale_id=NEW.parent_sale_id
       AND command.id<>NEW.id AND l.line_id=(original->>'id')::UUID
       AND l.inventory_kind=original->>'inventory_kind';
   IF v_cursors<>'' THEN v_cursors:=v_cursors||','; END IF;
   v_cursors:=v_cursors||'["'||(original->>'id')||'","'
     ||(original->>'inventory_kind')||'",'
     ||CASE WHEN original->>'inventory_kind'='legacy_pack'
       THEN '"'||to_char(v_prior,'FM999999999990.000')||'"'
       ELSE v_prior::BIGINT::TEXT END||']';
  END LOOP;
  FOR r IN SELECT * FROM public.pos_mixed_return_line
    WHERE tenant_id=NEW.tenant_id AND command_id=NEW.id ORDER BY line_id::TEXT COLLATE "C" LOOP
   IF v_selections<>'' THEN v_selections:=v_selections||','; END IF;
   v_selections:=v_selections||'["'||r.line_id||'","'||r.inventory_kind||'",'
     ||CASE WHEN r.inventory_kind='legacy_pack'
       THEN '"'||to_char(r.legacy_quantity,'FM999999999990.000')||'"'
       ELSE '"'||r.sale_format||'",'||r.quantity::TEXT END||']';
  END LOOP;
  v_review_payload:='{"cursors":['||v_cursors||'],"original_operation_id":"'||m.id
    ||'","parent_sale_id":"'||NEW.parent_sale_id||'","selections":['||v_selections
    ||'],"version":1}';
  IF NEW.review_hash<>encode(sha256(convert_to(v_review_payload,'UTF8')),'hex') THEN
   RAISE EXCEPTION 'mixed_cash_refund_review_invalid' USING ERRCODE='23514';
  END IF;
 END IF;
 v_payload:='{"actor_id":"'||NEW.actor_id||'","comment_hash":'
   ||CASE WHEN NEW.comment IS NULL THEN 'null'
      ELSE '"'||encode(sha256(convert_to(NEW.comment,'UTF8')),'hex')||'"' END
   ||',"journal_request_hash":"'||c.request_hash
   ||'","kind":"mixed_cash_refund","parent_sale_id":"'||p.id||'","reason_code":'
   ||CASE WHEN NEW.reason_code IS NULL THEN 'null' ELSE '"'||NEW.reason_code||'"' END
   ||',"review_hash":'
   ||CASE WHEN NEW.review_hash IS NULL THEN 'null' ELSE '"'||NEW.review_hash||'"' END
   ||',"shift_id":"'||NEW.shift_id||'","tenant_id":"'||NEW.tenant_id||'","version":4}';
 IF NEW.result_payload IS DISTINCT FROM v_expected
   OR NEW.request_hash<>encode(sha256(convert_to(v_payload,'UTF8')),'hex') THEN
  RAISE EXCEPTION 'mixed_cash_refund_document_invalid' USING ERRCODE='23514';
 END IF;
 IF NOT coalesce(public.tenant_actor_has_scoped_permission(
      NEW.tenant_id,'pos.refund',NEW.branch_id),false)
   OR NOT EXISTS(SELECT 1 FROM public.app_user WHERE id=NEW.actor_id AND status='active') THEN
  RAISE EXCEPTION 'mixed_cash_refund_actor_denied' USING ERRCODE='42501';
 END IF;
 NEW.created_at:=statement_timestamp(); NEW.updated_at:=NEW.created_at;
 NEW.created_by:=NEW.actor_id; NEW.updated_by:=NEW.actor_id;
 RETURN NEW;
END $$;

CREATE FUNCTION public.trg_mixed_cash_refund_components() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE m public.pos_mixed_cash_refund%ROWTYPE; v_sale UUID; v_tenant UUID;
BEGIN
 IF TG_OP='DELETE' THEN v_tenant:=OLD.tenant_id; ELSE v_tenant:=NEW.tenant_id; END IF;
 IF TG_TABLE_NAME='batch_movement' THEN
  IF NEW.source_table IS DISTINCT FROM 'sale' THEN RETURN NEW; END IF;
  v_sale:=NEW.source_id;
 ELSIF TG_TABLE_NAME='unit_stock_cart_command_v2' THEN
  SELECT * INTO m FROM public.pos_mixed_cash_refund
    WHERE tenant_id=v_tenant AND (id=NEW.id OR sale_id=NEW.checkout_sale_id
      OR sale_id=NEW.mixed_checkout_sale_id);
 ELSE
  IF TG_OP='DELETE' THEN v_sale:=OLD.sale_id; ELSE v_sale:=NEW.sale_id; END IF;
 END IF;
 IF TG_TABLE_NAME<>'unit_stock_cart_command_v2' THEN
  SELECT * INTO m FROM public.pos_mixed_cash_refund WHERE tenant_id=v_tenant AND sale_id=v_sale;
 END IF;
 IF m.id IS NULL THEN
  IF TG_OP='DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
 END IF;
 IF session_user<>'aurum_app' OR public.is_support_session()
   OR m.tenant_id IS DISTINCT FROM public.current_tenant_id()
   OR m.actor_id IS DISTINCT FROM public.current_app_user_id()
   OR NOT EXISTS(SELECT 1 FROM public.app_user WHERE id=m.actor_id AND status='active')
   OR NOT coalesce(public.tenant_actor_has_scoped_permission(
     m.tenant_id,'pos.refund',m.branch_id),false) THEN
  RAISE EXCEPTION 'mixed_cash_refund_actor_denied' USING ERRCODE='42501';
 END IF;
 IF TG_TABLE_NAME<>'sale_payment' OR TG_OP<>'INSERT' THEN
  RAISE EXCEPTION 'mixed_cash_refund_components_forbidden' USING ERRCODE='23514';
 END IF;
 IF (NEW.id,NEW.payment_method,NEW.amount,NEW.currency,NEW.operation_id,NEW.operation_hash)
    IS DISTINCT FROM (m.payment_id,'cash',(m.result_payload->>'total_amount')::NUMERIC,
      'TJS',NULL::UUID,NULL::TEXT)
   OR NEW.payment_attempt_id IS NOT NULL OR NEW.metadata IS NOT NULL THEN
  RAISE EXCEPTION 'mixed_cash_refund_payment_invalid' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION public.trg_mixed_cash_refund_complete() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE m public.pos_mixed_cash_refund%ROWTYPE; s public.sale%ROWTYPE;
 c public.pos_mixed_return_command%ROWTYPE; p public.sale_payment%ROWTYPE;
BEGIN
 IF TG_TABLE_NAME='pos_mixed_cash_refund' THEN
  SELECT * INTO m FROM public.pos_mixed_cash_refund WHERE tenant_id=NEW.tenant_id AND id=NEW.id;
 ELSIF TG_TABLE_NAME='sale' THEN
  SELECT * INTO m FROM public.pos_mixed_cash_refund WHERE tenant_id=NEW.tenant_id AND sale_id=NEW.id;
 ELSE
  SELECT * INTO m FROM public.pos_mixed_cash_refund
    WHERE tenant_id=NEW.tenant_id AND sale_id=NEW.sale_id;
 END IF;
 IF m.id IS NULL THEN RETURN NEW; END IF;
 IF session_user<>'aurum_app' OR public.is_support_session()
   OR m.tenant_id IS DISTINCT FROM public.current_tenant_id()
   OR m.actor_id IS DISTINCT FROM public.current_app_user_id()
   OR NOT coalesce(public.tenant_actor_has_scoped_permission(
      m.tenant_id,'pos.refund',m.branch_id),false)
   OR NOT EXISTS(SELECT 1 FROM public.app_user WHERE id=m.actor_id AND status='active') THEN
  RAISE EXCEPTION 'mixed_cash_refund_actor_denied' USING ERRCODE='42501';
 END IF;
 SELECT * INTO s FROM public.sale WHERE tenant_id=m.tenant_id AND id=m.sale_id;
 SELECT * INTO c FROM public.pos_mixed_return_command WHERE tenant_id=m.tenant_id AND id=m.id;
 SELECT * INTO p FROM public.sale_payment WHERE tenant_id=m.tenant_id AND id=m.payment_id;
 IF s.id IS NULL OR c.id IS NULL OR p.id IS NULL OR s.status<>'completed'
   OR (s.sale_type,s.parent_sale_id,s.branch_id,s.register_id,s.shift_id,s.cashier_user_id,
       s.operation_id,s.operation_hash,s.total_amount,s.currency,s.is_test,s.receipt_snapshot,
       s.receipt_number,s.receipt_seq,s.completed_at) IS DISTINCT FROM
      ('return',m.parent_sale_id,m.branch_id,m.register_id,m.shift_id,m.actor_id,
       m.id,m.request_hash,c.total_amount,'TJS',false,m.receipt_payload,
       m.receipt_payload->>'receipt_number',(m.receipt_payload->>'receipt_sequence')::BIGINT,
       (m.receipt_payload->>'completed_at')::TIMESTAMPTZ)
   OR s.refund_attempt_id IS NOT NULL OR s.fiscal_data IS NOT NULL OR s.marking_codes IS NOT NULL
   OR s.voided_at IS NOT NULL OR s.voided_by_sale_id IS NOT NULL
   OR (p.sale_id,p.payment_method,p.amount,p.currency,p.operation_id,p.operation_hash)
      IS DISTINCT FROM (s.id,'cash',c.total_amount,'TJS',NULL::UUID,NULL::TEXT)
   OR p.payment_attempt_id IS NOT NULL OR p.metadata IS NOT NULL
   OR (SELECT count(*) FROM public.sale_payment WHERE sale_id=s.id)<>1
   OR EXISTS(SELECT 1 FROM public.sale_item WHERE sale_id=s.id)
   OR EXISTS(SELECT 1 FROM public.pos_unit_cart_item WHERE sale_id=s.id)
   OR EXISTS(SELECT 1 FROM public.pos_payment_attempt WHERE sale_id=s.id)
   OR EXISTS(SELECT 1 FROM public.prescription_log WHERE sale_id=s.id)
   OR EXISTS(SELECT 1 FROM public.batch_movement WHERE source_table='sale' AND source_id=s.id)
   OR EXISTS(SELECT 1 FROM public.unit_stock_cart_command_v2
     WHERE tenant_id=m.tenant_id AND (id=m.id OR checkout_sale_id=s.id OR mixed_checkout_sale_id=s.id))
 THEN
  RAISE EXCEPTION 'mixed_cash_refund_incomplete' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
"""


def upgrade() -> None:
    _sql("""
      ALTER TABLE public.pos_mixed_return_command ADD COLUMN cash_refund_writer_xid xid8;
    """)
    _replace("trg_mixed_return_guard()", JOURNAL_OLD, JOURNAL_NEW)
    _sql("""
      CREATE TEMP TABLE mixed_cash_refund_reference_grants AS
        SELECT oid::regclass AS relation,
          has_table_privilege('aurum_schema_owner',oid,'REFERENCES') AS granted
        FROM pg_class WHERE oid IN ('public.sale'::regclass,
          'public.pos_mixed_return_command'::regclass);
      GRANT REFERENCES ON public.sale,public.pos_mixed_return_command TO aurum_schema_owner;
      CREATE TABLE public.pos_mixed_cash_refund (
        tenant_id UUID NOT NULL,id UUID NOT NULL,parent_sale_id UUID NOT NULL,sale_id UUID NOT NULL,
        branch_id UUID NOT NULL,register_id UUID NOT NULL,shift_id UUID NOT NULL,actor_id UUID NOT NULL,
        payment_id UUID NOT NULL CHECK(payment_id=id),
        request_hash TEXT NOT NULL CHECK(request_hash~'^[0-9a-f]{64}$'),
        reason_code TEXT CHECK(reason_code IS NULL OR reason_code IN (
          'dispensing_error','duplicate_sale','pricing_error','quality_issue',
          'damaged_package','customer_cancelled','other')),
        comment TEXT CHECK(comment IS NULL OR char_length(comment) BETWEEN 1 AND 500),
        review_hash TEXT CHECK(review_hash IS NULL OR review_hash~'^[0-9a-f]{64}$'),
        result_payload JSONB NOT NULL CHECK(jsonb_typeof(result_payload)='object'),
        receipt_payload JSONB NOT NULL CHECK(jsonb_typeof(receipt_payload)='object'),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        created_by UUID NOT NULL,updated_by UUID NOT NULL,
        PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,sale_id),UNIQUE(tenant_id,payment_id),
        FOREIGN KEY(tenant_id,id) REFERENCES public.pos_mixed_return_command(tenant_id,id),
        FOREIGN KEY(tenant_id,parent_sale_id) REFERENCES public.sale(tenant_id,id),
        FOREIGN KEY(tenant_id,sale_id) REFERENCES public.sale(tenant_id,id)
          DEFERRABLE INITIALLY DEFERRED
      );
      DO $$ DECLARE r RECORD; BEGIN
        FOR r IN SELECT * FROM pg_temp.mixed_cash_refund_reference_grants WHERE NOT granted LOOP
          EXECUTE format('REVOKE REFERENCES ON %s FROM aurum_schema_owner',r.relation);
        END LOOP;
      END $$;
      DROP TABLE pg_temp.mixed_cash_refund_reference_grants;
      ALTER TABLE public.pos_mixed_cash_refund OWNER TO aurum_schema_owner;
      ALTER TABLE public.pos_mixed_cash_refund ENABLE ROW LEVEL SECURITY;
      ALTER TABLE public.pos_mixed_cash_refund FORCE ROW LEVEL SECURITY;
      REVOKE ALL ON public.pos_mixed_cash_refund FROM PUBLIC,aurum_app,aurum_support;
      GRANT SELECT,INSERT ON public.pos_mixed_cash_refund TO aurum_app;
      GRANT SELECT ON public.pos_mixed_cash_refund TO aurum_support;
      CREATE POLICY schema_owner ON public.pos_mixed_cash_refund TO aurum_schema_owner
        USING(true) WITH CHECK(true);
      CREATE POLICY support ON public.pos_mixed_cash_refund FOR SELECT TO aurum_support USING(true);
      CREATE POLICY tenant_read ON public.pos_mixed_cash_refund FOR SELECT TO aurum_app
        USING(tenant_id=public.current_tenant_id() AND
          public.tenant_actor_has_scoped_permission(tenant_id,'pos.refund',branch_id));
      CREATE POLICY tenant_insert ON public.pos_mixed_cash_refund FOR INSERT TO aurum_app
        WITH CHECK(tenant_id=public.current_tenant_id() AND
          public.tenant_actor_has_scoped_permission(tenant_id,'pos.refund',branch_id));
    """)
    _sql(FUNCTIONS_SQL)
    for signature in FUNCTIONS:
        _sql(f"""
          ALTER FUNCTION public.{signature} OWNER TO aurum_schema_owner;
          REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC,aurum_app,aurum_support;
        """)
    for old, new in (
        (DECLARATION_OLD, DECLARATION_NEW),
        (PROBE_OLD, PROBE_NEW),
        (ITEM_OLD, ITEM_NEW),
        (TOTAL_OLD, TOTAL_NEW),
    ):
        _replace("trg_guard_sale_immutability()", old, new)
    _replace("trg_mixed_checkout_return_boundary()", BOUNDARY_OLD, BOUNDARY_NEW)
    _replace("audit_redact_table_snapshot(text,jsonb)", AUDIT_OLD, AUDIT_NEW)
    _sql("""
      CREATE TRIGGER a00_tenant_access BEFORE INSERT OR UPDATE OR DELETE ON public.pos_mixed_cash_refund
        FOR EACH ROW EXECUTE FUNCTION public.trg_enforce_tenant_access('pos');
      CREATE TRIGGER a10_mixed_cash_refund_guard BEFORE INSERT OR UPDATE OR DELETE
        ON public.pos_mixed_cash_refund FOR EACH ROW
        EXECUTE FUNCTION public.trg_mixed_cash_refund_guard();
      CREATE CONSTRAINT TRIGGER mixed_cash_refund_complete AFTER INSERT ON public.pos_mixed_cash_refund
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION public.trg_mixed_cash_refund_complete();
      CREATE CONSTRAINT TRIGGER mixed_cash_refund_complete AFTER INSERT OR UPDATE ON public.sale
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION public.trg_mixed_cash_refund_complete();
      CREATE CONSTRAINT TRIGGER mixed_cash_refund_complete AFTER INSERT ON public.sale_payment
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION public.trg_mixed_cash_refund_complete();
      CREATE TRIGGER z90_audit AFTER INSERT OR UPDATE OR DELETE ON public.pos_mixed_cash_refund
        FOR EACH ROW EXECUTE FUNCTION public.trg_audit_log();
    """)
    for table in COMPONENT_TABLES:
        events = "INSERT" if table == "batch_movement" else "INSERT OR UPDATE OR DELETE"
        _sql(f"""
          CREATE TRIGGER a04_mixed_cash_refund_components BEFORE {events} ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION public.trg_mixed_cash_refund_components();
        """)


def downgrade() -> None:
    _sql("""
      LOCK TABLE public.pos_mixed_cash_refund IN ACCESS EXCLUSIVE MODE;
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.pos_mixed_cash_refund) THEN
          RAISE EXCEPTION 'mixed_cash_refund_history_exists' USING ERRCODE='55000';
        END IF;
      END $$;
    """)
    for table in COMPONENT_TABLES:
        _sql(f"DROP TRIGGER a04_mixed_cash_refund_components ON public.{table};")
    _sql("""
      DROP TRIGGER mixed_cash_refund_complete ON public.sale;
      DROP TRIGGER mixed_cash_refund_complete ON public.sale_payment;
    """)
    _replace("audit_redact_table_snapshot(text,jsonb)", AUDIT_NEW, AUDIT_OLD)
    _replace("trg_mixed_checkout_return_boundary()", BOUNDARY_NEW, BOUNDARY_OLD)
    for old, new in (
        (TOTAL_OLD, TOTAL_NEW),
        (ITEM_OLD, ITEM_NEW),
        (PROBE_OLD, PROBE_NEW),
        (DECLARATION_OLD, DECLARATION_NEW),
    ):
        _replace("trg_guard_sale_immutability()", new, old)
    _sql("DROP TABLE public.pos_mixed_cash_refund;")
    for signature in reversed(FUNCTIONS):
        _sql(f"DROP FUNCTION public.{signature};")
    _replace("trg_mixed_return_guard()", JOURNAL_NEW, JOURNAL_OLD)
    _sql("ALTER TABLE public.pos_mixed_return_command DROP COLUMN cash_refund_writer_xid;")
