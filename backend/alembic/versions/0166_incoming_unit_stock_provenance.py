"""Receive explicit prepared documents into source-bound unit stock only.

Revision ID: 0166
Revises: 0165
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from alembic import op

revision: str = "0166"
down_revision: str | Sequence[str] | None = "0165"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HISTORY = (
    ("0106_guard_incoming_lifecycle.py", "DOCUMENT_GUARD_SQL"),
    ("0134_harden_incoming_and_suppliers.py", "INCOMING_ACCEPTANCE_INTEGRITY_SQL"),
    ("0163_incoming_unit_packaging_preparation.py", "ACCEPTANCE_GUARD_SQL"),
)
FUNCTIONS = (
    "unit_incoming_uuid5(text)",
    "assert_unit_incoming_provenance(uuid,uuid)",
    "trg_unit_incoming_binding()",
    "trg_unit_incoming_complete()",
    "trg_guard_incoming_document_lifecycle()",
    "trg_validate_incoming_acceptance_integrity()",
    "trg_validate_incoming_unit_packaging_preparation()",
)


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


def _historical_sql(filename: str, constant: str) -> str:
    path = Path(__file__).with_name(filename)
    spec = spec_from_file_location(f"aurum_0166_history_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load historical migration {filename}")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    value = getattr(module, constant)
    if not isinstance(value, str):
        raise TypeError("Historical guard SQL must be a string")
    return value.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)


def _replace_once(sql: str, original: str, replacement: str) -> str:
    if sql.count(original) != 1:
        raise RuntimeError("Historical incoming guard changed; review migration adaptation")
    return sql.replace(original, replacement, 1)


def _guards(*, prepared: bool) -> None:
    for index, (filename, constant) in enumerate(HISTORY):
        sql = _historical_sql(filename, constant)
        if prepared and index == 0:
            sql = _replace_once(
                sql,
                "AND item.created_batch_id IS NULL",
                "AND item.created_batch_id IS NULL AND item.created_unit_lot_id IS NULL",
            )
            sql = _replace_once(
                sql,
                "AND item.created_batch_id IS NOT NULL",
                "AND (item.created_batch_id IS NOT NULL OR item.created_unit_lot_id IS NOT NULL)",
            )
            sql = _replace_once(
                sql,
                "  RETURN NEW;\nEND;",
                """  IF NEW.status = 'accepted' AND EXISTS (
    SELECT 1 FROM public.incoming_item WHERE document_id = NEW.id
      AND created_unit_lot_id IS NOT NULL
  ) THEN
    IF EXISTS(SELECT 1 FROM public.incoming_item WHERE document_id=NEW.id
      AND created_batch_id IS NOT NULL) THEN
      RAISE EXCEPTION 'unit_incoming_mixed_stock_forbidden' USING ERRCODE='23514';
    END IF;
  END IF;
  RETURN NEW;
END;""",
            )
            sql = sql.replace("SECURITY INVOKER", "SECURITY DEFINER", 1)
        elif prepared:
            sql = _replace_once(
                sql,
                "BEGIN\n",
                """BEGIN
  IF NEW.status = 'accepted' AND EXISTS (
    SELECT 1 FROM public.incoming_item WHERE tenant_id = NEW.tenant_id
      AND document_id = NEW.id AND created_unit_lot_id IS NOT NULL
  ) THEN
    PERFORM public.assert_unit_incoming_provenance(NEW.tenant_id, NEW.id);
    RETURN NEW;
  END IF;
""",
            )
            sql = sql.replace("SECURITY INVOKER", "SECURITY DEFINER", 1)
        _sql(sql)


PROVENANCE_SQL = r"""
CREATE FUNCTION public.unit_incoming_uuid5(p_name TEXT) RETURNS UUID
LANGUAGE plpgsql IMMUTABLE STRICT SECURITY DEFINER
SET search_path=pg_catalog,pg_temp AS $$
DECLARE v_hash BYTEA;
BEGIN
 v_hash:=public.digest(uuid_send('6ba7b811-9dad-11d1-80b4-00c04fd430c8'::UUID)
   ||convert_to(p_name,'UTF8'),'sha1');
 v_hash:=set_byte(v_hash,6,(get_byte(v_hash,6)&15)|80);
 v_hash:=set_byte(v_hash,8,(get_byte(v_hash,8)&63)|128);
 RETURN substr(encode(v_hash,'hex'),1,32)::UUID;
END $$;

CREATE FUNCTION public.assert_unit_incoming_provenance(p_tenant UUID,p_document UUID)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE v_document public.incoming_document%ROWTYPE;
BEGIN
 SELECT * INTO v_document FROM public.incoming_document
   WHERE tenant_id=p_tenant AND id=p_document;
 IF NOT FOUND OR v_document.status<>'accepted' OR v_document.accepted_at IS NULL
   OR v_document.accepted_by IS NULL OR v_document.currency<>'TJS'
   OR NOT EXISTS(SELECT 1 FROM public.incoming_item
     WHERE tenant_id=p_tenant AND document_id=p_document)
   OR EXISTS(SELECT 1 FROM public.incoming_draft_row
     WHERE tenant_id=p_tenant AND document_id=p_document AND status='pending') THEN
   RAISE EXCEPTION 'unit_incoming_document_incomplete' USING ERRCODE='23514';
 END IF;
 IF EXISTS(
   SELECT 1 FROM public.incoming_item i
   LEFT JOIN public.unit_stock_lot l ON l.tenant_id=i.tenant_id AND l.id=i.created_unit_lot_id
   LEFT JOIN public.unit_stock_operation o ON o.tenant_id=i.tenant_id
     AND o.id=public.unit_incoming_uuid5('aurum-unit-incoming:'||i.tenant_id||':'||i.id)
   WHERE i.document_id=p_document AND (
     i.tenant_id IS DISTINCT FROM p_tenant OR i.created_batch_id IS NOT NULL
     OR i.created_unit_lot_id IS NULL OR i.unit_packaging_preparation IS NULL
     OR i.currency<>'TJS' OR i.qty<=0 OR i.qty<>trunc(i.qty)
     OR i.qty*(i.unit_packaging_preparation->>'units_per_pack')::INTEGER>99999999999
     OR l.id IS NULL OR o.id IS NULL
     OR l.id<>public.unit_incoming_uuid5('aurum-unit-lot:'||i.tenant_id||':'||o.id)
     OR (l.branch_id,l.catalog_id,l.expires_at,l.initial_closed_packs,
         l.purchase_cost_per_pack,l.cost_version,l.version,l.unit,l.units_per_pack,l.dispense_step)
       IS DISTINCT FROM
        (v_document.branch_id,i.catalog_id,i.expires_at,i.qty::BIGINT,i.purchase_price,1,
         (i.unit_packaging_preparation->>'version')::INTEGER,
         i.unit_packaging_preparation->>'unit',
         (i.unit_packaging_preparation->>'units_per_pack')::INTEGER,
         (i.unit_packaging_preparation->>'dispense_step')::INTEGER)
     OR (o.branch_id,o.catalog_id,o.kind,o.sale_format,o.quantity,o.base_units,
         o.unit,o.units_per_pack,o.dispense_step)
       IS DISTINCT FROM
        (v_document.branch_id,i.catalog_id,'initialize'::TEXT,'pack'::TEXT,i.qty::BIGINT,
         (i.qty*(i.unit_packaging_preparation->>'units_per_pack')::INTEGER)::BIGINT,
         i.unit_packaging_preparation->>'unit',
         (i.unit_packaging_preparation->>'units_per_pack')::INTEGER,
         (i.unit_packaging_preparation->>'dispense_step')::INTEGER)
     OR 1<>(SELECT count(*) FROM public.unit_stock_entry e
       WHERE e.tenant_id=i.tenant_id AND e.lot_id=l.id AND e.kind='initialize'
         AND e.operation_id=o.id AND e.branch_id=v_document.branch_id
         AND e.catalog_id=i.catalog_id AND e.closed_delta=i.qty
         AND e.units_delta=o.base_units AND e.opened_delta=0
         AND e.entry_order=0 AND e.pack_id IS NULL AND e.pack_remaining_after=0)
   )
 ) THEN
   RAISE EXCEPTION 'unit_incoming_provenance_invalid' USING ERRCODE='23514';
 END IF;
END $$;

CREATE FUNCTION public.trg_unit_incoming_binding() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE
 v_document public.incoming_document%ROWTYPE;
 v_lot public.unit_stock_lot%ROWTYPE;
 v_operation public.unit_stock_operation%ROWTYPE;
 v_operation_id UUID;
BEGIN
 IF TG_OP IN ('UPDATE','DELETE') AND OLD.created_unit_lot_id IS NOT NULL THEN
   IF TG_OP='DELETE' OR (to_jsonb(NEW)-ARRAY['updated_at','updated_by'])
       IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['updated_at','updated_by']) THEN
     RAISE EXCEPTION 'unit_incoming_source_immutable' USING ERRCODE='55000';
   END IF;
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 IF NEW.created_unit_lot_id IS NULL THEN RETURN NEW; END IF;
 IF TG_OP='UPDATE' AND OLD.created_unit_lot_id IS NOT NULL THEN RETURN NEW; END IF;
 SELECT * INTO v_document FROM public.incoming_document
   WHERE tenant_id=NEW.tenant_id AND id=NEW.document_id FOR SHARE;
 SELECT * INTO v_lot FROM public.unit_stock_lot
   WHERE tenant_id=NEW.tenant_id AND id=NEW.created_unit_lot_id;
 v_operation_id:=public.unit_incoming_uuid5(
   'aurum-unit-incoming:'||NEW.tenant_id||':'||NEW.id);
 SELECT * INTO v_operation FROM public.unit_stock_operation
   WHERE tenant_id=NEW.tenant_id AND id=v_operation_id;
 IF v_document.id IS NULL OR v_document.status<>'draft' OR v_lot.id IS NULL
   OR v_operation.id IS NULL OR NEW.created_batch_id IS NOT NULL
   OR NEW.unit_packaging_preparation IS NULL OR NEW.currency<>'TJS'
   OR v_document.currency<>'TJS' OR NEW.qty<>trunc(NEW.qty) OR NEW.qty<=0
   OR NEW.expires_at <= (statement_timestamp() AT TIME ZONE coalesce(
      (SELECT report_timezone FROM public.tenant_settings WHERE tenant_id=NEW.tenant_id),
      'Asia/Dushanbe'))::DATE
   OR NEW.qty*(NEW.unit_packaging_preparation->>'units_per_pack')::INTEGER>99999999999
   OR v_lot.closed_packs<>0 OR v_lot.is_blocked
   OR v_lot.id<>public.unit_incoming_uuid5('aurum-unit-lot:'||NEW.tenant_id||':'||v_operation_id)
   OR (v_lot.branch_id,v_lot.catalog_id,v_lot.expires_at,v_lot.initial_closed_packs,
       v_lot.purchase_cost_per_pack,v_lot.cost_version,v_lot.version,v_lot.unit,
       v_lot.units_per_pack,v_lot.dispense_step)
     IS DISTINCT FROM
      (v_document.branch_id,NEW.catalog_id,NEW.expires_at,NEW.qty::BIGINT,NEW.purchase_price,1,
       (NEW.unit_packaging_preparation->>'version')::INTEGER,
       NEW.unit_packaging_preparation->>'unit',
       (NEW.unit_packaging_preparation->>'units_per_pack')::INTEGER,
       (NEW.unit_packaging_preparation->>'dispense_step')::INTEGER)
   OR (v_operation.branch_id,v_operation.catalog_id,v_operation.kind,v_operation.sale_format,
       v_operation.quantity,v_operation.base_units,v_operation.unit,
       v_operation.units_per_pack,v_operation.dispense_step)
     IS DISTINCT FROM
      (v_document.branch_id,NEW.catalog_id,'initialize'::TEXT,'pack'::TEXT,NEW.qty::BIGINT,
       (NEW.qty*(NEW.unit_packaging_preparation->>'units_per_pack')::INTEGER)::BIGINT,
       NEW.unit_packaging_preparation->>'unit',
       (NEW.unit_packaging_preparation->>'units_per_pack')::INTEGER,
       (NEW.unit_packaging_preparation->>'dispense_step')::INTEGER)
   OR EXISTS(SELECT 1 FROM public.unit_stock_entry
     WHERE tenant_id=NEW.tenant_id AND (lot_id=v_lot.id OR operation_id=v_operation_id)) THEN
   RAISE EXCEPTION 'unit_incoming_source_binding_invalid' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION public.trg_unit_incoming_complete() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 IF NEW.created_unit_lot_id IS NOT NULL THEN
   PERFORM public.assert_unit_incoming_provenance(NEW.tenant_id,NEW.document_id);
 END IF;
 RETURN NEW;
END $$;
"""


def upgrade() -> None:
    _sql("""
      LOCK TABLE public.incoming_document,public.incoming_item,public.unit_stock_lot,
        public.unit_stock_operation,public.unit_stock_entry IN SHARE ROW EXCLUSIVE MODE;
      ALTER TABLE public.incoming_item ADD COLUMN created_unit_lot_id UUID;
      ALTER TABLE public.unit_stock_lot ADD CONSTRAINT uq_unit_stock_lot_tenant_catalog
        UNIQUE(tenant_id,id,catalog_id);
      ALTER TABLE public.incoming_item ADD CONSTRAINT uq_incoming_item_created_unit_lot
        UNIQUE(created_unit_lot_id);
      ALTER TABLE public.incoming_item ADD CONSTRAINT fk_incoming_item_created_unit_lot
        FOREIGN KEY(tenant_id,created_unit_lot_id,catalog_id)
        REFERENCES public.unit_stock_lot(tenant_id,id,catalog_id) ON DELETE RESTRICT;
      ALTER TABLE public.incoming_item ADD CONSTRAINT ck_incoming_item_stock_source
        CHECK(created_unit_lot_id IS NULL OR created_batch_id IS NULL);
    """)
    _sql(PROVENANCE_SQL)
    _guards(prepared=True)
    for function in FUNCTIONS:
        _sql(f"""
          ALTER FUNCTION public.{function} OWNER TO aurum_schema_owner;
          REVOKE ALL ON FUNCTION public.{function} FROM PUBLIC,aurum_app,aurum_support;
        """)
    _sql("""
      CREATE TRIGGER a15_unit_incoming_binding BEFORE INSERT OR UPDATE OR DELETE
        ON public.incoming_item FOR EACH ROW EXECUTE FUNCTION public.trg_unit_incoming_binding();
      CREATE CONSTRAINT TRIGGER unit_incoming_complete AFTER INSERT OR UPDATE
        ON public.incoming_item DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION public.trg_unit_incoming_complete();
    """)


def downgrade() -> None:
    _sql("""
      LOCK TABLE public.incoming_document,public.incoming_item,public.unit_stock_lot,
        public.unit_stock_operation,public.unit_stock_entry IN ACCESS EXCLUSIVE MODE;
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.incoming_item WHERE created_unit_lot_id IS NOT NULL) THEN
          RAISE EXCEPTION 'unit_incoming_nonempty_downgrade_forbidden' USING ERRCODE='55000';
        END IF;
      END $$;
      DROP TRIGGER unit_incoming_complete ON public.incoming_item;
      DROP TRIGGER a15_unit_incoming_binding ON public.incoming_item;
    """)
    _guards(prepared=False)
    for function in reversed(FUNCTIONS[:4]):
        _sql(f"DROP FUNCTION public.{function}")
    _sql("""
      ALTER TABLE public.incoming_item DROP CONSTRAINT ck_incoming_item_stock_source,
        DROP CONSTRAINT fk_incoming_item_created_unit_lot,
        DROP CONSTRAINT uq_incoming_item_created_unit_lot,
        DROP COLUMN created_unit_lot_id;
      ALTER TABLE public.unit_stock_lot DROP CONSTRAINT uq_unit_stock_lot_tenant_catalog;
    """)
