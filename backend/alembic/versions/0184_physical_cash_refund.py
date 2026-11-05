"""Atomic cash payout bound to immutable physical quarantine history.

Revision ID: 0184
Revises: 0183
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "0184"
down_revision: str | Sequence[str] | None = "0183"
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


def _replace(old: str, new: str) -> None:
    definition = op.get_bind().scalar(
        text("SELECT pg_get_functiondef('public.trg_guard_sale_immutability()'::regprocedure)")
    )
    if not isinstance(definition, str) or definition.count(old) != 1:
        raise RuntimeError("Physical refund sale guard differs from expected definition")
    op.get_bind().exec_driver_sql(definition.replace(old, new, 1))


DECLARATION_OLD = "  v_physical_cash BOOLEAN := false;"
DECLARATION_NEW = DECLARATION_OLD + "\n  v_physical_refund BOOLEAN := false;"
PROBE_OLD = """  IF current_user = 'aurum_app' AND TG_OP = 'UPDATE' THEN
    SELECT EXISTS(SELECT 1 FROM public.unit_stock_cart_command_v2"""
PROBE_NEW = """  IF current_user = 'aurum_app' AND TG_OP = 'UPDATE' THEN
    SELECT EXISTS(SELECT 1 FROM public.pos_physical_checkout
      WHERE tenant_id=NEW.tenant_id AND sale_id=NEW.parent_sale_id)
      INTO v_physical_refund;
    SELECT EXISTS(SELECT 1 FROM public.unit_stock_cart_command_v2"""
ITEM_OLD = "    IF v_physical_cash THEN"
ITEM_NEW = """    IF v_physical_refund THEN
      IF NOT EXISTS(SELECT 1 FROM public.pos_physical_cash_refund m
        WHERE m.tenant_id=NEW.tenant_id AND m.sale_id=NEW.id
          AND m.actor_id=public.current_app_user_id()
          AND session_user='aurum_app' AND NOT public.is_support_session()
          AND public.tenant_actor_has_scoped_permission(m.tenant_id,'pos.refund',m.branch_id)
          AND (NEW.sale_type,NEW.parent_sale_id,NEW.branch_id,NEW.register_id,NEW.shift_id,
            NEW.cashier_user_id,NEW.operation_id,NEW.operation_hash,NEW.total_amount,NEW.is_test,
            NEW.receipt_snapshot,NEW.receipt_number,NEW.receipt_seq,NEW.completed_at)
          IS NOT DISTINCT FROM ('return',m.parent_sale_id,m.branch_id,m.register_id,m.shift_id,
            m.actor_id,m.id,m.request_hash,(m.result_payload->>'total_amount')::NUMERIC,
            (m.receipt_payload->>'is_test')::BOOLEAN,
            m.receipt_payload,m.receipt_payload->>'receipt_number',
            (m.receipt_payload->>'receipt_sequence')::BIGINT,
            (m.receipt_payload->>'completed_at')::TIMESTAMPTZ)) THEN
        RAISE EXCEPTION 'physical_cash_refund_sale_invalid' USING ERRCODE='23514';
      END IF;
    ELSIF v_physical_cash THEN"""
TOTAL_OLD = "    IF NOT v_physical_cash AND ("
TOTAL_NEW = "    IF NOT v_physical_cash AND NOT v_physical_refund AND ("

FUNCTIONS_SQL = """
CREATE FUNCTION public.trg_physical_cash_refund_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS $$
DECLARE c public.pos_physical_return_command%ROWTYPE; s public.sale%ROWTYPE;
 p public.sale%ROWTYPE; h public.shift%ROWTYPE; m public.pos_physical_checkout%ROWTYPE;
 r RECORD; original JSONB; v_lines JSONB:='[]'::JSONB; v_expected JSONB;
 v_payload TEXT; v_completed TIMESTAMPTZ;
BEGIN
 IF TG_OP<>'INSERT' THEN
  RAISE EXCEPTION 'physical_cash_refund_immutable' USING ERRCODE='55000';
 END IF;
 IF current_user<>'aurum_app' OR session_user<>'aurum_app' OR public.is_support_session()
   OR NEW.tenant_id IS DISTINCT FROM public.current_tenant_id()
   OR NEW.actor_id IS DISTINCT FROM public.current_app_user_id()
   OR NOT EXISTS(SELECT 1 FROM public.app_user WHERE id=NEW.actor_id AND status='active')
   OR NOT coalesce(public.tenant_actor_has_scoped_permission(
     NEW.tenant_id,'pos.refund',NEW.branch_id),false) THEN
  RAISE EXCEPTION 'physical_cash_refund_actor_denied' USING ERRCODE='42501';
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(
   'aurum-physical-return:'||NEW.tenant_id||':'||NEW.id,0));
 SELECT * INTO p FROM public.sale
   WHERE tenant_id=NEW.tenant_id AND id=NEW.parent_sale_id FOR UPDATE;
 SELECT * INTO s FROM public.sale WHERE tenant_id=NEW.tenant_id AND id=NEW.sale_id FOR UPDATE;
 SELECT * INTO c FROM public.pos_physical_return_command
   WHERE tenant_id=NEW.tenant_id AND id=NEW.id;
 SELECT * INTO m FROM public.pos_physical_checkout
   WHERE tenant_id=NEW.tenant_id AND sale_id=NEW.parent_sale_id;
 SELECT * INTO h FROM public.shift WHERE tenant_id=NEW.tenant_id AND id=NEW.shift_id FOR UPDATE;
 PERFORM 1 FROM public.register WHERE tenant_id=NEW.tenant_id AND branch_id=NEW.branch_id
   AND id=NEW.register_id AND is_active FOR SHARE;
 IF NOT FOUND THEN
  RAISE EXCEPTION 'physical_cash_refund_register_inactive' USING ERRCODE='23514';
 END IF;
 IF c.id IS NULL OR m.id IS NULL OR p.id IS NULL OR s.id IS NULL OR h.id IS NULL
   OR c.parent_sale_id<>p.id OR c.parent_checkout_id<>m.id OR c.actor_id<>NEW.actor_id
   OR c.branch_id<>NEW.branch_id OR c.state<>'prepared_quarantine' OR c.total_amount<=0
   OR p.status<>'completed' OR p.sale_type<>'sale' OR p.currency<>'TJS'
   OR p.register_id<>NEW.register_id OR p.branch_id<>NEW.branch_id
   OR s.status<>'draft' OR s.sale_type<>'return' OR s.parent_sale_id IS DISTINCT FROM p.id
   OR (s.branch_id,s.register_id,s.shift_id,s.cashier_user_id,s.operation_id,s.operation_hash,
       s.total_amount,s.currency,s.is_test) IS DISTINCT FROM
      (NEW.branch_id,NEW.register_id,NEW.shift_id,NEW.actor_id,NEW.id,NEW.request_hash,
       c.total_amount,'TJS',p.is_test)
   OR s.refund_attempt_id IS NOT NULL OR s.receipt_snapshot IS NOT NULL
   OR s.receipt_number IS NOT NULL OR s.receipt_seq IS NOT NULL OR s.completed_at IS NOT NULL
   OR (h.status,h.branch_id,h.register_id,h.opened_by_user_id,h.currency) IS DISTINCT FROM
      ('open',NEW.branch_id,NEW.register_id,NEW.actor_id,'TJS')
   OR NEW.payment_id<>NEW.id THEN
  RAISE EXCEPTION 'physical_cash_refund_scope_invalid' USING ERRCODE='23514';
 END IF;
 FOR r IN SELECT * FROM public.pos_physical_return_line
   WHERE tenant_id=NEW.tenant_id AND command_id=NEW.id ORDER BY position LOOP
  SELECT value INTO original FROM jsonb_array_elements(m.result_payload->'lines')
    WHERE value->>'id'=r.line_id::TEXT;
  IF original IS NULL THEN
   RAISE EXCEPTION 'physical_cash_refund_source_invalid' USING ERRCODE='23514';
  END IF;
  v_lines:=v_lines||jsonb_build_array(jsonb_build_object(
    'original_line_id',r.line_id,'position',r.position,'name',original->'name',
    'packaging',original->'packaging','original_sale_format',original->'sale_format',
    'original_retail_unit_price',original->'original_retail_unit_price',
    'sale_format',r.sale_format,'quantity',r.quantity,'base_units',r.base_units,'offset',r."offset",
    'gross_amount',to_char(r.gross_amount,'FM999999999990.00'),
    'discount_amount',to_char(r.discount_amount,'FM999999999990.00'),
    'net_amount',to_char(r.net_amount,'FM999999999990.00')));
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
  RAISE EXCEPTION 'physical_cash_refund_receipt_invalid' USING ERRCODE='23514';
 END IF;
 v_completed:=(NEW.receipt_payload->>'completed_at')::TIMESTAMPTZ;
 IF NOT isfinite(v_completed) OR v_completed<p.completed_at OR v_completed>clock_timestamp() THEN
  RAISE EXCEPTION 'physical_cash_refund_time_invalid' USING ERRCODE='23514';
 END IF;
 v_expected:=jsonb_build_object('schema_version',3,'document_kind','physical_cash_refund_receipt',
   'parent_sale_id',p.id,'parent_receipt_number',m.result_payload->'receipt_number',
   'receipt_number',NEW.receipt_payload->'receipt_number',
   'receipt_sequence',NEW.receipt_payload->'receipt_sequence',
   'completed_at',NEW.receipt_payload->'completed_at','is_test',p.is_test,
   'lines',v_lines,'payment_method','cash','total_amount',to_char(c.total_amount,'FM999999999990.00'),
   'currency','TJS');
 IF NEW.receipt_payload IS DISTINCT FROM v_expected THEN
  RAISE EXCEPTION 'physical_cash_refund_receipt_invalid' USING ERRCODE='23514';
 END IF;
 v_expected:=jsonb_build_object('schema_version',3,'document_kind','physical_cash_refund',
   'operation_id',NEW.id,'parent_sale_id',p.id,'sale_id',s.id,'tenant_id',NEW.tenant_id,
   'branch_id',NEW.branch_id,'register_id',NEW.register_id,'shift_id',NEW.shift_id,
   'actor_id',NEW.actor_id,'payment_id',NEW.id,
   'total_amount',to_char(c.total_amount,'FM999999999990.00'),
   'total_cost_amount',to_char(c.total_cost_amount,'FM999999999990.00'),'receipt',NEW.receipt_payload);
 v_payload:='{"actor_id":"'||NEW.actor_id||'","journal_request_hash":"'||c.request_hash
   ||'","kind":"physical_cash_refund","parent_sale_id":"'||p.id
   ||'","shift_id":"'||NEW.shift_id||'","tenant_id":"'||NEW.tenant_id||'","version":3}';
 IF NEW.result_payload IS DISTINCT FROM v_expected
   OR NEW.request_hash<>encode(sha256(convert_to(v_payload,'UTF8')),'hex') THEN
  RAISE EXCEPTION 'physical_cash_refund_document_invalid' USING ERRCODE='23514';
 END IF;
 IF NOT coalesce(public.tenant_actor_has_scoped_permission(
      NEW.tenant_id,'pos.refund',NEW.branch_id),false)
   OR NOT EXISTS(SELECT 1 FROM public.app_user WHERE id=NEW.actor_id AND status='active') THEN
  RAISE EXCEPTION 'physical_cash_refund_actor_denied' USING ERRCODE='42501';
 END IF;
 NEW.created_at:=statement_timestamp(); NEW.updated_at:=NEW.created_at;
 NEW.created_by:=NEW.actor_id; NEW.updated_by:=NEW.actor_id;
 RETURN NEW;
END $$;

CREATE FUNCTION public.trg_physical_cash_refund_complete() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS $$
DECLARE s public.sale%ROWTYPE; c public.pos_physical_return_command%ROWTYPE;
 p public.sale_payment%ROWTYPE;
BEGIN
 IF current_user<>'aurum_app' OR session_user<>'aurum_app' OR public.is_support_session()
   OR NEW.tenant_id IS DISTINCT FROM public.current_tenant_id()
   OR NEW.actor_id IS DISTINCT FROM public.current_app_user_id()
   OR NOT coalesce(public.tenant_actor_has_scoped_permission(
      NEW.tenant_id,'pos.refund',NEW.branch_id),false)
   OR NOT EXISTS(SELECT 1 FROM public.app_user WHERE id=NEW.actor_id AND status='active') THEN
  RAISE EXCEPTION 'physical_cash_refund_actor_denied' USING ERRCODE='42501';
 END IF;
 SELECT * INTO s FROM public.sale WHERE tenant_id=NEW.tenant_id AND id=NEW.sale_id;
 SELECT * INTO c FROM public.pos_physical_return_command
   WHERE tenant_id=NEW.tenant_id AND id=NEW.id;
 SELECT * INTO p FROM public.sale_payment WHERE tenant_id=NEW.tenant_id AND id=NEW.payment_id;
 IF s.id IS NULL OR c.id IS NULL OR p.id IS NULL OR s.status<>'completed'
   OR (s.sale_type,s.parent_sale_id,s.branch_id,s.register_id,s.shift_id,s.cashier_user_id,
       s.operation_id,s.operation_hash,s.total_amount,s.currency,s.is_test,s.receipt_snapshot,
       s.receipt_number,s.receipt_seq,s.completed_at) IS DISTINCT FROM
      ('return',NEW.parent_sale_id,NEW.branch_id,NEW.register_id,NEW.shift_id,NEW.actor_id,
       NEW.id,NEW.request_hash,c.total_amount,'TJS',
       (NEW.receipt_payload->>'is_test')::BOOLEAN,NEW.receipt_payload,
       NEW.receipt_payload->>'receipt_number',(NEW.receipt_payload->>'receipt_sequence')::BIGINT,
       (NEW.receipt_payload->>'completed_at')::TIMESTAMPTZ)
   OR s.refund_attempt_id IS NOT NULL OR s.fiscal_data IS NOT NULL OR s.marking_codes IS NOT NULL
   OR (p.sale_id,p.payment_method,p.amount,p.currency,p.operation_id,p.operation_hash)
      IS DISTINCT FROM (s.id,'cash',c.total_amount,'TJS',NULL::UUID,NULL::TEXT)
   OR p.payment_attempt_id IS NOT NULL OR p.metadata IS NOT NULL
   OR (SELECT count(*) FROM public.sale_payment WHERE sale_id=s.id)<>1
   OR EXISTS(SELECT 1 FROM public.sale_item WHERE sale_id=s.id)
   OR EXISTS(SELECT 1 FROM public.pos_unit_cart_item WHERE tenant_id=NEW.tenant_id AND sale_id=s.id)
   OR EXISTS(SELECT 1 FROM public.pos_payment_attempt
      WHERE tenant_id=NEW.tenant_id AND sale_id=s.id)
   OR EXISTS(SELECT 1 FROM public.prescription_log WHERE sale_id=s.id) THEN
  RAISE EXCEPTION 'physical_cash_refund_incomplete' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
"""


def upgrade() -> None:
    _sql("""
      CREATE TEMP TABLE physical_cash_refund_reference_grants AS
        SELECT oid::regclass AS relation,
          has_table_privilege('aurum_schema_owner',oid,'REFERENCES') AS granted
        FROM pg_class WHERE oid IN ('public.sale'::regclass,
          'public.pos_physical_return_command'::regclass);
      GRANT REFERENCES ON public.sale,public.pos_physical_return_command TO aurum_schema_owner;
      CREATE TABLE public.pos_physical_cash_refund (
        tenant_id UUID NOT NULL,id UUID NOT NULL,parent_sale_id UUID NOT NULL,sale_id UUID NOT NULL,
        branch_id UUID NOT NULL,register_id UUID NOT NULL,
        shift_id UUID NOT NULL,actor_id UUID NOT NULL,
        payment_id UUID NOT NULL CHECK(payment_id=id),
        request_hash TEXT NOT NULL CHECK(request_hash~'^[0-9a-f]{64}$'),
        result_payload JSONB NOT NULL CHECK(jsonb_typeof(result_payload)='object'),
        receipt_payload JSONB NOT NULL CHECK(jsonb_typeof(receipt_payload)='object'),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        created_by UUID NOT NULL,updated_by UUID NOT NULL,
        PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,sale_id),UNIQUE(tenant_id,payment_id),
        FOREIGN KEY(tenant_id,id) REFERENCES public.pos_physical_return_command(tenant_id,id),
        FOREIGN KEY(tenant_id,parent_sale_id) REFERENCES public.sale(tenant_id,id),
        FOREIGN KEY(tenant_id,sale_id) REFERENCES public.sale(tenant_id,id)
      );
      DO $$ DECLARE r RECORD; BEGIN
        FOR r IN SELECT * FROM pg_temp.physical_cash_refund_reference_grants WHERE NOT granted LOOP
          EXECUTE format('REVOKE REFERENCES ON %s FROM aurum_schema_owner',r.relation);
        END LOOP;
      END $$;
      DROP TABLE pg_temp.physical_cash_refund_reference_grants;
      ALTER TABLE public.pos_physical_cash_refund OWNER TO aurum_schema_owner;
      ALTER TABLE public.pos_physical_cash_refund ENABLE ROW LEVEL SECURITY;
      ALTER TABLE public.pos_physical_cash_refund FORCE ROW LEVEL SECURITY;
      REVOKE ALL ON public.pos_physical_cash_refund FROM PUBLIC,aurum_app,aurum_support;
      GRANT SELECT,INSERT ON public.pos_physical_cash_refund TO aurum_app;
      GRANT SELECT ON public.pos_physical_cash_refund TO aurum_support;
      CREATE POLICY schema_owner ON public.pos_physical_cash_refund TO aurum_schema_owner
        USING(true) WITH CHECK(true);
      CREATE POLICY support ON public.pos_physical_cash_refund
        FOR SELECT TO aurum_support USING(true);
      CREATE POLICY tenant_read ON public.pos_physical_cash_refund FOR SELECT TO aurum_app
        USING(tenant_id=public.current_tenant_id() AND
          public.tenant_actor_has_scoped_permission(tenant_id,'pos.refund',branch_id));
      CREATE POLICY tenant_insert ON public.pos_physical_cash_refund FOR INSERT TO aurum_app
        WITH CHECK(tenant_id=public.current_tenant_id() AND
          public.tenant_actor_has_scoped_permission(tenant_id,'pos.refund',branch_id));
    """)
    _sql(FUNCTIONS_SQL)
    for signature in ("trg_physical_cash_refund_guard()", "trg_physical_cash_refund_complete()"):
        _sql(f"""
          ALTER FUNCTION public.{signature} OWNER TO aurum_schema_owner;
          REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC,aurum_app,aurum_support;
        """)
    _replace(DECLARATION_OLD, DECLARATION_NEW)
    _replace(PROBE_OLD, PROBE_NEW)
    _replace(ITEM_OLD, ITEM_NEW)
    _replace(TOTAL_OLD, TOTAL_NEW)
    _sql("""
      CREATE TRIGGER a00_tenant_access BEFORE INSERT OR UPDATE OR DELETE
        ON public.pos_physical_cash_refund
        FOR EACH ROW EXECUTE FUNCTION public.trg_enforce_tenant_access('pos');
      CREATE TRIGGER a10_physical_cash_refund_guard BEFORE INSERT OR UPDATE OR DELETE
        ON public.pos_physical_cash_refund FOR EACH ROW
        EXECUTE FUNCTION public.trg_physical_cash_refund_guard();
      CREATE CONSTRAINT TRIGGER physical_cash_refund_complete AFTER INSERT
        ON public.pos_physical_cash_refund DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION public.trg_physical_cash_refund_complete();
      CREATE TRIGGER z90_audit AFTER INSERT OR UPDATE OR DELETE ON public.pos_physical_cash_refund
        FOR EACH ROW EXECUTE FUNCTION public.trg_audit_log();
    """)


def downgrade() -> None:
    _sql("""
      LOCK TABLE public.pos_physical_cash_refund IN ACCESS EXCLUSIVE MODE;
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.pos_physical_cash_refund) THEN
          RAISE EXCEPTION 'physical_cash_refund_history_exists' USING ERRCODE='55000';
        END IF;
      END $$;
    """)
    _replace(TOTAL_NEW, TOTAL_OLD)
    _replace(ITEM_NEW, ITEM_OLD)
    _replace(PROBE_NEW, PROBE_OLD)
    _replace(DECLARATION_NEW, DECLARATION_OLD)
    _sql("""
      DROP TABLE public.pos_physical_cash_refund;
      DROP FUNCTION public.trg_physical_cash_refund_complete();
      DROP FUNCTION public.trg_physical_cash_refund_guard();
    """)
