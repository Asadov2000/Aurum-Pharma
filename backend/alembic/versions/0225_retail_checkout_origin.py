"""Capture immutable writer identity only for new app-role retail checkouts.

Revision ID: 0225
Revises: 0224
"""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

revision = "0225"
down_revision = "0224"
branch_labels = None
depends_on = None

SOURCE_TABLES = ("pos_physical_checkout", "pos_mixed_checkout")
FUNCTIONS = ("trg_capture_retail_checkout_origin()", "trg_retail_checkout_origin_guard()")


def _sql(statement: str) -> None:
    spec = spec_from_file_location(
        "aurum_0225_sql", Path(__file__).with_name("0171_unit_cash_intents.py")
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load historical SQL executor")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    module._sql(statement)


SQL = """
CREATE FUNCTION public.trg_capture_retail_checkout_origin() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE sale_row public.sale%ROWTYPE; source_kind TEXT; expected_version INTEGER;
 writer_node UUID; writer_epoch BIGINT; physical_id UUID; mixed_id UUID;
BEGIN
 IF TG_OP<>'INSERT' OR TG_TABLE_SCHEMA<>'public'
   OR TG_TABLE_NAME NOT IN ('pos_physical_checkout','pos_mixed_checkout') THEN
  RAISE EXCEPTION 'retail_origin_capture_context_invalid' USING ERRCODE='42501';
 END IF;
 -- Privileged imports and historical migrations are deliberately not assigned
 -- whichever writer happens to own the branch when they run.
 IF SESSION_USER<>'aurum_app' THEN RETURN NEW; END IF;
 SELECT * INTO sale_row FROM public.sale
  WHERE tenant_id=NEW.tenant_id AND id=NEW.sale_id;
 IF NOT FOUND THEN
  RAISE EXCEPTION 'retail_origin_sale_missing' USING ERRCODE='23514';
 END IF;
 IF sale_row.is_test THEN
  RETURN NEW;
 END IF;
 IF TG_TABLE_NAME='pos_physical_checkout' THEN
  source_kind:='physical'; expected_version:=3; physical_id:=NEW.id;
 ELSE
  source_kind:='mixed'; expected_version:=4; mixed_id:=NEW.id;
 END IF;
 IF public.is_tenant_support_session()
   OR public.current_tenant_id() IS DISTINCT FROM NEW.tenant_id
   OR public.current_app_user_id() IS DISTINCT FROM NEW.cashier_user_id
   OR NOT public.tenant_actor_has_scoped_permission(NEW.tenant_id,'pos.sell',NEW.branch_id)
   OR NEW.created_by IS DISTINCT FROM NEW.cashier_user_id
   OR (sale_row.branch_id,sale_row.register_id,sale_row.cashier_user_id)
      IS DISTINCT FROM (NEW.branch_id,NEW.register_id,NEW.cashier_user_id)
   OR sale_row.sale_type<>'sale' OR sale_row.parent_sale_id IS NOT NULL
   OR sale_row.currency<>'TJS'
   OR NEW.result_payload->'is_test' IS DISTINCT FROM 'false'::JSONB
   OR NEW.result_payload->'schema_version' IS DISTINCT FROM to_jsonb(expected_version)
   OR NEW.result_payload->>'event_id' IS DISTINCT FROM NEW.id::TEXT
   OR NEW.result_payload->>'operation_id' IS DISTINCT FROM NEW.id::TEXT
   OR NEW.result_payload->>'stock_command_id' IS DISTINCT FROM NEW.stock_command_id::TEXT
 THEN
  RAISE EXCEPTION 'retail_origin_source_scope_invalid' USING ERRCODE='23514';
 END IF;
 PERFORM public.assert_current_branch_writer(
  NEW.tenant_id,NEW.branch_id,NEW.register_id,false);
 SELECT stream.writer_node_id,stream.writer_epoch INTO writer_node,writer_epoch
 FROM public.sync_stream stream
 JOIN public.sync_writer_epoch epoch
  ON (epoch.tenant_id,epoch.branch_id,epoch.writer_epoch,epoch.writer_node_id)=
    (stream.tenant_id,stream.branch_id,stream.writer_epoch,stream.writer_node_id)
 JOIN public.sync_node node
  ON (node.id,node.tenant_id,node.branch_id)=
    (stream.writer_node_id,stream.tenant_id,stream.branch_id)
 WHERE stream.tenant_id=NEW.tenant_id AND stream.branch_id=NEW.branch_id
  AND epoch.state='active' AND epoch.capability='cloud_full'
  AND epoch.allowed_register_id IS NULL
  AND node.node_kind='cloud' AND node.mode='cloud_writer' AND node.status='active'
 FOR SHARE OF stream;
 IF NOT FOUND THEN
  RAISE EXCEPTION 'retail_origin_writer_unavailable' USING ERRCODE='55000';
 END IF;
 INSERT INTO public.pos_retail_checkout_origin(
  tenant_id,id,source_kind,sale_id,branch_id,register_id,cashier_user_id,
  physical_checkout_id,mixed_checkout_id,origin_node_id,writer_epoch,created_at,created_by)
 VALUES(NEW.tenant_id,NEW.id,source_kind,NEW.sale_id,NEW.branch_id,NEW.register_id,
  NEW.cashier_user_id,physical_id,mixed_id,writer_node,writer_epoch,
  statement_timestamp(),NEW.cashier_user_id);
 RETURN NEW;
END;
$$;

CREATE FUNCTION public.trg_retail_checkout_origin_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE source_row RECORD;
BEGIN
 IF TG_OP<>'INSERT' THEN
  RAISE EXCEPTION 'retail_checkout_origin_immutable' USING ERRCODE='23514';
 END IF;
 IF SESSION_USER<>'aurum_app' OR CURRENT_USER<>'aurum_schema_owner'
   OR pg_trigger_depth()<>2 OR public.is_tenant_support_session()
   OR public.current_tenant_id() IS DISTINCT FROM NEW.tenant_id
   OR public.current_app_user_id() IS DISTINCT FROM NEW.cashier_user_id
   OR NEW.created_by IS DISTINCT FROM NEW.cashier_user_id THEN
  RAISE EXCEPTION 'retail_origin_insert_requires_capture' USING ERRCODE='42501';
 END IF;
 IF NEW.source_kind='physical' THEN
  SELECT id,sale_id,branch_id,register_id,cashier_user_id INTO source_row
   FROM public.pos_physical_checkout WHERE tenant_id=NEW.tenant_id AND id=NEW.id;
 ELSIF NEW.source_kind='mixed' THEN
  SELECT id,sale_id,branch_id,register_id,cashier_user_id INTO source_row
   FROM public.pos_mixed_checkout WHERE tenant_id=NEW.tenant_id AND id=NEW.id;
 ELSE
  RAISE EXCEPTION 'retail_origin_source_kind_invalid' USING ERRCODE='23514';
 END IF;
 IF NOT FOUND OR (source_row.sale_id,source_row.branch_id,source_row.register_id,
   source_row.cashier_user_id) IS DISTINCT FROM
   (NEW.sale_id,NEW.branch_id,NEW.register_id,NEW.cashier_user_id) THEN
  RAISE EXCEPTION 'retail_origin_source_binding_invalid' USING ERRCODE='23514';
 END IF;
 PERFORM 1 FROM public.sync_stream stream
 JOIN public.sync_writer_epoch epoch
  ON (epoch.tenant_id,epoch.branch_id,epoch.writer_epoch,epoch.writer_node_id)=
    (stream.tenant_id,stream.branch_id,stream.writer_epoch,stream.writer_node_id)
 JOIN public.sync_node node
  ON (node.id,node.tenant_id,node.branch_id)=
    (stream.writer_node_id,stream.tenant_id,stream.branch_id)
 WHERE stream.tenant_id=NEW.tenant_id AND stream.branch_id=NEW.branch_id
  AND stream.writer_node_id=NEW.origin_node_id AND stream.writer_epoch=NEW.writer_epoch
  AND epoch.state='active' AND epoch.capability='cloud_full'
  AND epoch.allowed_register_id IS NULL
  AND node.node_kind='cloud' AND node.mode='cloud_writer' AND node.status='active'
 FOR SHARE OF stream;
 IF NOT FOUND THEN
  RAISE EXCEPTION 'retail_origin_writer_binding_invalid' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END;
$$;
"""


def upgrade() -> None:
    _sql("""
      CREATE TEMP TABLE retail_origin_reference_grants AS
       SELECT oid::regclass::TEXT AS relation,
        has_table_privilege('aurum_schema_owner',oid,'REFERENCES') AS granted
       FROM pg_class WHERE oid IN ('public.sale'::regclass,'public.app_user'::regclass,
        'public.register'::regclass,'public.pos_physical_checkout'::regclass,
        'public.pos_mixed_checkout'::regclass,'public.sync_writer_epoch'::regclass);
      GRANT REFERENCES ON public.sale,public.app_user,public.register,
       public.pos_physical_checkout,public.pos_mixed_checkout,public.sync_writer_epoch
       TO aurum_schema_owner;
      CREATE TABLE public.pos_retail_checkout_origin (
       tenant_id UUID NOT NULL,id UUID NOT NULL,
       source_kind TEXT NOT NULL CHECK(source_kind IN ('physical','mixed')),
       sale_id UUID NOT NULL,branch_id UUID NOT NULL,register_id UUID NOT NULL,
       cashier_user_id UUID NOT NULL REFERENCES public.app_user(id),
       physical_checkout_id UUID,mixed_checkout_id UUID,
       origin_node_id UUID NOT NULL,writer_epoch BIGINT NOT NULL CHECK(writer_epoch>0),
       created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
       created_by UUID NOT NULL REFERENCES public.app_user(id),
       PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,sale_id),
       CHECK(created_by=cashier_user_id),
       CHECK((source_kind='physical' AND physical_checkout_id IS NOT NULL
          AND physical_checkout_id=id AND mixed_checkout_id IS NULL)
        OR (source_kind='mixed' AND mixed_checkout_id IS NOT NULL
          AND mixed_checkout_id=id AND physical_checkout_id IS NULL)),
       FOREIGN KEY(tenant_id,sale_id) REFERENCES public.sale(tenant_id,id) ON DELETE RESTRICT,
       FOREIGN KEY(register_id,tenant_id,branch_id)
        REFERENCES public.register(id,tenant_id,branch_id) ON DELETE RESTRICT,
       FOREIGN KEY(tenant_id,physical_checkout_id)
        REFERENCES public.pos_physical_checkout(tenant_id,id) ON DELETE RESTRICT,
       FOREIGN KEY(tenant_id,mixed_checkout_id)
        REFERENCES public.pos_mixed_checkout(tenant_id,id) ON DELETE RESTRICT,
       FOREIGN KEY(tenant_id,branch_id,writer_epoch,origin_node_id)
        REFERENCES public.sync_writer_epoch(tenant_id,branch_id,writer_epoch,writer_node_id)
        ON DELETE RESTRICT);
      DO $$ DECLARE r RECORD; BEGIN
       FOR r IN SELECT * FROM pg_temp.retail_origin_reference_grants WHERE NOT granted LOOP
        EXECUTE format('REVOKE REFERENCES ON %s FROM aurum_schema_owner',r.relation);
       END LOOP;
      END $$;
      DROP TABLE pg_temp.retail_origin_reference_grants;
      ALTER TABLE public.pos_retail_checkout_origin OWNER TO aurum_schema_owner;
      ALTER TABLE public.pos_retail_checkout_origin ENABLE ROW LEVEL SECURITY;
      ALTER TABLE public.pos_retail_checkout_origin FORCE ROW LEVEL SECURITY;
      REVOKE ALL ON public.pos_retail_checkout_origin FROM PUBLIC,aurum_app,aurum_support;
      GRANT SELECT ON public.pos_retail_checkout_origin TO aurum_app,aurum_support;
      CREATE POLICY schema_owner ON public.pos_retail_checkout_origin TO aurum_schema_owner
       USING(true) WITH CHECK(true);
      CREATE POLICY support ON public.pos_retail_checkout_origin FOR SELECT TO aurum_support
       USING(true);
      CREATE POLICY tenant_actor ON public.pos_retail_checkout_origin FOR SELECT TO aurum_app
       USING(session_user='aurum_app' AND NOT public.is_tenant_support_session()
        AND tenant_id=public.current_tenant_id()
        AND cashier_user_id=public.current_app_user_id()
        AND EXISTS(SELECT 1 FROM public.app_user
         WHERE id=public.current_app_user_id() AND status='active')
        AND public.tenant_actor_has_scoped_permission(tenant_id,'pos.sell',branch_id));
    """)
    _sql(SQL)
    for signature in FUNCTIONS:
        _sql(f"""
          ALTER FUNCTION public.{signature} OWNER TO aurum_schema_owner;
          REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC,aurum_app,aurum_support;
        """)
    for table in SOURCE_TABLES:
        _sql(f"""
          CREATE TRIGGER z80_retail_checkout_origin AFTER INSERT ON public.{table}
           FOR EACH ROW EXECUTE FUNCTION public.trg_capture_retail_checkout_origin();
        """)
    _sql("""
      CREATE TRIGGER a00_tenant_access BEFORE INSERT OR UPDATE OR DELETE
       ON public.pos_retail_checkout_origin FOR EACH ROW
       EXECUTE FUNCTION public.trg_enforce_tenant_access('pos');
      CREATE TRIGGER a10_retail_checkout_origin_guard BEFORE INSERT OR UPDATE OR DELETE
       ON public.pos_retail_checkout_origin FOR EACH ROW
       EXECUTE FUNCTION public.trg_retail_checkout_origin_guard();
      CREATE TRIGGER z90_retail_checkout_origin_audit AFTER INSERT OR UPDATE OR DELETE
       ON public.pos_retail_checkout_origin FOR EACH ROW
       EXECUTE FUNCTION public.trg_audit_log();
    """)


def downgrade() -> None:
    _sql("""
      LOCK TABLE public.pos_physical_checkout,public.pos_mixed_checkout,
       public.pos_retail_checkout_origin IN ACCESS EXCLUSIVE MODE;
      DO $$ BEGIN
       IF EXISTS(SELECT 1 FROM public.pos_retail_checkout_origin) THEN
        RAISE EXCEPTION 'retail_origin_downgrade_requires_empty_history' USING ERRCODE='55000';
       END IF;
      END $$;
    """)
    for table in reversed(SOURCE_TABLES):
        _sql(f"DROP TRIGGER z80_retail_checkout_origin ON public.{table};")
    _sql("DROP TRIGGER a10_retail_checkout_origin_guard ON public.pos_retail_checkout_origin;")
    for signature in reversed(FUNCTIONS):
        _sql(f"DROP FUNCTION public.{signature};")
    _sql("DROP TABLE public.pos_retail_checkout_origin;")
