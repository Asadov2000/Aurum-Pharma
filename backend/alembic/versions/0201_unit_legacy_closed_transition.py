"""Atomically transfer reconciled closed legacy packs without rewriting their receipt.

Revision ID: 0201
Revises: 0200
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "0201"
down_revision: str | Sequence[str] | None = "0200"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _sql(sql: str) -> None:
    start = 0
    for match in re.finditer(r"\$\$.*?\$\$|'(?:''|[^'])*'|;", sql, flags=re.DOTALL):
        if match.group() == ";":
            statement = sql[start : match.start()].strip()
            if statement:
                op.get_bind().exec_driver_sql(statement)
            start = match.end()
    if sql[start:].strip():
        op.get_bind().exec_driver_sql(sql[start:].strip())


def _replace(signature: str, old: str, new: str) -> None:
    definition = op.get_bind().scalar(
        text("SELECT pg_get_functiondef(CAST(:name AS regprocedure))"), {"name": signature}
    )
    if not isinstance(definition, str) or definition.count(old) != 1:
        raise RuntimeError(f"Legacy transition requires the expected guard: {signature}")
    op.get_bind().exec_driver_sql(definition.replace(old, new, 1))


AUTH_SQL = """
CREATE FUNCTION public.assert_unit_legacy_transition_actor(p_tenant UUID,p_branch UUID)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE v_status TEXT; v_policy public.tenant_access_policy%ROWTYPE; v_permission TEXT;
BEGIN
 IF session_user<>'aurum_app' OR p_tenant IS DISTINCT FROM public.current_tenant_id()
   OR p_tenant IS NULL OR p_branch IS NULL OR public.is_support_session()
   OR public.is_tenant_support_session()
   OR NOT EXISTS(SELECT 1 FROM public.app_user
       WHERE id=public.current_app_user_id() AND status='active') THEN
   RAISE EXCEPTION 'legacy_transition_actor_denied' USING ERRCODE='42501';
 END IF;
 FOREACH v_permission IN ARRAY ARRAY['batches.view','batches.view_costs','batches.update','incoming.view'] LOOP
   IF NOT coalesce(public.tenant_actor_has_scoped_permission(p_tenant,v_permission,p_branch),false) THEN
     RAISE EXCEPTION 'legacy_transition_actor_denied' USING ERRCODE='42501';
   END IF;
 END LOOP;
 IF current_setting('transaction_isolation')<>'read committed' THEN
   RAISE EXCEPTION 'legacy_transition_isolation_invalid' USING ERRCODE='23514';
 END IF;
 PERFORM pg_advisory_xact_lock_shared(hashtextextended('tenant-access:'||p_tenant,0));
 SELECT status INTO v_status FROM public.tenant WHERE id=p_tenant;
 SELECT * INTO v_policy FROM public.tenant_access_policy WHERE tenant_id=p_tenant;
 IF NOT FOUND OR v_status IN ('readonly','suspended','archived') OR (
   (v_policy.expires_at IS NULL OR v_policy.expires_at>statement_timestamp())
   AND (v_policy.mode='readonly' OR 'inventory'=ANY(v_policy.blocked_modules))) THEN
   RAISE EXCEPTION 'legacy_transition_access_denied' USING ERRCODE='42501';
 END IF;
 PERFORM public.assert_current_branch_writer(p_tenant,p_branch,NULL,false);
END $$;
"""

GUARDS_SQL = """
CREATE FUNCTION public.trg_unit_legacy_transition_immutable() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 IF TG_OP<>'INSERT' OR current_user<>'aurum_schema_owner' OR session_user<>'aurum_app'
   OR NEW.created_by IS DISTINCT FROM public.current_app_user_id()
   OR NEW.tenant_id IS DISTINCT FROM public.current_tenant_id() THEN
   RAISE EXCEPTION 'legacy_transition_journal_immutable' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION public.unit_legacy_transition_row_matches(p_table TEXT,p_row JSONB)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE j public.unit_legacy_transition%ROWTYPE;
BEGIN
 SELECT * INTO j FROM public.unit_legacy_transition
   WHERE tenant_id=(p_row->>'tenant_id')::UUID AND source_item_id=(p_row->>'source_item_id')::UUID;
 IF NOT FOUND THEN RETURN false; END IF;
 IF (p_row->>'branch_id')::UUID IS DISTINCT FROM j.branch_id
   OR (p_row->>'catalog_id')::UUID IS DISTINCT FROM j.catalog_id
   OR (p_row->>'created_by')::UUID IS DISTINCT FROM j.created_by THEN
   RAISE EXCEPTION 'legacy_transition_row_identity' USING ERRCODE='23514';
 END IF;
 IF p_table='unit_stock_operation_v2' THEN
   IF (p_row->>'id')::UUID IS DISTINCT FROM j.operation_id OR p_row->'packaging' IS DISTINCT FROM j.packaging
     OR p_row->>'kind' IS DISTINCT FROM 'initialize' OR p_row->>'sale_format' IS DISTINCT FROM 'pack'
     OR (p_row->>'quantity')::BIGINT IS DISTINCT FROM j.quantity
     OR (p_row->>'base_units')::BIGINT IS DISTINCT FROM j.base_units
     OR p_row->>'request_hash' IS DISTINCT FROM j.request_hash THEN
     RAISE EXCEPTION 'legacy_transition_operation_invalid' USING ERRCODE='23514';
   END IF;
 ELSIF p_table='unit_stock_lot_v2' THEN
   IF (p_row->>'id')::UUID IS DISTINCT FROM j.lot_id OR p_row->'packaging' IS DISTINCT FROM j.packaging
     OR (p_row->>'initial_closed_packs')::BIGINT IS DISTINCT FROM j.quantity
     OR (p_row->>'closed_packs')::BIGINT IS DISTINCT FROM 0::BIGINT
     OR (p_row->>'purchase_cost_per_pack')::NUMERIC IS DISTINCT FROM j.purchase_cost_per_pack
     OR (p_row->>'cost_version')::INTEGER IS DISTINCT FROM 1
     OR (p_row->>'expires_at')::DATE IS DISTINCT FROM j.expires_at THEN
     RAISE EXCEPTION 'legacy_transition_lot_invalid' USING ERRCODE='23514';
   END IF;
 ELSIF p_table='unit_stock_entry_v2' THEN
   IF (p_row->>'id')::UUID IS DISTINCT FROM j.entry_id
     OR (p_row->>'operation_id')::UUID IS DISTINCT FROM j.operation_id
     OR (p_row->>'lot_id')::UUID IS DISTINCT FROM j.lot_id
     OR p_row->>'kind' IS DISTINCT FROM 'initialize'
     OR (p_row->>'closed_delta')::BIGINT IS DISTINCT FROM j.quantity
     OR (p_row->>'base_units')::BIGINT IS DISTINCT FROM j.base_units THEN
     RAISE EXCEPTION 'legacy_transition_entry_invalid' USING ERRCODE='23514';
   END IF;
 ELSE RETURN false;
 END IF;
 RETURN true;
END $$;

CREATE FUNCTION public.trg_unit_legacy_transition_movement() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE j public.unit_legacy_transition%ROWTYPE;
BEGIN
 -- Serialize before checking ownership: a concurrent transfer may commit while
 -- this legacy movement waits for the batch. Re-read its journal after the wait.
 PERFORM 1 FROM public.batch WHERE tenant_id=NEW.tenant_id AND id=NEW.batch_id FOR UPDATE;
 SELECT * INTO j FROM public.unit_legacy_transition
   WHERE tenant_id=NEW.tenant_id AND batch_id=NEW.batch_id;
 IF FOUND THEN
   IF current_user<>'aurum_schema_owner' OR session_user<>'aurum_app'
     OR NEW.id IS DISTINCT FROM j.movement_id OR NEW.movement_type<>'correction'
     OR NEW.qty_delta IS DISTINCT FROM -j.quantity::NUMERIC
     OR NEW.source_table IS DISTINCT FROM 'unit_legacy_transition'
     OR NEW.source_id IS DISTINCT FROM j.id OR NEW.created_by IS DISTINCT FROM j.created_by
     OR public.current_app_user_id() IS DISTINCT FROM j.created_by THEN
     RAISE EXCEPTION 'legacy_transition_stock_already_transferred' USING ERRCODE='23514';
   END IF;
 ELSIF NEW.source_table='unit_legacy_transition' THEN
   RAISE EXCEPTION 'legacy_transition_source_missing' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
"""

COMPLETE_SQL = """
CREATE FUNCTION public.assert_unit_legacy_transition_complete(p_tenant UUID,p_item UUID)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE j public.unit_legacy_transition%ROWTYPE; l public.unit_stock_lot_v2%ROWTYPE;
BEGIN
 SELECT * INTO j FROM public.unit_legacy_transition WHERE tenant_id=p_tenant AND source_item_id=p_item;
 SELECT * INTO l FROM public.unit_stock_lot_v2 WHERE tenant_id=p_tenant AND id=j.lot_id;
 IF j.id IS NULL OR l.id IS NULL
   OR NOT EXISTS(SELECT 1 FROM public.batch b WHERE b.tenant_id=p_tenant AND b.id=j.batch_id
     AND b.branch_id=j.branch_id AND b.catalog_id=j.catalog_id AND b.qty_remaining=0)
   OR NOT EXISTS(SELECT 1 FROM public.incoming_item i JOIN public.incoming_document d
     ON d.tenant_id=i.tenant_id AND d.id=i.document_id
     WHERE i.tenant_id=p_tenant AND i.id=j.source_item_id AND i.created_batch_id=j.batch_id
       AND i.created_unit_lot_id IS NULL AND i.created_unit_lot_v2_id IS NULL
       AND d.id=j.source_document_id AND d.status='accepted' AND d.branch_id=j.branch_id
       AND i.catalog_id=j.catalog_id AND i.purchase_price=j.purchase_cost_per_pack)
   OR NOT EXISTS(SELECT 1 FROM public.unit_stock_operation_v2 o WHERE o.tenant_id=p_tenant
     AND o.id=j.operation_id AND o.source_item_id=p_item AND o.quantity=j.quantity
     AND o.base_units=j.base_units AND o.request_hash=j.request_hash AND o.packaging=j.packaging
     AND o.created_at=j.created_at)
   OR NOT EXISTS(SELECT 1 FROM public.unit_stock_entry_v2 e WHERE e.tenant_id=p_tenant
     AND e.id=j.entry_id AND e.operation_id=j.operation_id AND e.lot_id=j.lot_id
     AND e.closed_delta=j.quantity AND e.base_units=j.base_units AND e.source_item_id=p_item
     AND e.created_at=j.created_at)
   OR NOT EXISTS(SELECT 1 FROM public.batch_movement m WHERE m.id=j.movement_id
     AND m.tenant_id=p_tenant AND m.batch_id=j.batch_id AND m.source_table='unit_legacy_transition'
     AND m.source_id=j.id AND m.movement_type='correction' AND m.qty_delta=-j.quantity::NUMERIC
     AND m.created_at=j.created_at)
   OR l.created_at IS DISTINCT FROM j.created_at
   OR (l.branch_id,l.catalog_id,l.source_item_id,l.packaging,l.initial_closed_packs,
       l.purchase_cost_per_pack,l.cost_version,l.expires_at) IS DISTINCT FROM
     (j.branch_id,j.catalog_id,j.source_item_id,j.packaging,j.quantity,
       j.purchase_cost_per_pack,1,j.expires_at)
   OR l.closed_packs<>j.quantity+(SELECT coalesce(sum(closed_delta),0)
     FROM public.unit_stock_movement_v2 WHERE tenant_id=p_tenant AND lot_id=l.id)
   OR j.base_units<>l.closed_packs*(j.packaging->>'units_per_pack')::BIGINT
     +(SELECT coalesce(sum(remaining_units),0) FROM public.unit_stock_opened_pack_v2
       WHERE tenant_id=p_tenant AND lot_id=l.id)
     +(SELECT coalesce(sum(units_taken),0) FROM public.unit_stock_movement_v2
       WHERE tenant_id=p_tenant AND lot_id=l.id) THEN
   RAISE EXCEPTION 'legacy_transition_incomplete' USING ERRCODE='23514';
 END IF;
END $$;

CREATE FUNCTION public.trg_unit_legacy_transition_complete() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 PERFORM public.assert_unit_legacy_transition_complete(NEW.tenant_id,NEW.source_item_id);
 RETURN NULL;
END $$;
"""

POST_SQL = """
CREATE FUNCTION public.post_unit_legacy_transition(
 p_tenant UUID,p_branch UUID,p_batch UUID,p_record UUID,p_command UUID)
RETURNS TABLE(command_id UUID,lot_id UUID,operation_id UUID,movement_id UUID)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE
 j public.unit_legacy_transition%ROWTYPE; b public.batch%ROWTYPE;
 r public.unit_legacy_reconciliation_record%ROWTYPE; i public.incoming_item%ROWTYPE;
 d public.incoming_document%ROWTYPE; v_catalog UUID; v_source_count BIGINT;
 v_source_movement UUID; v_packs BIGINT; v_units NUMERIC; v_cost NUMERIC;
BEGIN
 PERFORM public.assert_unit_legacy_transition_actor(p_tenant,p_branch);
 IF p_command IS NULL OR p_record IS NULL OR p_batch IS NULL THEN
   RAISE EXCEPTION 'legacy_transition_identity_required' USING ERRCODE='23514';
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(
   'aurum-legacy-transition:'||p_tenant||':'||p_command,0));
 PERFORM public.assert_unit_legacy_transition_actor(p_tenant,p_branch);
 SELECT * INTO j FROM public.unit_legacy_transition WHERE tenant_id=p_tenant AND id=p_command;
 IF FOUND THEN
   IF (j.branch_id,j.batch_id,j.record_id,j.created_by) IS DISTINCT FROM
      (p_branch,p_batch,p_record,public.current_app_user_id()) THEN
     RAISE EXCEPTION 'legacy_transition_replay_conflict' USING ERRCODE='23505';
   END IF;
   PERFORM public.assert_unit_legacy_transition_complete(p_tenant,j.source_item_id);
   RETURN QUERY SELECT j.id,j.lot_id,j.operation_id,j.movement_id;
   RETURN;
 END IF;
 SELECT catalog_id INTO v_catalog FROM public.batch
   WHERE tenant_id=p_tenant AND branch_id=p_branch AND id=p_batch;
 PERFORM 1 FROM public.tenant_catalog WHERE tenant_id=p_tenant AND id=v_catalog
   AND is_active AND deleted_at IS NULL FOR NO KEY UPDATE;
 IF NOT FOUND THEN
   RAISE EXCEPTION 'legacy_transition_source_missing' USING ERRCODE='P0002';
 END IF;
 SELECT * INTO b FROM public.batch WHERE tenant_id=p_tenant AND branch_id=p_branch AND id=p_batch
   FOR UPDATE;
 SELECT * INTO r FROM public.unit_legacy_reconciliation_record
   WHERE tenant_id=p_tenant AND branch_id=p_branch AND batch_id=p_batch AND id=p_record;
 PERFORM public.assert_unit_legacy_transition_actor(p_tenant,p_branch);
 IF b.id IS NULL OR r.id IS NULL OR b.catalog_id IS DISTINCT FROM v_catalog
   OR r.catalog_id IS DISTINCT FROM b.catalog_id THEN
   RAISE EXCEPTION 'legacy_transition_source_missing' USING ERRCODE='P0002';
 END IF;
 IF EXISTS(SELECT 1 FROM public.unit_legacy_transition WHERE tenant_id=p_tenant AND batch_id=p_batch) THEN
   RAISE EXCEPTION 'legacy_transition_batch_already_transferred' USING ERRCODE='23505';
 END IF;
 PERFORM public.validate_unit_legacy_reconciliation_count(r.count);
 IF r.count->'opened_packs'<>'[]'::JSONB OR b.unit_packaging_preparation IS NOT NULL
   OR b.unit_packaging_snapshot_v2 IS NOT NULL OR b.qty_remaining IS DISTINCT FROM r.legacy_quantity
   OR b.qty_remaining<=0 OR b.qty_remaining<>trunc(b.qty_remaining)
   OR b.qty_remaining>b.qty_initial OR b.is_blocked
   OR b.expires_at <= (statement_timestamp() AT TIME ZONE coalesce(
     (SELECT report_timezone FROM public.tenant_settings WHERE tenant_id=p_tenant),'Asia/Dushanbe'))::DATE
   OR (r.count->>'closed_packs')::NUMERIC IS DISTINCT FROM b.qty_remaining THEN
   RAISE EXCEPTION 'legacy_transition_requires_current_closed_stock' USING ERRCODE='23514';
 END IF;
 SELECT count(*) INTO v_source_count FROM public.incoming_item
   WHERE tenant_id=p_tenant AND created_batch_id=p_batch;
 IF v_source_count<>1 THEN
   RAISE EXCEPTION 'legacy_transition_source_ambiguous' USING ERRCODE='23514';
 END IF;
 SELECT * INTO i FROM public.incoming_item WHERE tenant_id=p_tenant AND created_batch_id=p_batch;
 SELECT * INTO d FROM public.incoming_document WHERE tenant_id=p_tenant AND id=i.document_id FOR SHARE;
 IF d.id IS NULL OR d.status<>'accepted' OR d.accepted_at IS NULL OR d.accepted_by IS NULL
   OR d.branch_id<>p_branch OR i.catalog_id<>b.catalog_id OR i.qty<>b.qty_initial
   OR i.expires_at<>b.expires_at OR i.batch_number IS DISTINCT FROM b.batch_number
   OR i.manufactured_at IS DISTINCT FROM b.manufactured_at
   OR i.currency<>'TJS' OR d.currency<>'TJS' OR b.currency<>'TJS'
   OR i.purchase_price IS DISTINCT FROM b.purchase_price
   OR i.created_unit_lot_id IS NOT NULL OR i.created_unit_lot_v2_id IS NOT NULL
   OR i.unit_packaging_preparation IS NOT NULL OR i.unit_packaging_snapshot_v2 IS NOT NULL
   OR (SELECT count(*) FROM public.batch_movement WHERE tenant_id=p_tenant
     AND batch_id=p_batch AND movement_type='incoming')<>1 THEN
   RAISE EXCEPTION 'legacy_transition_source_provenance' USING ERRCODE='23514';
 END IF;
 SELECT id INTO v_source_movement FROM public.batch_movement WHERE tenant_id=p_tenant
   AND batch_id=p_batch AND movement_type='incoming' AND source_table='incoming_item'
   AND source_id=i.id AND qty_delta=i.qty;
 IF NOT FOUND THEN
   RAISE EXCEPTION 'legacy_transition_source_provenance' USING ERRCODE='23514';
 END IF;
 v_packs:=b.qty_remaining::BIGINT;
 v_units:=v_packs::NUMERIC*(r.count->'packaging'->>'units_per_pack')::INTEGER;
 v_cost:=v_packs::NUMERIC*i.purchase_price;
 IF v_units NOT BETWEEN 1 AND 99999999999 OR v_cost NOT BETWEEN 0 AND 999999999999.99 THEN
   RAISE EXCEPTION 'legacy_transition_limits' USING ERRCODE='23514';
 END IF;
 INSERT INTO public.unit_legacy_transition(
   tenant_id,id,branch_id,batch_id,record_id,catalog_id,source_document_id,source_item_id,
   source_movement_id,lot_id,operation_id,movement_id,entry_id,packaging,quantity,base_units,
   purchase_cost_per_pack,total_cost,expires_at,request_hash,created_by,created_at)
 VALUES(p_tenant,p_command,p_branch,p_batch,p_record,b.catalog_id,d.id,i.id,v_source_movement,
   public.unit_incoming_uuid5('aurum-legacy-lot:'||p_tenant||':'||p_command),
   public.unit_incoming_uuid5('aurum-legacy-operation:'||p_tenant||':'||p_command),
   public.unit_incoming_uuid5('aurum-legacy-movement:'||p_tenant||':'||p_command),
   public.unit_incoming_uuid5('aurum-legacy-entry:'||p_tenant||':'||p_command),
   r.count->'packaging',v_packs,v_units,i.purchase_price,v_cost,b.expires_at,
   encode(sha256(convert_to('aurum-legacy-transition:1:'||p_tenant||':'||p_command||':'||p_record,
     'UTF8')),'hex'),public.current_app_user_id(),clock_timestamp()) RETURNING * INTO j;
 INSERT INTO public.unit_stock_operation_v2(id,tenant_id,branch_id,catalog_id,source_item_id,
   packaging,kind,sale_format,quantity,base_units,request_hash,created_by)
 VALUES(j.operation_id,j.tenant_id,j.branch_id,j.catalog_id,j.source_item_id,
   j.packaging,'initialize','pack',j.quantity,j.base_units,j.request_hash,j.created_by);
 INSERT INTO public.unit_stock_lot_v2(id,tenant_id,branch_id,catalog_id,source_item_id,
   packaging,expires_at,initial_closed_packs,purchase_cost_per_pack,cost_version,created_by)
 VALUES(j.lot_id,j.tenant_id,j.branch_id,j.catalog_id,j.source_item_id,
   j.packaging,j.expires_at,j.quantity,j.purchase_cost_per_pack,1,j.created_by);
 INSERT INTO public.batch_movement(id,tenant_id,batch_id,movement_type,qty_delta,source_table,
   source_id,created_by,created_at)
 VALUES(j.movement_id,j.tenant_id,j.batch_id,'correction',-j.quantity::NUMERIC,
   'unit_legacy_transition',j.id,j.created_by,j.created_at);
 INSERT INTO public.unit_stock_entry_v2(id,tenant_id,branch_id,catalog_id,source_item_id,
   operation_id,lot_id,kind,closed_delta,base_units,created_by)
 VALUES(j.entry_id,j.tenant_id,j.branch_id,j.catalog_id,j.source_item_id,
   j.operation_id,j.lot_id,'initialize',j.quantity,j.base_units,j.created_by);
 PERFORM public.assert_unit_legacy_transition_complete(p_tenant,i.id);
 RETURN QUERY SELECT j.id,j.lot_id,j.operation_id,j.movement_id;
END $$;
"""

ACTOR_ANCHOR = "BEGIN\n IF current_user<>'aurum_app' OR public.is_support_session()"
ACTOR_BRANCH = """BEGIN
 IF current_user='aurum_schema_owner' AND session_user='aurum_app' THEN
   IF public.unit_legacy_transition_row_matches(TG_TABLE_NAME,to_jsonb(NEW)) THEN
     SELECT created_at INTO NEW.created_at FROM public.unit_legacy_transition
       WHERE tenant_id=NEW.tenant_id AND source_item_id=NEW.source_item_id;
     NEW.updated_at:=NEW.created_at;
     NEW.updated_by:=public.current_app_user_id();RETURN NEW;
   END IF;
 END IF;
 IF current_user<>'aurum_app' OR public.is_support_session()"""
GUARD_ANCHOR = " SELECT * INTO i FROM public.incoming_item WHERE tenant_id=NEW.tenant_id AND id=NEW.source_item_id;"
GUARD_BRANCH = """ IF public.unit_legacy_transition_row_matches(TG_TABLE_NAME,to_jsonb(NEW)) THEN
   RETURN NEW;
 END IF;
""" + GUARD_ANCHOR
SOURCE_ANCHOR = "BEGIN\n SELECT * INTO i FROM public.incoming_item WHERE tenant_id=p_tenant AND id=p_item;"
SOURCE_BRANCH = """BEGIN
 IF EXISTS(SELECT 1 FROM public.unit_legacy_transition WHERE tenant_id=p_tenant AND source_item_id=p_item) THEN
   PERFORM public.assert_unit_legacy_transition_complete(p_tenant,p_item);
   RETURN;
 END IF;
 SELECT * INTO i FROM public.incoming_item WHERE tenant_id=p_tenant AND id=p_item;"""
AUDIT_ANCHOR = "IF p_table_name = 'incoming_document' THEN v_field := 'total_amount';"
AUDIT_BRANCH = """IF p_table_name='unit_legacy_transition' THEN
        RETURN v_result - ARRAY['purchase_cost_per_pack','total_cost'];
      END IF;
      """ + AUDIT_ANCHOR

PRIVATE_FUNCTIONS = (
    "assert_unit_legacy_transition_actor(uuid,uuid)",
    "trg_unit_legacy_transition_immutable()",
    "unit_legacy_transition_row_matches(text,jsonb)",
    "trg_unit_legacy_transition_movement()",
    "assert_unit_legacy_transition_complete(uuid,uuid)",
    "trg_unit_legacy_transition_complete()",
)
POST_SIGNATURE = "post_unit_legacy_transition(uuid,uuid,uuid,uuid,uuid)"


def _guards(*, upgrade: bool) -> None:
    changes = (
        ("public.trg_unit_stock_v2_actor()", ACTOR_ANCHOR, ACTOR_BRANCH),
        ("public.trg_unit_stock_v2_guard()", GUARD_ANCHOR, GUARD_BRANCH),
        ("public.assert_unit_incoming_v2_source(uuid,uuid,boolean)", SOURCE_ANCHOR, SOURCE_BRANCH),
        ("public.audit_redact_table_snapshot(text,jsonb)", AUDIT_ANCHOR, AUDIT_BRANCH),
    )
    for signature, before, after in changes:
        _replace(signature, before if upgrade else after, after if upgrade else before)


def upgrade() -> None:
    _sql("""
      CREATE TEMP TABLE legacy_transition_reference_grants AS
        SELECT oid::regclass AS relation,has_table_privilege('aurum_schema_owner',oid,'REFERENCES') AS granted
        FROM pg_class WHERE oid IN ('public.batch'::regclass,'public.app_user'::regclass,
          'public.incoming_item'::regclass,'public.incoming_document'::regclass,
          'public.batch_movement'::regclass);
      GRANT REFERENCES ON public.batch,public.app_user,public.incoming_item,
        public.incoming_document,public.batch_movement TO aurum_schema_owner;
      CREATE TABLE public.unit_legacy_transition (
        tenant_id UUID NOT NULL,id UUID NOT NULL,branch_id UUID NOT NULL,batch_id UUID NOT NULL,
        record_id UUID NOT NULL,catalog_id UUID NOT NULL,source_document_id UUID NOT NULL,
        source_item_id UUID NOT NULL,source_movement_id UUID NOT NULL,
        lot_id UUID NOT NULL,operation_id UUID NOT NULL,movement_id UUID NOT NULL,entry_id UUID NOT NULL,
        packaging JSONB NOT NULL,quantity BIGINT NOT NULL CHECK(quantity BETWEEN 1 AND 99999999999),
        base_units BIGINT NOT NULL CHECK(base_units BETWEEN 1 AND 99999999999),
        purchase_cost_per_pack NUMERIC(14,2) NOT NULL CHECK(purchase_cost_per_pack>=0),
        total_cost NUMERIC(14,2) NOT NULL CHECK(total_cost>=0),expires_at DATE NOT NULL,
        request_hash TEXT NOT NULL CHECK(request_hash ~ '^[0-9a-f]{64}$'),
        created_by UUID NOT NULL REFERENCES public.app_user(id),created_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,batch_id),UNIQUE(tenant_id,record_id),
        UNIQUE(tenant_id,source_item_id),UNIQUE(tenant_id,lot_id),UNIQUE(movement_id),
        FOREIGN KEY(tenant_id,batch_id) REFERENCES public.batch(tenant_id,id),
        FOREIGN KEY(tenant_id,record_id) REFERENCES public.unit_legacy_reconciliation_record(tenant_id,id),
        FOREIGN KEY(source_document_id) REFERENCES public.incoming_document(id),
        FOREIGN KEY(source_item_id) REFERENCES public.incoming_item(id),
        FOREIGN KEY(source_movement_id) REFERENCES public.batch_movement(id),
        CHECK(base_units::NUMERIC=quantity::NUMERIC*(packaging->>'units_per_pack')::INTEGER),
        CHECK(total_cost=quantity::NUMERIC*purchase_cost_per_pack)
      );
      DO $$ DECLARE r RECORD; BEGIN
        FOR r IN SELECT * FROM pg_temp.legacy_transition_reference_grants WHERE NOT granted LOOP
          EXECUTE format('REVOKE REFERENCES ON %s FROM aurum_schema_owner',r.relation);
        END LOOP;
      END $$;
      DROP TABLE pg_temp.legacy_transition_reference_grants;
      ALTER TABLE public.unit_legacy_transition OWNER TO aurum_schema_owner;
      ALTER TABLE public.unit_legacy_transition ENABLE ROW LEVEL SECURITY;
      ALTER TABLE public.unit_legacy_transition FORCE ROW LEVEL SECURITY;
      REVOKE ALL ON public.unit_legacy_transition FROM PUBLIC,aurum_app,aurum_support;
      GRANT SELECT ON public.unit_legacy_transition TO aurum_app,aurum_support;
      CREATE POLICY schema_owner ON public.unit_legacy_transition TO aurum_schema_owner
        USING(true) WITH CHECK(true);
      CREATE POLICY support_read ON public.unit_legacy_transition FOR SELECT TO aurum_support USING(true);
      CREATE POLICY tenant_read ON public.unit_legacy_transition FOR SELECT TO aurum_app USING(
        tenant_id=public.current_tenant_id()
        AND public.tenant_actor_has_scoped_permission(tenant_id,'batches.view',branch_id)
        AND public.tenant_actor_has_scoped_permission(tenant_id,'batches.view_costs',branch_id));
    """)
    for sql in (AUTH_SQL, GUARDS_SQL, COMPLETE_SQL, POST_SQL):
        _sql(sql)
    for signature in (*PRIVATE_FUNCTIONS, POST_SIGNATURE):
        _sql(f"""
          ALTER FUNCTION public.{signature} OWNER TO aurum_schema_owner;
          REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC,aurum_app,aurum_support;
        """)
    _sql(f"GRANT EXECUTE ON FUNCTION public.{POST_SIGNATURE} TO aurum_app;")
    _guards(upgrade=True)
    _sql("""
      CREATE TRIGGER a00_tenant_access BEFORE INSERT OR UPDATE OR DELETE ON public.unit_legacy_transition
        FOR EACH ROW EXECUTE FUNCTION public.trg_enforce_tenant_access('inventory');
      CREATE TRIGGER a10_unit_legacy_transition_immutable BEFORE INSERT OR UPDATE OR DELETE
        ON public.unit_legacy_transition FOR EACH ROW EXECUTE FUNCTION public.trg_unit_legacy_transition_immutable();
      CREATE TRIGGER z90_audit AFTER INSERT OR UPDATE OR DELETE ON public.unit_legacy_transition
        FOR EACH ROW EXECUTE FUNCTION public.trg_audit_log();
      CREATE CONSTRAINT TRIGGER unit_legacy_transition_complete AFTER INSERT ON public.unit_legacy_transition
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.trg_unit_legacy_transition_complete();
      CREATE TRIGGER a01_unit_legacy_transition_movement BEFORE INSERT ON public.batch_movement
        FOR EACH ROW EXECUTE FUNCTION public.trg_unit_legacy_transition_movement();
    """)


def downgrade() -> None:
    _sql("""
      LOCK TABLE public.unit_legacy_transition IN ACCESS EXCLUSIVE MODE;
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.unit_legacy_transition) THEN
          RAISE EXCEPTION 'legacy_transition_history_exists' USING ERRCODE='55000';
        END IF;
      END $$;
      DROP TRIGGER a01_unit_legacy_transition_movement ON public.batch_movement;
    """)
    _guards(upgrade=False)
    _sql(f"DROP FUNCTION public.{POST_SIGNATURE}; DROP TABLE public.unit_legacy_transition;")
    for signature in reversed(PRIVATE_FUNCTIONS):
        _sql(f"DROP FUNCTION public.{signature};")
