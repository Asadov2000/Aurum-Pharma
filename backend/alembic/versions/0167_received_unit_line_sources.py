"""Source-bound internal unit posting, without live Sale or receipt activation.

Revision ID: 0167
Revises: 0166
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from alembic import op

revision: str = "0167"
down_revision: str | Sequence[str] | None = "0166"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


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


def _line_functions() -> str:
    path = Path(__file__).with_name("0165_prepared_unit_sale_lines.py")
    spec = spec_from_file_location("aurum_0167_line_history", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load canonical prepared line guards")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    value = module.FUNCTIONS
    if not isinstance(value, str):
        raise TypeError("Historical prepared line guards must be SQL")
    return value.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION")


def _insert_before(sql: str, anchor: str, addition: str) -> str:
    if sql.count(anchor) != 1:
        raise RuntimeError("Prepared line guards changed; review the received policy adaptation")
    return sql.replace(anchor, addition + anchor, 1)


def _received_line_functions() -> str:
    sql = _line_functions()
    sql = _insert_before(
        sql,
        " IF v_command.request_hash<>",
        """ IF v_command.stock_source_kind='received' THEN
   v_payload:=replace(v_payload,',"tenant_id":',
     ',"stock_source_kind":"received","tenant_id":');
   v_payload:=regexp_replace(v_payload,'"version":1}$','"version":2}');
 END IF;
 IF v_stock.stock_source_kind IS DISTINCT FROM v_command.stock_source_kind THEN
   RAISE EXCEPTION 'pos_unit_line_stock_source_policy' USING ERRCODE='23514';
 END IF;
""",
    )
    sql = _insert_before(
        sql,
        " IF v_stock.request_hash<>",
        """ IF v_command.stock_source_kind='received' THEN
   v_stock_payload:=replace(v_stock_payload,',"tenant_id":',
     ',"stock_source_kind":"received","tenant_id":');
   v_stock_payload:=regexp_replace(v_stock_payload,'"version":1}$','"version":2}');
 END IF;
""",
    )
    return _insert_before(
        sql,
        " SELECT count(*),count(*) FILTER(WHERE cost_version=1 AND cost_amount IS NOT NULL),",
        """ IF v_command.stock_source_kind='received' AND EXISTS (
   SELECT 1 FROM public.unit_stock_entry e
   WHERE e.tenant_id=v_line.tenant_id AND e.operation_id=v_line.stock_operation_id
     AND NOT coalesce(public.unit_lot_has_received_source(
       e.tenant_id,e.branch_id,e.catalog_id,e.lot_id),false)
 ) THEN
   RAISE EXCEPTION 'pos_unit_line_received_source_invalid' USING ERRCODE='23514';
 END IF;
""",
    )


RECEIVED_SQL = r"""
CREATE FUNCTION public.unit_lot_has_received_source(
 p_tenant UUID,p_branch UUID,p_catalog UUID,p_lot UUID
) RETURNS BOOLEAN LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 -- The definer may inspect immutable provenance, never impersonate a caller.
 IF session_user<>'aurum_app' OR public.is_support_session()
   OR public.current_tenant_id() IS DISTINCT FROM p_tenant
   OR public.current_app_user_id() IS NULL
   OR NOT coalesce(public.tenant_actor_has_scoped_permission(p_tenant,'pos.sell',p_branch),false)
 THEN RETURN false; END IF;
 RETURN EXISTS(
   SELECT 1 FROM public.unit_stock_lot l
   JOIN public.incoming_item i ON i.tenant_id=l.tenant_id AND i.created_unit_lot_id=l.id
   JOIN public.incoming_document d ON d.tenant_id=i.tenant_id AND d.id=i.document_id
   WHERE l.tenant_id=p_tenant AND l.branch_id=p_branch AND l.catalog_id=p_catalog AND l.id=p_lot
     AND i.catalog_id=p_catalog AND i.created_batch_id IS NULL
     AND d.branch_id=p_branch AND d.status='accepted' AND d.accepted_at IS NOT NULL
     AND d.accepted_by IS NOT NULL AND d.currency='TJS' AND i.currency='TJS'
     AND i.unit_packaging_preparation IS NOT NULL AND i.qty=trunc(i.qty) AND i.qty>0
     AND l.cost_version=1 AND l.initial_closed_packs=i.qty
     AND l.purchase_cost_per_pack IS NOT DISTINCT FROM i.purchase_price
     AND l.expires_at=i.expires_at
     AND (l.version,l.unit,l.units_per_pack,l.dispense_step) IS NOT DISTINCT FROM
       ((i.unit_packaging_preparation->>'version')::INTEGER,
        i.unit_packaging_preparation->>'unit',
        (i.unit_packaging_preparation->>'units_per_pack')::INTEGER,
        (i.unit_packaging_preparation->>'dispense_step')::INTEGER)
 );
END $$;

CREATE FUNCTION public.trg_unit_received_stock_complete() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE v_operation public.unit_stock_operation%ROWTYPE; v_id UUID; v_payload TEXT;
BEGIN
 IF TG_TABLE_NAME='unit_stock_operation' THEN v_id:=NEW.id;
 ELSE v_id:=NEW.operation_id; END IF;
 SELECT * INTO v_operation FROM public.unit_stock_operation
   WHERE tenant_id=NEW.tenant_id AND id=v_id;
 IF v_operation.stock_source_kind IS DISTINCT FROM 'received' THEN RETURN NULL; END IF;
 v_payload:='{"branch_id":"'||v_operation.branch_id||'","catalog_id":"'||v_operation.catalog_id
   ||'","expires_at":null,"kind":"dispense","packaging":{"dispense_step":'
   ||v_operation.dispense_step||',"unit":"'||v_operation.unit||'","units_per_pack":'
   ||v_operation.units_per_pack||',"version":1},"quantity":'||v_operation.quantity
   ||',"sale_format":"'||v_operation.sale_format
   ||'","stock_source_kind":"received","tenant_id":"'||v_operation.tenant_id||'","version":2}';
 IF v_operation.kind<>'dispense'
   OR v_operation.request_hash<>encode(sha256(convert_to(v_payload,'UTF8')),'hex')
   OR NOT EXISTS(SELECT 1 FROM public.unit_stock_entry
     WHERE tenant_id=NEW.tenant_id AND operation_id=v_id)
   OR EXISTS(SELECT 1 FROM public.unit_stock_entry e
     WHERE e.tenant_id=NEW.tenant_id AND e.operation_id=v_id
       AND NOT coalesce(public.unit_lot_has_received_source(
         e.tenant_id,e.branch_id,e.catalog_id,e.lot_id),false)) THEN
   RAISE EXCEPTION 'unit_received_stock_source_invalid' USING ERRCODE='23514';
 END IF;
 RETURN NULL;
END $$;
"""


def upgrade() -> None:
    _sql("""
      LOCK TABLE public.pos_unit_sale_command,public.pos_unit_sale_line,
        public.unit_stock_operation,public.unit_stock_entry IN SHARE ROW EXCLUSIVE MODE;
      ALTER TABLE public.pos_unit_sale_command ADD COLUMN stock_source_kind TEXT NOT NULL
        DEFAULT 'prepared' CHECK(stock_source_kind IN ('prepared','received'));
      ALTER TABLE public.unit_stock_operation ADD COLUMN stock_source_kind TEXT NOT NULL
        DEFAULT 'prepared' CHECK(stock_source_kind IN ('prepared','received')),
        ADD CONSTRAINT ck_unit_stock_received_dispense
        CHECK(stock_source_kind<>'received' OR kind='dispense');
    """)
    _sql(RECEIVED_SQL)
    _sql(_received_line_functions())
    for function in (
        "unit_lot_has_received_source(uuid,uuid,uuid,uuid)",
        "trg_unit_received_stock_complete()",
        "trg_pos_unit_line_guard()",
        "trg_pos_unit_line_complete()",
    ):
        _sql(f"""
          ALTER FUNCTION public.{function} OWNER TO aurum_schema_owner;
          REVOKE ALL ON FUNCTION public.{function} FROM PUBLIC,aurum_app,aurum_support;
        """)
    _sql("""
      GRANT EXECUTE ON FUNCTION public.unit_lot_has_received_source(uuid,uuid,uuid,uuid)
        TO aurum_app;
      CREATE CONSTRAINT TRIGGER unit_received_source_complete
        AFTER INSERT ON public.unit_stock_operation DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION public.trg_unit_received_stock_complete();
      CREATE CONSTRAINT TRIGGER unit_received_source_complete
        AFTER INSERT ON public.unit_stock_entry DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION public.trg_unit_received_stock_complete();
    """)


def downgrade() -> None:
    _sql("""
      LOCK TABLE public.pos_unit_sale_command,public.pos_unit_sale_line,
        public.unit_stock_operation,public.unit_stock_entry IN ACCESS EXCLUSIVE MODE;
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.pos_unit_sale_command WHERE stock_source_kind='received')
          OR EXISTS(SELECT 1 FROM public.unit_stock_operation
            WHERE stock_source_kind='received') THEN
          RAISE EXCEPTION 'unit_received_nonempty_downgrade_forbidden' USING ERRCODE='55000';
        END IF;
      END $$;
      DROP TRIGGER unit_received_source_complete ON public.unit_stock_entry;
      DROP TRIGGER unit_received_source_complete ON public.unit_stock_operation;
    """)
    _sql(_line_functions())
    _sql("""
      DROP FUNCTION public.trg_unit_received_stock_complete();
      DROP FUNCTION public.unit_lot_has_received_source(uuid,uuid,uuid,uuid);
      ALTER TABLE public.pos_unit_sale_command DROP COLUMN stock_source_kind;
      ALTER TABLE public.unit_stock_operation DROP CONSTRAINT ck_unit_stock_received_dispense,
        DROP COLUMN stock_source_kind;
    """)
