"""Persist immutable electronic refund reviews without authorizing payouts.

Revision ID: 0214
Revises: 0213
"""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType

revision = "0214"
down_revision = "0213"
branch_labels = None
depends_on = None

TABLE = "pos_unit_electronic_refund_intent"
GUARD = "trg_unit_electronic_refund_intent_guard()"
AUDIT_OLD = "IF v_result IS NULL THEN RETURN NULL; END IF;"
AUDIT_NEW = AUDIT_OLD + """
      IF p_table_name='pos_unit_electronic_refund_intent' THEN
        RETURN v_result-ARRAY['request_payload','result_payload'];
      END IF;"""


def _history(filename: str) -> ModuleType:
    path = Path(__file__).with_name(filename)
    spec = spec_from_file_location(f"aurum_0214_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load historical migration {filename}")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sql(sql: str) -> None:
    _history("0171_unit_cash_intents.py")._sql(sql)


GUARD_SQL = """
CREATE FUNCTION public.trg_unit_electronic_refund_intent_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE actor UUID; s public.sale%ROWTYPE; r JSONB; q JSONB; p JSONB;
 total NUMERIC:=0; v_amount NUMERIC; electronic BOOLEAN:=false;
 seen TEXT[]:='{}'; identifier TEXT;
BEGIN
 IF TG_OP<>'INSERT' THEN
  RAISE EXCEPTION 'unit_electronic_refund_intent_immutable' USING ERRCODE='55000';
 END IF;
 actor:=public.current_app_user_id();
 IF session_user<>'aurum_app' OR public.is_support_session()
   OR NEW.tenant_id IS DISTINCT FROM public.current_tenant_id()
   OR NEW.actor_id IS DISTINCT FROM actor OR actor IS NULL
   OR NOT coalesce(public.tenant_actor_has_scoped_permission(
     NEW.tenant_id,'pos.refund',NEW.branch_id),false)
   OR NOT EXISTS(SELECT 1 FROM public.app_user WHERE id=actor AND status='active') THEN
  RAISE EXCEPTION 'unit_electronic_refund_intent_actor_denied' USING ERRCODE='42501';
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(
   'unit-electronic-refund-review:'||NEW.tenant_id||':'||NEW.id,0));
 SELECT * INTO s FROM public.sale
   WHERE tenant_id=NEW.tenant_id AND id=NEW.parent_sale_id;
 IF NOT FOUND OR s.branch_id<>NEW.branch_id OR s.sale_type<>'sale'
   OR s.status<>'completed' OR s.parent_sale_id IS NOT NULL OR s.is_test
   OR s.currency<>'TJS' OR NOT EXISTS(SELECT 1 FROM public.pos_physical_checkout c
     WHERE c.tenant_id=NEW.tenant_id AND c.sale_id=s.id AND c.branch_id=NEW.branch_id
       AND c.payment_journal_version=3) THEN
  RAISE EXCEPTION 'unit_electronic_refund_intent_source_invalid' USING ERRCODE='23514';
 END IF;
 r:=NEW.request_payload; q:=NEW.result_payload;
 IF r-ARRAY['parent_sale_id','selection','expected_total_amount','expected_revision_hash']<>'{}'
   OR NOT (r ?& ARRAY['parent_sale_id','selection','expected_total_amount',
     'expected_revision_hash'])
   OR r->>'parent_sale_id' IS DISTINCT FROM s.id::TEXT
   OR jsonb_typeof(r->'selection'->'items') IS DISTINCT FROM 'array'
   OR jsonb_array_length(r->'selection'->'items')=0
   OR q->>'state' IS DISTINCT FROM 'preview'
   OR q->>'parent_sale_id' IS DISTINCT FROM s.id::TEXT
   OR q->>'branch_id' IS DISTINCT FROM NEW.branch_id::TEXT
   OR q->>'original_receipt_number' IS DISTINCT FROM s.receipt_number
   OR q->>'currency' IS DISTINCT FROM 'TJS'
   OR jsonb_typeof(q->'revision_hash') IS DISTINCT FROM 'string'
   OR (q->>'revision_hash') !~ '^[0-9a-f]{64}$'
   OR r->'expected_revision_hash' IS DISTINCT FROM q->'revision_hash'
   OR r->'expected_total_amount' IS DISTINCT FROM q->'total_amount'
   OR jsonb_typeof(q->'total_amount') IS DISTINCT FROM 'string'
   OR (q->>'total_amount') !~ '^[0-9]{1,12}[.][0-9]{2}$'
   OR (q->>'total_amount')::NUMERIC<=0
   OR jsonb_typeof(q->'payments') IS DISTINCT FROM 'array'
   OR jsonb_typeof(q->'lines') IS DISTINCT FROM 'array' THEN
  RAISE EXCEPTION 'unit_electronic_refund_intent_snapshot_invalid' USING ERRCODE='23514';
 END IF;
 FOR p IN SELECT value FROM jsonb_array_elements(q->'payments') LOOP
  identifier:=p->>'original_payment_id';
  IF identifier IS NULL OR identifier=ANY(seen)
    OR p->>'method' IS NULL OR p->>'method' NOT IN ('cash','card','qr')
    OR p->>'currency' IS DISTINCT FROM 'TJS'
    OR jsonb_typeof(p->'amount') IS DISTINCT FROM 'string'
    OR (p->>'amount') !~ '^[0-9]{1,12}[.][0-9]{2}$' THEN
   RAISE EXCEPTION 'unit_electronic_refund_intent_payment_invalid' USING ERRCODE='23514';
  END IF;
  v_amount:=(p->>'amount')::NUMERIC;
  IF v_amount<=0 OR NOT EXISTS(SELECT 1 FROM public.sale_payment payment
    JOIN public.pos_unit_checkout_payment_component component
      ON component.tenant_id=payment.tenant_id AND component.id=payment.id
    JOIN public.pos_physical_checkout checkout
      ON checkout.tenant_id=component.tenant_id AND checkout.id=component.physical_checkout_id
    WHERE payment.tenant_id=NEW.tenant_id AND payment.sale_id=s.id
      AND payment.id::TEXT=identifier AND payment.payment_method=p->>'method'
      AND payment.currency='TJS' AND v_amount<=payment.amount
      AND component.sale_id=s.id AND component.method=payment.payment_method
      AND component.amount=payment.amount AND component.currency='TJS'
      AND component.mixed_checkout_id IS NULL AND checkout.sale_id=s.id
      AND checkout.payment_journal_version=3) THEN
   RAISE EXCEPTION 'unit_electronic_refund_intent_payment_invalid' USING ERRCODE='23514';
  END IF;
  seen:=array_append(seen,identifier); total:=total+v_amount;
  electronic:=electronic OR p->>'method' IN ('card','qr');
 END LOOP;
 IF NOT electronic OR total<>(q->>'total_amount')::NUMERIC THEN
  RAISE EXCEPTION 'unit_electronic_refund_intent_total_invalid' USING ERRCODE='23514';
 END IF;
 total:=0; seen:='{}';
 IF jsonb_array_length(q->'lines')<>jsonb_array_length(r->'selection'->'items') THEN
  RAISE EXCEPTION 'unit_electronic_refund_intent_lines_invalid' USING ERRCODE='23514';
 END IF;
 FOR p IN SELECT value FROM jsonb_array_elements(q->'lines') LOOP
  identifier:=p->>'line_id';
  IF identifier IS NULL OR identifier=ANY(seen)
    OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(r->'selection'->'items') item
      WHERE item->'line_id'=p->'line_id' AND item->'sale_format'=p->'sale_format'
        AND item->'quantity'=p->'quantity')
    OR NOT EXISTS(SELECT 1 FROM public.pos_physical_checkout checkout,
      LATERAL jsonb_array_elements(checkout.result_payload->'lines') line
      WHERE checkout.tenant_id=NEW.tenant_id AND checkout.sale_id=s.id
        AND line->'id'=p->'line_id')
    OR jsonb_typeof(p->'net_amount') IS DISTINCT FROM 'string'
    OR (p->>'net_amount') !~ '^[0-9]{1,12}[.][0-9]{2}$' THEN
   RAISE EXCEPTION 'unit_electronic_refund_intent_lines_invalid' USING ERRCODE='23514';
  END IF;
  seen:=array_append(seen,identifier); total:=total+(p->>'net_amount')::NUMERIC;
 END LOOP;
 IF total<>(q->>'total_amount')::NUMERIC THEN
  RAISE EXCEPTION 'unit_electronic_refund_intent_total_invalid' USING ERRCODE='23514';
 END IF;
 NEW.created_at:=statement_timestamp(); NEW.created_by:=actor;
 RETURN NEW;
END $$;
"""


def upgrade() -> None:
    _sql("""
      CREATE TEMP TABLE electronic_refund_intent_grants AS
        SELECT oid::regclass::TEXT AS relation,
          has_table_privilege('aurum_schema_owner',oid,'REFERENCES') AS granted
        FROM pg_class WHERE oid IN ('public.tenant'::regclass,'public.branch'::regclass,
          'public.app_user'::regclass,'public.sale'::regclass);
      GRANT REFERENCES ON public.tenant,public.branch,public.app_user,public.sale
        TO aurum_schema_owner;
      CREATE TABLE public.pos_unit_electronic_refund_intent (
        tenant_id UUID NOT NULL REFERENCES public.tenant(id), id UUID NOT NULL,
        parent_sale_id UUID NOT NULL, branch_id UUID NOT NULL,
        actor_id UUID NOT NULL REFERENCES public.app_user(id),
        request_hash TEXT NOT NULL CHECK(request_hash ~ '^[0-9a-f]{64}$'),
        request_payload JSONB NOT NULL CHECK(jsonb_typeof(request_payload)='object'),
        result_payload JSONB NOT NULL CHECK(jsonb_typeof(result_payload)='object'),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        created_by UUID NOT NULL REFERENCES public.app_user(id),
        PRIMARY KEY(tenant_id,id),
        CHECK(substring(id::TEXT,15,1)='4' AND substring(id::TEXT,20,1) IN ('8','9','a','b')),
        FOREIGN KEY(tenant_id,branch_id) REFERENCES public.branch(tenant_id,id),
        FOREIGN KEY(tenant_id,parent_sale_id)
          REFERENCES public.sale(tenant_id,id) ON DELETE RESTRICT
      );
      DO $$ DECLARE r RECORD; BEGIN
        FOR r IN SELECT * FROM pg_temp.electronic_refund_intent_grants WHERE NOT granted LOOP
          EXECUTE format('REVOKE REFERENCES ON TABLE %s FROM aurum_schema_owner',r.relation);
        END LOOP;
      END $$;
      DROP TABLE pg_temp.electronic_refund_intent_grants;
      ALTER TABLE public.pos_unit_electronic_refund_intent OWNER TO aurum_schema_owner;
      ALTER TABLE public.pos_unit_electronic_refund_intent ENABLE ROW LEVEL SECURITY;
      ALTER TABLE public.pos_unit_electronic_refund_intent FORCE ROW LEVEL SECURITY;
      REVOKE ALL ON public.pos_unit_electronic_refund_intent FROM PUBLIC,aurum_app,aurum_support;
      GRANT SELECT,INSERT ON public.pos_unit_electronic_refund_intent TO aurum_app;
      GRANT SELECT ON public.pos_unit_electronic_refund_intent TO aurum_support;
      CREATE POLICY schema_owner ON public.pos_unit_electronic_refund_intent TO aurum_schema_owner
        USING(true) WITH CHECK(true);
      CREATE POLICY support ON public.pos_unit_electronic_refund_intent
        FOR SELECT TO aurum_support USING(true);
      CREATE POLICY tenant_actor ON public.pos_unit_electronic_refund_intent TO aurum_app USING(
        current_user='aurum_app' AND session_user='aurum_app'
        AND NOT public.is_tenant_support_session()
        AND tenant_id=public.current_tenant_id() AND actor_id=public.current_app_user_id()
        AND EXISTS(SELECT 1 FROM public.app_user WHERE id=public.current_app_user_id()
          AND status='active')
        AND public.tenant_actor_has_scoped_permission(tenant_id,'pos.refund',branch_id))
        WITH CHECK(tenant_id=public.current_tenant_id()
        AND actor_id=public.current_app_user_id()
        AND public.tenant_actor_has_scoped_permission(tenant_id,'pos.refund',branch_id));
    """)
    _sql(GUARD_SQL)
    _sql(f"""
      ALTER FUNCTION public.{GUARD} OWNER TO aurum_schema_owner;
      REVOKE ALL ON FUNCTION public.{GUARD} FROM PUBLIC,aurum_app,aurum_support;
    """)
    _history("0205_physical_writeoff_cancellation.py")._patch(
        "public.audit_redact_table_snapshot(text,jsonb)", AUDIT_OLD, AUDIT_NEW
    )
    _sql("""
      CREATE TRIGGER a00_tenant_access BEFORE INSERT OR UPDATE OR DELETE
        ON public.pos_unit_electronic_refund_intent FOR EACH ROW
        EXECUTE FUNCTION public.trg_enforce_tenant_access('pos');
      CREATE TRIGGER a10_unit_electronic_refund_intent_guard BEFORE INSERT OR UPDATE OR DELETE
        ON public.pos_unit_electronic_refund_intent FOR EACH ROW
        EXECUTE FUNCTION public.trg_unit_electronic_refund_intent_guard();
      CREATE TRIGGER z90_unit_electronic_refund_intent_audit AFTER INSERT OR UPDATE OR DELETE
        ON public.pos_unit_electronic_refund_intent FOR EACH ROW
        EXECUTE FUNCTION public.trg_audit_log();
    """)


def downgrade() -> None:
    _sql("""
      LOCK TABLE public.pos_unit_electronic_refund_intent IN ACCESS EXCLUSIVE MODE;
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.pos_unit_electronic_refund_intent) THEN
          RAISE EXCEPTION 'unit_electronic_refund_intent_downgrade_requires_empty_history'
            USING ERRCODE='55000';
        END IF;
      END $$;
      DROP TABLE public.pos_unit_electronic_refund_intent;
    """)
    _history("0205_physical_writeoff_cancellation.py")._patch(
        "public.audit_redact_table_snapshot(text,jsonb)", AUDIT_NEW, AUDIT_OLD
    )
    _sql(f"DROP FUNCTION public.{GUARD};")
