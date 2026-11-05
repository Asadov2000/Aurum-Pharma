"""Immutable zero-amount physical returns without sale, receipt or payment creation.

Revision ID: 0186
Revises: 0185
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "0186"
down_revision: str | Sequence[str] | None = "0185"
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


def _redactor(old: str, new: str) -> None:
    definition = op.get_bind().scalar(
        text(
            "SELECT pg_get_functiondef('public.audit_redact_table_snapshot(text,jsonb)'::regprocedure)"
        )
    )
    if not isinstance(definition, str) or definition.count(old) != 1:
        raise RuntimeError("Physical zero return audit redactor differs from expected definition")
    op.get_bind().exec_driver_sql(definition.replace(old, new, 1))


AUDIT_OLD = "IF p_table_name = 'pos_physical_return_command' THEN"
AUDIT_NEW = """IF p_table_name = 'pos_physical_zero_return' THEN
        v_result := jsonb_set(v_result,ARRAY['result_payload'],to_jsonb('***'::TEXT),false);
        v_result := jsonb_set(v_result,ARRAY['comment'],to_jsonb('***'::TEXT),false);
      END IF;
      """ + AUDIT_OLD

GUARD_SQL = """
CREATE FUNCTION public.trg_physical_zero_return_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS $$
DECLARE c public.pos_physical_return_command%ROWTYPE; p public.sale%ROWTYPE;
 v_lines JSONB; v_expected JSONB; v_payload TEXT; v_reason_mode TEXT;
 -- Match Python str.strip(), including Unicode whitespace.
 v_trim TEXT:=chr(9)||chr(10)||chr(11)||chr(12)||chr(13)||chr(28)||chr(29)
   ||chr(30)||chr(31)||chr(32)||chr(133)||chr(160)||chr(5760)
   ||chr(8192)||chr(8193)||chr(8194)||chr(8195)||chr(8196)||chr(8197)
   ||chr(8198)||chr(8199)||chr(8200)||chr(8201)||chr(8202)
   ||chr(8232)||chr(8233)||chr(8239)||chr(8287)||chr(12288);
BEGIN
 IF TG_OP<>'INSERT' THEN
  RAISE EXCEPTION 'physical_zero_return_immutable' USING ERRCODE='55000';
 END IF;
 IF current_user<>'aurum_app' OR session_user<>'aurum_app' OR public.is_support_session()
   OR NEW.tenant_id IS DISTINCT FROM public.current_tenant_id()
   OR NEW.actor_id IS DISTINCT FROM public.current_app_user_id()
   OR NOT EXISTS(SELECT 1 FROM public.app_user WHERE id=NEW.actor_id AND status='active')
   OR NOT coalesce(public.tenant_actor_has_scoped_permission(
     NEW.tenant_id,'pos.refund',NEW.branch_id),false) THEN
  RAISE EXCEPTION 'physical_zero_return_actor_denied' USING ERRCODE='42501';
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(
   'aurum-physical-return:'||NEW.tenant_id||':'||NEW.id,0));
 SELECT * INTO p FROM public.sale
   WHERE tenant_id=NEW.tenant_id AND id=NEW.parent_sale_id FOR UPDATE;
 SELECT * INTO c FROM public.pos_physical_return_command
   WHERE tenant_id=NEW.tenant_id AND id=NEW.id;
 IF p.id IS NULL OR c.id IS NULL
   OR (p.status,p.sale_type,p.currency,p.branch_id) IS DISTINCT FROM
      ('completed','sale','TJS',NEW.branch_id)
   OR (c.parent_sale_id,c.branch_id,c.actor_id,c.state,c.total_amount) IS DISTINCT FROM
      (NEW.parent_sale_id,NEW.branch_id,NEW.actor_id,'prepared_quarantine',0::NUMERIC) THEN
  RAISE EXCEPTION 'physical_zero_return_scope_invalid' USING ERRCODE='23514';
 END IF;
 SELECT refund_reason_mode INTO v_reason_mode FROM public.tenant_settings
   WHERE tenant_id=NEW.tenant_id FOR SHARE;
 IF v_reason_mode IS NULL
   OR v_reason_mode NOT IN ('off','optional','required','required_with_text')
   OR (NEW.reason_code IS NOT NULL AND NEW.reason_code NOT IN (
     'dispensing_error','duplicate_sale','pricing_error','quality_issue',
     'damaged_package','customer_cancelled','other'))
   OR (NEW.comment IS NOT NULL AND (char_length(NEW.comment) NOT BETWEEN 1 AND 500
     OR NEW.comment IS DISTINCT FROM btrim(NEW.comment,v_trim)))
   OR NEW.review_hash IS NULL OR NEW.review_hash !~ '^[0-9a-f]{64}$'
   OR (v_reason_mode='off' AND (NEW.reason_code IS NOT NULL OR NEW.comment IS NOT NULL))
   OR (v_reason_mode IN ('required','required_with_text') AND NEW.reason_code IS NULL)
   OR (v_reason_mode='required_with_text' AND NEW.comment IS NULL) THEN
  RAISE EXCEPTION 'physical_zero_return_reason_invalid' USING ERRCODE='23514';
 END IF;
 SELECT jsonb_agg(jsonb_build_object('line_id',line_id,'base_units',base_units,'offset',"offset")
   ORDER BY position) INTO v_lines FROM public.pos_physical_return_line
   WHERE tenant_id=NEW.tenant_id AND command_id=NEW.id;
 IF v_lines IS NULL OR jsonb_array_length(v_lines) NOT BETWEEN 1 AND 200 THEN
  RAISE EXCEPTION 'physical_zero_return_lines_invalid' USING ERRCODE='23514';
 END IF;
 v_expected:=jsonb_build_object('version',1,'kind','physical_zero_return',
   'operation_id',NEW.id,'parent_sale_id',NEW.parent_sale_id,'tenant_id',NEW.tenant_id,
   'branch_id',NEW.branch_id,'actor_id',NEW.actor_id,'state','prepared_quarantine',
   'currency','TJS','total_amount','0.00','lines',v_lines);
 v_payload:='{"actor_id":"'||NEW.actor_id||'","comment_hash":'
   ||CASE WHEN NEW.comment IS NULL THEN 'null'
     ELSE '"'||encode(sha256(convert_to(NEW.comment,'UTF8')),'hex')||'"' END
   ||',"journal_request_hash":"'||c.request_hash
   ||'","kind":"physical_zero_return","parent_sale_id":"'||NEW.parent_sale_id
   ||'","reason_code":'
   ||CASE WHEN NEW.reason_code IS NULL THEN 'null' ELSE '"'||NEW.reason_code||'"' END
   ||',"review_hash":"'||NEW.review_hash||'","tenant_id":"'||NEW.tenant_id||'","version":1}';
 IF NEW.result_payload IS DISTINCT FROM v_expected
   OR NEW.request_hash IS DISTINCT FROM encode(sha256(convert_to(v_payload,'UTF8')),'hex') THEN
  RAISE EXCEPTION 'physical_zero_return_document_invalid' USING ERRCODE='23514';
 END IF;
 IF NOT coalesce(public.tenant_actor_has_scoped_permission(
     NEW.tenant_id,'pos.refund',NEW.branch_id),false)
   OR NOT EXISTS(SELECT 1 FROM public.app_user WHERE id=NEW.actor_id AND status='active') THEN
  RAISE EXCEPTION 'physical_zero_return_actor_denied' USING ERRCODE='42501';
 END IF;
 NEW.created_at:=statement_timestamp(); NEW.updated_at:=NEW.created_at;
 NEW.created_by:=NEW.actor_id; NEW.updated_by:=NEW.actor_id;
 RETURN NEW;
END $$;
"""


def upgrade() -> None:
    _sql("""
      CREATE TEMP TABLE physical_zero_return_reference_grants AS
        SELECT oid::regclass AS relation,
          has_table_privilege('aurum_schema_owner',oid,'REFERENCES') AS granted
        FROM pg_class WHERE oid IN ('public.sale'::regclass,
          'public.pos_physical_return_command'::regclass);
      GRANT REFERENCES ON public.sale,public.pos_physical_return_command TO aurum_schema_owner;
      CREATE TABLE public.pos_physical_zero_return (
        tenant_id UUID NOT NULL,id UUID NOT NULL,parent_sale_id UUID NOT NULL,
        branch_id UUID NOT NULL,actor_id UUID NOT NULL,
        request_hash TEXT NOT NULL CHECK(request_hash~'^[0-9a-f]{64}$'),
        reason_code TEXT CHECK(reason_code IS NULL OR reason_code IN (
          'dispensing_error','duplicate_sale','pricing_error','quality_issue',
          'damaged_package','customer_cancelled','other')),
        comment TEXT CHECK(comment IS NULL OR char_length(comment) BETWEEN 1 AND 500),
        review_hash TEXT NOT NULL CHECK(review_hash~'^[0-9a-f]{64}$'),
        result_payload JSONB NOT NULL CHECK(jsonb_typeof(result_payload)='object'),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        created_by UUID NOT NULL,updated_by UUID NOT NULL,
        PRIMARY KEY(tenant_id,id),
        FOREIGN KEY(tenant_id,id) REFERENCES public.pos_physical_return_command(tenant_id,id),
        FOREIGN KEY(tenant_id,parent_sale_id) REFERENCES public.sale(tenant_id,id)
      );
      DO $$ DECLARE r RECORD; BEGIN
        FOR r IN SELECT * FROM pg_temp.physical_zero_return_reference_grants WHERE NOT granted LOOP
          EXECUTE format('REVOKE REFERENCES ON %s FROM aurum_schema_owner',r.relation);
        END LOOP;
      END $$;
      DROP TABLE pg_temp.physical_zero_return_reference_grants;
      ALTER TABLE public.pos_physical_zero_return OWNER TO aurum_schema_owner;
      ALTER TABLE public.pos_physical_zero_return ENABLE ROW LEVEL SECURITY;
      ALTER TABLE public.pos_physical_zero_return FORCE ROW LEVEL SECURITY;
      REVOKE ALL ON public.pos_physical_zero_return FROM PUBLIC,aurum_app,aurum_support;
      GRANT SELECT,INSERT ON public.pos_physical_zero_return TO aurum_app;
      GRANT SELECT ON public.pos_physical_zero_return TO aurum_support;
      CREATE POLICY schema_owner ON public.pos_physical_zero_return TO aurum_schema_owner
        USING(true) WITH CHECK(true);
      CREATE POLICY support ON public.pos_physical_zero_return
        FOR SELECT TO aurum_support USING(true);
      CREATE POLICY tenant_read ON public.pos_physical_zero_return FOR SELECT TO aurum_app
        USING(tenant_id=public.current_tenant_id() AND
          public.tenant_actor_has_scoped_permission(tenant_id,'pos.refund',branch_id));
      CREATE POLICY tenant_insert ON public.pos_physical_zero_return FOR INSERT TO aurum_app
        WITH CHECK(tenant_id=public.current_tenant_id() AND
          public.tenant_actor_has_scoped_permission(tenant_id,'pos.refund',branch_id));
    """)
    _sql(GUARD_SQL)
    _sql("""
      ALTER FUNCTION public.trg_physical_zero_return_guard() OWNER TO aurum_schema_owner;
      REVOKE ALL ON FUNCTION public.trg_physical_zero_return_guard()
        FROM PUBLIC,aurum_app,aurum_support;
      CREATE TRIGGER a00_tenant_access BEFORE INSERT OR UPDATE OR DELETE
        ON public.pos_physical_zero_return
        FOR EACH ROW EXECUTE FUNCTION public.trg_enforce_tenant_access('pos');
      CREATE TRIGGER a10_physical_zero_return_guard BEFORE INSERT OR UPDATE OR DELETE
        ON public.pos_physical_zero_return FOR EACH ROW
        EXECUTE FUNCTION public.trg_physical_zero_return_guard();
      CREATE TRIGGER z90_audit AFTER INSERT OR UPDATE OR DELETE ON public.pos_physical_zero_return
        FOR EACH ROW EXECUTE FUNCTION public.trg_audit_log();
    """)
    _redactor(AUDIT_OLD, AUDIT_NEW)


def downgrade() -> None:
    _sql("""
      LOCK TABLE public.pos_physical_zero_return IN ACCESS EXCLUSIVE MODE;
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.pos_physical_zero_return) THEN
          RAISE EXCEPTION 'physical_zero_return_history_exists' USING ERRCODE='55000';
        END IF;
      END $$;
    """)
    _redactor(AUDIT_NEW, AUDIT_OLD)
    _sql("""
      DROP TABLE public.pos_physical_zero_return;
      DROP FUNCTION public.trg_physical_zero_return_guard();
    """)
