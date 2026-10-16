"""Fence saved payment plans without authorizing external payments.

Revision ID: 0209
Revises: 0208
"""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

revision = "0209"
down_revision = "0208"
branch_labels = None
depends_on = None

TABLES = ("pos_unit_payment_plan_binding", "pos_unit_payment_plan_binding_cancel")
FUNCTIONS = (
    "unit_payment_binding_active(uuid,uuid)",
    "trg_unit_payment_binding_guard()",
    "trg_unit_payment_binding_fence()",
)
FENCED = (
    "sale",
    "sale_item",
    "pos_unit_cart_item",
    "sale_payment",
    "pos_payment_attempt",
    "pos_unit_cash_intent",
    "unit_stock_cart_command_v2",
)


def _sql(sql: str) -> None:
    path = Path(__file__).with_name("0171_unit_cash_intents.py")
    spec = spec_from_file_location("aurum_0209_sql", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load historical SQL executor")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    module._sql(sql)


SQL = """
CREATE FUNCTION public.unit_payment_binding_active(p_tenant UUID,p_sale UUID) RETURNS BOOLEAN
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT EXISTS(SELECT 1 FROM public.pos_unit_payment_plan_binding b
   WHERE b.tenant_id=p_tenant AND b.sale_id=p_sale
     AND NOT EXISTS(SELECT 1 FROM public.pos_unit_payment_plan_binding_cancel c
       WHERE c.tenant_id=b.tenant_id AND c.id=b.id))
$$;
CREATE FUNCTION public.trg_unit_payment_binding_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE intent public.pos_unit_payment_plan_intent%ROWTYPE;
 actor UUID; q JSONB; settings public.tenant_settings%ROWTYPE; methods TEXT[];
BEGIN
 IF TG_OP<>'INSERT' THEN
  RAISE EXCEPTION 'unit_payment_binding_immutable' USING ERRCODE='55000';
 END IF;
 actor:=public.current_app_user_id();
 IF session_user<>'aurum_app' OR public.is_support_session() OR actor IS NULL
   OR NEW.tenant_id IS DISTINCT FROM public.current_tenant_id()
   OR NEW.cashier_user_id IS DISTINCT FROM actor THEN
  RAISE EXCEPTION 'unit_payment_binding_actor_denied' USING ERRCODE='42501';
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(
   'unit-payment-plan:'||NEW.tenant_id||':'||NEW.id,0));
 SELECT * INTO intent FROM public.pos_unit_payment_plan_intent
   WHERE tenant_id=NEW.tenant_id AND id=NEW.id;
 IF NOT FOUND OR (intent.sale_id,intent.branch_id,intent.cashier_user_id)
   IS DISTINCT FROM (NEW.sale_id,NEW.branch_id,actor) THEN
  RAISE EXCEPTION 'unit_payment_binding_scope_invalid' USING ERRCODE='23514';
 END IF;
 PERFORM 1 FROM public.sale WHERE tenant_id=NEW.tenant_id AND id=NEW.sale_id FOR UPDATE;
 IF NOT FOUND THEN
  RAISE EXCEPTION 'unit_payment_binding_sale_missing' USING ERRCODE='23514';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM public.app_user WHERE id=actor AND status='active')
   OR NOT coalesce(public.tenant_actor_has_scoped_permission(
     NEW.tenant_id,'pos.sell',NEW.branch_id),false) THEN
  RAISE EXCEPTION 'unit_payment_binding_actor_denied' USING ERRCODE='42501';
 END IF;
 IF EXISTS(SELECT 1 FROM public.sale_payment
     WHERE tenant_id=NEW.tenant_id AND sale_id=NEW.sale_id)
   OR EXISTS(SELECT 1 FROM public.pos_payment_attempt
     WHERE tenant_id=NEW.tenant_id AND sale_id=NEW.sale_id) THEN
  RAISE EXCEPTION 'unit_payment_binding_payment_started' USING ERRCODE='23514';
 END IF;
 IF TG_TABLE_NAME='pos_unit_payment_plan_binding' THEN
  IF EXISTS(SELECT 1 FROM public.pos_unit_payment_plan_binding_cancel
      WHERE tenant_id=NEW.tenant_id AND id=NEW.id) THEN
   RAISE EXCEPTION 'unit_payment_binding_cancelled' USING ERRCODE='23505';
  END IF;
  IF public.unit_payment_binding_active(NEW.tenant_id,NEW.sale_id) THEN
   RAISE EXCEPTION 'unit_payment_binding_already_active' USING ERRCODE='23505';
  END IF;
  q:=public.unit_intent_current_quote(NEW.tenant_id,NEW.sale_id,intent.branch_id,
    intent.register_id,intent.shift_id,intent.cashier_user_id);
  IF q IS DISTINCT FROM intent.quote_payload
    OR EXISTS(SELECT 1 FROM public.sale WHERE tenant_id=NEW.tenant_id AND id=NEW.sale_id
      AND is_test) THEN
   RAISE EXCEPTION 'unit_payment_binding_quote_changed' USING ERRCODE='23514';
  END IF;
  SELECT array_agg(value->>'method') INTO methods
    FROM jsonb_array_elements(intent.request_payload->'payments');
  SELECT * INTO settings FROM public.tenant_settings WHERE tenant_id=NEW.tenant_id FOR SHARE;
  IF NOT FOUND OR NOT settings.pos_payment_methods ?& methods
    OR (cardinality(methods)>1 AND NOT settings.pos_mixed_payment_enabled) THEN
   RAISE EXCEPTION 'unit_payment_binding_method_disabled' USING ERRCODE='23514';
  END IF;
 END IF;
 -- Recheck after catalog/settings waits; cancellation does not inspect stale retail prices.
 IF NOT EXISTS(SELECT 1 FROM public.app_user WHERE id=actor AND status='active')
   OR NOT coalesce(public.tenant_actor_has_scoped_permission(
     NEW.tenant_id,'pos.sell',NEW.branch_id),false) THEN
  RAISE EXCEPTION 'unit_payment_binding_actor_denied' USING ERRCODE='42501';
 END IF;
 -- A SQL statement may start before a preceding binding commits and then wait.
 -- Use the post-wait clock and preserve causal ordering even across clock adjustment.
 NEW.created_at:=greatest(clock_timestamp(),
   (SELECT created_at FROM public.pos_unit_payment_plan_binding
     WHERE tenant_id=NEW.tenant_id AND id=NEW.id));
 NEW.created_by:=actor;
 RETURN NEW;
END $$;
CREATE FUNCTION public.trg_unit_payment_binding_fence() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE parents JSONB:='[]'; payload JSONB; parent_id UUID; target RECORD;
BEGIN
 -- Check both sides of UPDATE: moving a row out of a bound draft must not escape the fence.
 IF TG_OP IN ('UPDATE','DELETE') THEN
  payload:=to_jsonb(OLD);
  parent_id:=(payload->>CASE WHEN TG_TABLE_NAME='sale' THEN 'id'
    WHEN TG_TABLE_NAME='unit_stock_cart_command_v2' THEN 'checkout_sale_id'
    ELSE 'sale_id' END)::UUID;
  IF parent_id IS NOT NULL THEN
   parents:=parents||jsonb_build_array(jsonb_build_object(
     'tenant',payload->>'tenant_id','sale',parent_id));
  END IF;
 END IF;
 IF TG_OP IN ('INSERT','UPDATE') THEN
  payload:=to_jsonb(NEW);
  parent_id:=(payload->>CASE WHEN TG_TABLE_NAME='sale' THEN 'id'
    WHEN TG_TABLE_NAME='unit_stock_cart_command_v2' THEN 'checkout_sale_id'
    ELSE 'sale_id' END)::UUID;
  IF parent_id IS NOT NULL THEN
   parents:=parents||jsonb_build_array(jsonb_build_object(
     'tenant',payload->>'tenant_id','sale',parent_id));
  END IF;
 END IF;
 FOR target IN SELECT DISTINCT (value->>'tenant')::UUID AS tenant,
   (value->>'sale')::UUID AS sale FROM jsonb_array_elements(parents)
   ORDER BY tenant,sale LOOP
  PERFORM 1 FROM public.sale WHERE tenant_id=target.tenant AND id=target.sale FOR UPDATE;
  IF public.unit_payment_binding_active(target.tenant,target.sale) THEN
   RAISE EXCEPTION 'unit_payment_binding_draft_frozen' USING ERRCODE='23514';
  END IF;
 END LOOP;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 RETURN NEW;
END $$;
"""


def upgrade() -> None:
    _sql("""
      CREATE TEMP TABLE unit_binding_reference_grants AS
        SELECT oid::regclass::TEXT AS relation,
          has_table_privilege('aurum_schema_owner',oid,'REFERENCES') AS granted
        FROM pg_class WHERE oid IN ('public.pos_unit_payment_plan_intent'::regclass,
          'public.app_user'::regclass);
      GRANT REFERENCES ON public.pos_unit_payment_plan_intent,public.app_user TO aurum_schema_owner;
    """)
    for table in TABLES:
        _sql(f"""
          CREATE TABLE public.{table} (
            tenant_id UUID NOT NULL,id UUID NOT NULL,sale_id UUID NOT NULL,branch_id UUID NOT NULL,
            cashier_user_id UUID NOT NULL REFERENCES public.app_user(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            created_by UUID NOT NULL REFERENCES public.app_user(id),
            PRIMARY KEY(tenant_id,id),
            FOREIGN KEY(tenant_id,id) REFERENCES public.pos_unit_payment_plan_intent(tenant_id,id),
            CHECK(substring(id::TEXT,15,1)='4' AND substring(id::TEXT,20,1) IN ('8','9','a','b'))
          );
          ALTER TABLE public.{table} OWNER TO aurum_schema_owner;
          ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY;
          ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY;
          REVOKE ALL ON public.{table} FROM PUBLIC,aurum_app,aurum_support;
          GRANT SELECT,INSERT ON public.{table} TO aurum_app;
          GRANT SELECT ON public.{table} TO aurum_support;
          CREATE POLICY schema_owner ON public.{table} TO aurum_schema_owner
            USING(true) WITH CHECK(true);
          CREATE POLICY support ON public.{table} FOR SELECT TO aurum_support USING(true);
          CREATE POLICY tenant_actor ON public.{table} TO aurum_app USING(
            tenant_id=public.current_tenant_id() AND cashier_user_id=public.current_app_user_id()
            AND public.tenant_actor_has_scoped_permission(tenant_id,'pos.sell',branch_id))
            WITH CHECK(tenant_id=public.current_tenant_id()
            AND cashier_user_id=public.current_app_user_id()
            AND public.tenant_actor_has_scoped_permission(tenant_id,'pos.sell',branch_id));
        """)
    _sql("""
      DO $$ DECLARE r RECORD; BEGIN
        FOR r IN SELECT * FROM pg_temp.unit_binding_reference_grants WHERE NOT granted LOOP
          EXECUTE format('REVOKE REFERENCES ON TABLE %s FROM aurum_schema_owner',r.relation);
        END LOOP;
      END $$;
      DROP TABLE pg_temp.unit_binding_reference_grants;
    """)
    _sql("""
      CREATE INDEX ix_unit_payment_binding_sale
        ON public.pos_unit_payment_plan_binding(tenant_id,sale_id);
    """)
    _sql(SQL)
    for signature in FUNCTIONS:
        _sql(f"""
          ALTER FUNCTION public.{signature} OWNER TO aurum_schema_owner;
          REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC,aurum_app,aurum_support;
        """)
    for table in TABLES:
        _sql(f"""
          CREATE TRIGGER a00_tenant_access BEFORE INSERT OR UPDATE OR DELETE ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION public.trg_enforce_tenant_access('pos');
          CREATE TRIGGER a10_unit_payment_binding_guard BEFORE INSERT OR UPDATE OR DELETE
            ON public.{table} FOR EACH ROW
            EXECUTE FUNCTION public.trg_unit_payment_binding_guard();
          CREATE TRIGGER z90_unit_payment_binding_audit AFTER INSERT OR UPDATE OR DELETE
            ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.trg_audit_log();
        """)
    for table in FENCED:
        events = "UPDATE OR DELETE" if table == "sale" else "INSERT OR UPDATE OR DELETE"
        _sql(f"""
          CREATE TRIGGER a01_unit_payment_binding_fence BEFORE {events} ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION public.trg_unit_payment_binding_fence();
        """)


def downgrade() -> None:
    _sql("""
      LOCK TABLE public.pos_unit_payment_plan_binding,
        public.pos_unit_payment_plan_binding_cancel IN ACCESS EXCLUSIVE MODE;
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.pos_unit_payment_plan_binding)
          OR EXISTS(SELECT 1 FROM public.pos_unit_payment_plan_binding_cancel) THEN
          RAISE EXCEPTION 'unit_payment_binding_downgrade_requires_empty_history'
            USING ERRCODE='55000';
        END IF;
      END $$;
    """)
    for table in FENCED:
        _sql(f"DROP TRIGGER a01_unit_payment_binding_fence ON public.{table};")
    for table in reversed(TABLES):
        _sql(f"DROP TABLE public.{table};")
    for signature in reversed(FUNCTIONS):
        _sql(f"DROP FUNCTION public.{signature};")
