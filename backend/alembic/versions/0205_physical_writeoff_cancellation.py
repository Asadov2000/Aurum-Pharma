"""Fence abandoned physical write-offs without changing inventory.

Revision ID: 0205
Revises: 0204
"""

from __future__ import annotations

import re

from alembic import op
from sqlalchemy import text

revision = "0205"
down_revision = "0204"
branch_labels = None
depends_on = None

CANCEL = "cancel_physical_writeoff(uuid,uuid,uuid,uuid,uuid,text,text,jsonb)"
RESULT = "physical_writeoff_cancel_result(uuid,uuid)"
RECORD = "record_physical_writeoff(uuid,uuid,uuid,uuid,uuid,text,text,jsonb)"
STATUS = "physical_writeoff_status(uuid,uuid,uuid)"
LOCK = (
    " PERFORM pg_advisory_xact_lock(hashtextextended("
    "'physical-writeoff:'||p_tenant||':'||p_operation,0));"
)
FENCE = """
 IF EXISTS(SELECT 1 FROM public.physical_writeoff_cancellation
   WHERE tenant_id=p_tenant AND id=p_operation) THEN
   RAISE EXCEPTION 'physical_writeoff_cancelled' USING ERRCODE='23505';
 END IF;"""
STATUS_OLD = "AND actor_id=public.current_app_user_id()) THEN RETURN NULL; END IF;"
STATUS_NEW = """AND actor_id=public.current_app_user_id()) THEN
   IF EXISTS(SELECT 1 FROM public.physical_writeoff_cancellation
     WHERE tenant_id=p_tenant AND id=p_operation AND branch_id=p_branch
       AND actor_id=public.current_app_user_id()) THEN
     RETURN public.physical_writeoff_cancel_result(p_tenant,p_operation);
   END IF;
   RETURN NULL;
 END IF;"""
AUDIT_OLD = "('physical_writeoff_command','physical_writeoff_entry')"
AUDIT_NEW = (
    "('physical_writeoff_command','physical_writeoff_entry','physical_writeoff_cancellation')"
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
        op.get_bind().exec_driver_sql(sql[start:].strip())


def _patch(signature: str, before: str, after: str) -> None:
    definition = op.get_bind().scalar(
        text("SELECT pg_get_functiondef(CAST(:name AS regprocedure))"), {"name": signature}
    )
    if not isinstance(definition, str) or definition.count(before) != 1:
        raise RuntimeError(f"Unexpected physical cancellation integration: {signature}")
    op.get_bind().exec_driver_sql(definition.replace(before, after, 1))


FUNCTION_SQL = """
CREATE FUNCTION public.physical_writeoff_cancel_result(p_tenant UUID,p_operation UUID)
RETURNS JSONB LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT jsonb_build_object('status','cancelled','operation_id',id,'branch_id',branch_id,
   'lot_id',lot_id,'reason',reason,'comment',comment,'selection',selection,
   'cancelled_at',to_char(created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'))
 FROM public.physical_writeoff_cancellation WHERE tenant_id=p_tenant AND id=p_operation
$$;
CREATE FUNCTION public.cancel_physical_writeoff(p_tenant UUID,p_branch UUID,p_operation UUID,
 p_actor UUID,p_lot UUID,p_reason TEXT,p_comment TEXT,p_selection JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE selection JSONB; hash TEXT; saved_hash TEXT;
BEGIN
 PERFORM public.assert_physical_writeoff_actor(p_tenant,p_branch,true);
 IF p_operation IS NULL OR p_lot IS NULL
   OR p_actor IS DISTINCT FROM public.current_app_user_id() THEN
   RAISE EXCEPTION 'physical_writeoff_actor_denied' USING ERRCODE='42501';
 END IF;
 IF p_reason IS NULL OR p_reason NOT IN ('expired','damaged','spoiled','theft','other')
   OR length(p_comment)>2000 THEN
   RAISE EXCEPTION 'physical_writeoff_reason_invalid' USING ERRCODE='23514';
 END IF;
 selection:=public.physical_writeoff_selection(p_selection);
 hash:=encode(sha256(convert_to(jsonb_build_object('branch',p_branch,'lot',p_lot,
   'actor',p_actor,'reason',p_reason,'comment',p_comment,
   'selection',selection)::TEXT,'UTF8')),'hex');
 PERFORM pg_advisory_xact_lock(
   hashtextextended('physical-writeoff:'||p_tenant||':'||p_operation,0));
 PERFORM public.assert_physical_writeoff_actor(p_tenant,p_branch,true);
 SELECT request_hash INTO saved_hash FROM public.physical_writeoff_command
   WHERE tenant_id=p_tenant AND id=p_operation;
 IF FOUND THEN
   IF saved_hash IS DISTINCT FROM hash THEN
     RAISE EXCEPTION 'physical_writeoff_idempotency_conflict' USING ERRCODE='23505';
   END IF;
   RETURN public.physical_writeoff_result(p_tenant,p_operation);
 END IF;
 SELECT request_hash INTO saved_hash FROM public.physical_writeoff_cancellation
   WHERE tenant_id=p_tenant AND id=p_operation;
 IF FOUND THEN
   IF saved_hash IS DISTINCT FROM hash THEN
     RAISE EXCEPTION 'physical_writeoff_idempotency_conflict' USING ERRCODE='23505';
   END IF;
   RETURN public.physical_writeoff_cancel_result(p_tenant,p_operation);
 END IF;
 IF NOT EXISTS(SELECT 1 FROM public.unit_stock_lot_v2
   WHERE tenant_id=p_tenant AND branch_id=p_branch AND id=p_lot) THEN
   RAISE EXCEPTION 'physical_writeoff_lot_not_found' USING ERRCODE='P0002';
 END IF;
 INSERT INTO public.physical_writeoff_cancellation
   (tenant_id,id,branch_id,lot_id,actor_id,reason,comment,selection,request_hash,
    created_at,created_by)
 VALUES(p_tenant,p_operation,p_branch,p_lot,p_actor,p_reason,p_comment,selection,hash,
   clock_timestamp(),p_actor);
 RETURN public.physical_writeoff_cancel_result(p_tenant,p_operation);
END $$;
"""


def upgrade() -> None:
    _sql("""
      CREATE TABLE public.physical_writeoff_cancellation (
        tenant_id UUID NOT NULL,id UUID NOT NULL,branch_id UUID NOT NULL,lot_id UUID NOT NULL,
        actor_id UUID NOT NULL,reason TEXT NOT NULL
          CHECK(reason IN ('expired','damaged','spoiled','theft','other')),
        comment TEXT CHECK(length(comment)<=2000),selection JSONB NOT NULL,
        request_hash TEXT NOT NULL CHECK(request_hash ~ '^[0-9a-f]{64}$'),
        created_at TIMESTAMPTZ NOT NULL,created_by UUID NOT NULL,
        PRIMARY KEY(tenant_id,id),
        FOREIGN KEY(tenant_id,lot_id) REFERENCES public.unit_stock_lot_v2(tenant_id,id)
      );
      ALTER TABLE public.physical_writeoff_cancellation OWNER TO aurum_schema_owner;
      ALTER TABLE public.physical_writeoff_cancellation ENABLE ROW LEVEL SECURITY;
      ALTER TABLE public.physical_writeoff_cancellation FORCE ROW LEVEL SECURITY;
      REVOKE ALL ON public.physical_writeoff_cancellation FROM PUBLIC,aurum_app,aurum_support;
      GRANT SELECT ON public.physical_writeoff_cancellation TO aurum_support;
      CREATE POLICY schema_owner ON public.physical_writeoff_cancellation
        TO aurum_schema_owner USING(true) WITH CHECK(true);
      CREATE POLICY support_read ON public.physical_writeoff_cancellation
        FOR SELECT TO aurum_support USING(true);
      CREATE TRIGGER a00_tenant_access BEFORE INSERT OR UPDATE OR DELETE
        ON public.physical_writeoff_cancellation FOR EACH ROW
        EXECUTE FUNCTION public.trg_enforce_tenant_access('inventory');
      CREATE TRIGGER a10_physical_writeoff_immutable BEFORE INSERT OR UPDATE OR DELETE
        ON public.physical_writeoff_cancellation FOR EACH ROW
        EXECUTE FUNCTION public.trg_physical_writeoff_immutable();
      CREATE TRIGGER z90_audit AFTER INSERT OR UPDATE OR DELETE
        ON public.physical_writeoff_cancellation FOR EACH ROW
        EXECUTE FUNCTION public.trg_audit_log();
    """)
    _sql(FUNCTION_SQL)
    for signature in (CANCEL, RESULT):
        _sql(f"""
          ALTER FUNCTION public.{signature} OWNER TO aurum_schema_owner;
          REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC,aurum_app,aurum_support;
        """)
    _sql(f"GRANT EXECUTE ON FUNCTION public.{CANCEL} TO aurum_app;")
    _patch(RECORD, LOCK, LOCK + FENCE)
    _patch(STATUS, STATUS_OLD, STATUS_NEW)
    _patch("audit_redact_table_snapshot(text,jsonb)", AUDIT_OLD, AUDIT_NEW)


def downgrade() -> None:
    _sql("""
      LOCK TABLE public.physical_writeoff_cancellation IN ACCESS EXCLUSIVE MODE;
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.physical_writeoff_cancellation) THEN
          RAISE EXCEPTION 'physical_writeoff_cancel_downgrade_requires_empty_history'
            USING ERRCODE='55000';
        END IF;
      END $$;
    """)
    _patch("audit_redact_table_snapshot(text,jsonb)", AUDIT_NEW, AUDIT_OLD)
    _patch(STATUS, STATUS_NEW, STATUS_OLD)
    _patch(RECORD, LOCK + FENCE, LOCK)
    _sql(f"DROP FUNCTION public.{CANCEL}; DROP FUNCTION public.{RESULT};")
    _sql("DROP TABLE public.physical_writeoff_cancellation;")
