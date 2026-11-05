"""Immutable, resolve-authorized whole-row physical quarantine decisions.

Revision ID: 0198
Revises: 0197
"""

import re

from sqlalchemy import text

from alembic import op

revision = "0198"
down_revision = "0197"
branch_labels = None
depends_on = None

WRITER = (
    "public.record_physical_customer_return_disposition"
    "(UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,TEXT)"
)
PREPARE = "public.physical_disposition_prepare(UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,TEXT)"
SOURCE = "public.physical_disposition_source(UUID,UUID,UUID,UUID)"
FINGERPRINT = "public.physical_disposition_fingerprint(UUID,UUID,UUID,UUID,TEXT,TEXT,TEXT)"
HISTORY = "public.physical_customer_return_disposition_history(UUID,UUID)"
STATUS = "public.physical_customer_return_disposition_status(UUID,UUID,UUID)"

QUEUE_OLD = """WHERE historical.value->>'id'=l.line_id::TEXT"""
QUEUE_NEW = """WHERE NOT EXISTS(SELECT 1 FROM public.physical_customer_return_disposition d
          WHERE d.tenant_id=m.tenant_id AND d.intake_operation_id=m.id
            AND d.line_id=l.line_id AND d.lot_id=q.lot_id)
          AND historical.value->>'id'=l.line_id::TEXT"""


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


AUDIT_OLD = "IF p_table_name = 'mixed_customer_return_disposition' THEN"
AUDIT_NEW = """IF p_table_name = 'physical_customer_return_disposition' THEN
        v_result := jsonb_set(v_result,ARRAY['result_payload'],to_jsonb('***'::TEXT),false);
        v_result := jsonb_set(v_result,ARRAY['comment'],to_jsonb('***'::TEXT),false);
        v_result := jsonb_set(v_result,ARRAY['reason_code'],to_jsonb('***'::TEXT),false);
      END IF;
      """ + AUDIT_OLD


def _redactor(old: str, new: str) -> None:
    definition = op.get_bind().scalar(
        text(
            "SELECT pg_get_functiondef("
            "'public.audit_redact_table_snapshot(text,jsonb)'::regprocedure)"
        )
    )
    if not isinstance(definition, str) or definition.count(old) != 1:
        raise RuntimeError("Physical disposition audit redactor differs from expected definition")
    op.get_bind().exec_driver_sql(definition.replace(old, new, 1))


def _queue(old: str, new: str) -> None:
    definition = op.get_bind().scalar(
        text(
            "SELECT pg_get_functiondef('public.physical_customer_return_queue(uuid,uuid,text)'"
            "::regprocedure)"
        )
    )
    if not isinstance(definition, str) or definition.count(old) != 1:
        raise RuntimeError("Physical pending queue differs from expected definition")
    op.get_bind().exec_driver_sql(definition.replace(old, new, 1))


SOURCE_SQL = """
CREATE FUNCTION public.physical_disposition_source(
 p_tenant UUID,p_intake UUID,p_line UUID,p_lot UUID
) RETURNS JSONB LANGUAGE SQL STABLE SECURITY DEFINER
SET search_path=pg_catalog,pg_temp AS $$
 WITH markers AS (
  SELECT m.tenant_id,m.id,m.parent_sale_id,m.branch_id,m.actor_id,'cash_refund'::TEXT AS kind
  FROM public.pos_physical_cash_refund m
  JOIN public.sale r ON r.tenant_id=m.tenant_id AND r.id=m.sale_id
    AND r.parent_sale_id=m.parent_sale_id AND r.branch_id=m.branch_id
    AND r.status='completed' AND r.sale_type='return' AND NOT r.is_test
    AND r.operation_id=m.id AND r.operation_hash=m.request_hash
    AND r.cashier_user_id=m.actor_id AND r.currency='TJS'
    AND r.receipt_snapshot=m.receipt_payload
  JOIN public.pos_physical_return_command c ON c.tenant_id=m.tenant_id AND c.id=m.id
    AND c.total_amount>0 AND r.total_amount=c.total_amount
  JOIN public.sale_payment pay ON pay.tenant_id=m.tenant_id AND pay.id=m.payment_id
    AND pay.sale_id=r.id AND pay.payment_method='cash' AND pay.amount=c.total_amount
    AND pay.currency='TJS'
  WHERE m.tenant_id=p_tenant AND m.id=p_intake
  UNION ALL
  SELECT m.tenant_id,m.id,m.parent_sale_id,m.branch_id,m.actor_id,'zero_return'::TEXT
  FROM public.pos_physical_zero_return m
  JOIN public.pos_physical_return_command c ON c.tenant_id=m.tenant_id AND c.id=m.id
    AND c.total_amount=0
  WHERE m.tenant_id=p_tenant AND m.id=p_intake
 ), source AS (
  SELECT m.tenant_id,m.id,m.branch_id,m.parent_sale_id,m.kind,
    l.line_id,l.catalog_id,
    historical.value->'packaging' AS packaging
  FROM markers m
  JOIN public.sale p ON p.tenant_id=m.tenant_id AND p.id=m.parent_sale_id
    AND p.branch_id=m.branch_id AND p.status='completed' AND p.sale_type='sale'
    AND NOT p.is_test AND p.parent_sale_id IS NULL AND p.currency='TJS'
  JOIN public.pos_physical_return_command c ON c.tenant_id=m.tenant_id AND c.id=m.id
    AND c.parent_sale_id=p.id AND c.branch_id=m.branch_id AND c.actor_id=m.actor_id
    AND c.state='prepared_quarantine'
  JOIN public.pos_physical_checkout checkout ON checkout.tenant_id=c.tenant_id
    AND checkout.id=c.parent_checkout_id AND checkout.sale_id=p.id
    AND checkout.branch_id=m.branch_id AND checkout.stock_command_id=c.stock_command_id
  JOIN public.pos_physical_return_line l ON l.tenant_id=c.tenant_id AND l.command_id=c.id
    AND l.branch_id=m.branch_id AND l.line_id=p_line
  CROSS JOIN LATERAL jsonb_array_elements(checkout.result_payload->'lines') historical(value)
  WHERE checkout.result_payload->>'schema_version'='3'
    AND checkout.result_payload->>'document_kind'='physical_checkout'
    AND checkout.result_payload->>'tenant_id'=p_tenant::TEXT
    AND checkout.result_payload->>'sale_id'=p.id::TEXT
    AND checkout.result_payload->>'is_test'='false'
    AND historical.value->>'id'=l.line_id::TEXT
    AND historical.value->>'catalog_id'=l.catalog_id::TEXT
    AND coalesce(public.tenant_actor_has_scoped_permission(
      m.tenant_id,'customer_returns.resolve',m.branch_id),false)
 ), projected AS (
  SELECT l.branch_id,l.parent_sale_id,l.kind,
    jsonb_build_object('inventory_kind','physical_unit','intake_operation_id',l.id,
      'line_id',l.line_id,'lot_id',s.lot_id,'base_units',sum(s.base_units)::BIGINT,
      'unit_label',CASE l.packaging->>'unit'
        WHEN 'tablet' THEN 'табл.' WHEN 'capsule' THEN 'капс.' WHEN 'ampoule' THEN 'амп.'
        WHEN 'sachet' THEN 'саше' WHEN 'suppository' THEN 'супп.' END) AS target
  FROM source l
  JOIN public.pos_physical_return_slice s ON s.tenant_id=l.tenant_id AND s.command_id=l.id
    AND s.line_id=l.line_id AND s.branch_id=l.branch_id AND s.lot_id=p_lot
  JOIN public.unit_stock_lot_v2 lot ON lot.tenant_id=s.tenant_id AND lot.id=s.lot_id
    AND lot.branch_id=l.branch_id AND lot.catalog_id=l.catalog_id
  WHERE true
    AND l.packaging->>'version'='2'
    AND l.packaging->>'unit' IN ('tablet','capsule','ampoule','sachet','suppository')
  GROUP BY l.branch_id,l.parent_sale_id,l.kind,l.id,l.line_id,s.lot_id,l.packaging
 )
 SELECT jsonb_build_object('branch_id',branch_id,'parent_sale_id',parent_sale_id,
   'intake_kind',kind,'target',target)
 FROM projected
 WHERE session_user='aurum_app' AND p_tenant=public.current_tenant_id()
   AND NOT coalesce(public.is_support_session(),true)
   AND NOT coalesce(public.is_tenant_support_session(),true)
   AND EXISTS(SELECT 1 FROM public.app_user
     WHERE id=public.current_app_user_id() AND status='active')
$$;
"""

FINGERPRINT_SQL = """
CREATE FUNCTION public.physical_disposition_fingerprint(
 p_tenant UUID,p_intake UUID,p_line UUID,p_lot UUID,
 p_disposition TEXT,p_reason TEXT,p_comment TEXT
) RETURNS TEXT LANGUAGE plpgsql IMMUTABLE SECURITY INVOKER
SET search_path=pg_catalog,pg_temp AS $$
DECLARE v_payload TEXT; v_target TEXT; v_comment TEXT;
 v_trim TEXT:=chr(9)||chr(10)||chr(11)||chr(12)||chr(13)||chr(28)||chr(29)
   ||chr(30)||chr(31)||chr(32)||chr(133)||chr(160)||chr(5760)
   ||chr(8192)||chr(8193)||chr(8194)||chr(8195)||chr(8196)||chr(8197)
   ||chr(8198)||chr(8199)||chr(8200)||chr(8201)||chr(8202)
   ||chr(8232)||chr(8233)||chr(8239)||chr(8287)||chr(12288);
BEGIN
 v_comment:=nullif(btrim(p_comment,v_trim),'');
 v_target:='{"intake_operation_id":"'||p_intake
    ||'","inventory_kind":"physical_unit","line_id":"'||p_line
    ||'","lot_id":"'||p_lot||'"}';
 v_payload:='{"comment_hash":'||CASE WHEN v_comment IS NULL THEN 'null'
    ELSE '"'||encode(sha256(convert_to(v_comment,'UTF8')),'hex')||'"' END
   ||',"disposition_type":'||to_json(p_disposition)::TEXT
   ||',"domain":"physical_customer_return_disposition","reason_code":'||to_json(p_reason)::TEXT
   ||',"target":'||v_target||',"tenant_id":"'||p_tenant||'","version":1}';
 RETURN encode(sha256(convert_to(v_payload,'UTF8')),'hex');
END $$;
"""

PREPARE_SQL = """
CREATE FUNCTION public.physical_disposition_prepare(
 p_tenant UUID,p_decision UUID,p_intake UUID,p_line UUID,p_lot UUID,
 p_disposition TEXT,p_reason TEXT,p_comment TEXT
) RETURNS JSONB LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,pg_temp AS $$
DECLARE v_source JSONB; v_branch UUID; v_comment TEXT; v_hash TEXT;
 v_policy public.tenant_access_policy%ROWTYPE; v_status TEXT;
 v_trim TEXT:=chr(9)||chr(10)||chr(11)||chr(12)||chr(13)||chr(28)||chr(29)
   ||chr(30)||chr(31)||chr(32)||chr(133)||chr(160)||chr(5760)
   ||chr(8192)||chr(8193)||chr(8194)||chr(8195)||chr(8196)||chr(8197)
   ||chr(8198)||chr(8199)||chr(8200)||chr(8201)||chr(8202)
   ||chr(8232)||chr(8233)||chr(8239)||chr(8287)||chr(12288);
BEGIN
 IF session_user<>'aurum_app' OR p_tenant IS DISTINCT FROM public.current_tenant_id()
   OR p_tenant IS NULL OR coalesce(public.is_support_session(),true)
   OR coalesce(public.is_tenant_support_session(),true)
   OR NOT EXISTS(SELECT 1 FROM public.app_user
       WHERE id=public.current_app_user_id() AND status='active') THEN
  RAISE EXCEPTION 'physical_disposition_actor_denied' USING ERRCODE='42501';
 END IF;
 IF current_setting('transaction_isolation')<>'read committed' THEN
  RAISE EXCEPTION 'physical_disposition_isolation_invalid' USING ERRCODE='23514';
 END IF;
 SELECT branch_id INTO v_branch FROM public.pos_physical_return_command
   WHERE tenant_id=p_tenant AND id=p_intake;
 IF v_branch IS NULL THEN
  RAISE EXCEPTION 'physical_disposition_source_missing' USING ERRCODE='P0002';
 END IF;
 IF NOT coalesce(public.tenant_actor_has_scoped_permission(
     p_tenant,'customer_returns.resolve',v_branch),false) THEN
  RAISE EXCEPTION 'physical_disposition_scope_denied' USING ERRCODE='42501';
 END IF;
 PERFORM pg_advisory_xact_lock_shared(hashtextextended('tenant-access:'||p_tenant,0));
 SELECT status INTO v_status FROM public.tenant WHERE id=p_tenant;
 SELECT * INTO v_policy FROM public.tenant_access_policy WHERE tenant_id=p_tenant;
 IF NOT FOUND OR v_status IN ('readonly','suspended','archived') OR (
   (v_policy.expires_at IS NULL OR v_policy.expires_at>statement_timestamp())
   AND (v_policy.mode='readonly' OR 'pos'=ANY(v_policy.blocked_modules))) THEN
  RAISE EXCEPTION 'physical_disposition_access_denied' USING ERRCODE='42501';
 END IF;
 v_comment:=nullif(btrim(p_comment,v_trim),'');
 IF p_decision IS NULL OR p_intake IS NULL OR p_line IS NULL OR p_decision=p_intake
   OR p_disposition IS NULL
   OR p_disposition NOT IN ('disposed','supplier_claim','regulatory_transfer')
   OR p_reason IS NULL OR p_reason NOT IN ('damaged','quality_issue','wrong_item','expired','other')
   OR char_length(v_comment)>2000
   OR p_lot IS NULL THEN
  RAISE EXCEPTION 'physical_disposition_contract_invalid' USING ERRCODE='23514';
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(
   'aurum-physical-disposition-key:'||p_tenant||':'||p_decision,0));
 PERFORM pg_advisory_xact_lock(hashtextextended(
   'aurum-physical-disposition-target:'||p_tenant||':'||p_intake||':'||p_line||':'||p_lot,0));
 v_source:=public.physical_disposition_source(p_tenant,p_intake,p_line,p_lot);
 IF v_source IS NULL THEN
  RAISE EXCEPTION 'physical_disposition_source_missing' USING ERRCODE='P0002';
 END IF;
 v_hash:=public.physical_disposition_fingerprint(p_tenant,p_intake,p_line,p_lot,
   p_disposition,p_reason,v_comment);
 RETURN v_source||jsonb_build_object('version',1,
   'document_kind','physical_customer_return_disposition','state','resolved',
   'decision_operation_id',p_decision,'tenant_id',p_tenant,
   'disposition_type',p_disposition,'reason_code',p_reason,'comment',v_comment,
   'request_hash',v_hash,'actor_id',public.current_app_user_id(),
   'resolved_at',statement_timestamp());
END $$;
"""

WRITER_SQL = """
CREATE FUNCTION public.record_physical_customer_return_disposition(
 p_tenant UUID,p_decision UUID,p_intake UUID,p_line UUID,p_lot UUID,
 p_disposition TEXT,p_reason TEXT,p_comment TEXT
) RETURNS JSONB LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,pg_temp AS $$
DECLARE v_prepared JSONB; v_existing public.physical_customer_return_disposition%ROWTYPE;
 v_result JSONB;
BEGIN
 v_prepared:=public.physical_disposition_prepare(p_tenant,p_decision,p_intake,p_line,p_lot,
   p_disposition,p_reason,p_comment);
 SELECT * INTO v_existing FROM public.physical_customer_return_disposition
   WHERE tenant_id=p_tenant AND id=p_decision;
 IF FOUND THEN
  IF v_existing.request_hash IS DISTINCT FROM v_prepared->>'request_hash'
    OR (v_existing.result_payload-ARRAY['actor_id','resolved_at']) IS DISTINCT FROM
       (v_prepared-ARRAY['actor_id','resolved_at']) THEN
   RAISE EXCEPTION 'physical_disposition_operation_conflict' USING ERRCODE='23505';
  END IF;
  RETURN v_existing.result_payload;
 END IF;
 IF EXISTS(SELECT 1 FROM public.physical_customer_return_disposition
   WHERE tenant_id=p_tenant AND intake_operation_id=p_intake AND line_id=p_line
     AND lot_id=p_lot) THEN
  RAISE EXCEPTION 'physical_disposition_target_conflict' USING ERRCODE='23505';
 END IF;
 INSERT INTO public.physical_customer_return_disposition(
   tenant_id,id,intake_operation_id,line_id,lot_id,
   disposition_type,reason_code,comment
 ) VALUES(p_tenant,p_decision,p_intake,p_line,p_lot,p_disposition,p_reason,p_comment)
 RETURNING result_payload INTO v_result;
 RETURN v_result;
END $$;

CREATE FUNCTION public.trg_physical_disposition_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS $$
DECLARE v_result JSONB; v_target JSONB;
BEGIN
 IF TG_OP<>'INSERT' THEN
  RAISE EXCEPTION 'physical_disposition_immutable' USING ERRCODE='55000';
 END IF;
 v_result:=public.physical_disposition_prepare(NEW.tenant_id,NEW.id,NEW.intake_operation_id,
   NEW.line_id,NEW.lot_id,
   NEW.disposition_type,NEW.reason_code,NEW.comment);
 v_target:=v_result->'target';
 NEW.branch_id:=(v_result->>'branch_id')::UUID;
 NEW.parent_sale_id:=(v_result->>'parent_sale_id')::UUID;
 NEW.intake_kind:=v_result->>'intake_kind';
 NEW.base_units:=(v_target->>'base_units')::BIGINT;
 NEW.unit_label:=v_target->>'unit_label';
 NEW.comment:=v_result->>'comment';
 NEW.request_hash:=v_result->>'request_hash';
 NEW.actor_id:=(v_result->>'actor_id')::UUID;
 NEW.resolved_at:=(v_result->>'resolved_at')::TIMESTAMPTZ;
 NEW.result_payload:=v_result;
 NEW.created_at:=NEW.resolved_at; NEW.updated_at:=NEW.resolved_at;
 NEW.created_by:=NEW.actor_id; NEW.updated_by:=NEW.actor_id;
 RETURN NEW;
END $$;
"""

HISTORY_SQL = """
CREATE FUNCTION public.physical_customer_return_disposition_history(
 p_tenant UUID,p_branch UUID DEFAULT NULL
) RETURNS SETOF JSONB LANGUAGE SQL STABLE SECURITY DEFINER
SET search_path=pg_catalog,pg_temp AS $$
 SELECT d.result_payload FROM public.physical_customer_return_disposition d
 WHERE session_user='aurum_app' AND p_tenant=public.current_tenant_id()
   AND d.tenant_id=p_tenant AND (p_branch IS NULL OR d.branch_id=p_branch)
   AND NOT coalesce(public.is_support_session(),true)
   AND NOT coalesce(public.is_tenant_support_session(),true)
   AND EXISTS(SELECT 1 FROM public.app_user
     WHERE id=public.current_app_user_id() AND status='active')
   AND coalesce(public.tenant_actor_has_scoped_permission(
     p_tenant,'customer_returns.view',d.branch_id),false)
 ORDER BY d.resolved_at DESC,d.id
$$;
"""


STATUS_SQL = """
      CREATE FUNCTION public.physical_customer_return_disposition_status(
        p_tenant UUID,p_branch UUID,p_decision UUID
      ) RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
      SET search_path=pg_catalog,pg_temp AS $$
      DECLARE v_result JSONB;
      BEGIN
        IF session_user<>'aurum_app' OR p_tenant IS NULL OR p_branch IS NULL
          OR p_decision IS NULL OR p_tenant IS DISTINCT FROM public.current_tenant_id()
          OR coalesce(public.is_support_session(),true)
          OR coalesce(public.is_tenant_support_session(),true)
          OR NOT EXISTS(SELECT 1 FROM public.app_user
            WHERE id=public.current_app_user_id() AND status='active')
          OR NOT coalesce(public.tenant_actor_has_scoped_permission(
            p_tenant,'customer_returns.resolve',p_branch),false) THEN
          RAISE EXCEPTION 'physical_disposition_status_denied' USING ERRCODE='42501';
        END IF;
        SELECT result_payload INTO v_result FROM public.physical_customer_return_disposition
          WHERE tenant_id=p_tenant AND branch_id=p_branch AND id=p_decision;
        IF FOUND THEN
          RETURN v_result;
        END IF;
        RETURN jsonb_build_object('state','not_found',
          'decision_operation_id',p_decision,'branch_id',p_branch);
      END $$
    """


def upgrade() -> None:
    _sql("""
      CREATE TEMP TABLE physical_disposition_reference_grants AS
        SELECT oid::regclass AS relation,
          has_table_privilege('aurum_schema_owner',oid,'REFERENCES') AS granted
        FROM pg_class WHERE oid IN ('public.sale'::regclass,
          'public.pos_physical_return_line'::regclass);
      GRANT REFERENCES ON public.sale,public.pos_physical_return_line TO aurum_schema_owner;
      CREATE TABLE public.physical_customer_return_disposition (
        tenant_id UUID NOT NULL,id UUID NOT NULL,intake_operation_id UUID NOT NULL,
        line_id UUID NOT NULL,lot_id UUID NOT NULL,
        branch_id UUID NOT NULL,parent_sale_id UUID NOT NULL,
        intake_kind TEXT NOT NULL CHECK(intake_kind IN ('cash_refund','zero_return')),
        base_units BIGINT NOT NULL CHECK(base_units>0),unit_label TEXT NOT NULL,
        disposition_type TEXT NOT NULL
          CHECK(disposition_type IN ('disposed','supplier_claim','regulatory_transfer')),
        reason_code TEXT NOT NULL
          CHECK(reason_code IN ('damaged','quality_issue','wrong_item','expired','other')),
        comment TEXT CHECK(comment IS NULL OR char_length(comment) BETWEEN 1 AND 2000),
        request_hash TEXT NOT NULL CHECK(request_hash~'^[0-9a-f]{64}$'),
        actor_id UUID NOT NULL,resolved_at TIMESTAMPTZ NOT NULL,result_payload JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,updated_at TIMESTAMPTZ NOT NULL,
        created_by UUID NOT NULL,updated_by UUID NOT NULL,
        PRIMARY KEY(tenant_id,id),CHECK(id<>intake_operation_id),
        FOREIGN KEY(tenant_id,intake_operation_id,line_id)
          REFERENCES public.pos_physical_return_line(tenant_id,command_id,line_id),
        FOREIGN KEY(tenant_id,parent_sale_id) REFERENCES public.sale(tenant_id,id),
        CHECK(unit_label IN ('табл.','капс.','амп.','саше','супп.'))
      );
      CREATE UNIQUE INDEX uq_physical_disposition_target
        ON public.physical_customer_return_disposition
          (tenant_id,intake_operation_id,line_id,lot_id);
      DO $$ DECLARE r RECORD; BEGIN
        FOR r IN SELECT * FROM pg_temp.physical_disposition_reference_grants WHERE NOT granted LOOP
          EXECUTE format('REVOKE REFERENCES ON %s FROM aurum_schema_owner',r.relation);
        END LOOP;
      END $$;
      DROP TABLE pg_temp.physical_disposition_reference_grants;
      ALTER TABLE public.physical_customer_return_disposition OWNER TO aurum_schema_owner;
      ALTER TABLE public.physical_customer_return_disposition ENABLE ROW LEVEL SECURITY;
      ALTER TABLE public.physical_customer_return_disposition FORCE ROW LEVEL SECURITY;
      REVOKE ALL ON public.physical_customer_return_disposition
        FROM PUBLIC,aurum_app,aurum_support;
      GRANT SELECT ON public.physical_customer_return_disposition TO aurum_support;
      CREATE POLICY schema_owner ON public.physical_customer_return_disposition
        TO aurum_schema_owner
        USING(true) WITH CHECK(true);
      CREATE POLICY support_read ON public.physical_customer_return_disposition
        FOR SELECT TO aurum_support USING(true);
      CREATE POLICY tenant_read ON public.physical_customer_return_disposition
        FOR SELECT TO aurum_app
        USING(tenant_id=public.current_tenant_id() AND
          public.tenant_actor_has_scoped_permission(
            tenant_id,'customer_returns.resolve',branch_id));
    """)
    _sql(SOURCE_SQL)
    _sql(FINGERPRINT_SQL)
    _sql(PREPARE_SQL)
    _sql(WRITER_SQL)
    _sql(HISTORY_SQL)
    _sql(STATUS_SQL)
    for signature in (
        SOURCE,
        FINGERPRINT,
        PREPARE,
        WRITER,
        HISTORY,
        STATUS,
        "public.trg_physical_disposition_guard()",
    ):
        op.execute(f"ALTER FUNCTION {signature} OWNER TO aurum_schema_owner")
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC,aurum_app,aurum_support")
    op.execute(f"GRANT EXECUTE ON FUNCTION {WRITER} TO aurum_app")
    op.execute(f"GRANT EXECUTE ON FUNCTION {HISTORY} TO aurum_app")
    op.execute(f"GRANT EXECUTE ON FUNCTION {STATUS} TO aurum_app")
    _sql("""
      CREATE TRIGGER a00_tenant_access BEFORE INSERT OR UPDATE OR DELETE
        ON public.physical_customer_return_disposition FOR EACH ROW
        EXECUTE FUNCTION public.trg_enforce_tenant_access('pos');
      CREATE TRIGGER a10_physical_disposition_guard BEFORE INSERT OR UPDATE OR DELETE
        ON public.physical_customer_return_disposition FOR EACH ROW
        EXECUTE FUNCTION public.trg_physical_disposition_guard();
      CREATE TRIGGER z90_audit AFTER INSERT OR UPDATE OR DELETE
        ON public.physical_customer_return_disposition FOR EACH ROW
        EXECUTE FUNCTION public.trg_audit_log();
    """)
    _redactor(AUDIT_OLD, AUDIT_NEW)
    _queue(QUEUE_OLD, QUEUE_NEW)


def downgrade() -> None:
    _sql("""
      LOCK TABLE public.physical_customer_return_disposition IN ACCESS EXCLUSIVE MODE;
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.physical_customer_return_disposition) THEN
          RAISE EXCEPTION 'physical_disposition_history_exists' USING ERRCODE='55000';
        END IF;
      END $$;
    """)
    _queue(QUEUE_NEW, QUEUE_OLD)
    _redactor(AUDIT_NEW, AUDIT_OLD)
    _sql("DROP TABLE public.physical_customer_return_disposition;")
    for signature in (
        WRITER,
        HISTORY,
        STATUS,
        "public.trg_physical_disposition_guard()",
        PREPARE,
        SOURCE,
        FINGERPRINT,
    ):
        op.execute(f"DROP FUNCTION {signature}")
