"""Immutable physical v3 quarantine receipt, without stock or cash posting.

Revision ID: 0183
Revises: 0182
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "0183"
down_revision: str | Sequence[str] | None = "0182"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("pos_physical_return_command", "pos_physical_return_line", "pos_physical_return_slice")


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
        raise RuntimeError("Physical return audit redactor differs from expected definition")
    op.get_bind().exec_driver_sql(definition.replace(old, new, 1))


AUDIT_OLD = "'pos_unit_return_command','pos_unit_return_slice',"
AUDIT_NEW = (
    "'pos_physical_return_command','pos_physical_return_line','pos_physical_return_slice',"
    + AUDIT_OLD
)
AUDIT_COST_OLD = "'purchase_cost_per_pack','cost_original_pack_cost','original_cost_amount'"
AUDIT_COST_NEW = AUDIT_COST_OLD + ",'total_cost_amount'"
AUDIT_PAYLOAD_OLD = "IF p_table_name IN (" + AUDIT_NEW
AUDIT_PAYLOAD_NEW = """IF p_table_name = 'pos_physical_return_command' THEN
        v_result := jsonb_set(v_result,ARRAY['result_payload'],to_jsonb('***'::TEXT),false);
      END IF;
      """ + AUDIT_PAYLOAD_OLD

FUNCTIONS_SQL = """
CREATE FUNCTION public.trg_physical_return_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS $$
DECLARE c public.pos_physical_return_command%ROWTYPE; m public.pos_physical_checkout%ROWTYPE;
 s public.sale%ROWTYPE; l public.unit_stock_cart_line_v2%ROWTYPE;
 r public.pos_physical_return_line%ROWTYPE; e RECORD;
 v_prior NUMERIC; v_factor NUMERIC; v_start NUMERIC; v_stop NUMERIC;
 v_net NUMERIC; v_discount NUMERIC; v_cost NUMERIC;
BEGIN
 IF TG_OP<>'INSERT' THEN
  RAISE EXCEPTION 'physical_return_immutable' USING ERRCODE='55000';
 END IF;
 IF current_user<>'aurum_app' OR session_user<>'aurum_app' OR public.is_support_session()
   OR NEW.tenant_id IS DISTINCT FROM public.current_tenant_id()
   OR NOT EXISTS(SELECT 1 FROM public.app_user
       WHERE id=public.current_app_user_id() AND status='active')
   OR NOT coalesce(public.tenant_actor_has_scoped_permission(
       NEW.tenant_id,'pos.refund',NEW.branch_id),false) THEN
  RAISE EXCEPTION 'physical_return_actor_denied' USING ERRCODE='42501';
 END IF;
 IF TG_TABLE_NAME='pos_physical_return_command' THEN
  PERFORM pg_advisory_xact_lock(hashtextextended(
    'aurum-physical-return:'||NEW.tenant_id||':'||NEW.id,0));
  SELECT * INTO s FROM public.sale
    WHERE tenant_id=NEW.tenant_id AND id=NEW.parent_sale_id FOR UPDATE;
  SELECT * INTO m FROM public.pos_physical_checkout
    WHERE tenant_id=NEW.tenant_id AND id=NEW.parent_checkout_id;
  IF s.id IS NULL OR s.status<>'completed' OR s.sale_type<>'sale'
    OR s.parent_sale_id IS NOT NULL OR s.currency<>'TJS' OR m.id IS NULL
    OR (m.sale_id,m.stock_command_id,m.branch_id)
      IS DISTINCT FROM (NEW.parent_sale_id,NEW.stock_command_id,NEW.branch_id)
    OR s.branch_id IS DISTINCT FROM NEW.branch_id THEN
   RAISE EXCEPTION 'physical_return_source_invalid' USING ERRCODE='23514';
  END IF;
  IF NEW.actor_id IS DISTINCT FROM public.current_app_user_id() THEN
   RAISE EXCEPTION 'physical_return_actor_denied' USING ERRCODE='42501';
  END IF;
 ELSE
  SELECT * INTO c FROM public.pos_physical_return_command
    WHERE tenant_id=NEW.tenant_id AND id=NEW.command_id;
  IF c.id IS NULL OR c.branch_id IS DISTINCT FROM NEW.branch_id
    OR c.actor_id IS DISTINCT FROM public.current_app_user_id() THEN
   RAISE EXCEPTION 'physical_return_scope_invalid' USING ERRCODE='23514';
  END IF;
  PERFORM 1 FROM public.sale WHERE tenant_id=c.tenant_id AND id=c.parent_sale_id FOR UPDATE;
  SELECT * INTO l FROM public.unit_stock_cart_line_v2
    WHERE tenant_id=c.tenant_id AND command_id=c.stock_command_id AND line_id=NEW.line_id;
  IF l.line_id IS NULL THEN
   RAISE EXCEPTION 'physical_return_source_line_invalid' USING ERRCODE='23514';
  END IF;
  IF TG_TABLE_NAME='pos_physical_return_line' THEN
   SELECT coalesce(sum(base_units),0) INTO v_prior FROM public.pos_physical_return_line
     WHERE tenant_id=NEW.tenant_id AND stock_command_id=c.stock_command_id
       AND line_id=NEW.line_id;
   v_factor:=CASE NEW.sale_format WHEN 'pack' THEN (l.packaging->>'units_per_pack')::NUMERIC
     WHEN 'blister' THEN (l.packaging->>'units_per_blister')::NUMERIC ELSE 1 END;
   v_net:=(div(l.net_amount*100*(v_prior+NEW.base_units),l.base_units)
      -div(l.net_amount*100*v_prior,l.base_units))/100;
   v_discount:=(div(l.discount_amount*100*(v_prior+NEW.base_units),l.base_units)
      -div(l.discount_amount*100*v_prior,l.base_units))/100;
   IF NEW.stock_command_id IS DISTINCT FROM c.stock_command_id
     OR NEW.catalog_id IS DISTINCT FROM l.catalog_id
     OR NEW.offset<>v_prior OR v_prior+NEW.base_units>l.base_units
     OR v_factor IS NULL OR NEW.base_units<>NEW.quantity*v_factor
     OR (NEW.sale_format='unit' AND NEW.quantity%(l.packaging->>'dispense_step')::BIGINT<>0)
     OR NEW.net_amount<>v_net OR NEW.discount_amount<>v_discount
     OR NEW.gross_amount<>v_net+v_discount THEN
    RAISE EXCEPTION 'physical_return_allocation_invalid' USING ERRCODE='23514';
   END IF;
  ELSE
   SELECT * INTO r FROM public.pos_physical_return_line
     WHERE tenant_id=NEW.tenant_id AND command_id=NEW.command_id AND line_id=NEW.line_id;
   SELECT * INTO e FROM (
     SELECT movement.*,coalesce(sum(units_taken) OVER(ORDER BY entry_order,id
       ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING),0) AS source_start
     FROM public.unit_stock_movement_v2 movement WHERE tenant_id=c.tenant_id
       AND command_id=c.stock_command_id AND line_id=NEW.line_id AND units_taken>0
   ) movement WHERE id=NEW.source_movement_id;
   IF r.line_id IS NULL OR e.id IS NULL OR e.branch_id IS DISTINCT FROM NEW.branch_id
      OR e.catalog_id IS DISTINCT FROM l.catalog_id OR e.lot_id IS DISTINCT FROM NEW.lot_id
      OR e.cost_version IS DISTINCT FROM 1 OR e.cost_base_units IS DISTINCT FROM e.units_taken
      OR e.cost_unit_offset IS NULL OR e.cost_original_pack_cost IS NULL THEN
    RAISE EXCEPTION 'physical_return_slice_source_invalid' USING ERRCODE='23514';
   END IF;
   v_start:=greatest(r.offset,e.source_start);
   v_stop:=least(r.offset+r.base_units,e.source_start+e.units_taken);
   v_factor:=(l.packaging->>'units_per_pack')::NUMERIC;
   v_cost:=(div(e.cost_original_pack_cost*100*(e.cost_unit_offset+v_stop-e.source_start),v_factor)
      -div(e.cost_original_pack_cost*100*(e.cost_unit_offset+v_start-e.source_start),v_factor))/100;
   IF v_start>=v_stop OR NEW.source_offset<>v_start-e.source_start
      OR NEW.base_units<>v_stop-v_start OR NEW.cost_amount<>v_cost THEN
    RAISE EXCEPTION 'physical_return_slice_allocation_invalid' USING ERRCODE='23514';
   END IF;
  END IF;
 END IF;
 IF NOT EXISTS(SELECT 1 FROM public.app_user
       WHERE id=public.current_app_user_id() AND status='active')
   OR NOT coalesce(public.tenant_actor_has_scoped_permission(
       NEW.tenant_id,'pos.refund',NEW.branch_id),false) THEN
  RAISE EXCEPTION 'physical_return_actor_denied' USING ERRCODE='42501';
 END IF;
 NEW.created_at:=statement_timestamp(); NEW.updated_at:=NEW.created_at;
 NEW.created_by:=public.current_app_user_id(); NEW.updated_by:=NEW.created_by;
 RETURN NEW;
END $$;

CREATE FUNCTION public.trg_physical_return_complete() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE c public.pos_physical_return_command%ROWTYPE; r RECORD; e RECORD;
 v_id UUID; v_units NUMERIC; v_cost NUMERIC; v_total NUMERIC:=0; v_total_cost NUMERIC:=0;
 v_count INTEGER:=0; v_slices JSONB; v_lines JSONB:='[]'::JSONB;
 v_expected JSONB; v_selections TEXT:=''; v_payload TEXT; v_factor NUMERIC;
BEGIN
 IF TG_TABLE_NAME='pos_physical_return_command' THEN v_id:=NEW.id; ELSE v_id:=NEW.command_id; END IF;
 SELECT * INTO c FROM public.pos_physical_return_command WHERE tenant_id=NEW.tenant_id AND id=v_id;
 IF c.id IS NULL THEN RAISE EXCEPTION 'physical_return_missing_command' USING ERRCODE='23514'; END IF;
 FOR r IN SELECT returned.*,original.base_units AS original_units,original.packaging
   FROM public.pos_physical_return_line returned JOIN public.unit_stock_cart_line_v2 original
    ON original.tenant_id=returned.tenant_id AND original.command_id=returned.stock_command_id
      AND original.line_id=returned.line_id
   WHERE returned.tenant_id=c.tenant_id AND returned.command_id=c.id ORDER BY returned.position LOOP
  v_count:=v_count+1;
  IF r.position<>v_count THEN
   RAISE EXCEPTION 'physical_return_line_positions_invalid' USING ERRCODE='23514';
  END IF;
  v_factor:=(r.packaging->>'units_per_pack')::NUMERIC;
  SELECT sum(units_taken),sum(cost_amount) INTO v_units,v_cost FROM public.unit_stock_movement_v2
    WHERE tenant_id=c.tenant_id AND command_id=c.stock_command_id AND line_id=r.line_id
      AND units_taken>0;
  IF v_units IS DISTINCT FROM r.original_units::NUMERIC OR v_cost IS NULL THEN
   RAISE EXCEPTION 'physical_return_source_coverage_invalid' USING ERRCODE='23514';
  END IF;
  FOR e IN SELECT * FROM public.unit_stock_movement_v2
    WHERE tenant_id=c.tenant_id AND command_id=c.stock_command_id AND line_id=r.line_id
      AND units_taken>0 LOOP
   IF e.cost_version IS DISTINCT FROM 1 OR e.cost_base_units IS DISTINCT FROM e.units_taken
     OR e.cost_unit_offset IS NULL OR e.cost_original_pack_cost IS NULL
     OR e.cost_unit_offset<0 OR e.cost_unit_offset>=v_factor
     OR e.cost_amount IS DISTINCT FROM
       (div(e.cost_original_pack_cost*100*(e.cost_unit_offset+e.units_taken),v_factor)
        -div(e.cost_original_pack_cost*100*e.cost_unit_offset,v_factor))/100 THEN
    RAISE EXCEPTION 'physical_return_historical_cost_invalid' USING ERRCODE='23514';
   END IF;
  END LOOP;
  SELECT sum(base_units),sum(cost_amount),jsonb_agg(jsonb_build_object(
     'source_movement_id',source_movement_id,'lot_id',lot_id,'source_offset',source_offset,
     'base_units',base_units,'cost_amount',to_char(cost_amount,'FM999999999990.00')) ORDER BY position)
     INTO v_units,v_cost,v_slices FROM public.pos_physical_return_slice
   WHERE tenant_id=c.tenant_id AND command_id=c.id AND line_id=r.line_id;
  IF v_units IS DISTINCT FROM r.base_units::NUMERIC OR v_cost IS DISTINCT FROM r.cost_amount
    OR EXISTS(SELECT 1 FROM (
     SELECT position,row_number() OVER(ORDER BY position) AS ordinal
       FROM public.pos_physical_return_slice WHERE tenant_id=c.tenant_id
       AND command_id=c.id AND line_id=r.line_id) numbered WHERE position<>ordinal) THEN
   RAISE EXCEPTION 'physical_return_incomplete_slices' USING ERRCODE='23514';
  END IF;
  v_total:=v_total+r.net_amount; v_total_cost:=v_total_cost+r.cost_amount;
  v_lines:=v_lines||jsonb_build_array(jsonb_build_object('line_id',r.line_id,
    'base_units',r.base_units,'offset',r.offset,'net_amount',to_char(r.net_amount,'FM999999999990.00'),
    'discount_amount',to_char(r.discount_amount,'FM999999999990.00'),
    'gross_amount',to_char(r.gross_amount,'FM999999999990.00'),
    'cost_amount',to_char(r.cost_amount,'FM999999999990.00'),'slices',v_slices));
  IF v_count>1 THEN v_selections:=v_selections||','; END IF;
  v_selections:=v_selections||'{"line_id":"'||r.line_id||'","quantity":'||r.quantity
    ||',"sale_format":"'||r.sale_format||'"}';
 END LOOP;
 v_expected:=jsonb_build_object('operation_id',c.id,'parent_sale_id',c.parent_sale_id,
   'tenant_id',c.tenant_id,'branch_id',c.branch_id,'actor_id',c.actor_id,
   'state','prepared_quarantine','currency','TJS','lines',v_lines,
   'total_amount',to_char(v_total,'FM999999999990.00'),
   'total_cost_amount',to_char(v_total_cost,'FM999999999990.00'));
 v_payload:='{"actor_id":"'||c.actor_id||'","kind":"physical_return_quarantine",'
   ||'"parent_sale_id":"'||c.parent_sale_id||'","selections":['||v_selections
   ||'],"tenant_id":"'||c.tenant_id||'","version":1}';
 IF v_count NOT BETWEEN 1 AND 200 OR c.total_amount<>v_total OR c.total_cost_amount<>v_total_cost
   OR c.result_payload IS DISTINCT FROM v_expected
   OR c.request_hash<>encode(sha256(convert_to(v_payload,'UTF8')),'hex') THEN
  RAISE EXCEPTION 'physical_return_document_invalid' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
"""


def upgrade() -> None:
    _sql("""
      CREATE TEMP TABLE physical_return_reference_grants AS
       SELECT oid::regclass::TEXT AS relation,
         has_table_privilege('aurum_schema_owner',oid,'REFERENCES') AS granted
       FROM pg_class WHERE oid IN ('public.sale'::regclass,'public.pos_physical_checkout'::regclass,
        'public.unit_stock_cart_line_v2'::regclass,'public.unit_stock_movement_v2'::regclass);
      GRANT REFERENCES ON public.sale,public.pos_physical_checkout,public.unit_stock_cart_line_v2,
        public.unit_stock_movement_v2 TO aurum_schema_owner;
      ALTER TABLE public.unit_stock_movement_v2
        ADD CONSTRAINT uq_physical_return_source_movement_scope UNIQUE(tenant_id,id);
      CREATE TABLE public.pos_physical_return_command (
       tenant_id UUID NOT NULL,id UUID NOT NULL,parent_sale_id UUID NOT NULL,
       parent_checkout_id UUID NOT NULL,stock_command_id UUID NOT NULL,branch_id UUID NOT NULL,
       actor_id UUID NOT NULL,request_hash TEXT NOT NULL CHECK(request_hash~'^[0-9a-f]{64}$'),
       state TEXT NOT NULL CHECK(state='prepared_quarantine'),
       total_amount NUMERIC(14,2) NOT NULL CHECK(total_amount>=0),
       total_cost_amount NUMERIC(14,2) NOT NULL CHECK(total_cost_amount>=0),
       result_payload JSONB NOT NULL CHECK(jsonb_typeof(result_payload)='object'),
       created_at TIMESTAMPTZ NOT NULL DEFAULT now(),updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
       created_by UUID NOT NULL,updated_by UUID NOT NULL,PRIMARY KEY(tenant_id,id),
       FOREIGN KEY(tenant_id,parent_sale_id) REFERENCES public.sale(tenant_id,id),
       FOREIGN KEY(tenant_id,parent_checkout_id) REFERENCES public.pos_physical_checkout(tenant_id,id)
      );
      CREATE TABLE public.pos_physical_return_line (
       tenant_id UUID NOT NULL,command_id UUID NOT NULL,line_id UUID NOT NULL,
       position INTEGER NOT NULL CHECK(position BETWEEN 1 AND 200),branch_id UUID NOT NULL,
       catalog_id UUID NOT NULL,stock_command_id UUID NOT NULL,
       "offset" BIGINT NOT NULL CHECK("offset">=0),base_units BIGINT NOT NULL CHECK(base_units>0),
       sale_format TEXT NOT NULL CHECK(sale_format IN ('pack','blister','unit')),
       quantity BIGINT NOT NULL CHECK(quantity>0),
       net_amount NUMERIC(14,2) NOT NULL CHECK(net_amount>=0),
       discount_amount NUMERIC(14,2) NOT NULL CHECK(discount_amount>=0),
       gross_amount NUMERIC(14,2) NOT NULL CHECK(gross_amount=net_amount+discount_amount),
       cost_amount NUMERIC(14,2) NOT NULL CHECK(cost_amount>=0),
       created_at TIMESTAMPTZ NOT NULL DEFAULT now(),updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
       created_by UUID NOT NULL,updated_by UUID NOT NULL,
       PRIMARY KEY(tenant_id,command_id,line_id),UNIQUE(tenant_id,command_id,position),
       FOREIGN KEY(tenant_id,command_id) REFERENCES public.pos_physical_return_command(tenant_id,id),
       FOREIGN KEY(tenant_id,stock_command_id,line_id)
         REFERENCES public.unit_stock_cart_line_v2(tenant_id,command_id,line_id)
      );
      CREATE INDEX ix_physical_return_source_cursor ON public.pos_physical_return_line
        (tenant_id,stock_command_id,line_id);
      CREATE TABLE public.pos_physical_return_slice (
       tenant_id UUID NOT NULL,command_id UUID NOT NULL,line_id UUID NOT NULL,
       position INTEGER NOT NULL CHECK(position>0),branch_id UUID NOT NULL,
       source_movement_id UUID NOT NULL,lot_id UUID NOT NULL,
       source_offset BIGINT NOT NULL CHECK(source_offset>=0),
       base_units BIGINT NOT NULL CHECK(base_units>0),
       cost_amount NUMERIC(14,2) NOT NULL CHECK(cost_amount>=0),
       created_at TIMESTAMPTZ NOT NULL DEFAULT now(),updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
       created_by UUID NOT NULL,updated_by UUID NOT NULL,
       PRIMARY KEY(tenant_id,command_id,line_id,position),
       UNIQUE(tenant_id,command_id,line_id,source_movement_id),
       FOREIGN KEY(tenant_id,command_id,line_id)
         REFERENCES public.pos_physical_return_line(tenant_id,command_id,line_id),
       FOREIGN KEY(tenant_id,source_movement_id) REFERENCES public.unit_stock_movement_v2(tenant_id,id)
      );
      DO $$ DECLARE r RECORD; BEGIN
       FOR r IN SELECT * FROM pg_temp.physical_return_reference_grants WHERE NOT granted LOOP
        EXECUTE format('REVOKE REFERENCES ON %s FROM aurum_schema_owner',r.relation);
       END LOOP;
      END $$;
      DROP TABLE pg_temp.physical_return_reference_grants;
    """)
    for table in TABLES:
        _sql(f"""
          ALTER TABLE public.{table} OWNER TO aurum_schema_owner;
          ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY;
          ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY;
          REVOKE ALL ON public.{table} FROM PUBLIC,aurum_app,aurum_support;
          GRANT SELECT,INSERT ON public.{table} TO aurum_app;
          GRANT SELECT ON public.{table} TO aurum_support;
          CREATE POLICY schema_owner ON public.{table} TO aurum_schema_owner
            USING(true) WITH CHECK(true);
          CREATE POLICY support ON public.{table} FOR SELECT TO aurum_support USING(true);
          CREATE POLICY tenant_read ON public.{table} FOR SELECT TO aurum_app
            USING(tenant_id=public.current_tenant_id() AND
              public.tenant_actor_has_scoped_permission(tenant_id,'pos.refund',branch_id));
          CREATE POLICY tenant_insert ON public.{table} FOR INSERT TO aurum_app
            WITH CHECK(tenant_id=public.current_tenant_id() AND
              public.tenant_actor_has_scoped_permission(tenant_id,'pos.refund',branch_id));
        """)
    for table in ("pos_physical_checkout", "unit_stock_movement_v2", "sale"):
        _sql(f"""
          CREATE POLICY physical_return_read ON public.{table} FOR SELECT TO aurum_app
            USING(tenant_id=public.current_tenant_id() AND
              public.tenant_actor_has_scoped_permission(tenant_id,'pos.refund',branch_id));
        """)
    _sql("""
      CREATE POLICY physical_return_read ON public.unit_stock_cart_line_v2 FOR SELECT TO aurum_app
       USING(tenant_id=public.current_tenant_id() AND EXISTS(
        SELECT 1 FROM public.pos_physical_checkout c WHERE c.tenant_id=unit_stock_cart_line_v2.tenant_id
          AND c.stock_command_id=unit_stock_cart_line_v2.command_id
          AND public.tenant_actor_has_scoped_permission(c.tenant_id,'pos.refund',c.branch_id)));
      CREATE POLICY physical_return_lock ON public.sale FOR UPDATE TO aurum_app
       USING(tenant_id=public.current_tenant_id() AND
        public.tenant_actor_has_scoped_permission(tenant_id,'pos.refund',branch_id)) WITH CHECK(false);
    """)
    _sql(FUNCTIONS_SQL)
    for signature in ("trg_physical_return_guard()", "trg_physical_return_complete()"):
        _sql(f"""
          ALTER FUNCTION public.{signature} OWNER TO aurum_schema_owner;
          REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC,aurum_app,aurum_support;
        """)
    for table in TABLES:
        _sql(f"""
          CREATE TRIGGER a00_tenant_access BEFORE INSERT OR UPDATE OR DELETE ON public.{table}
           FOR EACH ROW EXECUTE FUNCTION public.trg_enforce_tenant_access('pos');
          CREATE TRIGGER a10_physical_return_guard BEFORE INSERT OR UPDATE OR DELETE ON public.{table}
           FOR EACH ROW EXECUTE FUNCTION public.trg_physical_return_guard();
          CREATE CONSTRAINT TRIGGER physical_return_complete AFTER INSERT ON public.{table}
           DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
           EXECUTE FUNCTION public.trg_physical_return_complete();
          CREATE TRIGGER z90_audit AFTER INSERT OR UPDATE OR DELETE ON public.{table}
           FOR EACH ROW EXECUTE FUNCTION public.trg_audit_log();
        """)
    _redactor(AUDIT_OLD, AUDIT_NEW)
    _redactor(AUDIT_COST_OLD, AUDIT_COST_NEW)
    _redactor(AUDIT_PAYLOAD_OLD, AUDIT_PAYLOAD_NEW)


def downgrade() -> None:
    _sql("""
      LOCK TABLE public.pos_physical_return_command,public.pos_physical_return_line,
        public.pos_physical_return_slice IN ACCESS EXCLUSIVE MODE;
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.pos_physical_return_command) THEN
          RAISE EXCEPTION 'physical_return_history_exists' USING ERRCODE='55000';
        END IF;
      END $$;
    """)
    _redactor(AUDIT_PAYLOAD_NEW, AUDIT_PAYLOAD_OLD)
    _redactor(AUDIT_COST_NEW, AUDIT_COST_OLD)
    _redactor(AUDIT_NEW, AUDIT_OLD)
    for table in (
        "pos_physical_checkout",
        "unit_stock_cart_line_v2",
        "unit_stock_movement_v2",
        "sale",
    ):
        _sql(f"DROP POLICY physical_return_read ON public.{table};")
    _sql("DROP POLICY physical_return_lock ON public.sale;")
    for table in reversed(TABLES):
        _sql(f"DROP TABLE public.{table};")
    _sql("""
      DROP FUNCTION public.trg_physical_return_complete();
      DROP FUNCTION public.trg_physical_return_guard();
      ALTER TABLE public.unit_stock_movement_v2
        DROP CONSTRAINT uq_physical_return_source_movement_scope;
    """)
