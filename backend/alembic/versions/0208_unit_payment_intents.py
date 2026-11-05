"""Persist immutable electronic draft plans without authorizing any payment.

Revision ID: 0208
Revises: 0207
"""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType

revision = "0208"
down_revision = "0207"
branch_labels = None
depends_on = None

QUOTE = "unit_intent_current_quote(uuid,uuid,uuid,uuid,uuid,uuid)"
GUARD = "trg_unit_payment_plan_intent_guard()"
AUDIT_OLD = "IF v_result IS NULL THEN RETURN NULL; END IF;"
AUDIT_NEW = AUDIT_OLD + """
      IF p_table_name='pos_unit_payment_plan_intent' THEN
        RETURN v_result-ARRAY['request_payload','quote_payload'];
      END IF;"""


def _history(filename: str) -> ModuleType:
    path = Path(__file__).with_name(filename)
    spec = spec_from_file_location(f"aurum_0208_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load historical migration {filename}")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sql(sql: str) -> None:
    _history("0171_unit_cash_intents.py")._sql(sql)


def _replace(sql: str, old: str, new: str) -> str:
    if sql.count(old) != 1:
        raise RuntimeError("Historical cash quote guard differs from the expected definition")
    return sql.replace(old, new, 1)


def _quote_sql() -> str:
    # Reuse the proven physical retail arithmetic, without changing the cash guard
    # or creating a cash confirmation. This function is private to database guards.
    sql: str = _history("0179_unit_physical_cart_intents.py")._cash_guard()
    sql = _replace(
        sql,
        "CREATE OR REPLACE FUNCTION public.trg_unit_cash_intent_guard() RETURNS TRIGGER",
        """CREATE FUNCTION public.unit_intent_current_quote(
 p_tenant UUID,p_sale UUID,p_branch UUID,p_register UUID,p_shift UUID,p_cashier UUID)
 RETURNS JSONB""",
    )
    sql = _replace(
        sql,
        """ IF TG_OP<>'INSERT' THEN
  RAISE EXCEPTION 'unit_cash_intent_immutable' USING ERRCODE='55000';
 END IF;
""",
        "",
    )
    sql = _replace(
        sql,
        """   OR NEW.expected_total_amount IS DISTINCT FROM v_total
   OR NEW.cash_tendered<v_total
   OR NEW.change_amount IS DISTINCT FROM NEW.cash_tendered-v_total""",
        "",
    )
    sql = _replace(
        sql,
        """ IF NEW.quote_payload IS DISTINCT FROM v_payload THEN
  RAISE EXCEPTION 'unit_cash_intent_quote_mismatch' USING ERRCODE='23514';
 END IF;
 -- Python fingerprints are opaque here; JSONB equality proves the retail facts.
 NEW.created_at:=statement_timestamp(); NEW.created_by:=v_actor;
 RETURN NEW;""",
        " RETURN v_payload;",
    )
    for column, parameter in (
        ("tenant_id", "p_tenant"),
        ("sale_id", "p_sale"),
        ("branch_id", "p_branch"),
        ("register_id", "p_register"),
        ("shift_id", "p_shift"),
        ("cashier_user_id", "p_cashier"),
    ):
        if f"NEW.{column}" not in sql:
            raise RuntimeError(f"Historical quote no longer references {column}")
        sql = sql.replace(f"NEW.{column}", parameter)
    if "NEW." in sql or "TG_OP" in sql or "cash_tendered" in sql:
        raise RuntimeError("Neutral quote still depends on cash intent state")
    return sql


GUARD_SQL = """
CREATE FUNCTION public.trg_unit_payment_plan_intent_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE q JSONB; r JSONB; p JSONB; methods TEXT[]:='{}'; method TEXT;
 total NUMERIC:=0; cash NUMERIC; tendered NUMERIC; amount NUMERIC;
 settings public.tenant_settings%ROWTYPE; actor UUID;
BEGIN
 IF TG_OP<>'INSERT' THEN
  RAISE EXCEPTION 'unit_payment_plan_immutable' USING ERRCODE='55000';
 END IF;
 actor:=public.current_app_user_id();
 IF session_user<>'aurum_app' OR public.is_support_session()
   OR NEW.tenant_id IS DISTINCT FROM public.current_tenant_id()
   OR NEW.cashier_user_id IS DISTINCT FROM actor OR actor IS NULL
   OR NOT coalesce(public.tenant_actor_has_scoped_permission(
     NEW.tenant_id,'pos.sell',NEW.branch_id),false)
   OR NOT EXISTS(SELECT 1 FROM public.app_user WHERE id=actor AND status='active') THEN
  RAISE EXCEPTION 'unit_payment_plan_actor_denied' USING ERRCODE='42501';
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(
   'unit-payment-plan:'||NEW.tenant_id||':'||NEW.id,0));
 q:=public.unit_intent_current_quote(NEW.tenant_id,NEW.sale_id,NEW.branch_id,
   NEW.register_id,NEW.shift_id,NEW.cashier_user_id);
 IF EXISTS(SELECT 1 FROM public.sale WHERE tenant_id=NEW.tenant_id AND id=NEW.sale_id
     AND is_test) OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(q->'lines') line
       WHERE line->>'inventory_kind'='physical_unit')
   OR EXISTS(SELECT 1 FROM jsonb_array_elements(q->'lines') line
       WHERE line->>'inventory_kind'='prepared_unit')
   OR NEW.quote_payload IS DISTINCT FROM q THEN
  RAISE EXCEPTION 'unit_payment_plan_quote_invalid' USING ERRCODE='23514';
 END IF;
 -- Hashes are opaque comparison values, never payment authorization.
 -- Current retail facts are proven by exact JSONB equality above.
 r:=NEW.request_payload;
 IF jsonb_typeof(r) IS DISTINCT FROM 'object'
   OR r-ARRAY['sale_id','expected_revision_hash','expected_total_amount',
     'currency','payments','cash_tendered']<>'{}'
   OR NOT r ?& ARRAY['sale_id','expected_revision_hash','expected_total_amount',
     'currency','payments','cash_tendered']
   OR r->>'sale_id' IS DISTINCT FROM NEW.sale_id::TEXT
   OR jsonb_typeof(r->'expected_revision_hash') IS DISTINCT FROM 'string'
   OR (r->>'expected_revision_hash') !~ '^[0-9a-f]{64}$'
   OR r->'expected_total_amount' IS DISTINCT FROM q->'total_amount'
   OR r->>'currency' IS DISTINCT FROM 'TJS'
   OR jsonb_typeof(r->'payments') IS DISTINCT FROM 'array' THEN
  RAISE EXCEPTION 'unit_payment_plan_request_invalid' USING ERRCODE='23514';
 END IF;
 IF jsonb_array_length(r->'payments') NOT BETWEEN 1 AND 3 THEN
  RAISE EXCEPTION 'unit_payment_plan_parts_invalid' USING ERRCODE='23514';
 END IF;
 FOR p IN SELECT value FROM jsonb_array_elements(r->'payments') LOOP
  method:=p->>'method';
  IF jsonb_typeof(p) IS DISTINCT FROM 'object' OR p-ARRAY['method','amount']<>'{}'
    OR method IS NULL OR method NOT IN ('cash','card','qr') OR method=ANY(methods)
    OR jsonb_typeof(p->'amount') IS DISTINCT FROM 'string'
    OR (p->>'amount') !~ '^[0-9]{1,12}[.][0-9]{2}$' THEN
   RAISE EXCEPTION 'unit_payment_plan_part_invalid' USING ERRCODE='23514';
  END IF;
  amount:=(p->>'amount')::NUMERIC;
  IF amount<=0 OR amount>999999999999.99 THEN
   RAISE EXCEPTION 'unit_payment_plan_amount_invalid' USING ERRCODE='23514';
  END IF;
  methods:=array_append(methods,method); total:=total+amount;
  IF method='cash' THEN cash:=amount; END IF;
 END LOOP;
 IF NOT methods && ARRAY['card','qr'] OR total<>(q->>'total_amount')::NUMERIC THEN
  RAISE EXCEPTION 'unit_payment_plan_total_invalid' USING ERRCODE='23514';
 END IF;
 IF cash IS NULL THEN
  IF r->'cash_tendered' IS DISTINCT FROM 'null'::JSONB OR NEW.change_amount<>0 THEN
   RAISE EXCEPTION 'unit_payment_plan_cash_invalid' USING ERRCODE='23514';
  END IF;
 ELSE
  IF jsonb_typeof(r->'cash_tendered') IS DISTINCT FROM 'string'
    OR (r->>'cash_tendered') !~ '^[0-9]{1,12}[.][0-9]{2}$' THEN
   RAISE EXCEPTION 'unit_payment_plan_cash_invalid' USING ERRCODE='23514';
  END IF;
  tendered:=(r->>'cash_tendered')::NUMERIC;
  IF tendered<cash OR tendered>999999999999.99 OR NEW.change_amount<>tendered-cash THEN
   RAISE EXCEPTION 'unit_payment_plan_change_invalid' USING ERRCODE='23514';
  END IF;
 END IF;
 SELECT * INTO settings FROM public.tenant_settings WHERE tenant_id=NEW.tenant_id FOR SHARE;
 IF NOT FOUND OR NOT settings.pos_payment_methods ?& methods
   OR (cardinality(methods)>1 AND NOT settings.pos_mixed_payment_enabled) THEN
  RAISE EXCEPTION 'unit_payment_plan_method_disabled' USING ERRCODE='23514';
 END IF;
 -- Scope can change while the parent, catalog or settings locks are awaited.
 IF NOT EXISTS(SELECT 1 FROM public.app_user WHERE id=actor AND status='active')
   OR NOT coalesce(public.tenant_actor_has_scoped_permission(
     NEW.tenant_id,'pos.sell',NEW.branch_id),false) THEN
  RAISE EXCEPTION 'unit_payment_plan_actor_denied' USING ERRCODE='42501';
 END IF;
 NEW.created_at:=statement_timestamp(); NEW.created_by:=actor;
 RETURN NEW;
END $$;
"""


def upgrade() -> None:
    _sql("""
      CREATE TEMP TABLE unit_plan_reference_grants AS
        SELECT oid::regclass::TEXT AS relation,
          has_table_privilege('aurum_schema_owner',oid,'REFERENCES') AS granted
        FROM pg_class WHERE oid IN ('public.tenant'::regclass,'public.branch'::regclass,
          'public.app_user'::regclass,'public.sale'::regclass);
      GRANT REFERENCES ON public.tenant,public.branch,public.app_user,public.sale
        TO aurum_schema_owner;
      CREATE TABLE public.pos_unit_payment_plan_intent (
        tenant_id UUID NOT NULL REFERENCES public.tenant(id), id UUID NOT NULL,
        branch_id UUID NOT NULL, sale_id UUID NOT NULL, register_id UUID NOT NULL,
        shift_id UUID NOT NULL, cashier_user_id UUID NOT NULL REFERENCES public.app_user(id),
        request_hash TEXT NOT NULL CHECK(request_hash ~ '^[0-9a-f]{64}$'),
        plan_hash TEXT NOT NULL CHECK(plan_hash ~ '^[0-9a-f]{64}$'),
        request_payload JSONB NOT NULL CHECK(jsonb_typeof(request_payload)='object'),
        quote_payload JSONB NOT NULL CHECK(jsonb_typeof(quote_payload)='object'),
        change_amount NUMERIC(14,2) NOT NULL CHECK(change_amount>=0),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        created_by UUID NOT NULL REFERENCES public.app_user(id),
        PRIMARY KEY(tenant_id,id),
        CHECK(substring(id::TEXT,15,1)='4' AND substring(id::TEXT,20,1) IN ('8','9','a','b')),
        FOREIGN KEY(tenant_id,branch_id) REFERENCES public.branch(tenant_id,id),
        FOREIGN KEY(tenant_id,sale_id) REFERENCES public.sale(tenant_id,id) ON DELETE RESTRICT
      );
      DO $$ DECLARE r RECORD; BEGIN
        FOR r IN SELECT * FROM pg_temp.unit_plan_reference_grants WHERE NOT granted LOOP
          EXECUTE format('REVOKE REFERENCES ON TABLE %s FROM aurum_schema_owner',r.relation);
        END LOOP;
      END $$;
      DROP TABLE pg_temp.unit_plan_reference_grants;
      ALTER TABLE public.pos_unit_payment_plan_intent OWNER TO aurum_schema_owner;
      ALTER TABLE public.pos_unit_payment_plan_intent ENABLE ROW LEVEL SECURITY;
      ALTER TABLE public.pos_unit_payment_plan_intent FORCE ROW LEVEL SECURITY;
      REVOKE ALL ON public.pos_unit_payment_plan_intent FROM PUBLIC,aurum_app,aurum_support;
      GRANT SELECT,INSERT ON public.pos_unit_payment_plan_intent TO aurum_app;
      GRANT SELECT ON public.pos_unit_payment_plan_intent TO aurum_support;
      CREATE POLICY schema_owner ON public.pos_unit_payment_plan_intent TO aurum_schema_owner
        USING(true) WITH CHECK(true);
      CREATE POLICY support ON public.pos_unit_payment_plan_intent
        FOR SELECT TO aurum_support USING(true);
      CREATE POLICY tenant_actor ON public.pos_unit_payment_plan_intent TO aurum_app USING(
        tenant_id=public.current_tenant_id() AND cashier_user_id=public.current_app_user_id()
        AND public.tenant_actor_has_scoped_permission(tenant_id,'pos.sell',branch_id))
        WITH CHECK(tenant_id=public.current_tenant_id()
        AND cashier_user_id=public.current_app_user_id()
        AND public.tenant_actor_has_scoped_permission(tenant_id,'pos.sell',branch_id));
    """)
    _sql(_quote_sql())
    _sql(GUARD_SQL)
    for signature in (QUOTE, GUARD):
        _sql(f"""
          ALTER FUNCTION public.{signature} OWNER TO aurum_schema_owner;
          REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC,aurum_app,aurum_support;
        """)
    _history("0205_physical_writeoff_cancellation.py")._patch(
        "public.audit_redact_table_snapshot(text,jsonb)", AUDIT_OLD, AUDIT_NEW
    )
    _sql("""
      CREATE TRIGGER a00_tenant_access BEFORE INSERT OR UPDATE OR DELETE
        ON public.pos_unit_payment_plan_intent FOR EACH ROW
        EXECUTE FUNCTION public.trg_enforce_tenant_access('pos');
      CREATE TRIGGER a10_unit_payment_plan_intent_guard BEFORE INSERT OR UPDATE OR DELETE
        ON public.pos_unit_payment_plan_intent FOR EACH ROW
        EXECUTE FUNCTION public.trg_unit_payment_plan_intent_guard();
      CREATE TRIGGER z90_unit_payment_plan_intent_audit AFTER INSERT OR UPDATE OR DELETE
        ON public.pos_unit_payment_plan_intent FOR EACH ROW
        EXECUTE FUNCTION public.trg_audit_log();
    """)


def downgrade() -> None:
    _sql("""
      LOCK TABLE public.pos_unit_payment_plan_intent IN ACCESS EXCLUSIVE MODE;
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.pos_unit_payment_plan_intent) THEN
          RAISE EXCEPTION 'unit_payment_plan_downgrade_requires_empty_history'
            USING ERRCODE='55000';
        END IF;
      END $$;
      DROP TABLE public.pos_unit_payment_plan_intent;
    """)
    _history("0205_physical_writeoff_cancellation.py")._patch(
        "public.audit_redact_table_snapshot(text,jsonb)", AUDIT_NEW, AUDIT_OLD
    )
    _sql(f"DROP FUNCTION public.{GUARD}; DROP FUNCTION public.{QUOTE};")
