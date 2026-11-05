"""Record irreversible electronic refund activation without authorizing payout.

Revision ID: 0216
Revises: 0215
"""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from sqlalchemy import text

from alembic import op

revision = "0216"
down_revision = "0215"
branch_labels = None
depends_on = None

TABLE = "pos_unit_electronic_refund_activation"
FUNCTION = "trg_unit_electronic_refund_activation_guard()"
ANCHOR = (
    " -- Cancellation remains available after prices/history change. It is a permanent tombstone."
)
PATCH = """ IF TG_TABLE_NAME='pos_unit_electronic_refund_binding_cancel'
   AND EXISTS(SELECT 1 FROM public.pos_unit_electronic_refund_activation
     WHERE tenant_id=NEW.tenant_id AND id=NEW.id) THEN
  RAISE EXCEPTION 'unit_electronic_refund_activation_cancel_denied' USING ERRCODE='23514';
 END IF;
"""


def _sql(sql: str) -> None:
    spec = spec_from_file_location(
        "aurum_0216_sql", Path(__file__).with_name("0171_unit_cash_intents.py")
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load historical SQL executor")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    module._sql(sql)


def _patch_binding_guard(*, restore: bool = False) -> None:
    old, new = (PATCH + ANCHOR, ANCHOR) if restore else (ANCHOR, PATCH + ANCHOR)
    definition = op.get_bind().scalar(
        text(
            "SELECT pg_get_functiondef("
            "'public.trg_unit_electronic_refund_binding_guard()'::regprocedure)"
        )
    )
    if not isinstance(definition, str) or definition.count(old) != 1:
        raise RuntimeError("Electronic refund activation guard anchor differs")
    # Use the driver's single-statement path: the historical SQL splitter only
    # understands $$ bodies, not arbitrary nested dollar quote tags.
    op.get_bind().exec_driver_sql(definition.replace(old, new, 1))


SQL = """
CREATE FUNCTION public.trg_unit_electronic_refund_activation_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE actor UUID; binding public.pos_unit_electronic_refund_binding%ROWTYPE;
 parent public.sale%ROWTYPE; target_shift public.shift%ROWTYPE;
BEGIN
 IF TG_OP<>'INSERT' THEN
  RAISE EXCEPTION 'unit_electronic_refund_activation_immutable' USING ERRCODE='55000';
 END IF;
 IF current_setting('transaction_isolation')<>'read committed' THEN
  RAISE EXCEPTION 'unit_electronic_refund_activation_isolation_required' USING ERRCODE='23514';
 END IF;
 actor:=public.current_app_user_id();
 IF session_user<>'aurum_app' OR public.is_support_session() OR actor IS NULL
   OR NEW.tenant_id IS DISTINCT FROM public.current_tenant_id()
   OR NEW.actor_id IS DISTINCT FROM actor THEN
  RAISE EXCEPTION 'unit_electronic_refund_activation_actor_denied' USING ERRCODE='42501';
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(
   'unit-electronic-refund-review:'||NEW.tenant_id||':'||NEW.id,0));
 SELECT * INTO parent FROM public.sale
   WHERE tenant_id=NEW.tenant_id AND id=NEW.parent_sale_id FOR UPDATE;
 IF NOT FOUND OR parent.branch_id<>NEW.branch_id OR parent.register_id<>NEW.register_id
   OR parent.sale_type<>'sale' OR parent.status<>'completed'
   OR parent.parent_sale_id IS NOT NULL OR parent.is_test OR parent.currency<>'TJS'
   OR NOT EXISTS(SELECT 1 FROM public.pos_physical_checkout c
     WHERE c.tenant_id=NEW.tenant_id AND c.sale_id=parent.id AND c.branch_id=NEW.branch_id
       AND c.payment_journal_version=3) THEN
  RAISE EXCEPTION 'unit_electronic_refund_activation_source_invalid' USING ERRCODE='23514';
 END IF;
 SELECT * INTO binding FROM public.pos_unit_electronic_refund_binding
   WHERE tenant_id=NEW.tenant_id AND id=NEW.id;
 IF NOT FOUND OR (binding.parent_sale_id,binding.branch_id,binding.actor_id)
   IS DISTINCT FROM (NEW.parent_sale_id,NEW.branch_id,actor)
   OR EXISTS(SELECT 1 FROM public.pos_unit_electronic_refund_binding_cancel
     WHERE tenant_id=NEW.tenant_id AND id=NEW.id) THEN
  RAISE EXCEPTION 'unit_electronic_refund_activation_binding_invalid' USING ERRCODE='23514';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM public.app_user WHERE id=actor AND status='active')
   OR NOT coalesce(public.tenant_actor_has_scoped_permission(
     NEW.tenant_id,'pos.refund',NEW.branch_id),false) THEN
  RAISE EXCEPTION 'unit_electronic_refund_activation_actor_denied' USING ERRCODE='42501';
 END IF;
 SELECT * INTO target_shift FROM public.shift
   WHERE tenant_id=NEW.tenant_id AND id=NEW.shift_id FOR UPDATE;
 IF NOT FOUND OR target_shift.branch_id<>NEW.branch_id
   OR target_shift.register_id<>NEW.register_id OR target_shift.opened_by_user_id<>actor
   OR target_shift.status<>'open' OR target_shift.closed_at IS NOT NULL
   OR target_shift.currency<>'TJS' THEN
  RAISE EXCEPTION 'unit_electronic_refund_activation_shift_invalid' USING ERRCODE='23514';
 END IF;
 PERFORM 1 FROM public.register WHERE tenant_id=NEW.tenant_id AND id=NEW.register_id
   AND branch_id=NEW.branch_id AND is_active FOR SHARE;
 IF NOT FOUND THEN
  RAISE EXCEPTION 'unit_electronic_refund_activation_register_invalid' USING ERRCODE='23514';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM public.app_user WHERE id=actor AND status='active')
   OR NOT coalesce(public.tenant_actor_has_scoped_permission(
     NEW.tenant_id,'pos.refund',NEW.branch_id),false) THEN
  RAISE EXCEPTION 'unit_electronic_refund_activation_actor_denied' USING ERRCODE='42501';
 END IF;
 NEW.created_at:=greatest(clock_timestamp(),binding.created_at);
 NEW.created_by:=actor;
 RETURN NEW;
END $$;
"""


def upgrade() -> None:
    _sql("""
      CREATE TEMP TABLE electronic_refund_activation_reference_grants AS
        SELECT oid::regclass::TEXT AS relation,
          has_table_privilege('aurum_schema_owner',oid,'REFERENCES') AS granted
        FROM pg_class WHERE oid IN ('public.pos_unit_electronic_refund_binding'::regclass,
          'public.app_user'::regclass);
      GRANT REFERENCES ON public.pos_unit_electronic_refund_binding,public.app_user
        TO aurum_schema_owner;
      CREATE TABLE public.pos_unit_electronic_refund_activation (
        tenant_id UUID NOT NULL,id UUID NOT NULL,parent_sale_id UUID NOT NULL,
        branch_id UUID NOT NULL,actor_id UUID NOT NULL REFERENCES public.app_user(id),
        register_id UUID NOT NULL,shift_id UUID NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        created_by UUID NOT NULL REFERENCES public.app_user(id),
        PRIMARY KEY(tenant_id,id), FOREIGN KEY(tenant_id,id)
          REFERENCES public.pos_unit_electronic_refund_binding(tenant_id,id),
        CHECK(substring(id::TEXT,15,1)='4' AND substring(id::TEXT,20,1) IN ('8','9','a','b'))
      );
      ALTER TABLE public.pos_unit_electronic_refund_activation OWNER TO aurum_schema_owner;
      ALTER TABLE public.pos_unit_electronic_refund_activation ENABLE ROW LEVEL SECURITY;
      ALTER TABLE public.pos_unit_electronic_refund_activation FORCE ROW LEVEL SECURITY;
      REVOKE ALL ON public.pos_unit_electronic_refund_activation
        FROM PUBLIC,aurum_app,aurum_support;
      GRANT SELECT,INSERT ON public.pos_unit_electronic_refund_activation TO aurum_app;
      GRANT SELECT ON public.pos_unit_electronic_refund_activation TO aurum_support;
      CREATE POLICY schema_owner ON public.pos_unit_electronic_refund_activation
        TO aurum_schema_owner
        USING(true) WITH CHECK(true);
      CREATE POLICY support ON public.pos_unit_electronic_refund_activation FOR SELECT
        TO aurum_support USING(true);
      CREATE POLICY tenant_actor ON public.pos_unit_electronic_refund_activation TO aurum_app USING(
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
    _sql(f"""
      ALTER FUNCTION public.{FUNCTION} OWNER TO aurum_schema_owner;
      REVOKE ALL ON FUNCTION public.{FUNCTION} FROM PUBLIC,aurum_app,aurum_support;
      CREATE TRIGGER a00_tenant_access BEFORE INSERT OR UPDATE OR DELETE
        ON public.{TABLE} FOR EACH ROW EXECUTE FUNCTION public.trg_enforce_tenant_access('pos');
      CREATE TRIGGER a10_unit_electronic_refund_activation_guard BEFORE INSERT OR UPDATE OR DELETE
        ON public.{TABLE} FOR EACH ROW EXECUTE FUNCTION public.{FUNCTION};
      CREATE TRIGGER z90_unit_electronic_refund_activation_audit AFTER INSERT OR UPDATE OR DELETE
        ON public.{TABLE} FOR EACH ROW EXECUTE FUNCTION public.trg_audit_log();
    """)
    _patch_binding_guard()


def downgrade() -> None:
    _sql(f"""
      LOCK TABLE public.{TABLE} IN ACCESS EXCLUSIVE MODE;
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.{TABLE}) THEN
          RAISE EXCEPTION 'unit_electronic_refund_activation_downgrade_requires_empty_history'
            USING ERRCODE='55000';
        END IF;
      END $$;
    """)
    _patch_binding_guard(restore=True)
    _sql(f"DROP TABLE public.{TABLE}; DROP FUNCTION public.{FUNCTION};")
