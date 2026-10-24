"""Prepare saved mixed electronic refunds without authorizing any payout.

Revision ID: 0221
Revises: 0220
"""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

revision = "0221"
down_revision = "0220"
branch_labels = None
depends_on = None

BINDING_TABLES = (
    "pos_mixed_electronic_refund_binding",
    "pos_mixed_electronic_refund_binding_cancel",
)
TABLES = (*BINDING_TABLES, "pos_mixed_electronic_refund_activation")
FUNCTIONS = (
    "mixed_electronic_refund_binding_active(uuid,uuid)",
    "trg_mixed_electronic_refund_binding_guard()",
    "trg_mixed_electronic_refund_binding_fence()",
    "trg_mixed_electronic_refund_activation_guard()",
)
FENCED = ("sale", "pos_physical_return_command", "pos_mixed_return_command", "pos_refund_attempt")


def _sql(sql: str) -> None:
    path = Path(__file__).with_name("0171_unit_cash_intents.py")
    spec = spec_from_file_location("aurum_0221_sql", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load historical SQL executor")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    module._sql(sql)


SQL = """
CREATE FUNCTION public.mixed_electronic_refund_binding_active(p_tenant UUID,p_parent UUID)
RETURNS BOOLEAN LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT EXISTS(SELECT 1 FROM public.pos_mixed_electronic_refund_binding b
   WHERE b.tenant_id=p_tenant AND b.parent_sale_id=p_parent
     AND NOT EXISTS(SELECT 1 FROM public.pos_mixed_electronic_refund_binding_cancel c
       WHERE c.tenant_id=b.tenant_id AND c.id=b.id))
$$;
CREATE FUNCTION public.trg_mixed_electronic_refund_binding_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE actor UUID; intent public.pos_mixed_electronic_refund_intent%ROWTYPE;
 parent public.sale%ROWTYPE;
BEGIN
 IF TG_OP<>'INSERT' THEN
  RAISE EXCEPTION 'mixed_electronic_refund_binding_immutable' USING ERRCODE='55000';
 END IF;
 IF current_setting('transaction_isolation')<>'read committed' THEN
  RAISE EXCEPTION 'mixed_electronic_refund_binding_isolation_required' USING ERRCODE='23514';
 END IF;
 actor:=public.current_app_user_id();
 IF session_user<>'aurum_app' OR public.is_support_session() OR actor IS NULL
   OR NEW.tenant_id IS DISTINCT FROM public.current_tenant_id()
   OR NEW.actor_id IS DISTINCT FROM actor THEN
  RAISE EXCEPTION 'mixed_electronic_refund_binding_actor_denied' USING ERRCODE='42501';
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(
   'mixed-electronic-refund-review:'||NEW.tenant_id||':'||NEW.id,0));
 SELECT * INTO intent FROM public.pos_mixed_electronic_refund_intent
   WHERE tenant_id=NEW.tenant_id AND id=NEW.id;
 IF NOT FOUND OR (intent.parent_sale_id,intent.branch_id,intent.actor_id)
   IS DISTINCT FROM (NEW.parent_sale_id,NEW.branch_id,actor) THEN
  RAISE EXCEPTION 'mixed_electronic_refund_binding_scope_invalid' USING ERRCODE='23514';
 END IF;
 SELECT * INTO parent FROM public.sale
   WHERE tenant_id=NEW.tenant_id AND id=NEW.parent_sale_id FOR UPDATE;
 IF NOT FOUND OR parent.branch_id<>NEW.branch_id OR parent.sale_type<>'sale'
   OR parent.status<>'completed' OR parent.parent_sale_id IS NOT NULL OR parent.is_test
   OR parent.currency<>'TJS' OR NOT EXISTS(SELECT 1 FROM public.pos_mixed_checkout c
     WHERE c.tenant_id=NEW.tenant_id AND c.sale_id=parent.id AND c.branch_id=NEW.branch_id
       AND c.payment_journal_version=3) THEN
  RAISE EXCEPTION 'mixed_electronic_refund_binding_source_invalid' USING ERRCODE='23514';
 END IF;
 -- Recheck current rights after waiting on the original sale, across all authors.
 IF NOT EXISTS(SELECT 1 FROM public.app_user WHERE id=actor AND status='active')
   OR NOT coalesce(public.tenant_actor_has_scoped_permission(
     NEW.tenant_id,'pos.refund',NEW.branch_id),false) THEN
  RAISE EXCEPTION 'mixed_electronic_refund_binding_actor_denied' USING ERRCODE='42501';
 END IF;
 IF TG_TABLE_NAME='pos_mixed_electronic_refund_binding' THEN
  IF EXISTS(SELECT 1 FROM public.pos_mixed_electronic_refund_binding_cancel
      WHERE tenant_id=NEW.tenant_id AND id=NEW.id) THEN
   RAISE EXCEPTION 'mixed_electronic_refund_binding_cancelled' USING ERRCODE='23505';
  END IF;
  IF public.mixed_electronic_refund_binding_active(NEW.tenant_id,NEW.parent_sale_id) THEN
   RAISE EXCEPTION 'mixed_electronic_refund_binding_already_active' USING ERRCODE='23505';
  END IF;
  IF EXISTS(SELECT 1 FROM public.pos_refund_attempt WHERE tenant_id=NEW.tenant_id
      AND parent_sale_id=NEW.parent_sale_id
      AND status IN ('pending','requires_reconciliation','confirmed')) THEN
   RAISE EXCEPTION 'mixed_electronic_refund_binding_legacy_attempt_active' USING ERRCODE='23514';
  END IF;
 END IF;
 IF TG_TABLE_NAME='pos_mixed_electronic_refund_binding_cancel'
   AND EXISTS(SELECT 1 FROM public.pos_mixed_electronic_refund_activation
     WHERE tenant_id=NEW.tenant_id AND id=NEW.id) THEN
  RAISE EXCEPTION 'mixed_electronic_refund_activation_cancel_denied' USING ERRCODE='23514';
 END IF;
 -- Cancellation remains available after prices/history change. It is a permanent tombstone.
 NEW.created_at:=greatest(clock_timestamp(),intent.created_at,
   (SELECT created_at FROM public.pos_mixed_electronic_refund_binding
     WHERE tenant_id=NEW.tenant_id AND id=NEW.id));
 NEW.created_by:=actor;
 RETURN NEW;
END $$;
CREATE FUNCTION public.trg_mixed_electronic_refund_binding_fence() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE parents JSONB:='[]'; payload JSONB; parent_id UUID; target RECORD;
BEGIN
 -- Both old and new parents are protected when moving a return or attempt.
 IF TG_OP IN ('UPDATE','DELETE') THEN
  payload:=to_jsonb(OLD); parent_id:=(payload->>'parent_sale_id')::UUID;
  IF parent_id IS NOT NULL THEN
   parents:=parents||jsonb_build_array(jsonb_build_object(
     'tenant',payload->>'tenant_id','parent',parent_id));
  END IF;
 END IF;
 IF TG_OP IN ('INSERT','UPDATE') THEN
  payload:=to_jsonb(NEW); parent_id:=(payload->>'parent_sale_id')::UUID;
  IF parent_id IS NOT NULL THEN
   parents:=parents||jsonb_build_array(jsonb_build_object(
     'tenant',payload->>'tenant_id','parent',parent_id));
  END IF;
 END IF;
 FOR target IN SELECT DISTINCT (value->>'tenant')::UUID AS tenant,
   (value->>'parent')::UUID AS parent FROM jsonb_array_elements(parents)
   ORDER BY tenant,parent LOOP
  IF session_user='aurum_app'
    AND target.tenant IS DISTINCT FROM public.current_tenant_id() THEN
   RAISE EXCEPTION 'mixed_electronic_refund_binding_actor_denied' USING ERRCODE='42501';
  END IF;
  IF current_setting('transaction_isolation')<>'read committed' THEN
   RAISE EXCEPTION 'mixed_electronic_refund_binding_isolation_required' USING ERRCODE='23514';
  END IF;
  PERFORM 1 FROM public.sale WHERE tenant_id=target.tenant AND id=target.parent FOR UPDATE;
  IF public.mixed_electronic_refund_binding_active(target.tenant,target.parent) THEN
   RAISE EXCEPTION 'mixed_electronic_refund_binding_parent_frozen' USING ERRCODE='23514';
  END IF;
 END LOOP;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION public.trg_mixed_electronic_refund_activation_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE actor UUID; binding public.pos_mixed_electronic_refund_binding%ROWTYPE;
 parent public.sale%ROWTYPE; target_shift public.shift%ROWTYPE;
BEGIN
 IF TG_OP<>'INSERT' THEN
  RAISE EXCEPTION 'mixed_electronic_refund_activation_immutable' USING ERRCODE='55000';
 END IF;
 IF current_setting('transaction_isolation')<>'read committed' THEN
  RAISE EXCEPTION 'mixed_electronic_refund_activation_isolation_required' USING ERRCODE='23514';
 END IF;
 actor:=public.current_app_user_id();
 IF session_user<>'aurum_app' OR public.is_support_session() OR actor IS NULL
   OR NEW.tenant_id IS DISTINCT FROM public.current_tenant_id()
   OR NEW.actor_id IS DISTINCT FROM actor THEN
  RAISE EXCEPTION 'mixed_electronic_refund_activation_actor_denied' USING ERRCODE='42501';
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(
   'mixed-electronic-refund-review:'||NEW.tenant_id||':'||NEW.id,0));
 SELECT * INTO parent FROM public.sale
   WHERE tenant_id=NEW.tenant_id AND id=NEW.parent_sale_id FOR UPDATE;
 IF NOT FOUND OR parent.branch_id<>NEW.branch_id OR parent.register_id<>NEW.register_id
   OR parent.sale_type<>'sale' OR parent.status<>'completed'
   OR parent.parent_sale_id IS NOT NULL OR parent.is_test OR parent.currency<>'TJS'
   OR NOT EXISTS(SELECT 1 FROM public.pos_mixed_checkout c
     WHERE c.tenant_id=NEW.tenant_id AND c.sale_id=parent.id AND c.branch_id=NEW.branch_id
       AND c.payment_journal_version=3) THEN
  RAISE EXCEPTION 'mixed_electronic_refund_activation_source_invalid' USING ERRCODE='23514';
 END IF;
 SELECT * INTO binding FROM public.pos_mixed_electronic_refund_binding
   WHERE tenant_id=NEW.tenant_id AND id=NEW.id;
 IF NOT FOUND OR (binding.parent_sale_id,binding.branch_id,binding.actor_id)
   IS DISTINCT FROM (NEW.parent_sale_id,NEW.branch_id,actor)
   OR EXISTS(SELECT 1 FROM public.pos_mixed_electronic_refund_binding_cancel
     WHERE tenant_id=NEW.tenant_id AND id=NEW.id) THEN
  RAISE EXCEPTION 'mixed_electronic_refund_activation_binding_invalid' USING ERRCODE='23514';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM public.app_user WHERE id=actor AND status='active')
   OR NOT coalesce(public.tenant_actor_has_scoped_permission(
     NEW.tenant_id,'pos.refund',NEW.branch_id),false) THEN
  RAISE EXCEPTION 'mixed_electronic_refund_activation_actor_denied' USING ERRCODE='42501';
 END IF;
 SELECT * INTO target_shift FROM public.shift
   WHERE tenant_id=NEW.tenant_id AND id=NEW.shift_id FOR UPDATE;
 IF NOT FOUND OR target_shift.branch_id<>NEW.branch_id
   OR target_shift.register_id<>NEW.register_id OR target_shift.opened_by_user_id<>actor
   OR target_shift.status<>'open' OR target_shift.closed_at IS NOT NULL
   OR target_shift.currency<>'TJS' THEN
  RAISE EXCEPTION 'mixed_electronic_refund_activation_shift_invalid' USING ERRCODE='23514';
 END IF;
 PERFORM 1 FROM public.register WHERE tenant_id=NEW.tenant_id AND id=NEW.register_id
   AND branch_id=NEW.branch_id AND is_active FOR SHARE;
 IF NOT FOUND THEN
  RAISE EXCEPTION 'mixed_electronic_refund_activation_register_invalid' USING ERRCODE='23514';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM public.app_user WHERE id=actor AND status='active')
   OR NOT coalesce(public.tenant_actor_has_scoped_permission(
     NEW.tenant_id,'pos.refund',NEW.branch_id),false) THEN
  RAISE EXCEPTION 'mixed_electronic_refund_activation_actor_denied' USING ERRCODE='42501';
 END IF;
 NEW.created_at:=greatest(clock_timestamp(),binding.created_at);
 NEW.created_by:=actor;
 RETURN NEW;
END $$;
"""


def upgrade() -> None:
    _sql("""
      CREATE TEMP TABLE electronic_refund_binding_reference_grants AS
        SELECT oid::regclass::TEXT AS relation,
          has_table_privilege('aurum_schema_owner',oid,'REFERENCES') AS granted
        FROM pg_class WHERE oid IN ('public.pos_mixed_electronic_refund_intent'::regclass,
          'public.app_user'::regclass);
      GRANT REFERENCES ON public.pos_mixed_electronic_refund_intent,public.app_user
        TO aurum_schema_owner;
    """)
    for table in BINDING_TABLES:
        _sql(f"""
          CREATE TABLE public.{table} (
            tenant_id UUID NOT NULL,id UUID NOT NULL,parent_sale_id UUID NOT NULL,
            branch_id UUID NOT NULL,actor_id UUID NOT NULL REFERENCES public.app_user(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            created_by UUID NOT NULL REFERENCES public.app_user(id),
            PRIMARY KEY(tenant_id,id),
            FOREIGN KEY(tenant_id,id)
              REFERENCES public.pos_mixed_electronic_refund_intent(tenant_id,id),
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
    _sql("""
      DO $$ DECLARE r RECORD; BEGIN
        FOR r IN SELECT * FROM pg_temp.electronic_refund_binding_reference_grants
          WHERE NOT granted LOOP
          EXECUTE format('REVOKE REFERENCES ON TABLE %s FROM aurum_schema_owner',r.relation);
        END LOOP;
      END $$;
      DROP TABLE pg_temp.electronic_refund_binding_reference_grants;
      CREATE INDEX ix_mixed_electronic_refund_binding_parent
        ON public.pos_mixed_electronic_refund_binding(tenant_id,parent_sale_id);
    """)
    _sql("""
      CREATE TEMP TABLE electronic_refund_activation_reference_grants AS
        SELECT oid::regclass::TEXT AS relation,
          has_table_privilege('aurum_schema_owner',oid,'REFERENCES') AS granted
        FROM pg_class WHERE oid IN ('public.pos_mixed_electronic_refund_binding'::regclass,
          'public.app_user'::regclass);
      GRANT REFERENCES ON public.pos_mixed_electronic_refund_binding,public.app_user
        TO aurum_schema_owner;
      CREATE TABLE public.pos_mixed_electronic_refund_activation (
        tenant_id UUID NOT NULL,id UUID NOT NULL,parent_sale_id UUID NOT NULL,
        branch_id UUID NOT NULL,actor_id UUID NOT NULL REFERENCES public.app_user(id),
        register_id UUID NOT NULL,shift_id UUID NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        created_by UUID NOT NULL REFERENCES public.app_user(id),
        PRIMARY KEY(tenant_id,id), FOREIGN KEY(tenant_id,id)
          REFERENCES public.pos_mixed_electronic_refund_binding(tenant_id,id),
        CHECK(substring(id::TEXT,15,1)='4' AND substring(id::TEXT,20,1) IN ('8','9','a','b'))
      );
      ALTER TABLE public.pos_mixed_electronic_refund_activation OWNER TO aurum_schema_owner;
      ALTER TABLE public.pos_mixed_electronic_refund_activation ENABLE ROW LEVEL SECURITY;
      ALTER TABLE public.pos_mixed_electronic_refund_activation FORCE ROW LEVEL SECURITY;
      REVOKE ALL ON public.pos_mixed_electronic_refund_activation
        FROM PUBLIC,aurum_app,aurum_support;
      GRANT SELECT,INSERT ON public.pos_mixed_electronic_refund_activation TO aurum_app;
      GRANT SELECT ON public.pos_mixed_electronic_refund_activation TO aurum_support;
      CREATE POLICY schema_owner ON public.pos_mixed_electronic_refund_activation
        TO aurum_schema_owner
        USING(true) WITH CHECK(true);
      CREATE POLICY support ON public.pos_mixed_electronic_refund_activation FOR SELECT
        TO aurum_support USING(true);
      CREATE POLICY tenant_actor ON public.pos_mixed_electronic_refund_activation
        TO aurum_app USING(
        current_user='aurum_app' AND session_user='aurum_app'
        AND NOT public.is_tenant_support_session()
        AND tenant_id=public.current_tenant_id() AND actor_id=public.current_app_user_id()
        AND EXISTS(SELECT 1 FROM public.app_user
          WHERE id=public.current_app_user_id() AND status='active')
        AND public.tenant_actor_has_scoped_permission(tenant_id,'pos.refund',branch_id))
        WITH CHECK(tenant_id=public.current_tenant_id() AND actor_id=public.current_app_user_id()
        AND public.tenant_actor_has_scoped_permission(tenant_id,'pos.refund',branch_id));
      DO $$ DECLARE r RECORD; BEGIN
        FOR r IN SELECT * FROM pg_temp.electronic_refund_activation_reference_grants
          WHERE NOT granted LOOP
          EXECUTE format('REVOKE REFERENCES ON TABLE %s FROM aurum_schema_owner',r.relation);
        END LOOP;
      END $$;
      DROP TABLE pg_temp.electronic_refund_activation_reference_grants;
    """)
    _sql(SQL)
    for signature in FUNCTIONS:
        _sql(f"""
          ALTER FUNCTION public.{signature} OWNER TO aurum_schema_owner;
          REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC,aurum_app,aurum_support;
        """)
    for table in BINDING_TABLES:
        _sql(f"""
          CREATE TRIGGER a00_tenant_access BEFORE INSERT OR UPDATE OR DELETE ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION public.trg_enforce_tenant_access('pos');
          CREATE TRIGGER a10_mixed_electronic_refund_binding_guard BEFORE INSERT OR UPDATE OR DELETE
            ON public.{table} FOR EACH ROW
            EXECUTE FUNCTION public.trg_mixed_electronic_refund_binding_guard();
          CREATE TRIGGER z90_mixed_electronic_refund_binding_audit AFTER INSERT OR UPDATE OR DELETE
            ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.trg_audit_log();
        """)
    for table in FENCED:
        _sql(f"""
          CREATE TRIGGER a01_mixed_electronic_refund_binding_fence BEFORE INSERT OR UPDATE OR DELETE
            ON public.{table} FOR EACH ROW
            EXECUTE FUNCTION public.trg_mixed_electronic_refund_binding_fence();
        """)

    _sql("""
      CREATE TRIGGER a00_tenant_access BEFORE INSERT OR UPDATE OR DELETE
        ON public.pos_mixed_electronic_refund_activation
        FOR EACH ROW EXECUTE FUNCTION public.trg_enforce_tenant_access('pos');
      CREATE TRIGGER a10_mixed_electronic_refund_activation_guard BEFORE INSERT OR UPDATE OR DELETE
        ON public.pos_mixed_electronic_refund_activation
        FOR EACH ROW EXECUTE FUNCTION public.trg_mixed_electronic_refund_activation_guard();
      CREATE TRIGGER z90_mixed_electronic_refund_activation_audit AFTER INSERT OR UPDATE OR DELETE
        ON public.pos_mixed_electronic_refund_activation
        FOR EACH ROW EXECUTE FUNCTION public.trg_audit_log();
    """)


def downgrade() -> None:
    _sql("""
      LOCK TABLE public.pos_mixed_electronic_refund_binding,
        public.pos_mixed_electronic_refund_binding_cancel,
        public.pos_mixed_electronic_refund_activation IN ACCESS EXCLUSIVE MODE;
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.pos_mixed_electronic_refund_binding)
          OR EXISTS(SELECT 1 FROM public.pos_mixed_electronic_refund_binding_cancel)
          OR EXISTS(SELECT 1 FROM public.pos_mixed_electronic_refund_activation) THEN
          RAISE EXCEPTION 'mixed_electronic_refund_preparation_downgrade_requires_empty_history'
            USING ERRCODE='55000';
        END IF;
      END $$;
    """)
    for table in FENCED:
        _sql(f"DROP TRIGGER a01_mixed_electronic_refund_binding_fence ON public.{table};")
    for table in reversed(TABLES):
        _sql(f"DROP TABLE public.{table};")
    for signature in reversed(FUNCTIONS):
        _sql(f"DROP FUNCTION public.{signature};")
