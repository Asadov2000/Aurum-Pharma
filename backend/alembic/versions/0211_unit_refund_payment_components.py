"""Journal immutable cash refund components against their original payment.

Revision ID: 0211
Revises: 0210
"""

from __future__ import annotations

from collections.abc import Sequence
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from sqlalchemy import text

from alembic import op

revision: str = "0211"
down_revision: str | Sequence[str] | None = "0210"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "pos_unit_refund_payment_component"
FUNCTIONS = (
    "assert_unit_refund_payment_components(uuid,uuid,boolean)",
    "trg_unit_refund_payment_component_guard()",
    "trg_unit_refund_payment_component_complete()",
)
AUDIT_OLD = "IF v_result IS NULL THEN RETURN NULL; END IF;"
AUDIT_NEW = AUDIT_OLD + """
      IF p_table_name='pos_unit_refund_payment_component' THEN
        RETURN jsonb_build_object('created_at',v_result->'created_at',
          'created_by',v_result->'created_by');
      END IF;"""


def _sql(sql: str) -> None:
    path = Path(__file__).with_name("0171_unit_cash_intents.py")
    spec = spec_from_file_location("aurum_0211_sql", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load historical SQL executor")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    module._sql(sql)


def _patch_audit(*, upgrade: bool) -> None:
    definition = op.get_bind().scalar(
        text("SELECT pg_get_functiondef(CAST(:signature AS regprocedure))"),
        {"signature": "public.audit_redact_table_snapshot(text,jsonb)"},
    )
    old, new = (AUDIT_OLD, AUDIT_NEW) if upgrade else (AUDIT_NEW, AUDIT_OLD)
    if not isinstance(definition, str) or definition.count(old) != 1:
        raise RuntimeError("Historical audit redactor changed")
    op.get_bind().exec_driver_sql(definition.replace(old, new, 1))


SQL = """
CREATE FUNCTION public.assert_unit_refund_payment_components(
 p_tenant UUID,p_sale UUID,p_mixed BOOLEAN) RETURNS VOID
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE m RECORD; s public.sale%ROWTYPE; original public.sale_payment%ROWTYPE;
 c public.pos_unit_refund_payment_component%ROWTYPE; source_payments JSONB; refunded NUMERIC;
BEGIN
 IF p_mixed THEN
  SELECT * INTO m FROM public.pos_mixed_cash_refund
    WHERE tenant_id=p_tenant AND sale_id=p_sale;
 ELSE
  SELECT * INTO m FROM public.pos_physical_cash_refund
    WHERE tenant_id=p_tenant AND sale_id=p_sale;
 END IF;
 IF m.id IS NULL THEN
  RAISE EXCEPTION 'unit_refund_component_parent_missing' USING ERRCODE='23514';
 END IF;
 IF m.payment_journal_version=1 THEN
  IF EXISTS(SELECT 1 FROM public.pos_unit_refund_payment_component
    WHERE tenant_id=p_tenant AND sale_id=p_sale) THEN
   RAISE EXCEPTION 'unit_refund_component_legacy_parent' USING ERRCODE='23514';
  END IF;
  RETURN;
 END IF;
 IF current_setting('transaction_isolation')<>'read committed' THEN
  RAISE EXCEPTION 'unit_refund_component_isolation_invalid' USING ERRCODE='25000';
 END IF;
 -- The original sale is the shared serialization point for every refund writer.
 PERFORM 1 FROM public.sale WHERE tenant_id=p_tenant AND id=m.parent_sale_id FOR UPDATE;
 SELECT * INTO s FROM public.sale WHERE tenant_id=p_tenant AND id=p_sale;
 SELECT * INTO c FROM public.pos_unit_refund_payment_component
   WHERE tenant_id=p_tenant AND sale_id=p_sale;
 SELECT * INTO original FROM public.sale_payment
   WHERE tenant_id=p_tenant AND sale_id=m.parent_sale_id;
 IF p_mixed THEN
  SELECT result_payload->'payments' INTO source_payments FROM public.pos_mixed_checkout
    WHERE tenant_id=p_tenant AND sale_id=m.parent_sale_id;
 ELSE
  SELECT result_payload->'payments' INTO source_payments FROM public.pos_physical_checkout
    WHERE tenant_id=p_tenant AND sale_id=m.parent_sale_id;
 END IF;
 IF c.id IS NULL OR original.id IS NULL OR s.id IS NULL
   OR (SELECT count(*) FROM public.pos_unit_refund_payment_component
       WHERE tenant_id=p_tenant AND sale_id=p_sale)<>1
   OR (SELECT count(*) FROM public.sale_payment WHERE sale_id=m.parent_sale_id)<>1
   OR original.payment_method<>'cash' OR original.currency<>'TJS'
   OR original.payment_attempt_id IS NOT NULL
   OR source_payments IS DISTINCT FROM jsonb_build_array(jsonb_build_object(
     'id',original.id,'method','cash','amount',to_char(original.amount,'FM999999999990.00'),
     'currency','TJS'))
   OR (c.parent_sale_id,c.original_payment_id,c.id,c.physical_refund_id,c.mixed_refund_id,
       c.method,c.amount,c.currency,c.created_by) IS DISTINCT FROM
      (m.parent_sale_id,original.id,m.payment_id,
       CASE WHEN p_mixed THEN NULL::UUID ELSE m.id END,
       CASE WHEN p_mixed THEN m.id ELSE NULL::UUID END,
       'cash',(m.result_payload->>'total_amount')::NUMERIC,'TJS',m.actor_id)
   OR (s.sale_type,s.parent_sale_id,s.status,s.total_amount,s.currency,s.cashier_user_id,
       s.receipt_snapshot) IS DISTINCT FROM
      ('return',m.parent_sale_id,'completed',c.amount,'TJS',m.actor_id,m.receipt_payload)
   OR (m.receipt_payload->>'total_amount')::NUMERIC IS DISTINCT FROM c.amount
   OR m.receipt_payload->>'payment_method' IS DISTINCT FROM 'cash'
   OR m.receipt_payload->>'currency' IS DISTINCT FROM 'TJS'
   OR NOT EXISTS(SELECT 1 FROM public.sale_payment p
     WHERE p.tenant_id=p_tenant AND p.id=c.id AND p.sale_id=p_sale
       AND (p.payment_method,p.amount,p.currency,p.payment_attempt_id)
         IS NOT DISTINCT FROM ('cash',c.amount,'TJS',NULL::UUID))
   OR (SELECT count(*) FROM public.sale_payment WHERE sale_id=p_sale)<>1 THEN
  RAISE EXCEPTION 'unit_refund_payment_components_invalid' USING ERRCODE='23514';
 END IF;
 -- Count actual immutable payout facts, including historical v1 refund markers.
 -- Counting only components would allow a new journal to ignore old refunds.
 SELECT coalesce(sum(p.amount),0) INTO refunded FROM public.sale_payment p
   JOIN public.sale r ON r.tenant_id=p.tenant_id AND r.id=p.sale_id
   WHERE p.tenant_id=p_tenant AND r.parent_sale_id=m.parent_sale_id
     AND r.sale_type='return' AND r.status='completed' AND (
       EXISTS(SELECT 1 FROM public.pos_physical_cash_refund f
         WHERE f.tenant_id=r.tenant_id AND f.sale_id=r.id)
       OR EXISTS(SELECT 1 FROM public.pos_mixed_cash_refund f
         WHERE f.tenant_id=r.tenant_id AND f.sale_id=r.id));
 IF refunded>original.amount THEN
  RAISE EXCEPTION 'unit_refund_payment_amount_exceeded' USING ERRCODE='23514';
 END IF;
END $$;
CREATE FUNCTION public.trg_unit_refund_payment_component_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE m RECORD; s public.sale%ROWTYPE; original public.sale_payment%ROWTYPE;
 source_payments JSONB;
BEGIN
 IF TG_OP<>'INSERT' THEN
  RAISE EXCEPTION 'unit_refund_payment_component_immutable' USING ERRCODE='55000';
 END IF;
 IF current_setting('transaction_isolation')<>'read committed' THEN
  RAISE EXCEPTION 'unit_refund_component_isolation_invalid' USING ERRCODE='25000';
 END IF;
 IF session_user<>'aurum_app' OR public.is_support_session()
   OR NEW.tenant_id IS DISTINCT FROM public.current_tenant_id()
   OR public.current_app_user_id() IS NULL THEN
  RAISE EXCEPTION 'unit_refund_payment_component_actor_denied' USING ERRCODE='42501';
 END IF;
 IF NEW.physical_refund_id IS NOT NULL AND NEW.mixed_refund_id IS NULL THEN
  SELECT * INTO m FROM public.pos_physical_cash_refund
    WHERE tenant_id=NEW.tenant_id AND id=NEW.physical_refund_id;
  SELECT result_payload->'payments' INTO source_payments FROM public.pos_physical_checkout
    WHERE tenant_id=NEW.tenant_id AND sale_id=NEW.parent_sale_id;
 ELSIF NEW.mixed_refund_id IS NOT NULL AND NEW.physical_refund_id IS NULL THEN
  SELECT * INTO m FROM public.pos_mixed_cash_refund
    WHERE tenant_id=NEW.tenant_id AND id=NEW.mixed_refund_id;
  SELECT result_payload->'payments' INTO source_payments FROM public.pos_mixed_checkout
    WHERE tenant_id=NEW.tenant_id AND sale_id=NEW.parent_sale_id;
 ELSE
  RAISE EXCEPTION 'unit_refund_payment_component_source_invalid' USING ERRCODE='23514';
 END IF;
 IF m.id IS NULL OR m.payment_journal_version IS DISTINCT FROM 2
   OR (m.parent_sale_id,m.sale_id,m.payment_id) IS DISTINCT FROM
      (NEW.parent_sale_id,NEW.sale_id,NEW.id)
   OR NEW.method<>'cash' OR NEW.currency<>'TJS'
   OR (m.result_payload->>'total_amount')::NUMERIC IS DISTINCT FROM NEW.amount THEN
  RAISE EXCEPTION 'unit_refund_payment_component_source_invalid' USING ERRCODE='23514';
 END IF;
 IF m.actor_id IS DISTINCT FROM public.current_app_user_id()
   OR NOT coalesce(public.tenant_actor_has_scoped_permission(
      NEW.tenant_id,'pos.refund',m.branch_id),false)
   OR NOT EXISTS(SELECT 1 FROM public.app_user WHERE id=m.actor_id AND status='active') THEN
  RAISE EXCEPTION 'unit_refund_payment_component_actor_denied' USING ERRCODE='42501';
 END IF;
 SELECT * INTO s FROM public.sale
   WHERE tenant_id=NEW.tenant_id AND id=NEW.parent_sale_id FOR UPDATE;
 SELECT * INTO original FROM public.sale_payment
   WHERE tenant_id=NEW.tenant_id AND id=NEW.original_payment_id
     AND sale_id=NEW.parent_sale_id;
 -- Validate every tenant-scoped source before any global-id foreign key check.
 IF s.id IS NULL OR (s.status,s.sale_type,s.parent_sale_id,s.currency)
      IS DISTINCT FROM ('completed','sale',NULL::UUID,'TJS')
   OR original.id IS NULL OR original.payment_method<>'cash' OR original.currency<>'TJS'
   OR original.payment_attempt_id IS NOT NULL OR NEW.amount>original.amount
   OR (SELECT count(*) FROM public.sale_payment WHERE sale_id=NEW.parent_sale_id)<>1
   OR source_payments IS DISTINCT FROM jsonb_build_array(jsonb_build_object(
     'id',original.id,'method','cash','amount',to_char(original.amount,'FM999999999990.00'),
     'currency','TJS'))
   OR NOT EXISTS(SELECT 1 FROM public.sale r
     WHERE r.tenant_id=NEW.tenant_id AND r.id=NEW.sale_id AND r.sale_type='return'
       AND r.parent_sale_id=NEW.parent_sale_id AND r.status='draft'
       AND r.cashier_user_id=m.actor_id AND r.branch_id=m.branch_id)
   OR NOT EXISTS(SELECT 1 FROM public.sale_payment p
     WHERE p.tenant_id=NEW.tenant_id AND p.id=NEW.id AND p.sale_id=NEW.sale_id
       AND (p.payment_method,p.amount,p.currency,p.payment_attempt_id)
         IS NOT DISTINCT FROM ('cash',NEW.amount,'TJS',NULL::UUID)) THEN
  RAISE EXCEPTION 'unit_refund_payment_component_source_invalid' USING ERRCODE='23514';
 END IF;
 NEW.created_at:=clock_timestamp(); NEW.created_by:=m.actor_id;
 RETURN NEW;
END $$;
CREATE FUNCTION public.trg_unit_refund_payment_component_complete() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE mixed BOOLEAN;
BEGIN
 IF TG_TABLE_NAME='pos_unit_refund_payment_component' THEN
  mixed:=NEW.mixed_refund_id IS NOT NULL;
 ELSE
  mixed:=TG_TABLE_NAME='pos_mixed_cash_refund';
 END IF;
 PERFORM public.assert_unit_refund_payment_components(NEW.tenant_id,NEW.sale_id,mixed);
 RETURN NEW;
END $$;
"""


def upgrade() -> None:
    _sql("""
      CREATE TEMP TABLE unit_refund_component_reference_grants AS
        SELECT oid::regclass::TEXT AS relation,
          has_table_privilege('aurum_schema_owner',oid,'REFERENCES') AS granted
        FROM pg_class WHERE oid IN ('public.sale_payment'::regclass,'public.sale'::regclass,
          'public.app_user'::regclass,'public.pos_physical_cash_refund'::regclass,
          'public.pos_mixed_cash_refund'::regclass);
      GRANT REFERENCES ON public.sale_payment,public.sale,public.app_user,
        public.pos_physical_cash_refund,public.pos_mixed_cash_refund TO aurum_schema_owner;
      ALTER TABLE public.pos_physical_cash_refund
        ADD COLUMN payment_journal_version SMALLINT NOT NULL DEFAULT 1,
        ADD CONSTRAINT ck_physical_refund_payment_journal_version
          CHECK(payment_journal_version IN (1,2));
      ALTER TABLE public.pos_mixed_cash_refund
        ADD COLUMN payment_journal_version SMALLINT NOT NULL DEFAULT 1,
        ADD CONSTRAINT ck_mixed_refund_payment_journal_version
          CHECK(payment_journal_version IN (1,2));
      CREATE TABLE public.pos_unit_refund_payment_component (
        tenant_id UUID NOT NULL,id UUID NOT NULL,parent_sale_id UUID NOT NULL,
        sale_id UUID NOT NULL,original_payment_id UUID NOT NULL,
        physical_refund_id UUID,mixed_refund_id UUID,
        method TEXT NOT NULL CHECK(method IN ('cash','card','qr')),
        amount NUMERIC(14,2) NOT NULL CHECK(amount>0 AND amount<=999999999999.99),
        currency TEXT NOT NULL CHECK(currency='TJS'),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        created_by UUID NOT NULL REFERENCES public.app_user(id),
        PRIMARY KEY(tenant_id,id),
        CONSTRAINT ck_unit_refund_component_parent
          CHECK((physical_refund_id IS NULL)<>(mixed_refund_id IS NULL)),
        CONSTRAINT fk_unit_refund_component_sale FOREIGN KEY(tenant_id,sale_id)
          REFERENCES public.sale(tenant_id,id),
        CONSTRAINT fk_unit_refund_component_parent_sale FOREIGN KEY(tenant_id,parent_sale_id)
          REFERENCES public.sale(tenant_id,id),
        CONSTRAINT fk_unit_refund_component_payment FOREIGN KEY(id)
          REFERENCES public.sale_payment(id) DEFERRABLE INITIALLY DEFERRED,
        CONSTRAINT fk_unit_refund_component_original FOREIGN KEY(original_payment_id)
          REFERENCES public.sale_payment(id) DEFERRABLE INITIALLY DEFERRED,
        CONSTRAINT fk_unit_refund_component_physical FOREIGN KEY(tenant_id,physical_refund_id)
          REFERENCES public.pos_physical_cash_refund(tenant_id,id) DEFERRABLE INITIALLY DEFERRED,
        CONSTRAINT fk_unit_refund_component_mixed FOREIGN KEY(tenant_id,mixed_refund_id)
          REFERENCES public.pos_mixed_cash_refund(tenant_id,id) DEFERRABLE INITIALLY DEFERRED
      );
      DO $$ DECLARE r RECORD; BEGIN
        FOR r IN SELECT * FROM pg_temp.unit_refund_component_reference_grants
          WHERE NOT granted LOOP
          EXECUTE format('REVOKE REFERENCES ON %s FROM aurum_schema_owner',r.relation);
        END LOOP;
      END $$;
      DROP TABLE pg_temp.unit_refund_component_reference_grants;
      ALTER TABLE public.pos_unit_refund_payment_component OWNER TO aurum_schema_owner;
      ALTER TABLE public.pos_unit_refund_payment_component ENABLE ROW LEVEL SECURITY;
      ALTER TABLE public.pos_unit_refund_payment_component FORCE ROW LEVEL SECURITY;
      REVOKE ALL ON public.pos_unit_refund_payment_component FROM PUBLIC,aurum_app,aurum_support;
      GRANT SELECT,INSERT ON public.pos_unit_refund_payment_component TO aurum_app;
      GRANT SELECT ON public.pos_unit_refund_payment_component TO aurum_support;
      CREATE POLICY schema_owner ON public.pos_unit_refund_payment_component TO aurum_schema_owner
        USING(true) WITH CHECK(true);
      CREATE POLICY support ON public.pos_unit_refund_payment_component
        FOR SELECT TO aurum_support USING(true);
      CREATE POLICY tenant_actor ON public.pos_unit_refund_payment_component TO aurum_app
        USING(tenant_id=public.current_tenant_id() AND EXISTS(
          SELECT 1 FROM public.sale s
            WHERE s.tenant_id=pos_unit_refund_payment_component.tenant_id
            AND s.id=pos_unit_refund_payment_component.sale_id
            AND s.cashier_user_id=public.current_app_user_id()
            AND public.tenant_actor_has_scoped_permission(s.tenant_id,'pos.refund',s.branch_id)))
        WITH CHECK(tenant_id=public.current_tenant_id() AND EXISTS(
          SELECT 1 FROM public.sale s
            WHERE s.tenant_id=pos_unit_refund_payment_component.tenant_id
            AND s.id=pos_unit_refund_payment_component.sale_id
            AND s.cashier_user_id=public.current_app_user_id()
            AND public.tenant_actor_has_scoped_permission(s.tenant_id,'pos.refund',s.branch_id)));
      CREATE INDEX ix_unit_refund_component_sale
        ON public.pos_unit_refund_payment_component(tenant_id,sale_id);
      CREATE INDEX ix_unit_refund_component_original
        ON public.pos_unit_refund_payment_component(tenant_id,original_payment_id);
      CREATE INDEX ix_unit_refund_component_physical
        ON public.pos_unit_refund_payment_component(tenant_id,physical_refund_id)
        WHERE physical_refund_id IS NOT NULL;
      CREATE INDEX ix_unit_refund_component_mixed
        ON public.pos_unit_refund_payment_component(tenant_id,mixed_refund_id)
        WHERE mixed_refund_id IS NOT NULL;
    """)
    _sql(SQL)
    for signature in FUNCTIONS:
        _sql(f"""
          ALTER FUNCTION public.{signature} OWNER TO aurum_schema_owner;
          REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC,aurum_app,aurum_support;
        """)
    _patch_audit(upgrade=True)
    _sql("""
      CREATE TRIGGER a00_tenant_access BEFORE INSERT OR UPDATE OR DELETE
        ON public.pos_unit_refund_payment_component FOR EACH ROW
        EXECUTE FUNCTION public.trg_enforce_tenant_access('pos');
      CREATE TRIGGER a10_unit_refund_component_guard BEFORE INSERT OR UPDATE OR DELETE
        ON public.pos_unit_refund_payment_component FOR EACH ROW
        EXECUTE FUNCTION public.trg_unit_refund_payment_component_guard();
      CREATE CONSTRAINT TRIGGER unit_refund_component_complete
        AFTER INSERT ON public.pos_unit_refund_payment_component
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION public.trg_unit_refund_payment_component_complete();
      CREATE CONSTRAINT TRIGGER unit_refund_payment_components_complete
        AFTER INSERT ON public.pos_physical_cash_refund
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION public.trg_unit_refund_payment_component_complete();
      CREATE CONSTRAINT TRIGGER unit_refund_payment_components_complete
        AFTER INSERT ON public.pos_mixed_cash_refund
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION public.trg_unit_refund_payment_component_complete();
      CREATE TRIGGER z90_unit_refund_component_audit AFTER INSERT OR UPDATE OR DELETE
        ON public.pos_unit_refund_payment_component FOR EACH ROW
        EXECUTE FUNCTION public.trg_audit_log();
    """)


def downgrade() -> None:
    _sql("""
      LOCK TABLE public.pos_unit_refund_payment_component,public.pos_physical_cash_refund,
        public.pos_mixed_cash_refund IN ACCESS EXCLUSIVE MODE;
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.pos_unit_refund_payment_component)
          OR EXISTS(SELECT 1 FROM public.pos_physical_cash_refund WHERE payment_journal_version=2)
          OR EXISTS(SELECT 1 FROM public.pos_mixed_cash_refund WHERE payment_journal_version=2) THEN
          RAISE EXCEPTION 'unit_refund_components_downgrade_requires_empty_history'
            USING ERRCODE='55000';
        END IF;
      END $$;
      DROP TRIGGER unit_refund_payment_components_complete ON public.pos_physical_cash_refund;
      DROP TRIGGER unit_refund_payment_components_complete ON public.pos_mixed_cash_refund;
    """)
    _patch_audit(upgrade=False)
    _sql(f"DROP TABLE public.{TABLE};")
    for signature in reversed(FUNCTIONS):
        _sql(f"DROP FUNCTION public.{signature};")
    for kind in ("physical", "mixed"):
        _sql(f"""
          ALTER TABLE public.pos_{kind}_cash_refund
            DROP CONSTRAINT ck_{kind}_refund_payment_journal_version,
            DROP COLUMN payment_journal_version;
        """)
