"""Capture physical inventory facts in the existing stream, with delivery dormant.

Revision ID: 0229
Revises: 0228
"""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

revision = "0229"
down_revision = "0228"
branch_labels = None
depends_on = None

TABLES = (
    "sync_physical_stock_producer_gate",
    "sync_physical_stock_source",
    "sync_physical_stock_event",
    "sync_physical_stock_anchor",
    "sync_physical_stock_projection",
    "sync_physical_stock_baseline",
)
FUNCTIONS = (
    "read_physical_writeoff_cost_source(uuid,uuid,integer)",
    "trg_sync_stream_reservation_transaction()",
    "physical_stock_time(timestamptz)",
    "physical_stock_pack_state(jsonb)",
    "physical_stock_initial_lot(uuid,uuid)",
    "physical_stock_initial_source(uuid,uuid)",
    "physical_stock_source_payload(uuid,uuid)",
    "trg_physical_stock_capture()",
    "trg_physical_catalog_revision()",
    "emit_physical_stock_source(uuid,uuid)",
    "trg_physical_stock_complete()",
    "trg_physical_stock_audit()",
    "trg_physical_stock_private_guard()",
    "trg_physical_stock_projection_guard()",
    "trg_physical_stock_sale_slot()",
)


def _sql(sql: str) -> None:
    spec = spec_from_file_location(
        "aurum_0229_sql", Path(__file__).with_name("0171_unit_cash_intents.py")
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load historical SQL executor")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    module._sql(sql)


SQL = """
CREATE FUNCTION public.read_physical_writeoff_cost_source(
 p_tenant UUID,p_branch UUID,p_limit INTEGER)
RETURNS TABLE(id UUID,tenant_id UUID,branch_id UUID,catalog_id UUID,lot_id UUID,
 units_taken BIGINT,cost_version INTEGER,cost_amount NUMERIC)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 -- This private read exports historical costs, never comments or command input.
 -- SESSION_USER remains the actual caller while CURRENT_USER is the owner.
 IF SESSION_USER<>'aurum_app' OR p_tenant IS NULL OR p_branch IS NULL
  OR p_tenant IS DISTINCT FROM public.current_tenant_id()
  OR public.is_support_session() OR public.is_tenant_support_session()
  OR NOT EXISTS(SELECT 1 FROM public.app_user u
    WHERE u.id=public.current_app_user_id() AND u.status='active')
  OR NOT EXISTS(SELECT 1 FROM public.branch b WHERE b.id=p_branch AND b.tenant_id=p_tenant)
  OR NOT coalesce(public.tenant_actor_has_scoped_permission(
    p_tenant,'batches.view',p_branch),false)
  OR NOT coalesce(public.tenant_actor_has_scoped_permission(
    p_tenant,'batches.view_costs',p_branch),false) THEN
  RAISE EXCEPTION 'physical_writeoff_cost_read_denied' USING ERRCODE='42501'; END IF;
 IF p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 10001 THEN
  RAISE EXCEPTION 'physical_writeoff_cost_read_limit_invalid' USING ERRCODE='22023'; END IF;
 IF current_setting('transaction_isolation') NOT IN ('repeatable read','serializable') THEN
  RAISE EXCEPTION 'physical_writeoff_cost_read_snapshot_required' USING ERRCODE='23514'; END IF;
 RETURN QUERY SELECT e.id,e.tenant_id,e.branch_id,e.catalog_id,e.lot_id,
  e.units_taken,e.cost_version,e.cost_amount
 FROM public.physical_writeoff_entry e JOIN public.physical_writeoff_command c
  ON (c.tenant_id,c.id,c.branch_id,c.lot_id)=
     (e.tenant_id,e.operation_id,e.branch_id,e.lot_id)
 WHERE e.tenant_id=p_tenant AND e.branch_id=p_branch
 ORDER BY e.lot_id,e.operation_id,e.entry_order LIMIT p_limit;
END $$;
CREATE FUNCTION public.trg_sync_stream_reservation_transaction() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 -- txid_current is the top transaction ID even when this UPDATE runs inside a
 -- savepoint. Always replace caller input; this is metadata, never an event hash.
 NEW.last_reservation_transaction_id:=txid_current();
 RETURN NEW;
END $$;
CREATE FUNCTION public.physical_stock_time(p TIMESTAMPTZ) RETURNS TEXT
LANGUAGE sql IMMUTABLE STRICT SET search_path=pg_catalog,pg_temp AS $$
 SELECT replace(to_char(p AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),'.000000Z','Z')
$$;
CREATE FUNCTION public.physical_stock_pack_state(p JSONB) RETURNS JSONB
LANGUAGE sql IMMUTABLE STRICT SET search_path=pg_catalog,pg_temp AS $$
 SELECT jsonb_build_object('intact_blisters',coalesce(p->'intact_blisters','[]'::JSONB),
  'opened_blisters',coalesce(p->'opened_blisters','[]'::JSONB),
  'loose_units',coalesce(p->'loose_units','0'::JSONB))
$$;
CREATE FUNCTION public.physical_stock_initial_lot(p_tenant UUID,p_lot UUID) RETURNS JSONB
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE l public.unit_stock_lot_v2%ROWTYPE; packs JSONB; basis JSONB; amount NUMERIC;
BEGIN
 SELECT * INTO STRICT l FROM public.unit_stock_lot_v2 WHERE tenant_id=p_tenant AND id=p_lot;
 SELECT coalesce(jsonb_agg(jsonb_build_object('id',p.id,'pack_ordinal',p.pack_ordinal,
   'opened_at',public.physical_stock_time(p.opened_at),'state',p.state||jsonb_build_object(
    'id',p.id,'opened_at',public.physical_stock_time(p.opened_at)),
   'remaining_units',p.remaining_units) ORDER BY p.pack_ordinal),'[]'::JSONB),
  coalesce(jsonb_agg(jsonb_build_object('id',p.id,'lot_id',p.lot_id,'tenant_id',p.tenant_id,
   'branch_id',p.branch_id,'catalog_id',p.catalog_id,'transition_id',p.transition_id,
   'pack_ordinal',p.pack_ordinal,'opened_at',public.physical_stock_time(p.opened_at),
   'state',p.state||jsonb_build_object('id',p.id,'opened_at',
 public.physical_stock_time(p.opened_at)),
   'remaining_units',p.remaining_units,'cost_amount',p.cost_amount::TEXT)
    ORDER BY p.pack_ordinal),'[]'::JSONB) INTO packs,basis
 FROM public.unit_stock_opening_pack_v2 p WHERE p.tenant_id=p_tenant AND p.lot_id=p_lot;
 amount:=l.initial_closed_packs*l.purchase_cost_per_pack+l.initial_opened_cost;
 RETURN jsonb_build_object('record_type','physical_lot','id',l.id,'tenant_id',l.tenant_id,
  'branch_id',l.branch_id,'catalog_id',l.catalog_id,'source_item_id',l.source_item_id,
  'expires_at',l.expires_at::TEXT,'created_at',public.physical_stock_time(l.created_at),
  'packaging',l.packaging,'initial_closed_packs',l.initial_closed_packs,
  'initial_opened_packs',l.initial_opened_packs,'initial_opened_units',l.initial_opened_units,
  'initial_opened_cost',l.initial_opened_cost::TEXT,
  'purchase_cost_per_pack',l.purchase_cost_per_pack::TEXT,'closed_packs',l.initial_closed_packs,
  'is_blocked',false,'blocking_revision',0,'opened_packs',packs,'opening_basis',basis,
  'cost',jsonb_build_object('status','known','version',1,
    'initial_cost',amount::NUMERIC(14,2)::TEXT,'remaining_cost',amount::NUMERIC(14,2)::TEXT));
END $$;
CREATE FUNCTION public.physical_stock_initial_source(p_tenant UUID,p_operation UUID)
RETURNS TABLE(lot_id UUID,source_kind TEXT)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE o public.unit_stock_operation_v2%ROWTYPE; e public.unit_stock_entry_v2%ROWTYPE;
 l public.unit_stock_lot_v2%ROWTYPE; j public.unit_legacy_transition%ROWTYPE;
 i public.incoming_item%ROWTYPE; d public.incoming_document%ROWTYPE;
BEGIN
 SELECT * INTO o FROM public.unit_stock_operation_v2
  WHERE tenant_id=p_tenant AND id=p_operation;
 SELECT * INTO e FROM public.unit_stock_entry_v2
  WHERE tenant_id=p_tenant AND operation_id=p_operation;
 SELECT * INTO l FROM public.unit_stock_lot_v2 WHERE tenant_id=p_tenant AND id=e.lot_id;
 IF o.id IS NULL OR e.id IS NULL OR l.id IS NULL
  OR o.kind<>'initialize' OR o.sale_format<>'pack' OR e.kind<>'initialize'
  OR (e.branch_id,e.catalog_id,e.source_item_id,e.base_units) IS DISTINCT FROM
     (o.branch_id,o.catalog_id,o.source_item_id,o.base_units)
  OR (l.branch_id,l.catalog_id,l.source_item_id,l.packaging) IS DISTINCT FROM
     (o.branch_id,o.catalog_id,o.source_item_id,o.packaging) THEN
  RAISE EXCEPTION 'physical_stock_initial_source_invalid' USING ERRCODE='23514'; END IF;
 SELECT * INTO j FROM public.unit_legacy_transition
  WHERE tenant_id=p_tenant AND operation_id=p_operation;
 IF j.id IS NOT NULL THEN
  IF (j.lot_id,j.source_item_id,j.branch_id,j.catalog_id) IS DISTINCT FROM
     (l.id,o.source_item_id,o.branch_id,o.catalog_id) THEN
   RAISE EXCEPTION 'physical_stock_initial_source_invalid' USING ERRCODE='23514'; END IF;
  IF j.initialization_version=2 THEN
   PERFORM public.assert_unit_legacy_opened_complete(p_tenant,o.source_item_id);
  ELSE
   PERFORM public.assert_unit_legacy_transition_complete(p_tenant,o.source_item_id);
  END IF;
  RETURN QUERY SELECT l.id,'legacy_transition'::TEXT;
  RETURN;
 END IF;
 -- This is initialization evidence, not a statement about today's remainder.
 -- Later dispensing/write-off events own their changes to the current balance.
 SELECT * INTO i FROM public.incoming_item WHERE tenant_id=p_tenant AND id=o.source_item_id;
 SELECT * INTO d FROM public.incoming_document WHERE tenant_id=p_tenant AND id=i.document_id;
 IF i.id IS NULL OR d.id IS NULL OR d.status<>'accepted'
  OR d.accepted_at IS NULL OR d.accepted_by IS NULL
  OR i.created_batch_id IS NOT NULL OR i.created_unit_lot_id IS NOT NULL
  OR i.created_unit_lot_v2_id IS DISTINCT FROM l.id
  OR i.unit_packaging_snapshot_v2 IS NULL OR i.unit_packaging_preparation IS NULL
  OR i.currency<>'TJS' OR d.currency<>'TJS' OR i.qty<=0 OR i.qty<>trunc(i.qty)
  OR o.id IS DISTINCT FROM public.unit_incoming_uuid5(
    'aurum-unit-incoming-v2:'||p_tenant||':'||i.id)
  OR o.request_hash IS DISTINCT FROM public.unit_incoming_v2_fingerprint(p_tenant,i.id)
  OR (o.branch_id,o.catalog_id,o.quantity,o.base_units) IS DISTINCT FROM
     (d.branch_id,i.catalog_id,i.qty::BIGINT,
      (i.qty*(i.unit_packaging_snapshot_v2->>'units_per_pack')::INTEGER)::BIGINT)
  OR (l.id,l.packaging,l.expires_at,l.initial_closed_packs,l.purchase_cost_per_pack,
      l.cost_version,l.initial_opened_packs,l.initial_opened_units,l.initial_opened_cost)
     IS DISTINCT FROM
     (public.unit_incoming_uuid5('aurum-unit-lot-v2:'||p_tenant||':'||o.id),
      i.unit_packaging_snapshot_v2,i.expires_at,i.qty::BIGINT,i.purchase_price,1,0,0,0::NUMERIC)
  OR e.closed_delta IS DISTINCT FROM i.qty::BIGINT
  OR EXISTS(SELECT 1 FROM public.incoming_draft_row
    WHERE tenant_id=p_tenant AND document_id=d.id AND status='pending') THEN
  RAISE EXCEPTION 'physical_stock_initial_source_invalid' USING ERRCODE='23514'; END IF;
 RETURN QUERY SELECT l.id,'initialize'::TEXT;
END $$;
CREATE FUNCTION public.physical_stock_source_payload(p_tenant UUID,p_event UUID) RETURNS JSONB
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE s public.sync_physical_stock_source%ROWTYPE; result JSONB; movements JSONB;
 c public.unit_stock_cart_command_v2%ROWTYPE; sale UUID; financial_event UUID;
 lot UUID; kind TEXT;
BEGIN
 SELECT * INTO STRICT s FROM public.sync_physical_stock_source
  WHERE tenant_id=p_tenant AND id=p_event;
 result:=jsonb_build_object('schema_version',5,'event_id',s.id,'operation_id',s.id,
  'tenant_id',s.tenant_id,'branch_id',s.branch_id,'source_kind',s.source_kind,
  'source_id',s.source_id,'occurred_at',public.physical_stock_time(s.occurred_at),
  'source_owner','standalone','checkout_sale_id',NULL,'checkout_event_id',NULL,
  'initializations','[]'::JSONB,'movements','[]'::JSONB,'configuration',NULL,'blocking',NULL,
  'invalidation',NULL);
 IF s.source_kind IN ('catalog_config','block','baseline_required') THEN
  RETURN result||s.captured_payload;
 ELSIF s.source_kind='initialize' THEN
  SELECT evidence.lot_id,evidence.source_kind INTO STRICT lot,kind
   FROM public.physical_stock_initial_source(s.tenant_id,s.source_id) evidence;
  RETURN result||jsonb_build_object('source_kind',kind,'initializations',
    jsonb_build_array(public.physical_stock_initial_lot(s.tenant_id,lot)));
 ELSIF s.source_kind='dispense' THEN
  SELECT * INTO STRICT c FROM public.unit_stock_cart_command_v2
   WHERE tenant_id=s.tenant_id AND id=s.source_id;
  PERFORM public.assert_unit_cart_v2_complete(s.tenant_id,s.source_id);
  sale:=coalesce(c.checkout_sale_id,c.mixed_checkout_sale_id);
  IF sale IS NOT NULL THEN
   IF c.checkout_sale_id IS NOT NULL THEN
    PERFORM public.assert_physical_checkout(s.tenant_id,sale,true);
   ELSE PERFORM public.assert_mixed_checkout(s.tenant_id,sale,true); END IF;
   SELECT coalesce(physical_checkout_id,mixed_checkout_id) INTO STRICT financial_event
    FROM public.pos_retail_checkout_origin
    WHERE tenant_id=s.tenant_id AND sale_id=sale AND id=s.source_id
     AND origin_node_id=s.origin_node_id AND writer_epoch=s.writer_epoch;
   result:=result||jsonb_build_object('source_owner','checkout',
    'checkout_sale_id',sale,'checkout_event_id',financial_event);
  END IF;
  SELECT coalesce(jsonb_agg(jsonb_build_object('id',m.id,'line_id',m.line_id,
   'entry_order',m.entry_order,'lot_id',m.lot_id,'catalog_id',m.catalog_id,'pack_id',m.pack_id,
   'kind',m.kind,'closed_delta',m.closed_delta,'units_taken',m.units_taken,
   'sealed_start',CASE WHEN m.pack_id IS NULL THEN m.cost_pack_ordinal_start
    ELSE m.sealed_start END,
   'sealed_count',CASE WHEN m.pack_id IS NULL THEN m.cost_pack_count ELSE m.sealed_count END,
   'before_state',public.physical_stock_pack_state(
     CASE WHEN m.kind='pack_open' THEN '{}'::JSONB ELSE m.before_state END),
   'after_state',public.physical_stock_pack_state(m.after_state),'pack_ordinal',p.pack_ordinal,
   'opened_at',public.physical_stock_time(p.opened_at),'cost_unit_offset',m.cost_unit_offset,
   'cost_original_pack_cost',m.cost_original_pack_cost::TEXT,'cost_amount',m.cost_amount::TEXT,
   'cost_version',m.cost_version) ORDER BY m.entry_order),'[]'::JSONB) INTO movements
  FROM public.unit_stock_movement_v2 m LEFT JOIN public.unit_stock_opened_pack_v2 p
   ON p.tenant_id=m.tenant_id AND p.id=m.pack_id
  WHERE m.tenant_id=s.tenant_id AND m.command_id=s.source_id;
 ELSIF s.source_kind='writeoff' THEN
  SELECT coalesce(jsonb_agg(jsonb_build_object('id',m.id,'line_id',NULL,
   'entry_order',m.entry_order,'lot_id',m.lot_id,'catalog_id',m.catalog_id,'pack_id',m.pack_id,
   'kind','writeoff','closed_delta',m.closed_delta,'units_taken',m.units_taken,
   'sealed_start',m.sealed_start,'sealed_count',m.sealed_count,
   'before_state',public.physical_stock_pack_state(m.before_state),
   'after_state',public.physical_stock_pack_state(m.after_state),'pack_ordinal',p.pack_ordinal,
   'opened_at',public.physical_stock_time(p.opened_at),'cost_unit_offset',m.cost_unit_offset,
   'cost_original_pack_cost',m.cost_original_pack_cost::TEXT,'cost_amount',m.cost_amount::TEXT,
   'cost_version',m.cost_version) ORDER BY m.entry_order),'[]'::JSONB) INTO movements
  FROM public.physical_writeoff_entry m LEFT JOIN public.unit_stock_opened_pack_v2 p
   ON p.tenant_id=m.tenant_id AND p.id=m.pack_id
  WHERE m.tenant_id=s.tenant_id AND m.operation_id=s.source_id;
  IF jsonb_array_length(movements)=0 THEN
   RAISE EXCEPTION 'physical_stock_source_incomplete' USING ERRCODE='23514'; END IF;
 ELSIF s.source_kind='writeoff_cancelled' THEN RETURN result;
 ELSE RAISE EXCEPTION 'physical_stock_source_invalid' USING ERRCODE='23514'; END IF;
 IF jsonb_array_length(movements) NOT BETWEEN 1 AND 10000 THEN
  RAISE EXCEPTION 'physical_stock_source_incomplete' USING ERRCODE='23514'; END IF;
 RETURN result||jsonb_build_object('movements',movements);
END $$;
CREATE FUNCTION public.trg_physical_catalog_revision() RETURNS TRIGGER
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 IF TG_OP='INSERT' THEN NEW.physical_config_revision:=1;
 ELSIF to_jsonb(NEW)->'unit_sale_preparation'
  IS DISTINCT FROM to_jsonb(OLD)->'unit_sale_preparation'
  OR NEW.base_price IS DISTINCT FROM OLD.base_price THEN
  NEW.physical_config_revision:=OLD.physical_config_revision+1;
 ELSE NEW.physical_config_revision:=OLD.physical_config_revision; END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION public.trg_physical_stock_capture() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant UUID; branch UUID; source UUID; kind TEXT; captured JSONB; occurred TIMESTAMPTZ;
 stream public.sync_stream%ROWTYPE; gate RECORD; old_branch TEXT; unsupported BOOLEAN:=false;
BEGIN
 -- Historical owner/migrator rehearsals may temporarily remove additive columns.
 IF SESSION_USER IN ('aurum_schema_owner','aurum_migrator') THEN RETURN NEW; END IF;
 tenant:=NEW.tenant_id; source:=NEW.id; occurred:=clock_timestamp();
 IF TG_TABLE_NAME IN ('unit_stock_operation','unit_stock_entry','unit_stock_lot',
 'unit_stock_opened_pack') THEN
  branch:=NEW.branch_id; source:=gen_random_uuid(); kind:='baseline_required';
  captured:=jsonb_build_object('invalidation',jsonb_build_object(
   'reason','legacy_physical_change','source_table',TG_TABLE_NAME,'source_id',NEW.id));
  IF TG_OP='UPDATE' AND to_jsonb(NEW)=to_jsonb(OLD) THEN RETURN NEW; END IF;
 ELSIF TG_TABLE_NAME='tenant_catalog' THEN
  IF TG_OP='UPDATE' AND NEW.physical_config_revision=OLD.physical_config_revision THEN RETURN
 NEW; END IF;
  kind:='catalog_config'; source:=gen_random_uuid();
  captured:=jsonb_build_object('configuration',jsonb_build_object('catalog_id',NEW.id,
   'revision',NEW.physical_config_revision,'base_price',NEW.base_price::TEXT,
   'preparation',CASE WHEN NEW.unit_sale_preparation->'units_per_blister'='null'::JSONB
    THEN NEW.unit_sale_preparation-'units_per_blister' ELSE NEW.unit_sale_preparation END));
 ELSIF TG_TABLE_NAME='unit_stock_lot_v2' THEN
  IF NEW.is_blocked IS NOT DISTINCT FROM OLD.is_blocked THEN RETURN NEW; END IF;
  branch:=NEW.branch_id; source:=gen_random_uuid(); kind:='block';
  captured:=jsonb_build_object('blocking',jsonb_build_object('lot_id',NEW.id,
   'catalog_id',NEW.catalog_id,'is_blocked',NEW.is_blocked,'revision',NEW.blocking_revision));
 ELSE
  branch:=NEW.branch_id;
  kind:=CASE TG_TABLE_NAME WHEN 'unit_stock_operation_v2' THEN 'initialize'
   WHEN 'unit_stock_cart_command_v2' THEN 'dispense'
   WHEN 'physical_writeoff_command' THEN 'writeoff'
   WHEN 'physical_writeoff_cancellation' THEN 'writeoff_cancelled' END;
  occurred:=NEW.created_at;
 END IF;
 -- Only source triggers may create bindings; caller-controlled GUCs are not evidence.
 IF SESSION_USER NOT IN ('aurum_app','aurum_catalog_worker') THEN unsupported:=true; END IF;
 IF SESSION_USER='aurum_app' THEN
  IF public.is_tenant_support_session() THEN unsupported:=true; END IF;
 END IF;
 IF NOT unsupported AND tenant IS DISTINCT FROM public.current_tenant_id() THEN
  RAISE EXCEPTION 'physical_stock_tenant_invalid' USING ERRCODE='42501'; END IF;
 old_branch:=current_setting('app.branch_id',true);
 FOR gate IN SELECT id,enabled FROM public.sync_physical_stock_producer_gate
  WHERE tenant_id=tenant AND (branch IS NULL OR id=branch) ORDER BY id FOR SHARE LOOP
  IF NOT gate.enabled THEN CONTINUE; END IF;
  IF unsupported THEN
   RAISE EXCEPTION 'physical_stock_runtime_writer_unsupported' USING ERRCODE='42501'; END IF;
  SELECT * INTO stream FROM public.sync_stream
   WHERE tenant_id=tenant AND branch_id=gate.id;
  IF EXISTS(SELECT 1 FROM public.sync_physical_stock_source s
   JOIN public.sync_physical_stock_event e ON e.tenant_id=s.tenant_id AND e.id=s.id
   WHERE s.source_transaction_id=txid_current()) THEN
   RAISE EXCEPTION 'physical_stock_capture_after_dispatch' USING ERRCODE='23514'; END IF;
  IF NOT FOUND OR NOT EXISTS(SELECT 1 FROM public.sync_writer_epoch e JOIN public.sync_node n
   ON n.id=e.writer_node_id AND n.tenant_id=e.tenant_id AND n.branch_id=e.branch_id
   WHERE e.tenant_id=tenant AND e.branch_id=gate.id AND e.writer_epoch=stream.writer_epoch
    AND e.writer_node_id=stream.writer_node_id AND e.state='active' AND e.capability='cloud_full'
    AND e.allowed_register_id IS NULL AND n.node_kind='cloud' AND n.mode='cloud_writer'
    AND n.status='active') THEN
   RAISE EXCEPTION 'physical_stock_writer_invalid' USING ERRCODE='23514'; END IF;
  PERFORM set_config('app.branch_id',gate.id::TEXT,true);
  INSERT INTO public.sync_physical_stock_source(tenant_id,id,branch_id,source_kind,source_id,
   source_transaction_id,origin_node_id,writer_epoch,occurred_at,captured_payload,created_by)
  VALUES(tenant,gen_random_uuid(),gate.id,kind,source,txid_current(),stream.writer_node_id,
   stream.writer_epoch,occurred,captured,public.current_app_user_id());
 END LOOP;
 PERFORM set_config('app.branch_id',coalesce(old_branch,''),true);
 RETURN NEW;
END $$;
CREATE FUNCTION public.emit_physical_stock_source(p_tenant UUID,p_event UUID) RETURNS VOID
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE saved public.sync_physical_stock_source%ROWTYPE; payload JSONB; envelope JSONB;
 position RECORD; digest TEXT; checksum TEXT; projection TEXT;
 fingerprint TEXT; timestamp_text TEXT; old_branch TEXT;
BEGIN
 SELECT * INTO STRICT saved FROM public.sync_physical_stock_source
  WHERE tenant_id=p_tenant AND id=p_event;
 IF EXISTS(SELECT 1 FROM public.sync_physical_stock_event WHERE tenant_id=saved.tenant_id AND
 id=saved.id)
  THEN RETURN; END IF;
 IF saved.source_transaction_id IS DISTINCT FROM txid_current() THEN
  RAISE EXCEPTION 'physical_stock_current_source_required' USING ERRCODE='23514'; END IF;
 payload:=public.physical_stock_source_payload(saved.tenant_id,saved.id);
 IF octet_length(public.retail_event_canonical_json(payload))>900000 THEN
  RAISE EXCEPTION 'physical_stock_record_too_large' USING ERRCODE='23514'; END IF;
 old_branch:=current_setting('app.branch_id',true);
 PERFORM set_config('app.branch_id',saved.branch_id::TEXT,true);
 SELECT * INTO position FROM public.reserve_sync_event_position(saved.tenant_id,saved.branch_id);
 IF (position.origin_node_id,position.writer_epoch) IS DISTINCT FROM
   (saved.origin_node_id,saved.writer_epoch) THEN
  RAISE EXCEPTION 'physical_stock_writer_changed' USING ERRCODE='23514'; END IF;
 digest:=public.retail_event_hash(payload);
 timestamp_text:=to_char(saved.occurred_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"');
 checksum:=public.retail_event_hash(jsonb_build_object('previous_checksum',
 position.previous_checksum,
  'event_id',saved.id,'tenant_id',saved.tenant_id,'branch_id',saved.branch_id,
  'origin_node_id',saved.origin_node_id,'writer_epoch',saved.writer_epoch,'sequence',position.sequence,
  'operation_id',saved.id,'aggregate_type','physical_stock','aggregate_id',saved.source_id,
  'event_type','inventory.physical.changed.v5','schema_version',5,
  'occurred_at',timestamp_text,'payload_hash',digest));
 projection:=public.retail_event_hash(jsonb_build_object(
  'previous_checksum',position.previous_projection_checksum,'origin_node_id',saved.origin_node_id,
  'writer_epoch',saved.writer_epoch,'sequence',position.sequence,'sale_id',saved.source_id,
  'projection_hash',digest));
 envelope:=jsonb_build_object('event_id',saved.id,'tenant_id',saved.tenant_id,'branch_id',saved.branch_id,
  'origin_node_id',saved.origin_node_id,'writer_epoch',saved.writer_epoch,'sequence',position.sequence,
  'operation_id',saved.id,'aggregate_type','physical_stock','aggregate_id',saved.source_id,
  'event_type','inventory.physical.changed.v5','schema_version',5,
  'occurred_at',public.physical_stock_time(saved.occurred_at),'payload',payload,'payload_hash',digest,
  'stream_checksum',checksum,'projection_hash',digest,'projection_checksum',projection);
 fingerprint:=public.retail_event_hash(jsonb_build_object('domain','aurum-physical-stock-event',
  'schema_version',5,'envelope',envelope));
 INSERT INTO public.sync_outbox(event_id,tenant_id,branch_id,origin_node_id,writer_epoch,sequence,
  operation_id,aggregate_type,aggregate_id,event_type,schema_version,occurred_at,
  payload,payload_hash,stream_checksum,projection_hash,projection_checksum)
 VALUES(saved.id,saved.tenant_id,saved.branch_id,saved.origin_node_id,saved.writer_epoch,position.sequence,
  saved.id,'physical_stock',saved.source_id,'inventory.physical.changed.v5',5,saved.occurred_at,
  payload,digest,checksum,digest,projection);
 INSERT INTO public.sync_physical_stock_event(tenant_id,id,branch_id,origin_node_id,writer_epoch,
  sequence,previous_source_checksum,previous_projection_checksum,event_fingerprint,envelope,
 created_by)
 VALUES(saved.tenant_id,saved.id,saved.branch_id,saved.origin_node_id,saved.writer_epoch,position.sequence,
  position.previous_checksum,position.previous_projection_checksum,fingerprint,envelope,
 saved.created_by);
 PERFORM public.finalize_sync_event_position(position.stream_id,position.sequence,checksum,
 projection);
 PERFORM set_config('app.branch_id',coalesce(old_branch,''),true);
 RETURN;
END $$;
CREATE FUNCTION public.trg_physical_stock_complete() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE pending RECORD;
BEGIN
 IF NOT EXISTS(SELECT 1 FROM public.sync_physical_stock_source s
  WHERE s.source_transaction_id=txid_current() AND NOT EXISTS(
   SELECT 1 FROM public.sync_physical_stock_event e
    WHERE e.tenant_id=s.tenant_id AND e.id=s.id)) THEN RETURN NEW; END IF;
 -- One callback drains all captures in canonical branch/source order. This avoids
 -- opposite branch lock acquisition for catalog fanout and multi-branch work.
 -- A financial writer can already own a stream lock before deferred dispatch.
 -- Reject crossing another branch before requesting any new stream lock. The
 -- owner trigger records the top transaction across nested/released savepoints.
 IF EXISTS(SELECT 1 FROM public.sync_stream stream
  WHERE stream.last_reservation_transaction_id=txid_current() AND stream.last_sequence>0
   AND EXISTS(SELECT 1 FROM public.sync_physical_stock_source s
    WHERE s.source_transaction_id=txid_current()
     AND (s.tenant_id,s.branch_id) IS DISTINCT FROM (stream.tenant_id,stream.branch_id))) THEN
  RAISE EXCEPTION 'physical_stock_mixed_branch_transaction' USING ERRCODE='23514'; END IF;
 PERFORM 1 FROM public.sync_stream stream WHERE EXISTS(
  SELECT 1 FROM public.sync_physical_stock_source s
   WHERE s.source_transaction_id=txid_current()
    AND s.tenant_id=stream.tenant_id AND s.branch_id=stream.branch_id)
  ORDER BY stream.tenant_id,stream.branch_id FOR UPDATE OF stream;
 FOR pending IN SELECT s.tenant_id,s.id FROM public.sync_physical_stock_source s
  WHERE s.source_transaction_id=txid_current() AND NOT EXISTS(
   SELECT 1 FROM public.sync_physical_stock_event e WHERE e.tenant_id=s.tenant_id AND e.id=s.id)
  ORDER BY s.branch_id,s.capture_order LOOP
  PERFORM public.emit_physical_stock_source(pending.tenant_id,pending.id);
 END LOOP;
 RETURN NEW;
END $$;
CREATE FUNCTION public.trg_physical_stock_private_guard() RETURNS TRIGGER
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 IF CURRENT_USER<>'aurum_schema_owner' THEN
  RAISE EXCEPTION 'physical_stock_private_immutable' USING ERRCODE='55000'; END IF;
 IF TG_TABLE_NAME='sync_physical_stock_producer_gate' THEN
  IF TG_OP='TRUNCATE' THEN
   RAISE EXCEPTION 'physical_stock_private_immutable' USING ERRCODE='55000'; END IF;
  IF TG_OP='DELETE' THEN RETURN OLD; END IF;
  IF NOT EXISTS(SELECT 1 FROM public.branch WHERE tenant_id=NEW.tenant_id AND id=NEW.id) THEN
   RAISE EXCEPTION 'physical_stock_branch_invalid' USING ERRCODE='23514'; END IF;
  IF NEW.enabled AND EXISTS(SELECT 1 FROM public.sync_node
   WHERE tenant_id=NEW.tenant_id AND branch_id=NEW.id AND node_kind='edge' AND status='active') THEN
   RAISE EXCEPTION 'physical_stock_delivery_not_ready' USING ERRCODE='23514'; END IF;
 ELSIF TG_OP<>'INSERT' THEN
  RAISE EXCEPTION 'physical_stock_private_immutable' USING ERRCODE='55000'; END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION public.trg_physical_stock_audit() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE raw JSONB; technical JSONB; previous JSONB;
BEGIN
 raw:=CASE WHEN TG_OP='DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
 SELECT jsonb_object_agg(key,value) INTO technical FROM jsonb_each(raw)
 WHERE key IN ('id','event_id','source_event_id','tenant_id','branch_id','origin_node_id',
  'writer_epoch','sequence','source_kind','source_id','event_fingerprint',
  'enabled','target_edge_node_id','operation_id','source_transaction_id','capture_order',
  'previous_source_checksum','previous_projection_checksum','source_payload_hash',
  'projection_hash','snapshot_id','snapshot_hash','physical_records_hash','configuration_hash');
 IF TG_OP='UPDATE' THEN
  SELECT jsonb_object_agg(key,value) INTO previous FROM jsonb_each(to_jsonb(OLD))
  WHERE key IN ('id','tenant_id','enabled','target_edge_node_id','operation_id',
   'source_transaction_id','capture_order');
 END IF;
 INSERT INTO public.audit_log(tenant_id,user_id,action,table_name,record_id,
  old_values,new_values,changed_fields,created_at)
 VALUES((raw->>'tenant_id')::UUID,public.current_app_user_id(),TG_OP,TG_TABLE_NAME,
  coalesce(raw->>'id',raw->>'event_id',raw->>'source_event_id')::UUID,
  CASE WHEN TG_OP='DELETE' THEN technical ELSE previous END,
  CASE WHEN TG_OP<>'DELETE' THEN technical ELSE NULL END,NULL,statement_timestamp());
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION public.trg_physical_stock_projection_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE anchor public.sync_physical_stock_anchor%ROWTYPE; inbox public.sync_inbox%ROWTYPE;
 envelope JSONB;
BEGIN
 IF TG_OP<>'INSERT' THEN
  RAISE EXCEPTION 'physical_stock_projection_immutable' USING ERRCODE='55000'; END IF;
 SELECT * INTO anchor FROM public.sync_physical_stock_anchor
  WHERE event_id=NEW.source_event_id AND tenant_id=NEW.tenant_id AND branch_id=NEW.branch_id
   AND target_edge_node_id=NULLIF(current_setting('app.edge_node_id',true),'')::UUID;
 SELECT * INTO inbox FROM public.sync_inbox WHERE event_id=NEW.source_event_id;
 envelope:=(to_jsonb(inbox)-ARRAY['status','reason_code','received_at','applied_at',
  'created_at','created_by','updated_at','updated_by'])||jsonb_build_object(
   'occurred_at',public.physical_stock_time(inbox.occurred_at));
 IF anchor.event_id IS NULL OR inbox.event_id IS NULL
  OR (anchor.origin_node_id,anchor.writer_epoch,anchor.sequence) IS DISTINCT FROM
    (NEW.origin_node_id,NEW.writer_epoch,NEW.sequence)
  OR (inbox.tenant_id,inbox.branch_id,inbox.origin_node_id,inbox.writer_epoch,inbox.sequence,
      inbox.operation_id,inbox.aggregate_id,inbox.payload,inbox.payload_hash,inbox.projection_hash)
    IS DISTINCT FROM (NEW.tenant_id,NEW.branch_id,NEW.origin_node_id,NEW.writer_epoch,NEW.sequence,
      NEW.operation_id,NEW.source_id,NEW.payload,NEW.source_payload_hash,NEW.projection_hash)
  OR inbox.event_type<>'inventory.physical.changed.v5' OR inbox.schema_version<>5
  OR inbox.aggregate_type<>'physical_stock' OR inbox.status NOT IN ('received','applied')
  OR anchor.event_fingerprint IS DISTINCT FROM public.retail_event_hash(jsonb_build_object(
   'domain','aurum-physical-stock-event','schema_version',5,'envelope',envelope))
  OR inbox.stream_checksum IS DISTINCT FROM public.retail_event_hash(jsonb_build_object(
   'previous_checksum',anchor.previous_source_checksum,'event_id',inbox.event_id,
   'tenant_id',inbox.tenant_id,'branch_id',inbox.branch_id,'origin_node_id',inbox.origin_node_id,
   'writer_epoch',inbox.writer_epoch,'sequence',inbox.sequence,'operation_id',inbox.operation_id,
   'aggregate_type','physical_stock','aggregate_id',inbox.aggregate_id,
   'event_type','inventory.physical.changed.v5','schema_version',5,'occurred_at',
   to_char(inbox.occurred_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
   'payload_hash',inbox.payload_hash))
  OR inbox.projection_checksum IS DISTINCT FROM public.retail_event_hash(jsonb_build_object(
   'previous_checksum',anchor.previous_projection_checksum,'origin_node_id',inbox.origin_node_id,
   'writer_epoch',inbox.writer_epoch,'sequence',inbox.sequence,'sale_id',inbox.aggregate_id,
   'projection_hash',inbox.projection_hash))
  OR NEW.projection_hash IS DISTINCT FROM public.retail_event_hash(NEW.payload)
  OR EXISTS(SELECT 1 FROM public.sync_sale_projection
    WHERE origin_node_id=NEW.origin_node_id AND writer_epoch=NEW.writer_epoch AND
 sequence=NEW.sequence)
 THEN RAISE EXCEPTION 'physical_stock_projection_invalid' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION public.trg_physical_stock_sale_slot() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM public.sync_physical_stock_anchor
  WHERE tenant_id=NEW.tenant_id AND branch_id=NEW.branch_id
   AND target_edge_node_id=NULLIF(current_setting('app.edge_node_id',true),'')::UUID
   AND origin_node_id=NEW.origin_node_id AND writer_epoch=NEW.writer_epoch
   AND sequence=NEW.sequence) OR EXISTS(SELECT 1 FROM public.sync_physical_stock_projection
    WHERE origin_node_id=NEW.origin_node_id AND writer_epoch=NEW.writer_epoch
     AND sequence=NEW.sequence) THEN
  RAISE EXCEPTION 'physical_stock_slot_reserved' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
"""


def upgrade() -> None:
    _sql("""
      ALTER TABLE public.sync_stream ADD COLUMN last_reservation_transaction_id BIGINT;
      ALTER TABLE public.tenant_catalog ADD COLUMN physical_config_revision BIGINT NOT NULL
 DEFAULT 1
       CHECK(physical_config_revision>0);
      CREATE TABLE public.sync_physical_stock_producer_gate(
       tenant_id UUID NOT NULL,id UUID NOT NULL,enabled BOOLEAN NOT NULL DEFAULT false,
       created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),created_by UUID,
       PRIMARY KEY(tenant_id,id));
      CREATE TABLE public.sync_physical_stock_source(
       tenant_id UUID NOT NULL,id UUID NOT NULL,branch_id UUID NOT NULL,source_kind TEXT NOT NULL
        CHECK(source_kind IN ('initialize','dispense','writeoff','writeoff_cancelled','block',
          'catalog_config','baseline_required')),
       source_id UUID NOT NULL,source_transaction_id BIGINT NOT NULL,
       capture_order BIGINT GENERATED ALWAYS AS IDENTITY UNIQUE,
       origin_node_id UUID NOT NULL,writer_epoch BIGINT NOT NULL CHECK(writer_epoch>0),
       occurred_at TIMESTAMPTZ NOT NULL,captured_payload JSONB,
       created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),created_by UUID,
       PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,branch_id,source_kind,source_id));
      CREATE TABLE public.sync_physical_stock_event(
       tenant_id UUID NOT NULL,id UUID NOT NULL,branch_id UUID NOT NULL,
       origin_node_id UUID NOT NULL,writer_epoch BIGINT NOT NULL CHECK(writer_epoch>0),
       sequence BIGINT NOT NULL CHECK(sequence>0),
       previous_source_checksum TEXT NOT NULL CHECK(previous_source_checksum ~ '^[0-9a-f]{64}$'),
       previous_projection_checksum TEXT NOT NULL CHECK(previous_projection_checksum ~
 '^[0-9a-f]{64}$'),
       event_fingerprint TEXT NOT NULL CHECK(event_fingerprint ~ '^[0-9a-f]{64}$'),envelope
 JSONB NOT NULL,
       created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),created_by UUID,
       PRIMARY KEY(tenant_id,id),UNIQUE(origin_node_id,writer_epoch,sequence));
      CREATE TABLE public.sync_physical_stock_anchor(
       event_id UUID PRIMARY KEY,tenant_id UUID NOT NULL,branch_id UUID NOT NULL,
       target_edge_node_id UUID NOT NULL,origin_node_id UUID NOT NULL,
       writer_epoch BIGINT NOT NULL CHECK(writer_epoch>0),sequence BIGINT NOT NULL
 CHECK(sequence>0),
       previous_source_checksum TEXT NOT NULL CHECK(previous_source_checksum ~ '^[0-9a-f]{64}$'),
       previous_projection_checksum TEXT NOT NULL CHECK(previous_projection_checksum ~
 '^[0-9a-f]{64}$'),
       event_fingerprint TEXT NOT NULL CHECK(event_fingerprint ~ '^[0-9a-f]{64}$'),
       created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),created_by UUID,
       UNIQUE(tenant_id,branch_id,target_edge_node_id,origin_node_id,writer_epoch,sequence));
      CREATE TABLE public.sync_physical_stock_projection(
       source_event_id UUID PRIMARY KEY,tenant_id UUID NOT NULL,branch_id UUID NOT NULL,
       origin_node_id UUID NOT NULL,writer_epoch BIGINT NOT NULL CHECK(writer_epoch>0),
       sequence BIGINT NOT NULL CHECK(sequence>0),operation_id UUID NOT NULL,source_id UUID
 NOT NULL,
       payload JSONB NOT NULL,source_payload_hash TEXT NOT NULL,projection_hash TEXT NOT NULL,
       created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),created_by UUID,
       UNIQUE(tenant_id,operation_id),UNIQUE(origin_node_id,writer_epoch,sequence));
      CREATE TABLE public.sync_physical_stock_baseline(
       tenant_id UUID NOT NULL,branch_id UUID NOT NULL,target_edge_node_id UUID NOT NULL,
       snapshot_id UUID NOT NULL,snapshot_hash TEXT NOT NULL CHECK(snapshot_hash ~
 '^[0-9a-f]{64}$'),
       physical_records_hash TEXT NOT NULL CHECK(physical_records_hash ~ '^[0-9a-f]{64}$'),
       configuration_hash TEXT NOT NULL CHECK(configuration_hash ~ '^[0-9a-f]{64}$'),
       origin_node_id UUID NOT NULL,writer_epoch BIGINT NOT NULL CHECK(writer_epoch>0),
       sequence BIGINT NOT NULL CHECK(sequence>=0),
       source_checksum TEXT NOT NULL CHECK(source_checksum ~ '^[0-9a-f]{64}$'),
       projection_checksum TEXT NOT NULL CHECK(projection_checksum ~ '^[0-9a-f]{64}$'),
       created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),created_by UUID,
       PRIMARY KEY(tenant_id,branch_id,target_edge_node_id,snapshot_id));
    """)
    for table in TABLES:
        _sql(f"""
          ALTER TABLE public.{table} OWNER TO aurum_schema_owner;
          ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY;
          ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY;
          REVOKE ALL ON public.{table} FROM PUBLIC,aurum_app,aurum_support;
          CREATE POLICY schema_owner ON public.{table} TO aurum_schema_owner USING(true) WITH
 CHECK(true);
          CREATE TRIGGER a00_tenant_access BEFORE INSERT OR UPDATE OR DELETE ON public.{table}
           FOR EACH ROW EXECUTE FUNCTION public.trg_enforce_tenant_access('inventory');
        """)
    _sql(SQL)
    for signature in FUNCTIONS:
        _sql(f"""
          ALTER FUNCTION public.{signature} OWNER TO aurum_schema_owner;
          REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC,aurum_app,aurum_support,
 aurum_catalog_worker;
        """)
    _sql("""
      GRANT EXECUTE ON FUNCTION public.read_physical_writeoff_cost_source(uuid,uuid,integer)
       TO aurum_app;
    """)
    for table in TABLES:
        function = (
            "trg_physical_stock_projection_guard"
            if table.endswith("_projection")
            else "trg_physical_stock_private_guard"
        )
        _sql(f"""
          CREATE TRIGGER a10_physical_stock_guard BEFORE INSERT OR UPDATE OR DELETE ON
 public.{table}
           FOR EACH ROW EXECUTE FUNCTION public.{function}();
          CREATE TRIGGER a10_physical_stock_no_truncate BEFORE TRUNCATE ON public.{table}
           FOR EACH STATEMENT EXECUTE FUNCTION public.{function}();
          CREATE TRIGGER z90_audit AFTER INSERT OR UPDATE OR DELETE ON public.{table}
           FOR EACH ROW EXECUTE FUNCTION public.trg_physical_stock_audit();
        """)
    for table in ("sync_physical_stock_anchor", "sync_physical_stock_baseline"):
        _sql(f"""
          GRANT SELECT ON public.{table} TO aurum_app,aurum_support;
          CREATE POLICY scoped_reader ON public.{table} FOR SELECT TO aurum_app,aurum_support USING(
           tenant_id=public.current_tenant_id()
           AND branch_id=NULLIF(current_setting('app.branch_id',true),'')::UUID
           AND target_edge_node_id=NULLIF(current_setting('app.edge_node_id',true),'')::UUID
           AND (session_user='aurum_support' OR
             (session_user='aurum_app' AND NOT public.is_tenant_support_session())));
        """)
    for table in ("sync_physical_stock_event", "sync_physical_stock_projection"):
        reader = (
            "public.tenant_actor_has_scoped_permission(tenant_id,'batches.view',branch_id)"
            " AND public.tenant_actor_has_scoped_permission("
            "tenant_id,'batches.view_costs',branch_id)"
            " AND EXISTS(SELECT 1 FROM public.app_user"
            " WHERE id=public.current_app_user_id() AND status='active')"
            if table == "sync_physical_stock_event"
            else "NULLIF(current_setting('app.edge_node_id',true),'') IS NOT NULL"
        )
        _sql(f"""
          GRANT SELECT ON public.{table} TO aurum_app,aurum_support;
          CREATE POLICY scoped_reader ON public.{table} FOR SELECT TO aurum_app,aurum_support USING(
           tenant_id=public.current_tenant_id()
           AND branch_id=NULLIF(current_setting('app.branch_id',true),'')::UUID
           AND (session_user='aurum_support' OR (session_user='aurum_app'
             AND NOT public.is_tenant_support_session()
             AND {reader})));
        """)
    _sql("""
      CREATE TRIGGER a08_sync_stream_reservation_transaction BEFORE INSERT OR UPDATE
       ON public.sync_stream FOR EACH ROW
       EXECUTE FUNCTION public.trg_sync_stream_reservation_transaction();
      GRANT INSERT(source_event_id,tenant_id,branch_id,origin_node_id,writer_epoch,sequence,
       operation_id,source_id,payload,source_payload_hash,projection_hash)
       ON public.sync_physical_stock_projection TO aurum_app;
      CREATE POLICY scoped_writer ON public.sync_physical_stock_projection FOR INSERT TO aurum_app
       WITH CHECK(session_user='aurum_app' AND tenant_id=public.current_tenant_id()
        AND branch_id=NULLIF(current_setting('app.branch_id',true),'')::UUID
        AND NOT public.is_tenant_support_session());
      CREATE CONSTRAINT TRIGGER trg_physical_stock_source_complete AFTER INSERT
       ON public.sync_physical_stock_source DEFERRABLE INITIALLY DEFERRED
       FOR EACH ROW EXECUTE FUNCTION public.trg_physical_stock_complete();
      CREATE TRIGGER a09_physical_stock_sale_slot BEFORE INSERT ON public.sync_sale_projection
       FOR EACH ROW EXECUTE FUNCTION public.trg_physical_stock_sale_slot();
      CREATE TRIGGER a09_physical_catalog_revision BEFORE INSERT OR UPDATE ON public.tenant_catalog
       FOR EACH ROW EXECUTE FUNCTION public.trg_physical_catalog_revision();
      CREATE TRIGGER z80_physical_stock_capture AFTER INSERT OR UPDATE ON public.tenant_catalog
       FOR EACH ROW EXECUTE FUNCTION public.trg_physical_stock_capture();
      CREATE TRIGGER z80_physical_stock_capture AFTER UPDATE ON public.unit_stock_lot_v2
       FOR EACH ROW EXECUTE FUNCTION public.trg_physical_stock_capture();
    """)
    for table in (
        "unit_stock_operation_v2",
        "unit_stock_cart_command_v2",
        "physical_writeoff_command",
        "physical_writeoff_cancellation",
    ):
        _sql(f"""
          CREATE TRIGGER z80_physical_stock_capture AFTER INSERT ON public.{table}
           FOR EACH ROW EXECUTE FUNCTION public.trg_physical_stock_capture();
        """)
    for table in (
        "unit_stock_operation",
        "unit_stock_entry",
        "unit_stock_lot",
        "unit_stock_opened_pack",
    ):
        _sql(f"""
          CREATE TRIGGER z80_physical_stock_capture AFTER INSERT OR UPDATE ON public.{table}
           FOR EACH ROW EXECUTE FUNCTION public.trg_physical_stock_capture();
        """)


def downgrade() -> None:
    for table in TABLES:
        _sql(f"LOCK TABLE public.{table} IN ACCESS EXCLUSIVE MODE;")
    _sql("""DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM public.sync_physical_stock_event)
       OR EXISTS(SELECT 1 FROM public.sync_physical_stock_source)
       OR EXISTS(SELECT 1 FROM public.sync_physical_stock_anchor)
       OR EXISTS(SELECT 1 FROM public.sync_physical_stock_projection)
       OR EXISTS(SELECT 1 FROM public.sync_physical_stock_baseline) THEN
       RAISE EXCEPTION 'physical_stock_downgrade_requires_empty_history' USING ERRCODE='55000';
      END IF;
    END $$;""")
    for table in (
        "tenant_catalog",
        "unit_stock_lot_v2",
        "unit_stock_operation_v2",
        "unit_stock_cart_command_v2",
        "physical_writeoff_command",
        "physical_writeoff_cancellation",
        "unit_stock_operation",
        "unit_stock_entry",
        "unit_stock_lot",
        "unit_stock_opened_pack",
    ):
        _sql(f"DROP TRIGGER z80_physical_stock_capture ON public.{table};")
    _sql("DROP TRIGGER a09_physical_catalog_revision ON public.tenant_catalog;")
    _sql("DROP TRIGGER a09_physical_stock_sale_slot ON public.sync_sale_projection;")
    _sql("DROP TRIGGER a08_sync_stream_reservation_transaction ON public.sync_stream;")
    for table in reversed(TABLES):
        _sql(f"DROP TABLE public.{table};")
    for signature in reversed(FUNCTIONS):
        _sql(f"DROP FUNCTION public.{signature};")
    _sql("ALTER TABLE public.tenant_catalog DROP COLUMN physical_config_revision;")
    _sql("ALTER TABLE public.sync_stream DROP COLUMN last_reservation_transaction_id;")
