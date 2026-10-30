"""Capture committed retail refunds and receive trusted read-only refund history.

Revision ID: 0230
Revises: 0229
"""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

revision = "0230"
down_revision = "0229"
branch_labels = None
depends_on = None

TABLES = (
    "sync_retail_refund_producer_gate",
    "sync_retail_refund_source",
    "sync_retail_refund_event",
    "sync_retail_refund_anchor",
    "sync_retail_refund_projection",
)
MARKERS = (
    ("pos_physical_cash_refund", "physical_cash"),
    ("pos_mixed_cash_refund", "mixed_cash"),
    ("pos_unit_electronic_refund_posting", "physical_electronic"),
    ("pos_mixed_electronic_refund_posting", "mixed_electronic"),
)
FUNCTIONS = (
    "trg_retail_refund_private_guard()",
    "trg_retail_refund_audit()",
    "trg_retail_refund_capture()",
    "retail_refund_source_payload(uuid,uuid)",
    "emit_retail_refund_source(uuid,uuid)",
    "trg_retail_refund_complete()",
    "trg_retail_refund_projection_guard()",
    "trg_retail_refund_other_slot()",
)


def _sql(sql: str) -> None:
    spec = spec_from_file_location(
        "aurum_0230_sql", Path(__file__).with_name("0171_unit_cash_intents.py")
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load historical SQL executor")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    module._sql(sql)


SQL = """
CREATE FUNCTION public.trg_retail_refund_private_guard() RETURNS TRIGGER
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 IF CURRENT_USER<>'aurum_schema_owner' OR TG_OP='TRUNCATE' THEN
  RAISE EXCEPTION 'retail_refund_private_immutable' USING ERRCODE='55000'; END IF;
 IF TG_TABLE_NAME='sync_retail_refund_producer_gate' THEN
  IF TG_OP='DELETE' THEN RETURN OLD; END IF;
  IF NOT EXISTS(SELECT 1 FROM public.branch WHERE tenant_id=NEW.tenant_id AND id=NEW.id) THEN
   RAISE EXCEPTION 'retail_refund_branch_invalid' USING ERRCODE='23514'; END IF;
  IF NEW.enabled AND EXISTS(SELECT 1 FROM public.sync_node
   WHERE tenant_id=NEW.tenant_id AND branch_id=NEW.id AND node_kind='edge' AND status='active')
  THEN RAISE EXCEPTION 'retail_refund_delivery_not_ready' USING ERRCODE='23514'; END IF;
 ELSIF TG_OP<>'INSERT' THEN
  RAISE EXCEPTION 'retail_refund_private_immutable' USING ERRCODE='55000';
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION public.trg_retail_refund_audit() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE raw JSONB; technical JSONB; previous JSONB;
BEGIN
 raw:=CASE WHEN TG_OP='DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
 SELECT jsonb_object_agg(key,value) INTO technical FROM jsonb_each(raw)
 WHERE key IN ('id','event_id','source_event_id','tenant_id','branch_id','parent_sale_id',
  'sale_id','source_kind','source_id','origin_node_id','writer_epoch','sequence','operation_id',
  'target_edge_node_id','source_transaction_id','capture_order','event_fingerprint','enabled',
  'previous_source_checksum','previous_projection_checksum','source_payload_hash','projection_hash');
 IF TG_OP='UPDATE' THEN
  SELECT jsonb_object_agg(key,value) INTO previous FROM jsonb_each(to_jsonb(OLD))
   WHERE key IN ('tenant_id','id','enabled');
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
CREATE FUNCTION public.trg_retail_refund_capture() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE enabled BOOLEAN; writer RECORD;
BEGIN
 -- A trusted migration never acquires retrospective runtime evidence.
 IF SESSION_USER='aurum_migrator' THEN RETURN NEW; END IF;
 SELECT g.enabled INTO enabled FROM public.sync_retail_refund_producer_gate g
  WHERE g.tenant_id=NEW.tenant_id AND g.id=NEW.branch_id FOR SHARE;
 IF NOT coalesce(enabled,false) THEN RETURN NEW; END IF;
 IF SESSION_USER<>'aurum_app' OR public.is_support_session()
  OR public.is_tenant_support_session() OR NEW.tenant_id IS DISTINCT FROM public.current_tenant_id()
  OR NEW.actor_id IS DISTINCT FROM public.current_app_user_id()
  OR NOT EXISTS(SELECT 1 FROM public.app_user WHERE id=NEW.actor_id AND status='active')
  OR NOT coalesce(public.tenant_actor_has_scoped_permission(
   NEW.tenant_id,'pos.refund',NEW.branch_id),false) THEN
  RAISE EXCEPTION 'retail_refund_actor_denied' USING ERRCODE='42501'; END IF;
 IF EXISTS(SELECT 1 FROM public.sync_retail_refund_source s
  WHERE s.source_transaction_id=txid_current()
   AND (s.tenant_id,s.branch_id) IS DISTINCT FROM (NEW.tenant_id,NEW.branch_id)) THEN
  RAISE EXCEPTION 'retail_refund_mixed_branch_transaction' USING ERRCODE='23514'; END IF;
 -- Capture identity without an early stream lock; handover races fail at completion.
 SELECT stream.writer_node_id,stream.writer_epoch INTO writer FROM public.sync_stream stream
 JOIN public.sync_writer_epoch epoch ON
  (epoch.tenant_id,epoch.branch_id,epoch.writer_epoch,epoch.writer_node_id)=
  (stream.tenant_id,stream.branch_id,stream.writer_epoch,stream.writer_node_id)
 JOIN public.sync_node node ON (node.id,node.tenant_id,node.branch_id)=
  (stream.writer_node_id,stream.tenant_id,stream.branch_id)
 WHERE stream.tenant_id=NEW.tenant_id AND stream.branch_id=NEW.branch_id
  AND epoch.state='active' AND epoch.capability='cloud_full' AND epoch.allowed_register_id IS NULL
  AND node.node_kind='cloud' AND node.mode='cloud_writer' AND node.status='active';
 IF NOT FOUND THEN
  RAISE EXCEPTION 'retail_refund_writer_invalid' USING ERRCODE='23514'; END IF;
 INSERT INTO public.sync_retail_refund_source(tenant_id,id,branch_id,parent_sale_id,sale_id,
  source_kind,origin_node_id,writer_epoch,source_transaction_id,created_by)
 VALUES(NEW.tenant_id,NEW.id,NEW.branch_id,NEW.parent_sale_id,NEW.sale_id,TG_ARGV[0],
  writer.writer_node_id,writer.writer_epoch,txid_current(),NEW.actor_id);
 RETURN NEW;
END $$;
CREATE FUNCTION public.retail_refund_source_payload(p_tenant UUID,p_operation UUID) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE source public.sync_retail_refund_source%ROWTYPE; marker JSONB; journal JSONB;
 result JSONB; sale public.sale%ROWTYPE; parent public.pos_retail_checkout_event%ROWTYPE;
 cost_key TEXT; cost_value NUMERIC;
BEGIN
 SELECT * INTO STRICT source FROM public.sync_retail_refund_source
  WHERE tenant_id=p_tenant AND id=p_operation;
 IF source.source_transaction_id IS DISTINCT FROM txid_current()
  OR SESSION_USER<>'aurum_app' OR public.is_support_session()
  OR public.is_tenant_support_session() OR p_tenant IS DISTINCT FROM public.current_tenant_id()
  OR source.created_by IS DISTINCT FROM public.current_app_user_id()
  OR NOT EXISTS(SELECT 1 FROM public.app_user WHERE id=source.created_by AND status='active')
  OR NOT coalesce(public.tenant_actor_has_scoped_permission(
   p_tenant,'pos.refund',source.branch_id),false) THEN
  RAISE EXCEPTION 'retail_refund_current_source_required' USING ERRCODE='42501'; END IF;
 CASE source.source_kind
 WHEN 'physical_cash' THEN
  SELECT to_jsonb(m) INTO marker FROM public.pos_physical_cash_refund m
   WHERE tenant_id=p_tenant AND id=p_operation;
 WHEN 'mixed_cash' THEN
  SELECT to_jsonb(m) INTO marker FROM public.pos_mixed_cash_refund m
   WHERE tenant_id=p_tenant AND id=p_operation;
 WHEN 'physical_electronic' THEN
  SELECT to_jsonb(m) INTO marker FROM public.pos_unit_electronic_refund_posting m
   WHERE tenant_id=p_tenant AND id=p_operation;
  PERFORM public.assert_unit_electronic_refund_posting(p_tenant,source.sale_id);
 WHEN 'mixed_electronic' THEN
  SELECT to_jsonb(m) INTO marker FROM public.pos_mixed_electronic_refund_posting m
   WHERE tenant_id=p_tenant AND id=p_operation;
  PERFORM public.assert_mixed_electronic_refund_posting(p_tenant,source.sale_id);
 ELSE RAISE EXCEPTION 'retail_refund_source_invalid' USING ERRCODE='23514';
 END CASE;
 IF source.source_kind IN ('physical_cash','physical_electronic') THEN
  SELECT result_payload INTO journal FROM public.pos_physical_return_command
   WHERE tenant_id=p_tenant AND id=p_operation;
  cost_key:='total_cost_amount';
 ELSE
  SELECT result_payload INTO journal FROM public.pos_mixed_return_command
   WHERE tenant_id=p_tenant AND id=p_operation;
  cost_key:='physical_cost_amount';
 END IF;
 SELECT * INTO sale FROM public.sale WHERE tenant_id=p_tenant AND id=source.sale_id;
 SELECT * INTO parent FROM public.pos_retail_checkout_event
  WHERE tenant_id=p_tenant AND sale_id=source.parent_sale_id;
 IF marker IS NULL OR journal IS NULL OR sale.id IS NULL OR parent.id IS NULL
  OR (marker->>'branch_id',marker->>'parent_sale_id',marker->>'sale_id',marker->>'actor_id')
   IS DISTINCT FROM (source.branch_id::TEXT,source.parent_sale_id::TEXT,
     source.sale_id::TEXT,source.created_by::TEXT)
  OR (sale.status,sale.sale_type,sale.parent_sale_id,sale.branch_id,sale.currency,sale.is_test,
      sale.cashier_user_id,sale.operation_id) IS DISTINCT FROM
    ('completed','return',source.parent_sale_id,source.branch_id,'TJS',false,
     source.created_by,source.id)
  OR (sale.register_id::TEXT,sale.shift_id::TEXT) IS DISTINCT FROM
    (marker->>'register_id',marker->>'shift_id')
  OR (parent.branch_id,parent.register_id,parent.source_kind) IS DISTINCT FROM
    (source.branch_id,sale.register_id,
      CASE WHEN source.source_kind IN ('physical_cash','physical_electronic')
       THEN 'physical' ELSE 'mixed' END)
  OR (journal->>'tenant_id',journal->>'branch_id',journal->>'operation_id',
      journal->>'parent_sale_id',journal->>'actor_id',journal->>'state') IS DISTINCT FROM
    (p_tenant::TEXT,source.branch_id::TEXT,p_operation::TEXT,source.parent_sale_id::TEXT,
     source.created_by::TEXT,'prepared_quarantine')
  OR sale.total_amount IS DISTINCT FROM (journal->>'total_amount')::NUMERIC
  OR sale.completed_at IS NULL OR NOT isfinite(sale.completed_at)
  OR sale.completed_at IS DISTINCT FROM (sale.receipt_snapshot->>'completed_at')::TIMESTAMPTZ
  OR parent.created_at>sale.completed_at THEN
  RAISE EXCEPTION 'retail_refund_source_incomplete' USING ERRCODE='23514'; END IF;
 IF source.source_kind IN ('physical_cash','mixed_cash') THEN
  result:=marker->'result_payload';
  IF marker->>'payment_journal_version' IS DISTINCT FROM '2'
   OR marker->'receipt_payload' IS DISTINCT FROM sale.receipt_snapshot
   OR result->'receipt' IS DISTINCT FROM sale.receipt_snapshot
   OR (result->>'operation_id',result->>'parent_sale_id',result->>'sale_id',
       result->>'tenant_id',result->>'branch_id',result->>'register_id',result->>'shift_id',
       result->>'actor_id',result->>'payment_id') IS DISTINCT FROM
      (p_operation::TEXT,source.parent_sale_id::TEXT,source.sale_id::TEXT,p_tenant::TEXT,
       source.branch_id::TEXT,sale.register_id::TEXT,sale.shift_id::TEXT,
       source.created_by::TEXT,p_operation::TEXT)
   OR (result->>'total_amount')::NUMERIC IS DISTINCT FROM sale.total_amount
   OR result->cost_key IS DISTINCT FROM journal->cost_key THEN
   RAISE EXCEPTION 'retail_refund_cash_source_invalid' USING ERRCODE='23514'; END IF;
 ELSE
  cost_value:=(journal->>cost_key)::NUMERIC;
  result:=jsonb_build_object('state','posted_refund','operation_id',source.id,
   'parent_sale_id',source.parent_sale_id,'sale_id',source.sale_id,'tenant_id',p_tenant,
   'branch_id',source.branch_id,'register_id',sale.register_id,'shift_id',sale.shift_id,
   'actor_id',source.created_by,'total_amount',to_char(sale.total_amount,'FM999999999990.00'),
   cost_key,to_char(cost_value,'FM999999999990.00'),'receipt',sale.receipt_snapshot);
 END IF;
 -- Legacy native financial/journal completeness triggers remain mandatory at commit.
 RETURN jsonb_build_object('schema_version',6,'document_kind','retail_refund_event',
  'source_kind',source.source_kind,'result',result,'quarantine',journal);
END $$;
CREATE FUNCTION public.emit_retail_refund_source(p_tenant UUID,p_operation UUID) RETURNS VOID
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE source public.sync_retail_refund_source%ROWTYPE; payload JSONB; position RECORD;
 envelope JSONB; digest TEXT; checksum TEXT; projection TEXT; fingerprint TEXT;
 completed TIMESTAMPTZ; timestamp_text TEXT; old_branch TEXT;
BEGIN
 SELECT * INTO STRICT source FROM public.sync_retail_refund_source
  WHERE tenant_id=p_tenant AND id=p_operation;
 IF EXISTS(SELECT 1 FROM public.sync_retail_refund_event
  WHERE tenant_id=p_tenant AND id=p_operation) THEN RETURN; END IF;
 payload:=public.retail_refund_source_payload(p_tenant,p_operation);
 IF octet_length(public.retail_event_canonical_json(payload))>900000 THEN
  RAISE EXCEPTION 'retail_refund_record_too_large' USING ERRCODE='23514'; END IF;
 completed:=(payload->'result'->'receipt'->>'completed_at')::TIMESTAMPTZ;
 old_branch:=current_setting('app.branch_id',true);
 PERFORM set_config('app.branch_id',source.branch_id::TEXT,true);
 PERFORM public.assert_current_branch_writer(p_tenant,source.branch_id,NULL,false);
 SELECT stream.id AS stream_id,stream.writer_node_id AS origin_node_id,
  stream.writer_epoch,stream.last_sequence+1 AS sequence,
  stream.current_checksum AS previous_checksum,
  stream.current_projection_checksum AS previous_projection_checksum
 INTO STRICT position FROM public.sync_stream stream
 WHERE stream.tenant_id=p_tenant AND stream.branch_id=source.branch_id FOR UPDATE;
 IF (position.origin_node_id,position.writer_epoch) IS DISTINCT FROM
   (source.origin_node_id,source.writer_epoch) THEN
  RAISE EXCEPTION 'retail_refund_writer_changed' USING ERRCODE='23514'; END IF;
 digest:=public.retail_event_hash(payload);
 timestamp_text:=to_char(completed AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"');
 checksum:=public.retail_event_hash(jsonb_build_object('previous_checksum',position.previous_checksum,
  'event_id',source.id,'tenant_id',p_tenant,'branch_id',source.branch_id,
  'origin_node_id',source.origin_node_id,'writer_epoch',source.writer_epoch,
  'sequence',position.sequence,'operation_id',source.id,'aggregate_type','sale',
  'aggregate_id',source.sale_id,'event_type','pos.sale.refunded.v6','schema_version',6,
  'occurred_at',timestamp_text,'payload_hash',digest));
 projection:=public.retail_event_hash(jsonb_build_object(
  'previous_checksum',position.previous_projection_checksum,'origin_node_id',source.origin_node_id,
  'writer_epoch',source.writer_epoch,'sequence',position.sequence,'sale_id',source.sale_id,
  'projection_hash',digest));
 envelope:=jsonb_build_object('event_id',source.id,'tenant_id',p_tenant,'branch_id',source.branch_id,
  'origin_node_id',source.origin_node_id,'writer_epoch',source.writer_epoch,
  'sequence',position.sequence,'operation_id',source.id,'aggregate_type','sale',
  'aggregate_id',source.sale_id,'event_type','pos.sale.refunded.v6','schema_version',6,
  'occurred_at',public.physical_stock_time(completed),'payload',payload,'payload_hash',digest,
  'stream_checksum',checksum,'projection_hash',digest,'projection_checksum',projection);
 IF octet_length(public.retail_event_canonical_json(envelope))>1048576 THEN
  RAISE EXCEPTION 'retail_refund_record_too_large' USING ERRCODE='23514'; END IF;
 fingerprint:=public.retail_event_hash(jsonb_build_object('domain','aurum-retail-refund-event',
  'schema_version',6,'envelope',envelope));
 INSERT INTO public.sync_outbox(event_id,tenant_id,branch_id,origin_node_id,writer_epoch,sequence,
  operation_id,aggregate_type,aggregate_id,event_type,schema_version,occurred_at,
  payload,payload_hash,stream_checksum,projection_hash,projection_checksum)
 VALUES(source.id,p_tenant,source.branch_id,source.origin_node_id,source.writer_epoch,
  position.sequence,source.id,'sale',source.sale_id,'pos.sale.refunded.v6',6,completed,
  payload,digest,checksum,digest,projection);
 INSERT INTO public.sync_retail_refund_event(tenant_id,id,branch_id,origin_node_id,writer_epoch,
  sequence,previous_source_checksum,previous_projection_checksum,event_fingerprint,envelope,
  created_by)
 VALUES(p_tenant,source.id,source.branch_id,source.origin_node_id,source.writer_epoch,
  position.sequence,position.previous_checksum,position.previous_projection_checksum,
  fingerprint,envelope,source.created_by);
 UPDATE public.sync_stream stream SET last_sequence=position.sequence,
  current_checksum=checksum,current_projection_checksum=projection
 WHERE stream.id=position.stream_id AND stream.tenant_id=p_tenant
  AND stream.branch_id=source.branch_id AND stream.writer_node_id=position.origin_node_id
  AND stream.writer_epoch=position.writer_epoch
  AND (stream.last_sequence,stream.current_checksum,stream.current_projection_checksum)=
      (position.sequence-1,position.previous_checksum,position.previous_projection_checksum);
 IF NOT FOUND THEN
  RAISE EXCEPTION 'retail_refund_checkpoint_conflict' USING ERRCODE='40001'; END IF;
 PERFORM set_config('app.branch_id',coalesce(old_branch,''),true);
END $$;
CREATE FUNCTION public.trg_retail_refund_complete() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE pending RECORD;
BEGIN
 IF NOT EXISTS(SELECT 1 FROM public.sync_retail_refund_source s
  WHERE s.source_transaction_id=txid_current() AND NOT EXISTS(
   SELECT 1 FROM public.sync_retail_refund_event e
   WHERE e.tenant_id=s.tenant_id AND e.id=s.id)) THEN RETURN NEW; END IF;
 IF EXISTS(SELECT 1 FROM public.sync_stream stream
  WHERE stream.last_reservation_transaction_id=txid_current() AND EXISTS(
   SELECT 1 FROM public.sync_retail_refund_source s
   WHERE s.source_transaction_id=txid_current()
    AND (s.tenant_id,s.branch_id) IS DISTINCT FROM (stream.tenant_id,stream.branch_id)))
 OR EXISTS(SELECT 1 FROM public.sync_physical_stock_source stock
  JOIN public.sync_retail_refund_source refund ON
   stock.source_transaction_id=refund.source_transaction_id
  WHERE stock.source_transaction_id=txid_current()
   AND (stock.tenant_id,stock.branch_id) IS DISTINCT FROM (refund.tenant_id,refund.branch_id)) THEN
  RAISE EXCEPTION 'retail_refund_mixed_branch_transaction' USING ERRCODE='23514'; END IF;
 PERFORM 1 FROM public.sync_stream stream WHERE EXISTS(
  SELECT 1 FROM public.sync_retail_refund_source s WHERE s.source_transaction_id=txid_current()
   AND s.tenant_id=stream.tenant_id AND s.branch_id=stream.branch_id)
 ORDER BY stream.tenant_id,stream.branch_id FOR UPDATE OF stream;
 FOR pending IN SELECT s.tenant_id,s.id FROM public.sync_retail_refund_source s
  WHERE s.source_transaction_id=txid_current() AND NOT EXISTS(
   SELECT 1 FROM public.sync_retail_refund_event e WHERE e.tenant_id=s.tenant_id AND e.id=s.id)
  ORDER BY s.tenant_id,s.branch_id,s.capture_order LOOP
  PERFORM public.emit_retail_refund_source(pending.tenant_id,pending.id);
 END LOOP;
 RETURN NEW;
END $$;
CREATE FUNCTION public.trg_retail_refund_projection_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE anchor public.sync_retail_refund_anchor%ROWTYPE; inbox public.sync_inbox%ROWTYPE;
 envelope JSONB;
BEGIN
 IF TG_OP<>'INSERT' THEN
  RAISE EXCEPTION 'retail_refund_projection_immutable' USING ERRCODE='55000'; END IF;
 SELECT * INTO anchor FROM public.sync_retail_refund_anchor
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
  OR inbox.event_type<>'pos.sale.refunded.v6' OR inbox.schema_version<>6
  OR inbox.aggregate_type<>'sale' OR inbox.status NOT IN ('received','applied')
  OR inbox.event_id IS DISTINCT FROM inbox.operation_id
  OR NEW.parent_sale_id IS DISTINCT FROM (NEW.payload->'result'->>'parent_sale_id')::UUID
  OR (NEW.payload->'result'->>'sale_id') IS DISTINCT FROM NEW.source_id::TEXT
  OR (NEW.payload->'result'->>'operation_id') IS DISTINCT FROM NEW.operation_id::TEXT
  OR NEW.payload->>'schema_version' IS DISTINCT FROM '6'
  OR NEW.payload->>'document_kind' IS DISTINCT FROM 'retail_refund_event'
  OR anchor.event_fingerprint IS DISTINCT FROM public.retail_event_hash(jsonb_build_object(
   'domain','aurum-retail-refund-event','schema_version',6,'envelope',envelope))
  OR inbox.stream_checksum IS DISTINCT FROM public.retail_event_hash(jsonb_build_object(
   'previous_checksum',anchor.previous_source_checksum,'event_id',inbox.event_id,
   'tenant_id',inbox.tenant_id,'branch_id',inbox.branch_id,'origin_node_id',inbox.origin_node_id,
   'writer_epoch',inbox.writer_epoch,'sequence',inbox.sequence,'operation_id',inbox.operation_id,
   'aggregate_type','sale','aggregate_id',inbox.aggregate_id,
   'event_type','pos.sale.refunded.v6','schema_version',6,'occurred_at',
   to_char(inbox.occurred_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
   'payload_hash',inbox.payload_hash))
  OR inbox.projection_checksum IS DISTINCT FROM public.retail_event_hash(jsonb_build_object(
   'previous_checksum',anchor.previous_projection_checksum,'origin_node_id',inbox.origin_node_id,
   'writer_epoch',inbox.writer_epoch,'sequence',inbox.sequence,'sale_id',inbox.aggregate_id,
   'projection_hash',inbox.projection_hash))
  OR NEW.source_payload_hash IS DISTINCT FROM public.retail_event_hash(NEW.payload)
  OR NEW.projection_hash IS DISTINCT FROM NEW.source_payload_hash
  OR NOT EXISTS(SELECT 1 FROM public.sync_sale_projection parent
   WHERE parent.sale_id=NEW.parent_sale_id AND parent.tenant_id=NEW.tenant_id
    AND parent.branch_id=NEW.branch_id AND parent.schema_version IN (3,4)
    AND parent.sale_type='sale' AND parent.completed_at<=inbox.occurred_at)
  OR EXISTS(SELECT 1 FROM public.sync_sale_projection
    WHERE origin_node_id=NEW.origin_node_id AND writer_epoch=NEW.writer_epoch
     AND sequence=NEW.sequence)
  OR EXISTS(SELECT 1 FROM public.sync_physical_stock_projection
    WHERE origin_node_id=NEW.origin_node_id AND writer_epoch=NEW.writer_epoch
     AND sequence=NEW.sequence)
  OR EXISTS(SELECT 1 FROM public.sync_retail_event_anchor a
    WHERE a.tenant_id=NEW.tenant_id AND a.branch_id=NEW.branch_id
     AND a.target_edge_node_id=anchor.target_edge_node_id AND a.origin_node_id=NEW.origin_node_id
     AND a.writer_epoch=NEW.writer_epoch AND a.sequence=NEW.sequence)
  OR EXISTS(SELECT 1 FROM public.sync_physical_stock_anchor a
    WHERE a.tenant_id=NEW.tenant_id AND a.branch_id=NEW.branch_id
     AND a.target_edge_node_id=anchor.target_edge_node_id AND a.origin_node_id=NEW.origin_node_id
     AND a.writer_epoch=NEW.writer_epoch AND a.sequence=NEW.sequence)
 THEN RAISE EXCEPTION 'retail_refund_projection_invalid' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION public.trg_retail_refund_other_slot() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM public.sync_retail_refund_anchor a
  WHERE a.tenant_id=NEW.tenant_id AND a.branch_id=NEW.branch_id
   AND a.target_edge_node_id=NULLIF(current_setting('app.edge_node_id',true),'')::UUID
   AND a.origin_node_id=NEW.origin_node_id AND a.writer_epoch=NEW.writer_epoch
   AND a.sequence=NEW.sequence)
 OR EXISTS(SELECT 1 FROM public.sync_retail_refund_projection p
   WHERE p.origin_node_id=NEW.origin_node_id AND p.writer_epoch=NEW.writer_epoch
    AND p.sequence=NEW.sequence) THEN
  RAISE EXCEPTION 'retail_refund_slot_reserved' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
"""


def upgrade() -> None:
    _sql("""
      CREATE TABLE public.sync_retail_refund_producer_gate(
       tenant_id UUID NOT NULL,id UUID NOT NULL,enabled BOOLEAN NOT NULL DEFAULT false,
       created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),created_by UUID,
       PRIMARY KEY(tenant_id,id));
      CREATE TABLE public.sync_retail_refund_source(
       tenant_id UUID NOT NULL,id UUID NOT NULL,branch_id UUID NOT NULL,
       parent_sale_id UUID NOT NULL,sale_id UUID NOT NULL,source_kind TEXT NOT NULL
        CHECK(source_kind IN
         ('physical_cash','mixed_cash','physical_electronic','mixed_electronic')),
       origin_node_id UUID NOT NULL,writer_epoch BIGINT NOT NULL CHECK(writer_epoch>0),
       source_transaction_id BIGINT NOT NULL,capture_order BIGINT GENERATED ALWAYS AS IDENTITY,
       created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),created_by UUID NOT NULL,
       PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,sale_id));
      CREATE TABLE public.sync_retail_refund_event(
       tenant_id UUID NOT NULL,id UUID NOT NULL,branch_id UUID NOT NULL,
       origin_node_id UUID NOT NULL,writer_epoch BIGINT NOT NULL CHECK(writer_epoch>0),
       sequence BIGINT NOT NULL CHECK(sequence>0),previous_source_checksum TEXT NOT NULL
        CHECK(previous_source_checksum ~ '^[0-9a-f]{64}$'),
       previous_projection_checksum TEXT NOT NULL
        CHECK(previous_projection_checksum ~ '^[0-9a-f]{64}$'),
       event_fingerprint TEXT NOT NULL CHECK(event_fingerprint ~ '^[0-9a-f]{64}$'),
       envelope JSONB NOT NULL CHECK(jsonb_typeof(envelope)='object'),
       created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),created_by UUID NOT NULL,
       PRIMARY KEY(tenant_id,id),UNIQUE(origin_node_id,writer_epoch,sequence));
      CREATE TABLE public.sync_retail_refund_anchor(
       event_id UUID PRIMARY KEY,tenant_id UUID NOT NULL,branch_id UUID NOT NULL,
       target_edge_node_id UUID NOT NULL,origin_node_id UUID NOT NULL,
       writer_epoch BIGINT NOT NULL CHECK(writer_epoch>0),
       sequence BIGINT NOT NULL CHECK(sequence>0),
       previous_source_checksum TEXT NOT NULL CHECK(previous_source_checksum ~ '^[0-9a-f]{64}$'),
       previous_projection_checksum TEXT NOT NULL
        CHECK(previous_projection_checksum ~ '^[0-9a-f]{64}$'),
       event_fingerprint TEXT NOT NULL CHECK(event_fingerprint ~ '^[0-9a-f]{64}$'),
       created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),created_by UUID,
       UNIQUE(tenant_id,branch_id,target_edge_node_id,origin_node_id,writer_epoch,sequence));
      CREATE TABLE public.sync_retail_refund_projection(
       source_event_id UUID PRIMARY KEY,tenant_id UUID NOT NULL,branch_id UUID NOT NULL,
       parent_sale_id UUID NOT NULL,origin_node_id UUID NOT NULL,
       writer_epoch BIGINT NOT NULL CHECK(writer_epoch>0),
       sequence BIGINT NOT NULL CHECK(sequence>0),
       operation_id UUID NOT NULL,source_id UUID NOT NULL,payload JSONB NOT NULL,
       source_payload_hash TEXT NOT NULL CHECK(source_payload_hash ~ '^[0-9a-f]{64}$'),
       projection_hash TEXT NOT NULL CHECK(projection_hash ~ '^[0-9a-f]{64}$'),
       created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),created_by UUID,
       CONSTRAINT uq_sync_retail_refund_projection_operation UNIQUE(tenant_id,operation_id),
       CONSTRAINT uq_sync_retail_refund_projection_source_sale UNIQUE(tenant_id,source_id),
       CONSTRAINT uq_sync_retail_refund_projection_sequence
        UNIQUE(origin_node_id,writer_epoch,sequence));
      CREATE INDEX ix_sync_retail_refund_parent ON public.sync_retail_refund_projection
       (tenant_id,parent_sale_id,writer_epoch,sequence);
    """)
    for table in TABLES:
        _sql(f"""
          ALTER TABLE public.{table} OWNER TO aurum_schema_owner;
          ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY;
          ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY;
          REVOKE ALL ON public.{table} FROM PUBLIC,aurum_app,aurum_support;
          CREATE POLICY schema_owner ON public.{table} TO aurum_schema_owner
           USING(true) WITH CHECK(true);
        """)
    _sql(SQL)
    for signature in FUNCTIONS:
        _sql(f"""
          ALTER FUNCTION public.{signature} OWNER TO aurum_schema_owner;
          REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC,aurum_app,aurum_support;
        """)
    for table in TABLES:
        guard = (
            "trg_retail_refund_projection_guard"
            if table.endswith("_projection")
            else "trg_retail_refund_private_guard"
        )
        _sql(f"""
          CREATE TRIGGER a10_retail_refund_guard BEFORE INSERT OR UPDATE OR DELETE
           ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.{guard}();
          CREATE TRIGGER a10_retail_refund_no_truncate BEFORE TRUNCATE
           ON public.{table} FOR EACH STATEMENT EXECUTE FUNCTION public.{guard}();
          CREATE TRIGGER z90_audit AFTER INSERT OR UPDATE OR DELETE
           ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.trg_retail_refund_audit();
        """)
    for table, kind in MARKERS:
        _sql(f"""
          CREATE TRIGGER a20_retail_refund_capture AFTER INSERT ON public.{table}
           FOR EACH ROW EXECUTE FUNCTION public.trg_retail_refund_capture('{kind}');
        """)
    for table in ("sync_sale_projection", "sync_physical_stock_projection"):
        _sql(f"""
          CREATE TRIGGER a09_retail_refund_slot BEFORE INSERT ON public.{table}
           FOR EACH ROW EXECUTE FUNCTION public.trg_retail_refund_other_slot();
        """)
    _sql("""
      CREATE CONSTRAINT TRIGGER trg_retail_refund_source_complete AFTER INSERT
       ON public.sync_retail_refund_source DEFERRABLE INITIALLY DEFERRED
       FOR EACH ROW EXECUTE FUNCTION public.trg_retail_refund_complete();
      GRANT SELECT ON public.sync_retail_refund_source,public.sync_retail_refund_event TO aurum_app;
      CREATE POLICY scoped_reader ON public.sync_retail_refund_source FOR SELECT TO aurum_app
       USING(session_user='aurum_app' AND NOT public.is_tenant_support_session()
        AND tenant_id=public.current_tenant_id()
        AND EXISTS(SELECT 1 FROM public.app_user
         WHERE id=public.current_app_user_id() AND status='active')
        AND public.tenant_actor_has_scoped_permission(tenant_id,'pos.refund',branch_id));
      CREATE POLICY scoped_reader ON public.sync_retail_refund_event FOR SELECT TO aurum_app
       USING(session_user='aurum_app' AND NOT public.is_tenant_support_session()
        AND tenant_id=public.current_tenant_id()
        AND EXISTS(SELECT 1 FROM public.app_user
         WHERE id=public.current_app_user_id() AND status='active')
        AND public.tenant_actor_has_scoped_permission(tenant_id,'pos.refund',branch_id)
        AND public.tenant_actor_has_scoped_permission(tenant_id,'batches.view',branch_id)
        AND public.tenant_actor_has_scoped_permission(tenant_id,'batches.view_costs',branch_id));
    """)
    for table in ("sync_retail_refund_anchor", "sync_retail_refund_projection"):
        _sql(f"""
          GRANT SELECT ON public.{table} TO aurum_app,aurum_support;
          CREATE POLICY scoped_reader ON public.{table} FOR SELECT TO aurum_app,aurum_support
           USING(tenant_id=public.current_tenant_id()
            AND branch_id=NULLIF(current_setting('app.branch_id',true),'')::UUID
            AND NULLIF(current_setting('app.edge_node_id',true),'') IS NOT NULL
            AND (session_user='aurum_support' OR (session_user='aurum_app'
             AND NOT public.is_tenant_support_session())));
        """)
    _sql("""
      DROP POLICY scoped_reader ON public.sync_retail_refund_anchor;
      CREATE POLICY scoped_reader ON public.sync_retail_refund_anchor
       FOR SELECT TO aurum_app,aurum_support
       USING(tenant_id=public.current_tenant_id()
        AND branch_id=NULLIF(current_setting('app.branch_id',true),'')::UUID
        AND target_edge_node_id=NULLIF(current_setting('app.edge_node_id',true),'')::UUID
        AND (session_user='aurum_support' OR (session_user='aurum_app'
         AND NOT public.is_tenant_support_session())));
      GRANT INSERT(source_event_id,tenant_id,branch_id,parent_sale_id,origin_node_id,writer_epoch,
       sequence,operation_id,source_id,payload,source_payload_hash,projection_hash)
       ON public.sync_retail_refund_projection TO aurum_app;
      CREATE POLICY scoped_writer ON public.sync_retail_refund_projection FOR INSERT TO aurum_app
       WITH CHECK(session_user='aurum_app' AND tenant_id=public.current_tenant_id()
        AND branch_id=NULLIF(current_setting('app.branch_id',true),'')::UUID
        AND NULLIF(current_setting('app.edge_node_id',true),'') IS NOT NULL
        AND NOT public.is_tenant_support_session());
    """)


def downgrade() -> None:
    _sql("""
      LOCK TABLE public.sync_retail_refund_source,public.sync_retail_refund_event,
       public.sync_retail_refund_anchor,public.sync_retail_refund_projection,
       public.sync_retail_refund_producer_gate IN ACCESS EXCLUSIVE MODE;
      DO $$ BEGIN
       IF EXISTS(SELECT 1 FROM public.sync_retail_refund_source)
        OR EXISTS(SELECT 1 FROM public.sync_retail_refund_event)
        OR EXISTS(SELECT 1 FROM public.sync_retail_refund_anchor)
        OR EXISTS(SELECT 1 FROM public.sync_retail_refund_projection)
        OR EXISTS(SELECT 1 FROM public.sync_retail_refund_producer_gate WHERE enabled) THEN
        RAISE EXCEPTION 'retail_refund_downgrade_requires_empty_history' USING ERRCODE='55000';
       END IF;
      END $$;
    """)
    for table, _kind in MARKERS:
        _sql(f"DROP TRIGGER a20_retail_refund_capture ON public.{table};")
    for table in ("sync_sale_projection", "sync_physical_stock_projection"):
        _sql(f"DROP TRIGGER a09_retail_refund_slot ON public.{table};")
    for table in reversed(TABLES):
        _sql(f"DROP TABLE public.{table};")
    for signature in reversed(FUNCTIONS):
        _sql(f"DROP FUNCTION public.{signature};")
