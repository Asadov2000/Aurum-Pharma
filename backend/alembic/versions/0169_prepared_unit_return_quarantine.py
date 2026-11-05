"""Internal immutable unit return allocations and non-saleable quarantine.

Revision ID: 0169
Revises: 0168
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from alembic import op

revision: str = "0169"
down_revision: str | Sequence[str] | None = "0168"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("pos_unit_return_command", "pos_unit_return_slice")
IDENTITY = """
 id UUID NOT NULL, tenant_id UUID NOT NULL REFERENCES public.tenant(id) ON DELETE RESTRICT,
 branch_id UUID NOT NULL, catalog_id UUID NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 created_by UUID REFERENCES public.app_user(id) ON DELETE RESTRICT,
 updated_by UUID REFERENCES public.app_user(id) ON DELETE RESTRICT,
 FOREIGN KEY(tenant_id,branch_id) REFERENCES public.branch(tenant_id,id) ON DELETE RESTRICT,
 FOREIGN KEY(tenant_id,catalog_id) REFERENCES public.tenant_catalog(tenant_id,id) ON DELETE RESTRICT
"""


def _sql(sql: str) -> None:
    start = 0
    for match in re.finditer(r"\$\$.*?\$\$|'(?:''|[^'])*'|;", sql, flags=re.DOTALL):
        if match.group() == ";":
            statement = sql[start : match.start()].strip()
            if statement:
                op.execute(statement.replace(":", r"\:"))
            start = match.end()
    if sql[start:].strip():
        op.execute(sql[start:].replace(":", r"\:"))


FUNCTIONS = r"""
CREATE FUNCTION public.trg_unit_return_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE
 v_line public.pos_unit_sale_line%ROWTYPE; v_cmd public.pos_unit_return_command%ROWTYPE;
 v_original public.pos_unit_sale_command%ROWTYPE; v_entry RECORD;
 v_prior NUMERIC; v_factor NUMERIC; v_start NUMERIC; v_end NUMERIC;
 v_relative NUMERIC; v_units NUMERIC; v_absolute NUMERIC; v_offset NUMERIC;
 v_pack_start NUMERIC; v_pack_count NUMERIC; v_cost NUMERIC; v_net NUMERIC;
 v_payload TEXT;
BEGIN
 IF TG_OP<>'INSERT' THEN
   RAISE EXCEPTION 'unit_return_immutable' USING ERRCODE='55000';
 END IF;
 IF session_user<>'aurum_app' OR public.is_support_session()
   OR public.current_app_user_id() IS NULL
   OR public.current_tenant_id() IS DISTINCT FROM NEW.tenant_id
   OR NOT coalesce(public.tenant_actor_has_scoped_permission(
       NEW.tenant_id,'pos.refund',NEW.branch_id),false) THEN
   RAISE EXCEPTION 'unit_return_actor_denied' USING ERRCODE='42501';
 END IF;
 IF TG_TABLE_NAME='pos_unit_return_command' THEN
   SELECT * INTO v_line FROM public.pos_unit_sale_line
     WHERE tenant_id=NEW.tenant_id AND id=NEW.original_line_id FOR NO KEY UPDATE;
   IF NOT FOUND OR v_line.branch_id<>NEW.branch_id OR v_line.catalog_id<>NEW.catalog_id THEN
     RAISE EXCEPTION 'unit_return_source_invalid' USING ERRCODE='23514';
   END IF;
   SELECT * INTO v_original FROM public.pos_unit_sale_command
     WHERE tenant_id=v_line.tenant_id AND id=v_line.command_id;
   IF NOT FOUND OR v_original.stock_source_kind<>'received' THEN
     RAISE EXCEPTION 'unit_return_source_invalid' USING ERRCODE='23514';
   END IF;
   SELECT coalesce(sum(quantity),0) INTO v_prior FROM public.pos_unit_return_command
     WHERE tenant_id=NEW.tenant_id AND original_line_id=NEW.original_line_id;
   v_factor:=CASE WHEN v_line.sale_format='pack' THEN v_line.units_per_pack ELSE 1 END;
   v_net:=(div(v_line.net_amount*100*(v_prior+NEW.quantity),v_line.quantity)
     -div(v_line.net_amount*100*v_prior,v_line.quantity))/100;
   v_payload:='{"branch_id":"'||NEW.branch_id||'","kind":"unit_return_quarantine",'
     ||'"original_line_id":"'||NEW.original_line_id||'","quantity":'||NEW.quantity
     ||',"tenant_id":"'||NEW.tenant_id||'","version":1}';
   IF NEW.actor_id IS DISTINCT FROM public.current_app_user_id()
     OR NEW.request_hash<>encode(sha256(convert_to(v_payload,'UTF8')),'hex')
     OR NEW.allocation_offset<>v_prior OR NEW.quantity<=0
     OR v_prior+NEW.quantity>v_line.quantity
     OR (v_line.sale_format='unit' AND NEW.quantity%v_line.dispense_step<>0)
     OR (NEW.version,NEW.unit,NEW.units_per_pack,NEW.dispense_step,NEW.sale_format,
         NEW.original_retail_unit_price,NEW.money_version,NEW.currency)
       IS DISTINCT FROM (v_line.version,v_line.unit,v_line.units_per_pack,v_line.dispense_step,
         v_line.sale_format,v_line.original_retail_unit_price,v_line.money_version,v_line.currency)
     OR NEW.base_units<>NEW.quantity*v_factor
     OR NEW.gross_amount<>v_line.original_retail_unit_price*NEW.quantity
     OR NEW.net_amount<>v_net OR NEW.discount_amount<>NEW.gross_amount-v_net THEN
     RAISE EXCEPTION 'unit_return_allocation_invalid' USING ERRCODE='23514';
   END IF;
 ELSE
   SELECT * INTO v_cmd FROM public.pos_unit_return_command
     WHERE tenant_id=NEW.tenant_id AND id=NEW.command_id;
   IF NOT FOUND OR v_cmd.branch_id<>NEW.branch_id OR v_cmd.catalog_id<>NEW.catalog_id THEN
     RAISE EXCEPTION 'unit_return_slice_invalid' USING ERRCODE='23514';
   END IF;
   SELECT * INTO v_line FROM public.pos_unit_sale_line
     WHERE tenant_id=v_cmd.tenant_id AND id=v_cmd.original_line_id;
   SELECT * INTO v_entry FROM (
     SELECT e.*,coalesce(sum(cost_base_units) OVER(ORDER BY entry_order
       ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING),0) AS source_start
     FROM public.unit_stock_entry e
     WHERE tenant_id=v_line.tenant_id AND operation_id=v_line.stock_operation_id
   ) ranges WHERE id=NEW.original_entry_id;
   IF NOT FOUND OR v_entry.cost_version IS DISTINCT FROM 1
     OR v_entry.branch_id<>NEW.branch_id OR v_entry.catalog_id<>NEW.catalog_id
     OR v_entry.lot_id<>NEW.lot_id OR v_entry.cost_base_units IS NULL
     OR v_entry.cost_unit_offset IS NULL OR v_entry.cost_pack_ordinal_start IS NULL
     OR v_entry.cost_original_pack_cost IS NULL THEN
     RAISE EXCEPTION 'unit_return_slice_invalid' USING ERRCODE='23514';
   END IF;
   v_factor:=CASE WHEN v_line.sale_format='pack' THEN v_line.units_per_pack ELSE 1 END;
   v_start:=greatest(v_entry.source_start,v_cmd.allocation_offset*v_factor);
   v_end:=least(v_entry.source_start+v_entry.cost_base_units,
     (v_cmd.allocation_offset+v_cmd.quantity)*v_factor);
   v_units:=v_end-v_start;
   v_relative:=v_start-v_entry.source_start;
   v_absolute:=v_entry.cost_unit_offset+v_relative;
   v_offset:=mod(v_absolute,v_line.units_per_pack);
   v_pack_start:=v_entry.cost_pack_ordinal_start+div(v_absolute,v_line.units_per_pack);
   v_pack_count:=div(v_offset+v_units+v_line.units_per_pack-1,v_line.units_per_pack);
   v_cost:=(div(v_absolute+v_units,v_line.units_per_pack)
       -div(v_absolute,v_line.units_per_pack))*v_entry.cost_original_pack_cost
     +(div(v_entry.cost_original_pack_cost*100*mod(v_absolute+v_units,v_line.units_per_pack),
         v_line.units_per_pack)
       -div(v_entry.cost_original_pack_cost*100*mod(v_absolute,v_line.units_per_pack),
         v_line.units_per_pack))/100;
   IF v_units<=0 OR (NEW.base_units,NEW.unit_offset,NEW.pack_ordinal_start,NEW.pack_count,
       NEW.original_pack_cost,NEW.cost_amount,NEW.money_version,NEW.currency)
     IS DISTINCT FROM (v_units,v_offset,v_pack_start,v_pack_count,
       v_entry.cost_original_pack_cost,v_cost,1,'TJS') THEN
     RAISE EXCEPTION 'unit_return_slice_invalid' USING ERRCODE='23514';
   END IF;
 END IF;
 NEW.created_at:=statement_timestamp(); NEW.updated_at:=NEW.created_at;
 NEW.created_by:=public.current_app_user_id(); NEW.updated_by:=NEW.created_by;
 RETURN NEW;
END $$;
CREATE FUNCTION public.trg_unit_return_complete() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE v_cmd public.pos_unit_return_command%ROWTYPE; v_id UUID; v_count BIGINT;
 v_units NUMERIC; v_cost NUMERIC; v_min INTEGER; v_max INTEGER;
BEGIN
 IF TG_TABLE_NAME='pos_unit_return_command' THEN
  v_id:=NEW.id;
 ELSE
  v_id:=NEW.command_id;
 END IF;
 SELECT * INTO v_cmd FROM public.pos_unit_return_command WHERE tenant_id=NEW.tenant_id AND id=v_id;
 SELECT count(*),sum(base_units),sum(cost_amount),min(position),max(position)
   INTO v_count,v_units,v_cost,v_min,v_max FROM public.pos_unit_return_slice
   WHERE tenant_id=NEW.tenant_id AND command_id=v_id;
 IF v_count=0 OR v_units IS DISTINCT FROM v_cmd.base_units
   OR v_cost IS DISTINCT FROM v_cmd.cost_amount OR v_min<>1 OR v_max<>v_count THEN
   RAISE EXCEPTION 'unit_return_incomplete' USING ERRCODE='23514';
 END IF;
 RETURN NULL;
END $$;
"""


def _audit_redactor(*, returns: bool) -> str:
    path = Path(__file__).with_name("0144_persistent_incoming_draft_rows.py")
    spec = spec_from_file_location("unit_return_audit_history", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load original audit redactor")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    sql = module._audit_redactor(drafts=True)
    if not isinstance(sql, str):
        raise TypeError("Original audit redactor must be SQL")
    if not returns:
        return sql
    anchor = "IF p_table_name = 'incoming_document' THEN v_field := 'total_amount';"
    addition = """
      IF p_table_name IN ('pos_unit_return_command','pos_unit_return_slice',
          'unit_stock_lot','unit_stock_entry','pos_unit_sale_line') THEN
        FOREACH v_field IN ARRAY ARRAY['cost_amount','original_pack_cost',
            'purchase_cost_per_pack','cost_original_pack_cost','original_cost_amount'] LOOP
          IF v_result ? v_field AND v_result -> v_field <> 'null'::JSONB THEN
            v_result := jsonb_set(v_result,ARRAY[v_field],to_jsonb('***'::TEXT),false);
          END IF;
        END LOOP;
        RETURN v_result;
      END IF;
    """
    if sql.count(anchor) != 1:
        raise RuntimeError("Audit redactor changed; review the return privacy boundary")
    return sql.replace(anchor, addition + anchor, 1)


def upgrade() -> None:
    _sql("""
      CREATE TEMP TABLE unit_return_reference_grants AS
        SELECT oid::regclass::TEXT AS relation,
          has_table_privilege('aurum_schema_owner',oid,'REFERENCES') AS granted
        FROM pg_class WHERE oid IN ('public.tenant'::regclass,'public.branch'::regclass,
          'public.tenant_catalog'::regclass,'public.app_user'::regclass);
      GRANT REFERENCES ON public.tenant,public.branch,public.tenant_catalog,public.app_user
        TO aurum_schema_owner;
      ALTER TABLE public.pos_unit_sale_line ADD CONSTRAINT uq_unit_line_return_scope
        UNIQUE(tenant_id,id);
      ALTER TABLE public.unit_stock_entry ADD CONSTRAINT uq_unit_entry_return_scope
        UNIQUE(tenant_id,id);
    """)
    op.execute(f"""
      CREATE TABLE public.pos_unit_return_command (
        {IDENTITY}, PRIMARY KEY(tenant_id,id), actor_id UUID NOT NULL
          REFERENCES public.app_user(id) ON DELETE RESTRICT,
        original_line_id UUID NOT NULL, request_hash TEXT NOT NULL
          CHECK(request_hash ~ '^[0-9a-f]{{64}}$'),
        state TEXT NOT NULL DEFAULT 'prepared_quarantine' CHECK(state='prepared_quarantine'),
        version INTEGER NOT NULL CHECK(version=1), unit TEXT NOT NULL,
        units_per_pack INTEGER NOT NULL, dispense_step INTEGER NOT NULL,
        sale_format TEXT NOT NULL CHECK(sale_format IN ('pack','unit')),
        quantity BIGINT NOT NULL CHECK(quantity BETWEEN 1 AND 99999999999),
        allocation_offset BIGINT NOT NULL CHECK(allocation_offset>=0),
        base_units BIGINT NOT NULL CHECK(base_units BETWEEN 1 AND 99999999999),
        original_retail_unit_price NUMERIC(14,2) NOT NULL CHECK(original_retail_unit_price>0),
        gross_amount NUMERIC(14,2) NOT NULL CHECK(gross_amount>0),
        discount_amount NUMERIC(14,2) NOT NULL CHECK(discount_amount>=0),
        net_amount NUMERIC(14,2) NOT NULL CHECK(net_amount>=0),
        cost_amount NUMERIC(14,2) NOT NULL CHECK(cost_amount>=0),
        money_version INTEGER NOT NULL CHECK(money_version=1),
        currency TEXT NOT NULL CHECK(currency='TJS'),
        UNIQUE(tenant_id,original_line_id,allocation_offset),
        FOREIGN KEY(tenant_id,original_line_id) REFERENCES public.pos_unit_sale_line(tenant_id,id)
          ON DELETE RESTRICT
      )
    """)
    op.execute(f"""
      CREATE TABLE public.pos_unit_return_slice (
        {IDENTITY}, PRIMARY KEY(tenant_id,id), command_id UUID NOT NULL,
        position INTEGER NOT NULL CHECK(position>0), original_entry_id UUID NOT NULL,
        lot_id UUID NOT NULL, pack_ordinal_start BIGINT NOT NULL CHECK(pack_ordinal_start>0),
        pack_count BIGINT NOT NULL CHECK(pack_count>0),
        unit_offset INTEGER NOT NULL CHECK(unit_offset>=0),
        base_units BIGINT NOT NULL CHECK(base_units BETWEEN 1 AND 99999999999),
        original_pack_cost NUMERIC(14,2) NOT NULL CHECK(original_pack_cost>=0),
        cost_amount NUMERIC(14,2) NOT NULL CHECK(cost_amount>=0),
        money_version INTEGER NOT NULL CHECK(money_version=1),
        currency TEXT NOT NULL CHECK(currency='TJS'),
        UNIQUE(tenant_id,command_id,position), UNIQUE(tenant_id,command_id,original_entry_id),
        FOREIGN KEY(tenant_id,command_id) REFERENCES public.pos_unit_return_command(tenant_id,id)
          ON DELETE RESTRICT,
        FOREIGN KEY(tenant_id,original_entry_id) REFERENCES public.unit_stock_entry(tenant_id,id)
          ON DELETE RESTRICT,
        FOREIGN KEY(tenant_id,lot_id) REFERENCES public.unit_stock_lot(tenant_id,id)
          ON DELETE RESTRICT
      )
    """)
    _sql("""
      DO $$ DECLARE r RECORD; BEGIN
        FOR r IN SELECT * FROM pg_temp.unit_return_reference_grants WHERE NOT granted LOOP
          EXECUTE format('REVOKE REFERENCES ON %s FROM aurum_schema_owner',r.relation);
        END LOOP;
      END $$;
      DROP TABLE pg_temp.unit_return_reference_grants;
    """)
    _sql(FUNCTIONS)
    for function in ("trg_unit_return_guard", "trg_unit_return_complete"):
        _sql(f"""
          ALTER FUNCTION public.{function}() OWNER TO aurum_schema_owner;
          REVOKE ALL ON FUNCTION public.{function}() FROM PUBLIC,aurum_app,aurum_support;
        """)
    for table in TABLES:
        _sql(f"""
          ALTER TABLE public.{table} OWNER TO aurum_schema_owner;
          ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY;
          ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY;
          REVOKE ALL ON public.{table} FROM PUBLIC,aurum_app,aurum_support;
          GRANT SELECT,INSERT ON public.{table} TO aurum_app,aurum_support;
          CREATE POLICY schema_owner ON public.{table} TO aurum_schema_owner
            USING(true) WITH CHECK(true);
          CREATE POLICY support ON public.{table} TO aurum_support USING(true) WITH CHECK(true);
          CREATE POLICY tenant_read ON public.{table} FOR SELECT TO aurum_app USING(
            tenant_id=public.current_tenant_id() AND
            public.tenant_actor_has_scoped_permission(tenant_id,'pos.refund',branch_id));
          CREATE POLICY tenant_insert ON public.{table} FOR INSERT TO aurum_app WITH CHECK(
            tenant_id=public.current_tenant_id() AND
            public.tenant_actor_has_scoped_permission(tenant_id,'pos.refund',branch_id));
          CREATE TRIGGER a00_tenant_access BEFORE INSERT OR UPDATE OR DELETE ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION public.trg_enforce_tenant_access('pos');
          CREATE TRIGGER a10_unit_return_guard BEFORE INSERT OR UPDATE OR DELETE ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION public.trg_unit_return_guard();
          CREATE TRIGGER z90_audit AFTER INSERT OR UPDATE OR DELETE ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION public.trg_audit_log();
          CREATE CONSTRAINT TRIGGER unit_return_complete AFTER INSERT ON public.{table}
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION public.trg_unit_return_complete();
        """)
    for table in ("pos_unit_sale_command", "pos_unit_sale_line", "unit_stock_entry"):
        op.execute(
            f"CREATE POLICY tenant_refund_read ON public.{table} FOR SELECT TO aurum_app USING("
            "tenant_id=public.current_tenant_id() AND "
            "public.tenant_actor_has_scoped_permission(tenant_id,'pos.refund',branch_id))"
        )
    _sql("""
      GRANT UPDATE(id) ON public.pos_unit_sale_line TO aurum_app;
      CREATE POLICY tenant_refund_lock ON public.pos_unit_sale_line FOR UPDATE TO aurum_app
        USING(tenant_id=public.current_tenant_id() AND
          public.tenant_actor_has_scoped_permission(tenant_id,'pos.refund',branch_id))
        WITH CHECK(false);
    """)
    _sql(_audit_redactor(returns=True))


def downgrade() -> None:
    _sql("""
      LOCK TABLE public.pos_unit_return_command,public.pos_unit_return_slice
        IN ACCESS EXCLUSIVE MODE;
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.pos_unit_return_command)
          OR EXISTS(SELECT 1 FROM public.pos_unit_return_slice) THEN
          RAISE EXCEPTION 'unit_return_nonempty_downgrade_forbidden' USING ERRCODE='55000';
        END IF;
      END $$;
      DROP POLICY tenant_refund_lock ON public.pos_unit_sale_line;
      REVOKE UPDATE(id) ON public.pos_unit_sale_line FROM aurum_app;
    """)
    for table in ("pos_unit_sale_command", "pos_unit_sale_line", "unit_stock_entry"):
        op.execute(f"DROP POLICY tenant_refund_read ON public.{table}")
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE public.{table}")
    _sql("""
      DROP FUNCTION public.trg_unit_return_guard();
      DROP FUNCTION public.trg_unit_return_complete();
      ALTER TABLE public.unit_stock_entry DROP CONSTRAINT uq_unit_entry_return_scope;
      ALTER TABLE public.pos_unit_sale_line DROP CONSTRAINT uq_unit_line_return_scope;
    """)
    _sql(_audit_redactor(returns=False))
