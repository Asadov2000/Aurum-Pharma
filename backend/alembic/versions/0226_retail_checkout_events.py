"""Atomically capture gated retail checkout events without enabling delivery.

Revision ID: 0226
Revises: 0225
"""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from sqlalchemy import text

from alembic import op

revision = "0226"
down_revision = "0225"
branch_labels = None
depends_on = None

FUNCTIONS = (
    "retail_event_canonical_json(jsonb)",
    "retail_event_hash(jsonb)",
    "trg_retail_event_gate_guard()",
    "trg_retail_event_capture()",
    "trg_retail_event_immutable()",
    "assert_retail_event_source(uuid,uuid,uuid)",
    "reserve_retail_checkout_event_position(uuid,uuid,uuid)",
    "store_retail_checkout_event(uuid,uuid,uuid,bytea,text,text,text)",
    "trg_retail_checkout_event_required()",
)
PUBLIC_FUNCTIONS = FUNCTIONS[6:8]
AUDIT_OLD = "IF v_result IS NULL THEN RETURN NULL; END IF;"
AUDIT_NEW = AUDIT_OLD + """
 IF p_table_name IN ('pos_retail_checkout_event','sync_retail_event_producer_gate') THEN
  RETURN jsonb_build_object(
   'created_at',v_result->'created_at','created_by',v_result->'created_by');
 END IF;"""


def _sql(sql: str) -> None:
    path = Path(__file__).with_name("0171_unit_cash_intents.py")
    spec = spec_from_file_location("aurum_0226_sql", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load historical SQL executor")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    module._sql(sql)


def _audit(upgrade: bool) -> None:
    definition = op.get_bind().scalar(
        text(
            "SELECT pg_get_functiondef("
            "'public.audit_redact_table_snapshot(text,jsonb)'::regprocedure)"
        )
    )
    old, new = (AUDIT_OLD, AUDIT_NEW) if upgrade else (AUDIT_NEW, AUDIT_OLD)
    if not isinstance(definition, str) or definition.count(old) != 1:
        raise RuntimeError("Historical audit redactor changed")
    op.get_bind().exec_driver_sql(definition.replace(old, new, 1))


SQL = """
CREATE FUNCTION public.retail_event_canonical_json(p JSONB) RETURNS TEXT
LANGUAGE plpgsql IMMUTABLE STRICT SET search_path=pg_catalog,pg_temp AS $$
DECLARE result TEXT;
BEGIN
 CASE jsonb_typeof(p)
 WHEN 'object' THEN
  SELECT '{'||coalesce(string_agg(to_jsonb(key)::TEXT||':'||
    public.retail_event_canonical_json(value),',' ORDER BY key COLLATE "C"),'')||'}'
    INTO result FROM jsonb_each(p);
 WHEN 'array' THEN
  SELECT '['||coalesce(string_agg(public.retail_event_canonical_json(value),','
    ORDER BY ordinality),'')||']' INTO result FROM jsonb_array_elements(p) WITH ORDINALITY;
 WHEN 'number' THEN
  IF p::TEXT !~ '^-?(0|[1-9][0-9]*)$' THEN
   RAISE EXCEPTION 'retail_event_invalid_record' USING ERRCODE='23514';
  END IF;
  result:=p::TEXT;
 ELSE result:=p::TEXT;
 END CASE;
 RETURN result;
END $$;
CREATE FUNCTION public.retail_event_hash(p JSONB) RETURNS TEXT
LANGUAGE sql IMMUTABLE STRICT SET search_path=pg_catalog,pg_temp AS $$
 SELECT encode(sha256(convert_to(public.retail_event_canonical_json(p),'UTF8')),'hex')
$$;
CREATE FUNCTION public.trg_retail_event_gate_guard() RETURNS TRIGGER
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 IF CURRENT_USER<>'aurum_schema_owner' THEN
  RAISE EXCEPTION 'retail_event_gate_owner_required' USING ERRCODE='42501';
 END IF;
 IF TG_OP<>'DELETE' AND NEW.enabled AND EXISTS(SELECT 1 FROM public.sync_node n
   WHERE n.tenant_id=NEW.tenant_id AND n.branch_id=NEW.id
     AND n.node_kind='edge' AND n.status='active') THEN
  RAISE EXCEPTION 'retail_event_delivery_not_ready' USING ERRCODE='23514';
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION public.trg_retail_event_capture() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE enabled BOOLEAN;
BEGIN
 NEW.source_transaction_id:=NULL; NEW.event_capture_required:=false;
 IF SESSION_USER='aurum_app' AND CURRENT_USER='aurum_schema_owner' AND pg_trigger_depth()=2
   AND NOT public.is_tenant_support_session()
   AND public.current_tenant_id()=NEW.tenant_id
   AND public.current_app_user_id()=NEW.cashier_user_id THEN
  NEW.source_transaction_id:=txid_current();
  SELECT g.enabled INTO enabled FROM public.sync_retail_event_producer_gate g
    WHERE g.tenant_id=NEW.tenant_id AND g.id=NEW.branch_id FOR SHARE;
  NEW.event_capture_required:=coalesce(enabled,false);
 END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION public.trg_retail_event_immutable() RETURNS TRIGGER
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 RAISE EXCEPTION 'retail_event_immutable' USING ERRCODE='55000';
END $$;
CREATE FUNCTION public.assert_retail_event_source(p_tenant UUID,p_sale UUID,p_op UUID)
RETURNS public.pos_retail_checkout_origin
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE origin public.pos_retail_checkout_origin%ROWTYPE;
BEGIN
 IF SESSION_USER<>'aurum_app' OR public.is_tenant_support_session()
   OR p_tenant IS DISTINCT FROM public.current_tenant_id() THEN
  RAISE EXCEPTION 'retail_event_actor_denied' USING ERRCODE='42501';
 END IF;
 SELECT * INTO origin FROM public.pos_retail_checkout_origin
  WHERE tenant_id=p_tenant AND id=p_op AND sale_id=p_sale;
 IF NOT FOUND OR origin.cashier_user_id IS DISTINCT FROM public.current_app_user_id()
   OR NOT coalesce(public.tenant_actor_has_scoped_permission(
     p_tenant,'pos.sell',origin.branch_id),false)
   OR NOT EXISTS(SELECT 1 FROM public.app_user
     WHERE id=origin.cashier_user_id AND status='active') THEN
  RAISE EXCEPTION 'retail_event_actor_denied' USING ERRCODE='42501';
 END IF;
 IF NOT origin.event_capture_required
   OR origin.source_transaction_id IS DISTINCT FROM txid_current()
   OR NOT EXISTS(SELECT 1 FROM public.sale s WHERE s.tenant_id=p_tenant AND s.id=p_sale
      AND s.status='completed' AND s.sale_type='sale' AND s.parent_sale_id IS NULL
      AND NOT s.is_test AND s.currency='TJS'
      AND (s.branch_id,s.register_id,s.cashier_user_id)=
          (origin.branch_id,origin.register_id,origin.cashier_user_id)) THEN
  RAISE EXCEPTION 'retail_event_current_source_required' USING ERRCODE='23514';
 END IF;
 IF origin.source_kind='physical' THEN
  PERFORM public.assert_physical_checkout(p_tenant,p_sale,true);
 ELSE
  PERFORM public.assert_mixed_checkout(p_tenant,p_sale,true);
 END IF;
 PERFORM 1 FROM public.sync_stream stream
 JOIN public.sync_writer_epoch epoch ON
  (epoch.tenant_id,epoch.branch_id,epoch.writer_epoch,epoch.writer_node_id)=
  (stream.tenant_id,stream.branch_id,stream.writer_epoch,stream.writer_node_id)
 JOIN public.sync_node node ON (node.id,node.tenant_id,node.branch_id)=
  (stream.writer_node_id,stream.tenant_id,stream.branch_id)
 WHERE stream.tenant_id=p_tenant AND stream.branch_id=origin.branch_id
   AND stream.writer_node_id=origin.origin_node_id AND stream.writer_epoch=origin.writer_epoch
   AND epoch.state='active' AND epoch.capability='cloud_full' AND epoch.allowed_register_id IS NULL
   AND node.node_kind='cloud' AND node.mode='cloud_writer' AND node.status='active'
 FOR UPDATE OF stream;
 IF NOT FOUND THEN
  RAISE EXCEPTION 'retail_event_writer_invalid' USING ERRCODE='23514';
 END IF;
 RETURN origin;
END $$;
CREATE FUNCTION public.reserve_retail_checkout_event_position(p_tenant UUID,p_sale UUID,p_op UUID)
RETURNS TABLE(stream_id UUID,origin_node_id UUID,writer_epoch BIGINT,sequence BIGINT,
 previous_checksum TEXT,previous_projection_checksum TEXT)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE origin public.pos_retail_checkout_origin%ROWTYPE;
BEGIN
 origin:=public.assert_retail_event_source(p_tenant,p_sale,p_op);
 IF EXISTS(SELECT 1 FROM public.pos_retail_checkout_event WHERE tenant_id=p_tenant AND id=p_op) THEN
  RAISE EXCEPTION 'retail_event_already_stored' USING ERRCODE='23514';
 END IF;
 PERFORM set_config('app.branch_id',origin.branch_id::TEXT,true);
 RETURN QUERY SELECT * FROM public.reserve_sync_event_position(p_tenant,origin.branch_id);
END $$;
CREATE FUNCTION public.store_retail_checkout_event(p_tenant UUID,p_sale UUID,p_op UUID,
 p_record BYTEA,p_previous_source TEXT,p_previous_projection TEXT,p_fingerprint TEXT) RETURNS VOID
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE origin public.pos_retail_checkout_origin%ROWTYPE;
 existing public.pos_retail_checkout_event%ROWTYPE; stream public.sync_stream%ROWTYPE;
 raw TEXT; doc JSONB; envelope JSONB; payload JSONB; expected JSONB;
 version INTEGER; occurred TIMESTAMPTZ; occurred_text TEXT; payload_hash TEXT;
 source_hash TEXT; projection_hash TEXT; fingerprint TEXT;
BEGIN
 -- Replay remains readable after writer handover and outbox retention.
 IF SESSION_USER<>'aurum_app' OR public.is_tenant_support_session()
   OR p_tenant IS DISTINCT FROM public.current_tenant_id() THEN
  RAISE EXCEPTION 'retail_event_actor_denied' USING ERRCODE='42501';
 END IF;
 SELECT * INTO origin FROM public.pos_retail_checkout_origin
  WHERE tenant_id=p_tenant AND id=p_op AND sale_id=p_sale;
 IF NOT FOUND OR origin.cashier_user_id IS DISTINCT FROM public.current_app_user_id()
   OR NOT coalesce(public.tenant_actor_has_scoped_permission(
     p_tenant,'pos.sell',origin.branch_id),false)
   OR NOT EXISTS(SELECT 1 FROM public.app_user
     WHERE id=origin.cashier_user_id AND status='active') THEN
  RAISE EXCEPTION 'retail_event_actor_denied' USING ERRCODE='42501';
 END IF;
 SELECT * INTO existing FROM public.pos_retail_checkout_event WHERE tenant_id=p_tenant AND id=p_op;
 IF FOUND THEN
  IF (existing.sale_id,existing.record,existing.previous_source_checksum,
      existing.previous_projection_checksum,existing.event_fingerprint) IS DISTINCT FROM
      (p_sale,p_record,p_previous_source,p_previous_projection,p_fingerprint) THEN
   RAISE EXCEPTION 'retail_event_replay_conflict' USING ERRCODE='23514';
  END IF;
  RETURN;
 END IF;
 origin:=public.assert_retail_event_source(p_tenant,p_sale,p_op);
 IF p_record IS NULL OR octet_length(p_record) NOT BETWEEN 1 AND 1048576 THEN
  RAISE EXCEPTION 'retail_event_invalid_record' USING ERRCODE='23514';
 END IF;
 BEGIN
  raw:=convert_from(p_record,'UTF8');
  IF NOT (raw IS JSON OBJECT WITH UNIQUE KEYS) THEN
   RAISE EXCEPTION 'retail_event_invalid_record';
  END IF;
  doc:=raw::JSONB;
 EXCEPTION WHEN OTHERS THEN
  RAISE EXCEPTION 'retail_event_invalid_record' USING ERRCODE='23514';
 END;
 IF p_record IS DISTINCT FROM convert_to(public.retail_event_canonical_json(doc),'UTF8') THEN
  RAISE EXCEPTION 'retail_event_invalid_record' USING ERRCODE='23514';
 END IF;
 IF origin.source_kind='physical' THEN
  SELECT result_payload INTO payload FROM public.pos_physical_checkout
    WHERE tenant_id=p_tenant AND id=p_op AND sale_id=p_sale;
  version:=3;
 ELSE
  SELECT result_payload INTO payload FROM public.pos_mixed_checkout
    WHERE tenant_id=p_tenant AND id=p_op AND sale_id=p_sale;
  version:=4;
 END IF;
 SELECT * INTO stream FROM public.sync_stream
   WHERE tenant_id=p_tenant AND branch_id=origin.branch_id FOR UPDATE;
 occurred:=(payload->>'completed_at')::TIMESTAMPTZ;
 occurred_text:=to_char(occurred AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"');
 payload_hash:=public.retail_event_hash(payload);
 source_hash:=public.retail_event_hash(jsonb_build_object(
  'previous_checksum',p_previous_source,'event_id',payload->>'event_id','tenant_id',p_tenant,
  'branch_id',origin.branch_id,'origin_node_id',origin.origin_node_id,
  'writer_epoch',origin.writer_epoch,
  'sequence',stream.last_sequence,'operation_id',p_op,'aggregate_type','sale','aggregate_id',p_sale,
  'event_type','pos.sale.completed.v'||version,'schema_version',version,
  'occurred_at',occurred_text,'payload_hash',payload_hash));
 projection_hash:=public.retail_event_hash(jsonb_build_object(
  'previous_checksum',p_previous_projection,'origin_node_id',origin.origin_node_id,
  'writer_epoch',origin.writer_epoch,'sequence',stream.last_sequence,'sale_id',p_sale,
  'projection_hash',payload_hash));
 envelope:=jsonb_build_object(
  'event_id',payload->>'event_id','tenant_id',p_tenant,'branch_id',origin.branch_id,
  'origin_node_id',origin.origin_node_id,'writer_epoch',origin.writer_epoch,
  'sequence',stream.last_sequence,'operation_id',p_op,'aggregate_type','sale','aggregate_id',p_sale,
  'event_type','pos.sale.completed.v'||version,'schema_version',version,
  'occurred_at',replace(occurred_text,'.000000Z','Z'),'payload',payload,
  'payload_hash',payload_hash,'stream_checksum',source_hash,
  'projection_hash',payload_hash,'projection_checksum',projection_hash);
 fingerprint:=public.retail_event_hash(jsonb_build_object(
  'domain','aurum-pos-retail-sale-event','schema_version',version,'envelope',envelope));
 expected:=jsonb_build_object('record_kind','retail_sale_event','storage_version',1,
  'envelope',envelope,'previous_source_checksum',p_previous_source,
  'previous_projection_checksum',p_previous_projection,'event_fingerprint',fingerprint);
 IF doc IS DISTINCT FROM expected OR p_previous_source IS DISTINCT FROM stream.current_checksum
   OR p_previous_projection IS DISTINCT FROM stream.current_projection_checksum
   OR p_fingerprint IS DISTINCT FROM fingerprint OR stream.last_sequence<1 THEN
  RAISE EXCEPTION 'retail_event_binding_invalid' USING ERRCODE='23514';
 END IF;
 PERFORM set_config('app.branch_id',origin.branch_id::TEXT,true);
 INSERT INTO public.sync_outbox(event_id,tenant_id,branch_id,origin_node_id,writer_epoch,sequence,
  operation_id,aggregate_type,aggregate_id,event_type,schema_version,occurred_at,
  payload,payload_hash,
  stream_checksum,projection_hash,projection_checksum)
 VALUES((payload->>'event_id')::UUID,p_tenant,origin.branch_id,
  origin.origin_node_id,origin.writer_epoch,
  stream.last_sequence,p_op,'sale',p_sale,'pos.sale.completed.v'||version,version,occurred,payload,
  payload_hash,source_hash,payload_hash,projection_hash);
 INSERT INTO public.pos_retail_checkout_event(tenant_id,id,sale_id,event_id,source_kind,branch_id,
  register_id,cashier_id,origin_node_id,writer_epoch,sequence,stream_id,previous_source_checksum,
  previous_projection_checksum,event_fingerprint,payload_hash,stream_checksum,projection_checksum,
  record,created_by)
 VALUES(p_tenant,p_op,p_sale,(payload->>'event_id')::UUID,origin.source_kind,origin.branch_id,
  origin.register_id,origin.cashier_user_id,origin.origin_node_id,origin.writer_epoch,
  stream.last_sequence,stream.id,p_previous_source,p_previous_projection,fingerprint,payload_hash,
  source_hash,projection_hash,p_record,origin.cashier_user_id);
 PERFORM public.finalize_sync_event_position(
  stream.id,stream.last_sequence,source_hash,projection_hash);
END $$;
CREATE FUNCTION public.trg_retail_checkout_event_required() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 IF NEW.event_capture_required AND NOT EXISTS(
  SELECT 1 FROM public.pos_retail_checkout_event e
  JOIN public.sync_outbox o ON o.event_id=e.event_id
  WHERE e.tenant_id=NEW.tenant_id AND e.id=NEW.id AND e.sale_id=NEW.sale_id
   AND (e.branch_id,e.origin_node_id,e.writer_epoch)=
       (NEW.branch_id,NEW.origin_node_id,NEW.writer_epoch)
   AND (o.tenant_id,o.operation_id,o.aggregate_id,o.origin_node_id,o.writer_epoch,o.sequence,
        o.payload_hash,o.stream_checksum,o.projection_hash,o.projection_checksum)=
       (e.tenant_id,e.id,e.sale_id,e.origin_node_id,e.writer_epoch,e.sequence,
        e.payload_hash,e.stream_checksum,e.payload_hash,e.projection_checksum)) THEN
  RAISE EXCEPTION 'retail_event_required' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
"""


def upgrade() -> None:
    _sql("""
      CREATE TEMP TABLE retail_event_reference_grants AS
       SELECT oid::regclass::TEXT AS relation,
        has_table_privilege('aurum_schema_owner',oid,'REFERENCES') AS granted
       FROM pg_class WHERE oid IN ('public.pos_retail_checkout_origin'::regclass,
        'public.branch'::regclass,'public.app_user'::regclass);
      GRANT REFERENCES ON public.pos_retail_checkout_origin,public.branch,public.app_user
       TO aurum_schema_owner;
      ALTER TABLE public.pos_retail_checkout_origin
       ADD COLUMN source_transaction_id BIGINT,
       ADD COLUMN event_capture_required BOOLEAN NOT NULL DEFAULT false;
      CREATE TABLE public.sync_retail_event_producer_gate (
       tenant_id UUID NOT NULL,id UUID NOT NULL,enabled BOOLEAN NOT NULL DEFAULT false,
       created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
       created_by UUID REFERENCES public.app_user(id),PRIMARY KEY(tenant_id,id),
       FOREIGN KEY(tenant_id,id) REFERENCES public.branch(tenant_id,id) ON DELETE RESTRICT);
      CREATE TABLE public.pos_retail_checkout_event (
       tenant_id UUID NOT NULL,id UUID NOT NULL,sale_id UUID NOT NULL,event_id UUID NOT NULL,
       source_kind TEXT NOT NULL CHECK(source_kind IN ('physical','mixed')),
       branch_id UUID NOT NULL,register_id UUID NOT NULL,cashier_id UUID NOT NULL,
       origin_node_id UUID NOT NULL,writer_epoch BIGINT NOT NULL CHECK(writer_epoch>0),
       sequence BIGINT NOT NULL CHECK(sequence>0),stream_id UUID NOT NULL,
       previous_source_checksum TEXT NOT NULL,previous_projection_checksum TEXT NOT NULL,
       event_fingerprint TEXT NOT NULL,record BYTEA NOT NULL,
       payload_hash TEXT NOT NULL,stream_checksum TEXT NOT NULL,projection_checksum TEXT NOT NULL,
       created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
       created_by UUID NOT NULL REFERENCES public.app_user(id),
       PRIMARY KEY(tenant_id,id),UNIQUE(event_id),UNIQUE(tenant_id,sale_id),
       UNIQUE(tenant_id,branch_id,writer_epoch,sequence),CHECK(id=event_id),
       CHECK(created_by=cashier_id),CHECK(octet_length(record) BETWEEN 1 AND 1048576),
       FOREIGN KEY(tenant_id,id) REFERENCES public.pos_retail_checkout_origin(tenant_id,id)
        ON DELETE RESTRICT);
      DO $$ DECLARE r RECORD; BEGIN
       FOR r IN SELECT * FROM pg_temp.retail_event_reference_grants WHERE NOT granted LOOP
        EXECUTE format('REVOKE REFERENCES ON %s FROM aurum_schema_owner',r.relation);
       END LOOP;
      END $$;
      DROP TABLE pg_temp.retail_event_reference_grants;
    """)
    for table in ("sync_retail_event_producer_gate", "pos_retail_checkout_event"):
        _sql(f"""
          ALTER TABLE public.{table} OWNER TO aurum_schema_owner;
          ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY;
          ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY;
          REVOKE ALL ON public.{table} FROM PUBLIC,aurum_app,aurum_support;
          CREATE POLICY schema_owner ON public.{table} TO aurum_schema_owner
           USING(true) WITH CHECK(true);
          CREATE TRIGGER a00_tenant_access BEFORE INSERT OR UPDATE OR DELETE
           ON public.{table} FOR EACH ROW
           EXECUTE FUNCTION public.trg_enforce_tenant_access('pos');
          CREATE TRIGGER z90_retail_event_audit AFTER INSERT OR UPDATE OR DELETE
           ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.trg_audit_log();
        """)
    _sql("""
      GRANT SELECT ON public.pos_retail_checkout_event TO aurum_app,aurum_support;
      CREATE POLICY support ON public.pos_retail_checkout_event FOR SELECT TO aurum_support
       USING(true);
      CREATE POLICY tenant_actor ON public.pos_retail_checkout_event FOR SELECT TO aurum_app
       USING(session_user='aurum_app' AND NOT public.is_tenant_support_session()
        AND tenant_id=public.current_tenant_id() AND cashier_id=public.current_app_user_id()
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
    for signature in PUBLIC_FUNCTIONS:
        _sql(f"GRANT EXECUTE ON FUNCTION public.{signature} TO aurum_app;")
    _sql("""
      CREATE TRIGGER a05_retail_event_capture BEFORE INSERT
       ON public.pos_retail_checkout_origin FOR EACH ROW
       EXECUTE FUNCTION public.trg_retail_event_capture();
      CREATE CONSTRAINT TRIGGER trg_retail_checkout_event_required AFTER INSERT
       ON public.pos_retail_checkout_origin DEFERRABLE INITIALLY DEFERRED
       FOR EACH ROW EXECUTE FUNCTION public.trg_retail_checkout_event_required();
      CREATE TRIGGER a10_retail_event_gate_guard BEFORE INSERT OR UPDATE OR DELETE
       ON public.sync_retail_event_producer_gate FOR EACH ROW
       EXECUTE FUNCTION public.trg_retail_event_gate_guard();
      CREATE TRIGGER a10_retail_event_immutable BEFORE UPDATE OR DELETE
       ON public.pos_retail_checkout_event FOR EACH ROW
       EXECUTE FUNCTION public.trg_retail_event_immutable();
      CREATE TRIGGER a10_retail_event_no_truncate BEFORE TRUNCATE
       ON public.pos_retail_checkout_event FOR EACH STATEMENT
       EXECUTE FUNCTION public.trg_retail_event_immutable();
    """)
    _audit(True)


def downgrade() -> None:
    _sql("""
      LOCK TABLE public.pos_physical_checkout,public.pos_mixed_checkout,
       public.pos_retail_checkout_origin,public.pos_retail_checkout_event,
       public.sync_retail_event_producer_gate IN ACCESS EXCLUSIVE MODE;
      DO $$ BEGIN
       IF EXISTS(SELECT 1 FROM public.pos_retail_checkout_event) THEN
        RAISE EXCEPTION 'retail_event_downgrade_requires_empty_history' USING ERRCODE='55000';
       END IF;
      END $$;
      DROP TRIGGER trg_retail_checkout_event_required ON public.pos_retail_checkout_origin;
      DROP TRIGGER a05_retail_event_capture ON public.pos_retail_checkout_origin;
      DROP TRIGGER a10_retail_event_gate_guard ON public.sync_retail_event_producer_gate;
      DROP TRIGGER a10_retail_event_immutable ON public.pos_retail_checkout_event;
      DROP TRIGGER a10_retail_event_no_truncate ON public.pos_retail_checkout_event;
    """)
    _audit(False)
    for signature in reversed(FUNCTIONS):
        _sql(f"DROP FUNCTION public.{signature};")
    _sql("""
      DROP TABLE public.pos_retail_checkout_event;
      DROP TABLE public.sync_retail_event_producer_gate;
      ALTER TABLE public.pos_retail_checkout_origin DROP COLUMN source_transaction_id,
       DROP COLUMN event_capture_required;
    """)
