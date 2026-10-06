"""Initialize a separate source-bound physical packaging ledger, without dispensing.

Revision ID: 0175
Revises: 0174
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType

from alembic import op

revision: str = "0175"
down_revision: str | Sequence[str] | None = "0174"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("unit_stock_operation_v2", "unit_stock_lot_v2", "unit_stock_entry_v2")
FUNCTIONS = (
    "unit_incoming_v2_fingerprint(uuid,uuid)",
    "assert_unit_incoming_v2_source(uuid,uuid,boolean)",
    "trg_unit_incoming_v2_binding()",
    "trg_unit_stock_v2_actor()",
    "trg_unit_stock_v2_guard()",
    "trg_unit_stock_v2_apply()",
    "trg_unit_stock_v2_complete()",
    "trg_unit_incoming_v2_document()",
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


def _history(filename: str) -> ModuleType:
    path = Path(__file__).with_name(filename)
    spec = spec_from_file_location(f"aurum_0175_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load historical migration {filename}")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _replace(sql: str, old: str, new: str) -> str:
    if sql.count(old) != 1:
        raise RuntimeError("Historical guard changed; review physical source adaptation")
    return sql.replace(old, new, 1)


def _existing_guards(*, physical: bool) -> None:
    incoming = _history("0166_incoming_unit_stock_provenance.py")
    incoming._guards(prepared=True)
    if physical:
        lifecycle = incoming._historical_sql(
            "0106_guard_incoming_lifecycle.py", "DOCUMENT_GUARD_SQL"
        )
        lifecycle = _replace(
            lifecycle,
            "AND item.created_batch_id IS NULL",
            "AND item.created_batch_id IS NULL AND item.created_unit_lot_id IS NULL "
            "AND item.created_unit_lot_v2_id IS NULL",
        )
        lifecycle = _replace(
            lifecycle,
            "AND item.created_batch_id IS NOT NULL",
            "AND (item.created_batch_id IS NOT NULL OR item.created_unit_lot_id IS NOT NULL "
            "OR item.created_unit_lot_v2_id IS NOT NULL)",
        )
        lifecycle = lifecycle.replace("SECURITY INVOKER", "SECURITY DEFINER", 1)
        lifecycle = _replace(
            lifecycle,
            "  RETURN NEW;\nEND;",
            """  IF NEW.status='accepted' AND EXISTS (
    SELECT 1 FROM public.incoming_item WHERE document_id=NEW.id
      AND created_unit_lot_v2_id IS NOT NULL
  ) AND EXISTS (
    SELECT 1 FROM public.incoming_item WHERE document_id=NEW.id
      AND created_unit_lot_v2_id IS NULL
  ) THEN
    RAISE EXCEPTION 'unit_incoming_v2_mixed_stock_forbidden' USING ERRCODE='23514';
  END IF;
  IF NEW.status='accepted' AND EXISTS (
    SELECT 1 FROM public.incoming_item WHERE document_id=NEW.id
      AND created_unit_lot_id IS NOT NULL
  ) AND EXISTS (
    SELECT 1 FROM public.incoming_item WHERE document_id=NEW.id
      AND created_batch_id IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'unit_incoming_mixed_stock_forbidden' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END;""",
        )
        _sql(lifecycle)
        for filename, constant in incoming.HISTORY[1:]:
            sql = incoming._historical_sql(filename, constant)
            sql = _replace(
                sql,
                "BEGIN\n",
                """BEGIN
  IF NEW.status='accepted' AND EXISTS (
    SELECT 1 FROM public.incoming_item WHERE tenant_id=NEW.tenant_id
      AND document_id=NEW.id AND created_unit_lot_v2_id IS NOT NULL
  ) THEN
    PERFORM public.assert_unit_incoming_v2_source(NEW.tenant_id,id,true)
      FROM public.incoming_item WHERE tenant_id=NEW.tenant_id AND document_id=NEW.id;
    RETURN NEW;
  END IF;
  IF NEW.status='accepted' AND EXISTS (
    SELECT 1 FROM public.incoming_item WHERE tenant_id=NEW.tenant_id
      AND document_id=NEW.id AND created_unit_lot_id IS NOT NULL
  ) THEN
    PERFORM public.assert_unit_incoming_provenance(NEW.tenant_id,NEW.id);
    RETURN NEW;
  END IF;
""",
            ).replace("SECURITY INVOKER", "SECURITY DEFINER", 1)
            _sql(sql)
    snapshot = _history("0174_incoming_blister_snapshot.py").ACCEPTANCE_GUARD_SQL
    if physical:
        snapshot = _replace(
            snapshot,
            "WHERE item.tenant_id = NEW.tenant_id AND item.document_id = NEW.id",
            "WHERE item.tenant_id = NEW.tenant_id AND item.document_id = NEW.id "
            "AND item.created_unit_lot_v2_id IS NULL",
        )
    _sql(snapshot.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))


PROVENANCE_SQL = """
CREATE FUNCTION public.unit_incoming_v2_fingerprint(p_tenant UUID,p_item UUID)
RETURNS TEXT LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE
 i public.incoming_item%ROWTYPE;
 d public.incoming_document%ROWTYPE;
 v_canonical TEXT;
BEGIN
 SELECT * INTO i FROM public.incoming_item WHERE tenant_id=p_tenant AND id=p_item;
 SELECT * INTO d FROM public.incoming_document WHERE tenant_id=p_tenant AND id=i.document_id;
 IF i.id IS NULL OR d.id IS NULL OR i.unit_packaging_snapshot_v2 IS NULL THEN RETURN NULL; END IF;
 -- Every value is a fixed token, UUID, date, or number: none can contain a space.
 v_canonical:=replace(json_build_array(
   'aurum-unit-receive-v2:1',i.id::TEXT,d.id::TEXT,i.tenant_id::TEXT,
   d.branch_id::TEXT,i.catalog_id::TEXT,
   (i.unit_packaging_snapshot_v2->>'version')::INTEGER,
   i.unit_packaging_snapshot_v2->>'unit',
   (i.unit_packaging_snapshot_v2->>'units_per_pack')::INTEGER,
   (i.unit_packaging_snapshot_v2->>'units_per_blister')::INTEGER,
   (i.unit_packaging_snapshot_v2->>'dispense_step')::INTEGER,
   i.qty::BIGINT,to_char(i.purchase_price,'FM9999999999990.00'),
   to_char(i.expires_at,'YYYY-MM-DD'),
   'TJS','initialize','pack')::TEXT,' ','');
 RETURN encode(public.digest(convert_to(v_canonical,'UTF8'),'sha256'),'hex');
END $$;

CREATE FUNCTION public.assert_unit_incoming_v2_source(p_tenant UUID,p_item UUID,p_accepted BOOLEAN)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE
 i public.incoming_item%ROWTYPE;
 d public.incoming_document%ROWTYPE;
 l public.unit_stock_lot_v2%ROWTYPE;
 o public.unit_stock_operation_v2%ROWTYPE;
 v_operation UUID;
BEGIN
 SELECT * INTO i FROM public.incoming_item WHERE tenant_id=p_tenant AND id=p_item;
 SELECT * INTO d FROM public.incoming_document
   WHERE tenant_id=p_tenant AND id=i.document_id FOR SHARE;
 v_operation:=public.unit_incoming_uuid5('aurum-unit-incoming-v2:'||p_tenant||':'||p_item);
 SELECT * INTO o FROM public.unit_stock_operation_v2 WHERE tenant_id=p_tenant AND id=v_operation;
 SELECT * INTO l FROM public.unit_stock_lot_v2
   WHERE tenant_id=p_tenant AND id=i.created_unit_lot_v2_id;
 IF i.id IS NULL OR d.id IS NULL OR l.id IS NULL OR o.id IS NULL
   OR (p_accepted AND d.status<>'accepted')
   OR (NOT p_accepted AND d.status<>'draft')
   OR (p_accepted AND (d.accepted_at IS NULL OR d.accepted_by IS NULL))
   OR i.created_batch_id IS NOT NULL OR i.created_unit_lot_id IS NOT NULL
   OR i.unit_packaging_snapshot_v2 IS NULL OR i.unit_packaging_preparation IS NULL
   OR i.currency<>'TJS' OR d.currency<>'TJS' OR i.qty<=0 OR i.qty<>trunc(i.qty)
   OR o.request_hash IS DISTINCT FROM public.unit_incoming_v2_fingerprint(p_tenant,p_item)
   OR (l.id,l.branch_id,l.catalog_id,l.source_item_id,l.packaging,l.expires_at,
       l.initial_closed_packs,l.purchase_cost_per_pack,l.cost_version)
     IS DISTINCT FROM
      (public.unit_incoming_uuid5('aurum-unit-lot-v2:'||p_tenant||':'||v_operation),
       d.branch_id,i.catalog_id,i.id,i.unit_packaging_snapshot_v2,i.expires_at,
       i.qty::BIGINT,i.purchase_price,1)
   OR (o.branch_id,o.catalog_id,o.source_item_id,o.packaging,o.kind,o.sale_format,
       o.quantity,o.base_units)
     IS DISTINCT FROM
      (d.branch_id,i.catalog_id,i.id,i.unit_packaging_snapshot_v2,'initialize'::TEXT,'pack'::TEXT,
       i.qty::BIGINT,(i.qty*(i.unit_packaging_snapshot_v2->>'units_per_pack')::INTEGER)::BIGINT)
 THEN
   RAISE EXCEPTION 'unit_incoming_v2_provenance_invalid' USING ERRCODE='23514';
 END IF;
 IF p_accepted THEN
   IF EXISTS(SELECT 1 FROM public.incoming_draft_row
       WHERE tenant_id=p_tenant AND document_id=d.id AND status='pending')
     OR l.closed_packs<>l.initial_closed_packs
     OR 1<>(SELECT count(*) FROM public.unit_stock_entry_v2 e
       WHERE e.tenant_id=p_tenant AND e.operation_id=o.id AND e.lot_id=l.id
         AND e.source_item_id=i.id AND e.branch_id=d.branch_id AND e.catalog_id=i.catalog_id
         AND e.kind='initialize' AND e.closed_delta=i.qty AND e.base_units=o.base_units) THEN
     RAISE EXCEPTION 'unit_incoming_v2_incomplete' USING ERRCODE='23514';
   END IF;
 END IF;
END $$;

CREATE FUNCTION public.trg_unit_incoming_v2_binding() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 IF TG_OP IN ('UPDATE','DELETE') AND OLD.created_unit_lot_v2_id IS NOT NULL THEN
   IF TG_OP='DELETE' OR (to_jsonb(NEW)-ARRAY['updated_at','updated_by'])
      IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['updated_at','updated_by']) THEN
     RAISE EXCEPTION 'unit_incoming_v2_source_immutable' USING ERRCODE='55000';
   END IF;
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 IF NEW.created_unit_lot_v2_id IS NULL THEN RETURN NEW; END IF;
 IF TG_OP='UPDATE' AND OLD.created_unit_lot_v2_id IS NOT NULL THEN RETURN NEW; END IF;
 -- BEFORE trigger sees NEW source binding before it exists in the table.
 IF NOT EXISTS (
   SELECT 1 FROM public.incoming_document d
   JOIN public.unit_stock_lot_v2 l ON l.tenant_id=d.tenant_id AND l.id=NEW.created_unit_lot_v2_id
   JOIN public.unit_stock_operation_v2 o ON o.tenant_id=d.tenant_id
     AND o.id=public.unit_incoming_uuid5('aurum-unit-incoming-v2:'||NEW.tenant_id||':'||NEW.id)
   WHERE d.tenant_id=NEW.tenant_id AND d.id=NEW.document_id AND d.status='draft'
     AND NEW.created_batch_id IS NULL AND NEW.created_unit_lot_id IS NULL
     AND NEW.currency='TJS' AND d.currency='TJS'
     AND NEW.unit_packaging_snapshot_v2 IS NOT NULL
     AND (l.id,l.branch_id,l.catalog_id,l.source_item_id,l.packaging,l.expires_at,
          l.initial_closed_packs,l.purchase_cost_per_pack,l.closed_packs,l.cost_version)
       IS NOT DISTINCT FROM
        (public.unit_incoming_uuid5('aurum-unit-lot-v2:'||NEW.tenant_id||':'||o.id),
         d.branch_id,NEW.catalog_id,NEW.id,NEW.unit_packaging_snapshot_v2,NEW.expires_at,
         NEW.qty::BIGINT,NEW.purchase_price,0::BIGINT,1)
     AND (o.branch_id,o.catalog_id,o.source_item_id,o.packaging,o.quantity,o.base_units)
       IS NOT DISTINCT FROM
        (d.branch_id,NEW.catalog_id,NEW.id,NEW.unit_packaging_snapshot_v2,NEW.qty::BIGINT,
         (NEW.qty*(NEW.unit_packaging_snapshot_v2->>'units_per_pack')::INTEGER)::BIGINT)
     AND NOT EXISTS(SELECT 1 FROM public.unit_stock_entry_v2
       WHERE tenant_id=NEW.tenant_id AND (lot_id=l.id OR operation_id=o.id))
 ) THEN
   RAISE EXCEPTION 'unit_incoming_v2_binding_invalid' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION public.trg_unit_stock_v2_actor() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 IF current_user<>'aurum_app' OR public.is_support_session()
   OR public.current_app_user_id() IS NULL
   OR NEW.created_by IS DISTINCT FROM public.current_app_user_id()
   OR NEW.tenant_id IS DISTINCT FROM public.current_tenant_id()
   OR NOT public.tenant_actor_has_scoped_permission(NEW.tenant_id,'incoming.finalize',NEW.branch_id)
 THEN
   RAISE EXCEPTION 'unit_stock_v2_actor_forbidden' USING ERRCODE='42501';
 END IF;
 NEW.created_at:=statement_timestamp();
 NEW.updated_at:=NEW.created_at;
 NEW.updated_by:=public.current_app_user_id();
 RETURN NEW;
END $$;

CREATE FUNCTION public.trg_unit_stock_v2_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE
 i public.incoming_item%ROWTYPE;
 d public.incoming_document%ROWTYPE;
 l public.unit_stock_lot_v2%ROWTYPE;
 o public.unit_stock_operation_v2%ROWTYPE;
BEGIN
 IF TG_OP<>'INSERT' THEN
   IF TG_TABLE_NAME='unit_stock_lot_v2' AND TG_OP='UPDATE'
     AND current_user='aurum_schema_owner' AND pg_trigger_depth()>1
     AND (to_jsonb(NEW)-ARRAY['closed_packs','updated_at','updated_by'])
       IS NOT DISTINCT FROM (to_jsonb(OLD)-ARRAY['closed_packs','updated_at','updated_by'])
     AND NEW.closed_packs=(SELECT coalesce(sum(closed_delta),0)
       FROM public.unit_stock_entry_v2 WHERE tenant_id=OLD.tenant_id AND lot_id=OLD.id) THEN
     RETURN NEW;
   END IF;
   RAISE EXCEPTION 'unit_stock_v2_history_immutable' USING ERRCODE='55000';
 END IF;
 SELECT * INTO i FROM public.incoming_item WHERE tenant_id=NEW.tenant_id AND id=NEW.source_item_id;
 SELECT * INTO d FROM public.incoming_document
   WHERE tenant_id=NEW.tenant_id AND id=i.document_id FOR SHARE;
 IF i.id IS NULL OR d.id IS NULL OR d.status<>'draft'
   OR (NEW.branch_id,NEW.catalog_id) IS DISTINCT FROM (d.branch_id,i.catalog_id)
   OR i.created_batch_id IS NOT NULL OR i.created_unit_lot_id IS NOT NULL
   OR i.unit_packaging_snapshot_v2 IS NULL OR i.currency<>'TJS' OR d.currency<>'TJS'
   OR i.qty<>trunc(i.qty) OR i.qty<=0
   OR i.expires_at <= (statement_timestamp() AT TIME ZONE coalesce(
      (SELECT report_timezone FROM public.tenant_settings WHERE tenant_id=NEW.tenant_id),
      'Asia/Dushanbe'))::DATE THEN
   RAISE EXCEPTION 'unit_stock_v2_source_invalid' USING ERRCODE='23514';
 END IF;
 IF TG_TABLE_NAME='unit_stock_operation_v2' THEN
   IF i.created_unit_lot_v2_id IS NOT NULL
     OR NEW.request_hash IS DISTINCT FROM
       public.unit_incoming_v2_fingerprint(NEW.tenant_id,i.id)
     OR NEW.id<>public.unit_incoming_uuid5('aurum-unit-incoming-v2:'||NEW.tenant_id||':'||i.id)
     OR (NEW.packaging,NEW.quantity,NEW.base_units) IS DISTINCT FROM
       (i.unit_packaging_snapshot_v2,i.qty::BIGINT,
        (i.qty*(i.unit_packaging_snapshot_v2->>'units_per_pack')::INTEGER)::BIGINT) THEN
     RAISE EXCEPTION 'unit_stock_v2_command_invalid' USING ERRCODE='23514';
   END IF;
 ELSIF TG_TABLE_NAME='unit_stock_lot_v2' THEN
   SELECT * INTO o FROM public.unit_stock_operation_v2 WHERE tenant_id=NEW.tenant_id
     AND id=public.unit_incoming_uuid5('aurum-unit-incoming-v2:'||NEW.tenant_id||':'||i.id);
   IF o.id IS NULL OR i.created_unit_lot_v2_id IS NOT NULL OR NEW.closed_packs<>0
     OR (NEW.id,NEW.packaging,NEW.expires_at,NEW.initial_closed_packs,
         NEW.purchase_cost_per_pack,NEW.cost_version) IS DISTINCT FROM
       (public.unit_incoming_uuid5('aurum-unit-lot-v2:'||NEW.tenant_id||':'||o.id),
        i.unit_packaging_snapshot_v2,i.expires_at,i.qty::BIGINT,i.purchase_price,1) THEN
     RAISE EXCEPTION 'unit_stock_v2_lot_invalid' USING ERRCODE='23514';
   END IF;
 ELSE
   SELECT * INTO l FROM public.unit_stock_lot_v2
     WHERE tenant_id=NEW.tenant_id AND id=NEW.lot_id FOR UPDATE;
   SELECT * INTO o FROM public.unit_stock_operation_v2
     WHERE tenant_id=NEW.tenant_id AND id=NEW.operation_id;
   PERFORM public.assert_unit_incoming_v2_source(NEW.tenant_id,i.id,false);
   IF l.id IS NULL OR o.id IS NULL OR i.created_unit_lot_v2_id IS DISTINCT FROM l.id
     OR l.closed_packs<>0 OR (NEW.closed_delta,NEW.base_units) IS DISTINCT FROM
       (l.initial_closed_packs,o.base_units)
     OR o.source_item_id<>i.id OR l.source_item_id<>i.id THEN
     RAISE EXCEPTION 'unit_stock_v2_entry_invalid' USING ERRCODE='23514';
   END IF;
 END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION public.trg_unit_stock_v2_apply() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 UPDATE public.unit_stock_lot_v2 SET closed_packs=closed_packs+NEW.closed_delta,
   updated_at=statement_timestamp(),updated_by=public.current_app_user_id()
   WHERE tenant_id=NEW.tenant_id AND id=NEW.lot_id;
 RETURN NEW;
END $$;

CREATE FUNCTION public.trg_unit_stock_v2_complete() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 IF TG_TABLE_NAME='incoming_item' THEN
   IF NEW.created_unit_lot_v2_id IS NOT NULL THEN
     PERFORM public.assert_unit_incoming_v2_source(NEW.tenant_id,NEW.id,true);
   END IF;
 ELSE
   PERFORM public.assert_unit_incoming_v2_source(NEW.tenant_id,NEW.source_item_id,true);
 END IF;
 RETURN NULL;
END $$;

CREATE FUNCTION public.trg_unit_incoming_v2_document() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 IF NEW.status='accepted' AND EXISTS(SELECT 1 FROM public.incoming_item
   WHERE tenant_id=NEW.tenant_id AND document_id=NEW.id AND created_unit_lot_v2_id IS NOT NULL) THEN
   PERFORM public.assert_unit_incoming_v2_source(NEW.tenant_id,id,true)
     FROM public.incoming_item WHERE tenant_id=NEW.tenant_id AND document_id=NEW.id;
 END IF;
 RETURN NULL;
END $$;
"""


def _tables() -> None:
    old = _history("0162_prepared_unit_stock_ledger.py")
    identity = old.IDENTITY
    snapshot = _history("0174_incoming_blister_snapshot.py").SNAPSHOT_CHECK
    snapshot = (
        snapshot.replace("    AND unit_packaging_preparation IS NOT NULL\n", "")
        .replace(
            "    AND (unit_packaging_snapshot_v2 - ARRAY['version', 'units_per_blister'])\n"
            "      = (unit_packaging_preparation - 'version')\n",
            "",
        )
        .replace("unit_packaging_snapshot_v2 IS NULL OR ", "")
    )
    snapshot = snapshot.replace("unit_packaging_snapshot_v2", "packaging")
    _sql("""
      ALTER TABLE public.incoming_item ADD CONSTRAINT uq_incoming_item_v2_source
        UNIQUE(tenant_id,id,catalog_id);
    """)
    source = """
      source_item_id UUID NOT NULL,
      FOREIGN KEY(tenant_id,source_item_id,catalog_id)
        REFERENCES public.incoming_item(tenant_id,id,catalog_id) ON DELETE RESTRICT
    """
    _sql(f"""
      CREATE TABLE public.unit_stock_operation_v2 (
        {identity}, {source}, packaging JSONB NOT NULL CHECK({snapshot}),
        kind TEXT NOT NULL CHECK(kind='initialize'),
        sale_format TEXT NOT NULL CHECK(sale_format='pack'),
        quantity BIGINT NOT NULL CHECK(quantity BETWEEN 1 AND 99999999999),
        base_units BIGINT NOT NULL CHECK(base_units BETWEEN 1 AND 99999999999),
        request_hash TEXT NOT NULL CHECK(request_hash ~ '^[0-9a-f]{{64}}$'),
        UNIQUE(tenant_id,id,branch_id,catalog_id,source_item_id),
        UNIQUE(tenant_id,id), UNIQUE(tenant_id,source_item_id),
        CHECK(base_units::NUMERIC=quantity::NUMERIC*(packaging->>'units_per_pack')::INTEGER)
      );
      CREATE TABLE public.unit_stock_lot_v2 (
        {identity}, {source}, packaging JSONB NOT NULL CHECK({snapshot}),
        expires_at DATE NOT NULL,
        initial_closed_packs BIGINT NOT NULL CHECK(initial_closed_packs BETWEEN 1 AND 99999999999),
        closed_packs BIGINT NOT NULL DEFAULT 0 CHECK(closed_packs>=0),
        purchase_cost_per_pack NUMERIC(14,2) NOT NULL CHECK(purchase_cost_per_pack>=0),
        cost_version INTEGER NOT NULL DEFAULT 1 CHECK(cost_version=1),
        UNIQUE(tenant_id,id), UNIQUE(tenant_id,id,catalog_id),
        UNIQUE(tenant_id,id,branch_id,catalog_id,source_item_id),
        UNIQUE(tenant_id,source_item_id),
        CHECK(closed_packs<=initial_closed_packs),
        CHECK(initial_closed_packs::NUMERIC*(packaging->>'units_per_pack')::INTEGER<=99999999999)
      );
      CREATE TABLE public.unit_stock_entry_v2 (
        {identity}, {source}, operation_id UUID NOT NULL,lot_id UUID NOT NULL,
        kind TEXT NOT NULL CHECK(kind='initialize'),
        closed_delta BIGINT NOT NULL CHECK(closed_delta BETWEEN 1 AND 99999999999),
        base_units BIGINT NOT NULL CHECK(base_units BETWEEN 1 AND 99999999999),
        UNIQUE(tenant_id,operation_id), UNIQUE(tenant_id,lot_id),
        FOREIGN KEY(tenant_id,operation_id,branch_id,catalog_id,source_item_id)
          REFERENCES public.unit_stock_operation_v2
            (tenant_id,id,branch_id,catalog_id,source_item_id),
        FOREIGN KEY(tenant_id,lot_id,branch_id,catalog_id,source_item_id)
          REFERENCES public.unit_stock_lot_v2(tenant_id,id,branch_id,catalog_id,source_item_id)
      );
      ALTER TABLE public.incoming_item ADD COLUMN created_unit_lot_v2_id UUID,
        ADD CONSTRAINT uq_incoming_item_created_unit_lot_v2 UNIQUE(created_unit_lot_v2_id),
        ADD CONSTRAINT fk_incoming_item_created_unit_lot_v2
          FOREIGN KEY(tenant_id,created_unit_lot_v2_id,catalog_id)
          REFERENCES public.unit_stock_lot_v2(tenant_id,id,catalog_id) ON DELETE RESTRICT,
        ADD CONSTRAINT ck_ii_stock_source_v2_exclusive CHECK(
          num_nonnulls(created_batch_id,created_unit_lot_id,created_unit_lot_v2_id)<=1);
    """)


def _policies() -> None:
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
            tenant_id=public.current_tenant_id() AND (
              public.tenant_actor_has_scoped_permission(tenant_id,'batches.view',branch_id)
              OR public.tenant_actor_has_scoped_permission(tenant_id,'pos.sell',branch_id)
              OR public.tenant_actor_has_scoped_permission(
                tenant_id,'incoming.finalize',branch_id)));
          CREATE POLICY tenant_insert ON public.{table} FOR INSERT TO aurum_app WITH CHECK(
            tenant_id=public.current_tenant_id() AND
            public.tenant_actor_has_scoped_permission(tenant_id,'incoming.finalize',branch_id));
          CREATE TRIGGER a00_tenant_access BEFORE INSERT OR UPDATE OR DELETE ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION public.trg_enforce_tenant_access('inventory');
          CREATE TRIGGER a10_unit_stock_v2_guard BEFORE INSERT OR UPDATE OR DELETE ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION public.trg_unit_stock_v2_guard();
          CREATE TRIGGER a05_unit_stock_v2_actor BEFORE INSERT ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION public.trg_unit_stock_v2_actor();
          CREATE TRIGGER z90_audit AFTER INSERT OR UPDATE OR DELETE ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION public.trg_audit_log();
          CREATE CONSTRAINT TRIGGER unit_stock_v2_complete AFTER INSERT ON public.{table}
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION public.trg_unit_stock_v2_complete();
        """)
    _sql("""
      CREATE TRIGGER b10_unit_stock_v2_apply AFTER INSERT ON public.unit_stock_entry_v2
        FOR EACH ROW EXECUTE FUNCTION public.trg_unit_stock_v2_apply();
      CREATE TRIGGER a16_unit_incoming_v2_binding BEFORE INSERT OR UPDATE OR DELETE
        ON public.incoming_item FOR EACH ROW EXECUTE FUNCTION public.trg_unit_incoming_v2_binding();
      CREATE CONSTRAINT TRIGGER unit_incoming_v2_complete AFTER INSERT OR UPDATE
        ON public.incoming_item DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION public.trg_unit_stock_v2_complete();
      CREATE CONSTRAINT TRIGGER unit_incoming_v2_document AFTER INSERT OR UPDATE
        ON public.incoming_document DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION public.trg_unit_incoming_v2_document();
    """)


def _audit(*, physical: bool) -> None:
    sql = _history("0169_prepared_unit_return_quarantine.py")._audit_redactor(returns=True)
    if physical:
        sql = _replace(
            sql,
            "'unit_stock_lot','unit_stock_entry','pos_unit_sale_line'",
            "'unit_stock_lot','unit_stock_entry','pos_unit_sale_line',"
            "'unit_stock_lot_v2','unit_stock_entry_v2'",
        )
    _sql(sql)


def upgrade() -> None:
    _tables()
    _sql(PROVENANCE_SQL)
    _existing_guards(physical=True)
    for function in FUNCTIONS:
        _sql(f"""
          ALTER FUNCTION public.{function} OWNER TO aurum_schema_owner;
          REVOKE ALL ON FUNCTION public.{function} FROM PUBLIC,aurum_app,aurum_support;
        """)
    _policies()
    _audit(physical=True)


def downgrade() -> None:
    _sql("""
      LOCK TABLE public.incoming_item,public.unit_stock_operation_v2,
        public.unit_stock_lot_v2,public.unit_stock_entry_v2 IN ACCESS EXCLUSIVE MODE;
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.unit_stock_operation_v2)
          OR EXISTS(SELECT 1 FROM public.unit_stock_lot_v2)
          OR EXISTS(SELECT 1 FROM public.unit_stock_entry_v2)
          OR EXISTS(SELECT 1 FROM public.incoming_item
            WHERE created_unit_lot_v2_id IS NOT NULL) THEN
          RAISE EXCEPTION 'unit_stock_v2_nonempty_downgrade_forbidden' USING ERRCODE='55000';
        END IF;
      END $$;
      DROP TRIGGER unit_incoming_v2_document ON public.incoming_document;
      DROP TRIGGER unit_incoming_v2_complete ON public.incoming_item;
      DROP TRIGGER a16_unit_incoming_v2_binding ON public.incoming_item;
    """)
    _existing_guards(physical=False)
    _audit(physical=False)
    _sql("""
      ALTER TABLE public.incoming_item DROP CONSTRAINT ck_ii_stock_source_v2_exclusive,
        DROP CONSTRAINT fk_incoming_item_created_unit_lot_v2,
        DROP CONSTRAINT uq_incoming_item_created_unit_lot_v2,
        DROP COLUMN created_unit_lot_v2_id;
    """)
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE public.{table}")
    for function in reversed(FUNCTIONS):
        op.execute(f"DROP FUNCTION public.{function}")
    op.execute("ALTER TABLE public.incoming_item DROP CONSTRAINT uq_incoming_item_v2_source")
