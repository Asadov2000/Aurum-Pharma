"""Journal immutable payment components for versioned unit cash checkouts.

Revision ID: 0210
Revises: 0209
"""

from __future__ import annotations

from collections.abc import Sequence
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from sqlalchemy import text

from alembic import op

revision: str = "0210"
down_revision: str | Sequence[str] | None = "0209"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "pos_unit_checkout_payment_component"
FUNCTIONS = (
    "assert_unit_checkout_payment_components(uuid,uuid,boolean)",
    "trg_unit_checkout_payment_component_guard()",
    "trg_unit_checkout_payment_component_complete()",
)
AUDIT_OLD = "IF v_result IS NULL THEN RETURN NULL; END IF;"
AUDIT_NEW = AUDIT_OLD + """
      IF p_table_name='pos_unit_checkout_payment_component' THEN
        RETURN jsonb_build_object('created_at',v_result->'created_at',
          'created_by',v_result->'created_by');
      END IF;"""


def _sql(sql: str) -> None:
    path = Path(__file__).with_name("0171_unit_cash_intents.py")
    spec = spec_from_file_location("aurum_0210_sql", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load historical SQL executor")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    module._sql(sql)


def _patch_assert(kind: str, *, upgrade: bool) -> None:
    signature = f"public.assert_{kind}_checkout(uuid,uuid,boolean)"
    definition, source = op.get_bind().execute(
        text(
            "SELECT pg_get_functiondef(oid),prosrc FROM pg_proc "
            "WHERE oid=CAST(:signature AS regprocedure)"
        ),
        {"signature": signature},
    ).one()
    mixed = "true" if kind == "mixed" else "false"
    block = (
        " IF p_final THEN\n"
        "  PERFORM public.assert_unit_checkout_payment_components("
        f"p_tenant,p_sale,{mixed});\n"
        " END IF;\n"
    )
    if upgrade:
        end = source.rfind("END")
        if end < 0 or source[end:].strip() not in ("END", "END;") or block in source:
            raise RuntimeError("Historical checkout assertion changed")
        updated = source[:end] + block + source[end:]
    else:
        if source.count(block) != 1:
            raise RuntimeError("Checkout payment assertion changed")
        updated = source.replace(block, "", 1)
    if definition.count(source) != 1:
        raise RuntimeError("Cannot locate checkout assertion body")
    op.get_bind().exec_driver_sql(definition.replace(source, updated, 1))


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
CREATE FUNCTION public.assert_unit_checkout_payment_components(
 p_tenant UUID,p_sale UUID,p_mixed BOOLEAN) RETURNS VOID
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE marker_id UUID; version SMALLINT; payments JSONB; actual JSONB; component_count BIGINT;
BEGIN
 IF p_mixed THEN
  SELECT id,payment_journal_version,result_payload->'payments' INTO marker_id,version,payments
    FROM public.pos_mixed_checkout WHERE tenant_id=p_tenant AND sale_id=p_sale;
 ELSE
  SELECT id,payment_journal_version,result_payload->'payments' INTO marker_id,version,payments
    FROM public.pos_physical_checkout WHERE tenant_id=p_tenant AND sale_id=p_sale;
 END IF;
 IF marker_id IS NULL THEN
  RAISE EXCEPTION 'unit_checkout_component_parent_missing' USING ERRCODE='23514';
 END IF;
 IF version=1 THEN
  IF EXISTS(SELECT 1 FROM public.pos_unit_checkout_payment_component
    WHERE tenant_id=p_tenant AND sale_id=p_sale) THEN
   RAISE EXCEPTION 'unit_checkout_component_legacy_parent' USING ERRCODE='23514';
  END IF;
  RETURN;
 END IF;
 SELECT count(*),jsonb_agg(jsonb_build_object('id',c.id,'method',c.method,
   'amount',to_char(c.amount,'FM999999999990.00'),'currency',c.currency) ORDER BY c.id)
   INTO component_count,actual FROM public.pos_unit_checkout_payment_component c
   WHERE c.tenant_id=p_tenant AND c.sale_id=p_sale;
 -- This migration enables only the existing cash writers. Electronic methods
 -- remain blocked until their complete settlement and recovery lifecycle exists.
 IF component_count<>1 OR actual IS DISTINCT FROM payments
   OR EXISTS(SELECT 1 FROM public.pos_unit_checkout_payment_component c
     LEFT JOIN public.sale_payment p ON p.id=c.id
     WHERE c.tenant_id=p_tenant AND c.sale_id=p_sale AND (
       (c.physical_checkout_id,c.mixed_checkout_id) IS DISTINCT FROM
         (CASE WHEN p_mixed THEN NULL::UUID ELSE marker_id END,
          CASE WHEN p_mixed THEN marker_id ELSE NULL::UUID END)
       OR c.method<>'cash' OR c.payment_attempt_id IS NOT NULL
       OR p.id IS NULL OR (p.tenant_id,p.sale_id,p.payment_method,p.amount,p.currency,
                          p.payment_attempt_id) IS DISTINCT FROM
                         (c.tenant_id,c.sale_id,c.method,c.amount,c.currency,c.payment_attempt_id)))
   OR (SELECT count(*) FROM public.sale_payment WHERE sale_id=p_sale)<>component_count THEN
  RAISE EXCEPTION 'unit_checkout_payment_components_invalid' USING ERRCODE='23514';
 END IF;
END $$;
CREATE FUNCTION public.trg_unit_checkout_payment_component_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE s public.sale%ROWTYPE; marker_sale UUID; version SMALLINT; payments JSONB;
BEGIN
 IF TG_OP<>'INSERT' THEN
  RAISE EXCEPTION 'unit_checkout_payment_component_immutable' USING ERRCODE='55000';
 END IF;
 IF session_user<>'aurum_app' OR public.is_support_session()
   OR NEW.tenant_id IS DISTINCT FROM public.current_tenant_id()
   OR public.current_app_user_id() IS NULL THEN
  RAISE EXCEPTION 'unit_checkout_payment_component_actor_denied' USING ERRCODE='42501';
 END IF;
 SELECT * INTO s FROM public.sale WHERE tenant_id=NEW.tenant_id AND id=NEW.sale_id FOR UPDATE;
 IF s.id IS NULL OR s.cashier_user_id IS DISTINCT FROM public.current_app_user_id()
   OR s.status<>'draft' OR s.sale_type<>'sale' OR s.parent_sale_id IS NOT NULL
   OR NOT coalesce(public.tenant_actor_has_scoped_permission(
     NEW.tenant_id,'pos.sell',s.branch_id),false)
   OR NOT EXISTS(SELECT 1 FROM public.app_user
     WHERE id=public.current_app_user_id() AND status='active') THEN
  RAISE EXCEPTION 'unit_checkout_payment_component_scope_invalid' USING ERRCODE='42501';
 END IF;
 IF NEW.physical_checkout_id IS NOT NULL AND NEW.mixed_checkout_id IS NULL THEN
  SELECT sale_id,payment_journal_version,result_payload->'payments'
    INTO marker_sale,version,payments
    FROM public.pos_physical_checkout
    WHERE tenant_id=NEW.tenant_id AND id=NEW.physical_checkout_id;
 ELSIF NEW.mixed_checkout_id IS NOT NULL AND NEW.physical_checkout_id IS NULL THEN
  SELECT sale_id,payment_journal_version,result_payload->'payments'
    INTO marker_sale,version,payments
    FROM public.pos_mixed_checkout
    WHERE tenant_id=NEW.tenant_id AND id=NEW.mixed_checkout_id;
 END IF;
 IF marker_sale IS DISTINCT FROM NEW.sale_id OR version IS DISTINCT FROM 2
   OR NEW.method<>'cash' OR NEW.payment_attempt_id IS NOT NULL THEN
  RAISE EXCEPTION 'unit_checkout_payment_component_parent_invalid' USING ERRCODE='23514';
 END IF;
 -- Check the complete tenant-scoped source before the deferred global-id FK.
 -- Foreign and missing source IDs must fail identically, including when a caller
 -- explicitly makes that FK immediate before deferred checkout assertions run.
 IF payments IS DISTINCT FROM jsonb_build_array(jsonb_build_object(
     'id',NEW.id,'method',NEW.method,'amount',to_char(NEW.amount,'FM999999999990.00'),
     'currency',NEW.currency))
   OR NOT EXISTS(SELECT 1 FROM public.sale_payment p
     WHERE p.tenant_id=NEW.tenant_id AND p.id=NEW.id AND p.sale_id=NEW.sale_id
       AND (p.payment_method,p.amount,p.currency,p.payment_attempt_id)
         IS NOT DISTINCT FROM (NEW.method,NEW.amount,NEW.currency,NEW.payment_attempt_id)) THEN
  RAISE EXCEPTION 'unit_checkout_payment_component_source_invalid' USING ERRCODE='23514';
 END IF;
 NEW.created_at:=clock_timestamp(); NEW.created_by:=public.current_app_user_id();
 RETURN NEW;
END $$;
CREATE FUNCTION public.trg_unit_checkout_payment_component_complete() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 IF NEW.mixed_checkout_id IS NOT NULL THEN
  PERFORM public.assert_mixed_checkout(NEW.tenant_id,NEW.sale_id,true);
 ELSE
  PERFORM public.assert_physical_checkout(NEW.tenant_id,NEW.sale_id,true);
 END IF;
 RETURN NEW;
END $$;
"""


def upgrade() -> None:
    _sql("""
      CREATE TEMP TABLE unit_component_reference_grants AS
        SELECT oid::regclass::TEXT AS relation,
          has_table_privilege('aurum_schema_owner',oid,'REFERENCES') AS granted
        FROM pg_class WHERE oid IN ('public.sale_payment'::regclass,'public.sale'::regclass,
          'public.app_user'::regclass,'public.pos_physical_checkout'::regclass,
          'public.pos_mixed_checkout'::regclass);
      GRANT REFERENCES ON public.sale_payment,public.sale,public.app_user,
        public.pos_physical_checkout,public.pos_mixed_checkout TO aurum_schema_owner;
      ALTER TABLE public.pos_physical_checkout
        ADD COLUMN payment_journal_version SMALLINT NOT NULL DEFAULT 1,
        ADD CONSTRAINT ck_physical_checkout_payment_journal_version
          CHECK(payment_journal_version IN (1,2));
      ALTER TABLE public.pos_mixed_checkout
        ADD COLUMN payment_journal_version SMALLINT NOT NULL DEFAULT 1,
        ADD CONSTRAINT ck_mixed_checkout_payment_journal_version
          CHECK(payment_journal_version IN (1,2));
      CREATE TABLE public.pos_unit_checkout_payment_component (
        tenant_id UUID NOT NULL,id UUID NOT NULL,sale_id UUID NOT NULL,
        physical_checkout_id UUID,mixed_checkout_id UUID,
        method TEXT NOT NULL CHECK(method IN ('cash','card','qr')),
        amount NUMERIC(14,2) NOT NULL CHECK(amount>0 AND amount<=999999999999.99),
        currency TEXT NOT NULL CHECK(currency='TJS'),payment_attempt_id UUID,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        created_by UUID NOT NULL REFERENCES public.app_user(id),
        PRIMARY KEY(tenant_id,id),
        CONSTRAINT ck_unit_component_parent
          CHECK((physical_checkout_id IS NULL)<>(mixed_checkout_id IS NULL)),
        CONSTRAINT fk_unit_component_sale FOREIGN KEY(tenant_id,sale_id)
          REFERENCES public.sale(tenant_id,id),
        CONSTRAINT fk_unit_component_payment FOREIGN KEY(id) REFERENCES public.sale_payment(id)
          DEFERRABLE INITIALLY DEFERRED,
        CONSTRAINT fk_unit_component_physical FOREIGN KEY(tenant_id,physical_checkout_id)
          REFERENCES public.pos_physical_checkout(tenant_id,id) DEFERRABLE INITIALLY DEFERRED,
        CONSTRAINT fk_unit_component_mixed FOREIGN KEY(tenant_id,mixed_checkout_id)
          REFERENCES public.pos_mixed_checkout(tenant_id,id) DEFERRABLE INITIALLY DEFERRED
      );
      DO $$ DECLARE r RECORD; BEGIN
        FOR r IN SELECT * FROM pg_temp.unit_component_reference_grants WHERE NOT granted LOOP
          EXECUTE format('REVOKE REFERENCES ON %s FROM aurum_schema_owner',r.relation);
        END LOOP;
      END $$;
      DROP TABLE pg_temp.unit_component_reference_grants;
      ALTER TABLE public.pos_unit_checkout_payment_component OWNER TO aurum_schema_owner;
      ALTER TABLE public.pos_unit_checkout_payment_component ENABLE ROW LEVEL SECURITY;
      ALTER TABLE public.pos_unit_checkout_payment_component FORCE ROW LEVEL SECURITY;
      REVOKE ALL ON public.pos_unit_checkout_payment_component FROM PUBLIC,aurum_app,aurum_support;
      GRANT SELECT,INSERT ON public.pos_unit_checkout_payment_component TO aurum_app;
      GRANT SELECT ON public.pos_unit_checkout_payment_component TO aurum_support;
      CREATE POLICY schema_owner ON public.pos_unit_checkout_payment_component TO aurum_schema_owner
        USING(true) WITH CHECK(true);
      CREATE POLICY support ON public.pos_unit_checkout_payment_component
        FOR SELECT TO aurum_support USING(true);
      CREATE POLICY tenant_actor ON public.pos_unit_checkout_payment_component TO aurum_app
        USING(tenant_id=public.current_tenant_id() AND EXISTS(
          SELECT 1 FROM public.sale s
            WHERE s.tenant_id=pos_unit_checkout_payment_component.tenant_id
            AND s.id=pos_unit_checkout_payment_component.sale_id
            AND s.cashier_user_id=public.current_app_user_id()
            AND public.tenant_actor_has_scoped_permission(s.tenant_id,'pos.sell',s.branch_id)))
        WITH CHECK(tenant_id=public.current_tenant_id() AND EXISTS(
          SELECT 1 FROM public.sale s
            WHERE s.tenant_id=pos_unit_checkout_payment_component.tenant_id
            AND s.id=pos_unit_checkout_payment_component.sale_id
            AND s.cashier_user_id=public.current_app_user_id()
            AND public.tenant_actor_has_scoped_permission(s.tenant_id,'pos.sell',s.branch_id)));
      CREATE INDEX ix_unit_checkout_component_sale
        ON public.pos_unit_checkout_payment_component(tenant_id,sale_id);
    """)
    _sql(SQL)
    for signature in FUNCTIONS:
        _sql(f"""
          ALTER FUNCTION public.{signature} OWNER TO aurum_schema_owner;
          REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC,aurum_app,aurum_support;
        """)
    for kind in ("physical", "mixed"):
        _patch_assert(kind, upgrade=True)
    _patch_audit(upgrade=True)
    _sql("""
      CREATE TRIGGER a00_tenant_access BEFORE INSERT OR UPDATE OR DELETE
        ON public.pos_unit_checkout_payment_component FOR EACH ROW
        EXECUTE FUNCTION public.trg_enforce_tenant_access('pos');
      CREATE TRIGGER a10_unit_checkout_component_guard BEFORE INSERT OR UPDATE OR DELETE
        ON public.pos_unit_checkout_payment_component FOR EACH ROW
        EXECUTE FUNCTION public.trg_unit_checkout_payment_component_guard();
      CREATE CONSTRAINT TRIGGER unit_checkout_component_complete
        AFTER INSERT ON public.pos_unit_checkout_payment_component
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION public.trg_unit_checkout_payment_component_complete();
      CREATE TRIGGER z90_unit_checkout_component_audit AFTER INSERT OR UPDATE OR DELETE
        ON public.pos_unit_checkout_payment_component FOR EACH ROW
        EXECUTE FUNCTION public.trg_audit_log();
    """)


def downgrade() -> None:
    _sql("""
      LOCK TABLE public.pos_unit_checkout_payment_component,public.pos_physical_checkout,
        public.pos_mixed_checkout IN ACCESS EXCLUSIVE MODE;
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.pos_unit_checkout_payment_component)
          OR EXISTS(SELECT 1 FROM public.pos_physical_checkout WHERE payment_journal_version=2)
          OR EXISTS(SELECT 1 FROM public.pos_mixed_checkout WHERE payment_journal_version=2) THEN
          RAISE EXCEPTION 'unit_checkout_components_downgrade_requires_empty_history'
            USING ERRCODE='55000';
        END IF;
      END $$;
    """)
    for kind in ("physical", "mixed"):
        _patch_assert(kind, upgrade=False)
    _patch_audit(upgrade=False)
    _sql(f"DROP TABLE public.{TABLE};")
    for signature in reversed(FUNCTIONS):
        _sql(f"DROP FUNCTION public.{signature};")
    for kind in ("physical", "mixed"):
        _sql(f"""
          ALTER TABLE public.pos_{kind}_checkout
            DROP CONSTRAINT ck_{kind}_checkout_payment_journal_version,
            DROP COLUMN payment_journal_version;
        """)
