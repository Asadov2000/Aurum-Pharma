"""Internal immutable prepared POS line snapshots, independent of live checkout.

Revision ID: 0165
Revises: 0164
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from alembic import op

revision: str = "0165"
down_revision: str | Sequence[str] | None = "0164"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("pos_unit_sale_command", "pos_unit_sale_line")
IDENTITY = """
 id UUID NOT NULL,
 tenant_id UUID NOT NULL REFERENCES public.tenant(id) ON DELETE RESTRICT,
 branch_id UUID NOT NULL,
 catalog_id UUID NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
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
                # Alembic treats strings as SQLAlchemy text: literal JSON colons
                # must be escaped to avoid accidental parameters such as :1.
                op.execute(statement.replace(":", r"\:"))
            start = match.end()
    if sql[start:].strip():
        op.execute(sql[start:].replace(":", r"\:"))


FUNCTIONS = r"""
CREATE FUNCTION public.trg_pos_unit_line_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE
 v_catalog public.tenant_catalog%ROWTYPE; v_preparation JSONB; v_price NUMERIC;
 v_hash BYTEA; v_stock_id UUID; v_command_id UUID;
BEGIN
 IF TG_OP<>'INSERT' THEN
   RAISE EXCEPTION 'pos_unit_line_immutable' USING ERRCODE='55000';
 END IF;
 IF TG_TABLE_NAME='pos_unit_sale_command' THEN v_command_id:=NEW.id;
 ELSE v_command_id:=NEW.command_id; END IF;
 -- UUIDv5 uses the existing pgcrypto SHA1 implementation; runtime roles cannot call it.
 v_hash:=public.digest(uuid_send('6ba7b811-9dad-11d1-80b4-00c04fd430c8'::UUID)
   ||convert_to('aurum-unit-line-stock:'||NEW.tenant_id||':'||v_command_id,'UTF8'),'sha1');
 v_hash:=set_byte(v_hash,6,(get_byte(v_hash,6)&15)|80);
 v_hash:=set_byte(v_hash,8,(get_byte(v_hash,8)&63)|128);
 v_stock_id:=substr(encode(v_hash,'hex'),1,32)::UUID;
 IF TG_TABLE_NAME='pos_unit_sale_command' THEN
   IF NOT EXISTS(SELECT 1 FROM public.pos_unit_sale_command
       WHERE tenant_id=NEW.tenant_id AND id=NEW.id)
     AND EXISTS(SELECT 1 FROM public.unit_stock_operation
     WHERE tenant_id=NEW.tenant_id AND id=v_stock_id) THEN
     RAISE EXCEPTION 'pos_unit_line_stock_already_recorded' USING ERRCODE='23514';
   END IF;
 ELSE
   IF NEW.stock_operation_id<>v_stock_id THEN
     RAISE EXCEPTION 'pos_unit_line_stock_identity' USING ERRCODE='23514';
   END IF;
 END IF;
 IF TG_TABLE_NAME='pos_unit_sale_line' THEN
   SELECT * INTO v_catalog FROM public.tenant_catalog
     WHERE tenant_id=NEW.tenant_id AND id=NEW.catalog_id FOR NO KEY UPDATE;
   v_preparation:=v_catalog.unit_sale_preparation;
   IF v_catalog.id IS NULL OR NOT v_catalog.is_active OR v_catalog.deleted_at IS NOT NULL
     OR v_catalog.currency<>'TJS' OR v_preparation IS NULL
     OR (NEW.version,NEW.unit,NEW.units_per_pack,NEW.dispense_step)
       IS DISTINCT FROM ((v_preparation->>'version')::INTEGER,v_preparation->>'unit',
         (v_preparation->>'units_per_pack')::INTEGER,
         (v_preparation->>'dispense_step')::INTEGER) THEN
     RAISE EXCEPTION 'pos_unit_line_catalog_snapshot' USING ERRCODE='23514';
   END IF;
   IF NEW.sale_format='unit' THEN v_price:=(v_preparation->>'unit_price')::NUMERIC;
   ELSE v_price:=v_catalog.base_price; END IF;
   IF v_price IS NULL OR v_price<=0 OR v_price>999999999999.99
     OR NEW.original_retail_unit_price<>v_price THEN
     RAISE EXCEPTION 'pos_unit_line_retail_snapshot' USING ERRCODE='23514';
   END IF;
 END IF;
 NEW.created_at:=statement_timestamp(); NEW.updated_at:=NEW.created_at;
 NEW.created_by:=public.current_app_user_id(); NEW.updated_by:=NEW.created_by;
 RETURN NEW;
END $$;

CREATE FUNCTION public.trg_pos_unit_line_complete() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE
 v_command public.pos_unit_sale_command%ROWTYPE;
 v_line public.pos_unit_sale_line%ROWTYPE;
 v_stock public.unit_stock_operation%ROWTYPE;
 v_id UUID; v_cost NUMERIC; v_units NUMERIC; v_count BIGINT; v_known BIGINT;
 v_payload TEXT; v_stock_payload TEXT;
BEGIN
 IF TG_TABLE_NAME='pos_unit_sale_command' THEN v_id:=NEW.id;
 ELSE v_id:=NEW.command_id; END IF;
 SELECT * INTO v_command FROM public.pos_unit_sale_command
   WHERE tenant_id=NEW.tenant_id AND id=v_id;
 SELECT * INTO v_line FROM public.pos_unit_sale_line
   WHERE tenant_id=NEW.tenant_id AND command_id=v_id;
 IF v_command.id IS NULL OR v_line.id IS NULL THEN
   RAISE EXCEPTION 'pos_unit_line_incomplete' USING ERRCODE='23514';
 END IF;
 SELECT * INTO v_stock FROM public.unit_stock_operation
   WHERE tenant_id=v_line.tenant_id AND id=v_line.stock_operation_id;
 IF v_stock.id IS NULL OR v_stock.kind<>'dispense'
   OR (v_command.branch_id,v_command.catalog_id,v_command.sale_format,v_command.quantity,
       v_command.discount_amount) IS DISTINCT FROM
      (v_line.branch_id,v_line.catalog_id,v_line.sale_format,v_line.quantity,v_line.discount_amount)
   OR (v_stock.branch_id,v_stock.catalog_id,v_stock.unit,v_stock.units_per_pack,
       v_stock.dispense_step,v_stock.sale_format,v_stock.quantity,v_stock.base_units)
      IS DISTINCT FROM
      (v_line.branch_id,v_line.catalog_id,v_line.unit,v_line.units_per_pack,
       v_line.dispense_step,v_line.sale_format,v_line.quantity,v_line.base_units) THEN
   RAISE EXCEPTION 'pos_unit_line_stock_scope' USING ERRCODE='23514';
 END IF;
 v_payload:='{"branch_id":"'||v_command.branch_id||'","catalog_id":"'||v_command.catalog_id
   ||'","discount_amount":"'||to_char(v_command.discount_amount,'FM999999999990.00')
   ||'","quantity":'||v_command.quantity||',"sale_format":"'||v_command.sale_format
   ||'","tenant_id":"'||v_command.tenant_id||'","version":1}';
 IF v_command.request_hash<>encode(sha256(convert_to(v_payload,'UTF8')),'hex') THEN
   RAISE EXCEPTION 'pos_unit_line_request_fingerprint' USING ERRCODE='23514';
 END IF;
 v_stock_payload:='{"branch_id":"'||v_stock.branch_id||'","catalog_id":"'||v_stock.catalog_id
   ||'","expires_at":null,"kind":"dispense","packaging":{"dispense_step":'
   ||v_stock.dispense_step||',"unit":"'||v_stock.unit||'","units_per_pack":'
   ||v_stock.units_per_pack||',"version":1},"quantity":'||v_stock.quantity
   ||',"sale_format":"'||v_stock.sale_format||'","tenant_id":"'||v_stock.tenant_id
   ||'","version":1}';
 IF v_stock.request_hash<>encode(sha256(convert_to(v_stock_payload,'UTF8')),'hex') THEN
   RAISE EXCEPTION 'pos_unit_line_stock_fingerprint' USING ERRCODE='23514';
 END IF;
 SELECT count(*),count(*) FILTER(WHERE cost_version=1 AND cost_amount IS NOT NULL),
   sum(cost_amount),-sum(units_delta::NUMERIC)
 INTO v_count,v_known,v_cost,v_units FROM public.unit_stock_entry
   WHERE tenant_id=v_line.tenant_id AND operation_id=v_line.stock_operation_id;
 IF v_count=0 OR v_count<>v_known OR v_units<>v_line.base_units
   OR v_cost IS NULL OR v_cost<>v_line.original_cost_amount THEN
   RAISE EXCEPTION 'pos_unit_line_cost_incomplete' USING ERRCODE='23514';
 END IF;
 RETURN NULL;
END $$;
"""


def upgrade() -> None:
    _sql("""
      CREATE TEMP TABLE unit_line_reference_grants AS
        SELECT oid::regclass::TEXT AS relation,
          has_table_privilege('aurum_schema_owner',oid,'REFERENCES') AS granted
        FROM pg_class WHERE oid IN ('public.tenant'::regclass,'public.branch'::regclass,
          'public.tenant_catalog'::regclass,'public.app_user'::regclass);
      GRANT REFERENCES ON public.tenant,public.branch,public.tenant_catalog,public.app_user
        TO aurum_schema_owner;
    """)
    op.execute(f"""
      CREATE TABLE public.pos_unit_sale_command (
        {IDENTITY}, PRIMARY KEY(tenant_id,id), UNIQUE(tenant_id,id,branch_id,catalog_id),
        request_hash TEXT NOT NULL CHECK(request_hash ~ '^[0-9a-f]{{64}}$'),
        sale_format TEXT NOT NULL CHECK(sale_format IN ('pack','unit')),
        quantity BIGINT NOT NULL CHECK(quantity BETWEEN 1 AND 99999999999),
         discount_amount NUMERIC(14,2) NOT NULL
           CHECK(discount_amount>=0 AND discount_amount<=999999999999.99)
      )
    """)
    op.execute(f"""
      CREATE TABLE public.pos_unit_sale_line (
        {IDENTITY}, PRIMARY KEY(id), UNIQUE(tenant_id,command_id),
        UNIQUE(tenant_id,stock_operation_id), command_id UUID NOT NULL,
        stock_operation_id UUID NOT NULL, version INTEGER NOT NULL CHECK(version=1),
        unit TEXT NOT NULL CHECK(unit IN ('tablet','capsule','ampoule','sachet','suppository')),
        units_per_pack INTEGER NOT NULL CHECK(units_per_pack BETWEEN 2 AND 10000),
        dispense_step INTEGER NOT NULL CHECK(dispense_step BETWEEN 1 AND 10000),
        CHECK(units_per_pack % dispense_step=0),
        sale_format TEXT NOT NULL CHECK(sale_format IN ('pack','unit')),
        quantity BIGINT NOT NULL CHECK(quantity BETWEEN 1 AND 99999999999),
        base_units BIGINT NOT NULL CHECK(base_units BETWEEN 1 AND 99999999999),
        CHECK((sale_format='pack' AND base_units::NUMERIC=quantity::NUMERIC*units_per_pack)
          OR (sale_format='unit' AND base_units=quantity AND quantity%dispense_step=0)),
         original_retail_unit_price NUMERIC(14,2) NOT NULL
           CHECK(original_retail_unit_price>0 AND original_retail_unit_price<=999999999999.99),
        gross_amount NUMERIC(14,2) NOT NULL CHECK(gross_amount>0 AND gross_amount<=999999999999.99),
         discount_amount NUMERIC(14,2) NOT NULL CHECK(discount_amount>=0
           AND discount_amount<=999999999999.99 AND discount_amount<gross_amount),
        net_amount NUMERIC(14,2) NOT NULL CHECK(net_amount>0 AND net_amount<=999999999999.99),
         original_cost_amount NUMERIC(14,2) NOT NULL
           CHECK(original_cost_amount>=0 AND original_cost_amount<=999999999999.99),
        money_version INTEGER NOT NULL CHECK(money_version=1),
        currency TEXT NOT NULL CHECK(currency='TJS'),
        CHECK(gross_amount=original_retail_unit_price*quantity::NUMERIC),
        CHECK(net_amount=gross_amount-discount_amount),
        FOREIGN KEY(tenant_id,command_id,branch_id,catalog_id)
           REFERENCES public.pos_unit_sale_command(tenant_id,id,branch_id,catalog_id)
           ON DELETE RESTRICT,
        FOREIGN KEY(tenant_id,stock_operation_id,branch_id,catalog_id)
           REFERENCES public.unit_stock_operation(tenant_id,id,branch_id,catalog_id)
           ON DELETE RESTRICT
      )
    """)
    _sql("""
      DO $$ DECLARE r RECORD; BEGIN
        FOR r IN SELECT * FROM pg_temp.unit_line_reference_grants WHERE NOT granted LOOP
          EXECUTE format('REVOKE REFERENCES ON %s FROM aurum_schema_owner',r.relation);
        END LOOP;
      END $$;
      DROP TABLE pg_temp.unit_line_reference_grants;
    """)
    _sql(FUNCTIONS)
    for function in ("trg_pos_unit_line_guard", "trg_pos_unit_line_complete"):
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
            tenant_id=public.current_tenant_id()
            AND public.tenant_actor_has_scoped_permission(tenant_id,'pos.sell',branch_id));
          CREATE POLICY tenant_insert ON public.{table} FOR INSERT TO aurum_app WITH CHECK(
            tenant_id=public.current_tenant_id()
            AND public.tenant_actor_has_scoped_permission(tenant_id,'pos.sell',branch_id));
          CREATE TRIGGER a00_tenant_access BEFORE INSERT OR UPDATE OR DELETE ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION public.trg_enforce_tenant_access('pos');
          CREATE TRIGGER a10_pos_unit_line_guard BEFORE INSERT OR UPDATE OR DELETE ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION public.trg_pos_unit_line_guard();
          CREATE TRIGGER z90_audit AFTER INSERT OR UPDATE OR DELETE ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION public.trg_audit_log();
          CREATE CONSTRAINT TRIGGER pos_unit_line_complete AFTER INSERT ON public.{table}
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION public.trg_pos_unit_line_complete();
        """)


def downgrade() -> None:
    _sql("""
      LOCK TABLE public.pos_unit_sale_command,public.pos_unit_sale_line IN ACCESS EXCLUSIVE MODE;
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.pos_unit_sale_command)
          OR EXISTS(SELECT 1 FROM public.pos_unit_sale_line) THEN
          RAISE EXCEPTION 'pos_unit_line_nonempty_downgrade_forbidden' USING ERRCODE='55000';
        END IF;
      END $$;
      DROP TABLE public.pos_unit_sale_line;
      DROP TABLE public.pos_unit_sale_command;
      DROP FUNCTION public.trg_pos_unit_line_complete();
      DROP FUNCTION public.trg_pos_unit_line_guard();
    """)
