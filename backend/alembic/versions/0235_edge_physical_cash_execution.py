"""Execute a physical cash command through its actual native database identity.

Revision ID: 0235
Revises: 0234
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0235"
down_revision: str | Sequence[str] | None = "0234"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The allocator owns its candidate universe. The wire plan is a comparison
# oracle, never a source of authorization, cost, inventory or receipt numbers.
PACK_SQL = r"""
CREATE FUNCTION public.edge_physical_uuid5_v1(p_namespace UUID,p_name TEXT)
RETURNS UUID LANGUAGE plpgsql IMMUTABLE SECURITY DEFINER
SET search_path=pg_catalog,pg_temp AS $function$
DECLARE bytes BYTEA;
BEGIN
 bytes:=substring(public.digest(uuid_send(p_namespace)||convert_to(p_name,'UTF8'),'sha1')
   FROM 1 FOR 16);
 bytes:=set_byte(bytes,6,(get_byte(bytes,6)&15)|80);
 bytes:=set_byte(bytes,8,(get_byte(bytes,8)&63)|128);
 RETURN encode(bytes,'hex')::UUID;
END $function$;

CREATE FUNCTION public.edge_physical_pack_take_v1(
 p_state JSONB,p_packaging JSONB,p_format TEXT,p_needed BIGINT)
RETURNS JSONB LANGUAGE plpgsql IMMUTABLE SECURITY INVOKER
SET search_path=pg_catalog,pg_temp AS $function$
DECLARE
 remaining BIGINT; quota BIGINT; taken BIGINT:=0; amount BIGINT; size INTEGER;
 loose BIGINT; part JSONB; number INTEGER; left_units BIGINT;
 intact JSONB:='[]'; fragments JSONB:='[]'; new_state JSONB;
BEGIN
 remaining:=public.unit_pack_v2_remaining(p_state,p_packaging);
 IF p_needed IS NULL OR p_needed<1 OR p_needed>99999999999
   OR p_format NOT IN ('unit','blister') THEN
  RAISE EXCEPTION 'Native physical pack request invalid' USING ERRCODE='22023'; END IF;
 size:=(p_packaging->>'units_per_blister')::INTEGER;
 loose:=(p_state->>'loose_units')::BIGINT;
 IF p_format='blister' THEN
  IF size IS NULL THEN RAISE EXCEPTION 'Native blister size unavailable'
    USING ERRCODE='23514'; END IF;
  quota:=LEAST(p_needed/size,jsonb_array_length(p_state->'intact_blisters'));
  FOR part IN SELECT value FROM jsonb_array_elements(p_state->'intact_blisters')
    ORDER BY value::TEXT::INTEGER LOOP
   IF quota>0 THEN quota:=quota-1; taken:=taken+size;
   ELSE intact:=intact||jsonb_build_array(part); END IF;
  END LOOP;
  fragments:=p_state->'opened_blisters';
 ELSE
  quota:=LEAST(p_needed,remaining/(p_packaging->>'dispense_step')::BIGINT
    *(p_packaging->>'dispense_step')::BIGINT);
  amount:=LEAST(quota,loose); loose:=loose-amount; quota:=quota-amount; taken:=amount;
  FOR part IN SELECT value FROM jsonb_array_elements(p_state->'opened_blisters')
    ORDER BY (value->>'number')::INTEGER LOOP
   number:=(part->>'number')::INTEGER;
   amount:=LEAST(quota,(part->>'remaining_units')::BIGINT);
   quota:=quota-amount; taken:=taken+amount;
   left_units:=(part->>'remaining_units')::BIGINT-amount;
   IF left_units>0 THEN fragments:=fragments||jsonb_build_array(
     jsonb_build_object('number',number,'remaining_units',left_units)); END IF;
  END LOOP;
  FOR part IN SELECT value FROM jsonb_array_elements(p_state->'intact_blisters')
    ORDER BY value::TEXT::INTEGER LOOP
   number:=part::TEXT::INTEGER;
   amount:=LEAST(quota,size); quota:=quota-amount; taken:=taken+amount;
   IF amount=0 THEN intact:=intact||jsonb_build_array(number);
   ELSIF amount<size THEN fragments:=fragments||jsonb_build_array(
     jsonb_build_object('number',number,'remaining_units',size-amount)); END IF;
  END LOOP;
  SELECT coalesce(jsonb_agg(value ORDER BY (value->>'number')::INTEGER),'[]'::JSONB)
    INTO fragments FROM jsonb_array_elements(fragments);
 END IF;
 new_state:=jsonb_build_object('intact_blisters',intact,'opened_blisters',fragments,
   'loose_units',loose);
 IF taken>0 THEN PERFORM public.unit_pack_v2_assert_transition(
   p_state,new_state,p_packaging,p_format,taken); END IF;
 RETURN jsonb_build_object('state',new_state,'taken',taken);
END
$function$;
"""

POST_SQL = r"""
CREATE FUNCTION public.edge_physical_post_v1(p_tenant UUID,p_command UUID,p_line UUID,
 p_lot UUID,p_pack UUID,p_alias UUID,p_kind TEXT,p_order INTEGER,p_closed BIGINT,
 p_taken BIGINT,p_before JSONB,p_after JSONB,p_candidate JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS $function$
DECLARE lot RECORD; expected JSONB; mid UUID; start BIGINT;
BEGIN
 SELECT branch_id,catalog_id,initial_closed_packs,initial_opened_packs,closed_packs
 INTO STRICT lot FROM public.unit_stock_lot_v2
 WHERE tenant_id=p_tenant AND id=p_lot;
 expected:=jsonb_build_object('line_id',p_line,'lot_id',p_lot,'pack_id',p_alias,
  'kind',p_kind,'entry_order',p_order,'closed_delta',p_closed,'units_taken',p_taken,
  'before_state',p_before,'after_state',p_after);
 IF jsonb_typeof(p_candidate) IS DISTINCT FROM 'object'
  OR p_candidate-'id' IS DISTINCT FROM expected THEN
  RAISE EXCEPTION 'Native physical allocation differs from canonical stock' USING ERRCODE='23514';
 END IF;
 mid:=(p_candidate->>'id')::UUID;
 IF mid IS NULL OR (get_byte(uuid_send(mid),6)>>4)<>4
   OR (get_byte(uuid_send(mid),8)&192)<>128 THEN
  RAISE EXCEPTION 'Native movement ID must be UUID4' USING ERRCODE='22023'; END IF;
 IF p_closed<>0 THEN start:=lot.initial_closed_packs+lot.initial_opened_packs-lot.closed_packs+1;
 END IF;
 INSERT INTO public.unit_stock_movement_v2(id,tenant_id,branch_id,catalog_id,
  command_id,line_id,lot_id,pack_id,kind,entry_order,closed_delta,units_taken,
  sealed_start,sealed_count,before_state,after_state,created_by)
 VALUES(mid,p_tenant,lot.branch_id,lot.catalog_id,p_command,p_line,p_lot,p_pack,p_kind,p_order,
  p_closed,p_taken,start,CASE WHEN p_closed<>0 THEN -p_closed END,p_before,p_after,
  public.current_app_user_id());
END $function$;

CREATE FUNCTION public.edge_physical_allocate_v1(p_tenant UUID,p_command UUID,
 p_snapshot JSONB,p_date DATE,p_moves JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS $function$
DECLARE
 line RECORD; lot RECORD; pack RECORD; lot_ids UUID[]; original_packs UUID[];
 aliases JSONB:='{}'; needed BIGINT; whole BIGINT; size BIGINT; seq INTEGER:=0;
 result JSONB; taken BIGINT; alias UUID; real_id UUID; ordinal BIGINT;
 virtual_ordinal BIGINT; attempt INTEGER; used_aliases UUID[];
BEGIN
 IF jsonb_typeof(p_moves) IS DISTINCT FROM 'array'
   OR jsonb_array_length(p_moves) NOT BETWEEN 1 AND 8192 THEN
  RAISE EXCEPTION 'Native movement batch invalid' USING ERRCODE='22023'; END IF;
 SELECT coalesce(array_agg((value->>'id')::UUID),'{}'::UUID[]) INTO lot_ids
 FROM jsonb_array_elements(p_snapshot);
 SELECT coalesce(array_agg((pack->>'id')::UUID),'{}'::UUID[]) INTO original_packs
 FROM jsonb_array_elements(p_snapshot) batch
 CROSS JOIN LATERAL jsonb_array_elements(batch->'opened_packs') pack;
 used_aliases:=original_packs;
 FOR line IN SELECT line_id,catalog_id,packaging,sale_format,base_units,position
  FROM public.unit_stock_cart_line_v2
  WHERE tenant_id=p_tenant AND command_id=p_command ORDER BY
   CASE sale_format WHEN 'pack' THEN 0 WHEN 'blister' THEN 1 ELSE 2 END,position LOOP
  needed:=line.base_units; size:=(line.packaging->>'units_per_pack')::BIGINT;
  FOR lot IN SELECT id,packaging,created_at,initial_closed_packs,initial_opened_packs,closed_packs
   FROM public.unit_stock_lot_v2
   WHERE tenant_id=p_tenant AND id=ANY(lot_ids) AND catalog_id=line.catalog_id
    AND packaging=line.packaging AND NOT is_blocked AND expires_at>p_date
   ORDER BY expires_at,created_at,id LOOP
   IF needed=0 THEN EXIT; END IF;
   IF line.sale_format<>'pack' THEN
    FOR pack IN SELECT id,state,opened_at FROM public.unit_stock_opened_pack_v2
     WHERE tenant_id=p_tenant AND lot_id=lot.id AND remaining_units>0
     ORDER BY CASE WHEN id=ANY(original_packs) THEN 0 ELSE 1 END,
      CASE WHEN id=ANY(original_packs) THEN opened_at ELSE lot.created_at END,
      CASE WHEN id=ANY(original_packs) THEN id ELSE (aliases->>id::TEXT)::UUID END LOOP
     IF needed=0 THEN EXIT; END IF;
     result:=public.edge_physical_pack_take_v1(pack.state,line.packaging,line.sale_format,needed);
     taken:=(result->>'taken')::BIGINT;
     IF taken>0 THEN
      alias:=coalesce((aliases->>pack.id::TEXT)::UUID,pack.id);
      PERFORM public.edge_physical_post_v1(p_tenant,p_command,line.line_id,lot.id,
       pack.id,alias,line.sale_format||'_take',seq,0,taken,pack.state,result->'state',p_moves->seq);
      needed:=needed-taken;seq:=seq+1;
     END IF;
    END LOOP;
   END IF;
   SELECT id,packaging,created_at,initial_closed_packs,initial_opened_packs,closed_packs
    INTO STRICT lot FROM public.unit_stock_lot_v2 WHERE tenant_id=p_tenant AND id=lot.id;
   whole:=LEAST(lot.closed_packs,needed/size);
   IF whole>0 THEN
    PERFORM public.edge_physical_post_v1(p_tenant,p_command,line.line_id,lot.id,NULL,NULL,
     'pack_take',seq,-whole,whole*size,NULL,NULL,p_moves->seq);
    needed:=needed-whole*size;seq:=seq+1;
   END IF;
   SELECT id,packaging,created_at,initial_closed_packs,initial_opened_packs,closed_packs
    INTO STRICT lot FROM public.unit_stock_lot_v2 WHERE tenant_id=p_tenant AND id=lot.id;
   IF needed>0 AND lot.closed_packs>0 AND line.sale_format<>'pack' THEN
    ordinal:=lot.initial_closed_packs+lot.initial_opened_packs-lot.closed_packs+1;
    real_id:=public.unit_incoming_uuid5(
     'aurum-unit-open-v2:'||p_tenant||':'||p_command||':'||lot.id||':'||ordinal);
    SELECT (value->>'closed_packs')::BIGINT-lot.closed_packs INTO STRICT virtual_ordinal
      FROM jsonb_array_elements(p_snapshot) WHERE value->>'id'=lot.id::TEXT;
    attempt:=0;
    alias:=public.edge_physical_uuid5_v1(lot.id,'unit-cart-v2:'||virtual_ordinal||':'||attempt);
    WHILE alias=ANY(used_aliases) LOOP
     attempt:=attempt+1;
     alias:=public.edge_physical_uuid5_v1(lot.id,'unit-cart-v2:'||virtual_ordinal||':'||attempt);
    END LOOP;
    used_aliases:=array_append(used_aliases,alias);
    aliases:=aliases||jsonb_build_object(real_id::TEXT,alias);
    PERFORM public.edge_physical_post_v1(p_tenant,p_command,line.line_id,lot.id,
     real_id,alias,'pack_open',seq,-1,0,NULL,
     public.unit_pack_v2_initial_state(line.packaging),p_moves->seq);
    seq:=seq+1;
    SELECT id,state INTO STRICT pack FROM public.unit_stock_opened_pack_v2
     WHERE tenant_id=p_tenant AND id=real_id;
    result:=public.edge_physical_pack_take_v1(pack.state,line.packaging,line.sale_format,needed);
    taken:=(result->>'taken')::BIGINT;
    IF taken<=0 THEN RAISE EXCEPTION 'Native pack opening has no consumption'
     USING ERRCODE='23514'; END IF;
    PERFORM public.edge_physical_post_v1(p_tenant,p_command,line.line_id,lot.id,
     real_id,alias,line.sale_format||'_take',seq,0,taken,pack.state,result->'state',p_moves->seq);
    needed:=needed-taken;seq:=seq+1;
   END IF;
  END LOOP;
  IF needed<>0 THEN RAISE EXCEPTION 'Native physical stock insufficient'
    USING ERRCODE='23514'; END IF;
 END LOOP;
 IF seq<>jsonb_array_length(p_moves) THEN RAISE EXCEPTION 'Native movement batch has extra rows'
   USING ERRCODE='23514'; END IF;
 PERFORM public.assert_unit_cart_v2_complete(p_tenant,p_command);
END $function$;
"""

ACTOR_SQL = r"""
CREATE FUNCTION public.native_edge_physical_actor_v1(
 p_tenant UUID,p_branch UUID,p_register UUID,p_user UUID)
RETURNS BOOLEAN LANGUAGE SQL STABLE SECURITY DEFINER
SET search_path=pg_catalog,pg_temp AS $function$
 SELECT coalesce(p_tenant IS NOT NULL AND p_branch IS NOT NULL AND p_register IS NOT NULL
  AND p_user IS NOT NULL AND p_tenant=public.current_tenant_id()
  AND p_user=public.current_app_user_id() AND NOT public.is_tenant_support_session()
  AND EXISTS(SELECT 1 FROM public.tenant_settings settings WHERE settings.tenant_id=p_tenant
   AND settings.pos_payment_methods ? 'cash')
  AND coalesce(public.tenant_actor_has_scoped_permission(p_tenant,'pos.sell',p_branch),false)
  AND EXISTS(SELECT 1 FROM public.app_user u JOIN public.tenant_membership m
   ON m.user_id=u.id AND m.tenant_id=p_tenant AND m.status='active'
   WHERE u.id=p_user AND u.status='active')
  AND EXISTS(SELECT 1 FROM public.edge_cash_node_identity identity
   JOIN pg_catalog.pg_roles role ON role.rolname=identity.database_role
    AND role.oid=identity.database_role_oid
   JOIN public.sync_stream stream ON stream.tenant_id=identity.tenant_id
    AND stream.branch_id=identity.branch_id AND stream.writer_node_id=identity.edge_node_id
   JOIN public.sync_writer_epoch epoch ON epoch.tenant_id=stream.tenant_id
    AND epoch.branch_id=stream.branch_id AND epoch.writer_node_id=stream.writer_node_id
    AND epoch.writer_epoch=stream.writer_epoch AND epoch.allowed_register_id=identity.register_id
   JOIN public.sync_writer_activation activation ON activation.activation_id=epoch.activation_id
    AND activation.tenant_id=epoch.tenant_id AND activation.branch_id=epoch.branch_id
    AND activation.writer_node_id=epoch.writer_node_id
    AND activation.writer_epoch=epoch.writer_epoch
    AND activation.allowed_register_id=identity.register_id
   JOIN public.sync_node node ON node.id=identity.edge_node_id AND node.tenant_id=identity.tenant_id
    AND node.branch_id=identity.branch_id AND node.register_id=identity.register_id
   JOIN public.register register ON register.id=identity.register_id
    AND register.tenant_id=identity.tenant_id AND register.branch_id=identity.branch_id
   JOIN public.branch branch ON branch.id=identity.branch_id AND branch.tenant_id=identity.tenant_id
   JOIN public.tenant tenant ON tenant.id=identity.tenant_id
   WHERE identity.database_role=SESSION_USER AND identity.database_role='aurum_edge_node_'
    ||replace(identity.edge_node_id::TEXT,'-','')
    AND (identity.tenant_id,identity.branch_id,identity.register_id)=(p_tenant,p_branch,p_register)
    AND role.rolcanlogin AND role.rolinherit AND role.rolconnlimit=1
    AND NOT role.rolsuper AND NOT role.rolcreatedb AND NOT role.rolcreaterole
    AND NOT role.rolreplication AND NOT role.rolbypassrls
    AND (SELECT count(*) FROM pg_catalog.pg_auth_members WHERE member=role.oid)=1
    AND EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members membership
      JOIN pg_catalog.pg_roles granted ON granted.oid=membership.roleid
      WHERE membership.member=role.oid AND granted.rolname='aurum_edge_cash_executor'
       AND NOT membership.admin_option AND membership.inherit_option AND NOT membership.set_option)
    AND node.status='active' AND node.node_kind='edge' AND node.mode='edge_writer'
    AND epoch.state='active' AND epoch.capability='cash_sale_v1'
    AND activation.state='activated' AND activation.capability='cash_sale_v1'
    AND register.is_active AND branch.is_active AND tenant.status IN ('trial','active')
    AND NOT EXISTS(SELECT 1 FROM public.sync_writer_activation pending
      WHERE pending.tenant_id=p_tenant AND pending.branch_id=p_branch
       AND pending.state IN ('prepared','ready'))),false)
$function$;

CREATE FUNCTION public.native_edge_physical_context_v1()
RETURNS BOOLEAN LANGUAGE SQL STABLE SECURITY DEFINER
SET search_path=pg_catalog,pg_temp AS $function$
 SELECT coalesce(bool_or(public.native_edge_physical_actor_v1(
  identity.tenant_id,identity.branch_id,identity.register_id,public.current_app_user_id())),false)
 FROM public.edge_cash_node_identity identity
 WHERE identity.database_role=SESSION_USER AND identity.tenant_id=public.current_tenant_id()
$function$;

CREATE FUNCTION public.lock_native_physical_gates_v1(
 p_tenant UUID,p_branch UUID,p_register UUID,p_actor UUID)
RETURNS VOID LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,pg_temp AS $function$
BEGIN
 IF NOT public.native_edge_physical_actor_v1(p_tenant,p_branch,p_register,p_actor) THEN
  RAISE EXCEPTION 'Native physical gate actor invalid' USING ERRCODE='42501'; END IF;
 PERFORM 1 FROM public.sync_retail_event_producer_gate
  WHERE tenant_id=p_tenant AND id=p_branch AND enabled FOR SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION 'Native retail producer gate disabled'
  USING ERRCODE='55000'; END IF;
 PERFORM 1 FROM public.sync_physical_stock_producer_gate
  WHERE tenant_id=p_tenant AND id=p_branch AND enabled FOR SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION 'Native physical producer gate disabled'
  USING ERRCODE='55000'; END IF;
END $function$;
"""

EVENT_SQL = r"""
CREATE FUNCTION public.emit_native_physical_retail_event_v1(p_tenant UUID,p_sale UUID,p_op UUID)
RETURNS VOID LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS $function$
DECLARE position RECORD; payload JSONB; origin RECORD; occurred TEXT; payload_hash TEXT;
 source_hash TEXT; projection_hash TEXT; envelope JSONB; fingerprint TEXT; doc JSONB;
BEGIN
 SELECT * INTO STRICT origin FROM public.assert_retail_event_source(p_tenant,p_sale,p_op);
 SELECT * INTO STRICT position FROM
  public.reserve_retail_checkout_event_position(p_tenant,p_sale,p_op);
 SELECT result_payload INTO STRICT payload FROM public.pos_physical_checkout
  WHERE tenant_id=p_tenant AND sale_id=p_sale AND id=p_op;
 occurred:=to_char((payload->>'completed_at')::TIMESTAMPTZ AT TIME ZONE 'UTC',
  'YYYY-MM-DD"T"HH24:MI:SS.US"Z"');
 payload_hash:=public.retail_event_hash(payload);
 source_hash:=public.retail_event_hash(jsonb_build_object(
  'previous_checksum',position.previous_checksum,'event_id',payload->>'event_id',
  'tenant_id',p_tenant,'branch_id',origin.branch_id,'origin_node_id',origin.origin_node_id,
  'writer_epoch',origin.writer_epoch,'sequence',position.sequence,'operation_id',p_op,
  'aggregate_type','sale','aggregate_id',p_sale,'event_type','pos.sale.completed.v3',
  'schema_version',3,'occurred_at',occurred,'payload_hash',payload_hash));
 projection_hash:=public.retail_event_hash(jsonb_build_object(
  'previous_checksum',position.previous_projection_checksum,'origin_node_id',origin.origin_node_id,
  'writer_epoch',origin.writer_epoch,'sequence',position.sequence,'sale_id',p_sale,
  'projection_hash',payload_hash));
 envelope:=jsonb_build_object('event_id',payload->>'event_id','tenant_id',p_tenant,
  'branch_id',origin.branch_id,'origin_node_id',origin.origin_node_id,
  'writer_epoch',origin.writer_epoch,
  'sequence',position.sequence,'operation_id',p_op,'aggregate_type','sale','aggregate_id',p_sale,
  'event_type','pos.sale.completed.v3','schema_version',3,
  'occurred_at',replace(occurred,'.000000Z','Z'),
  'payload',payload,'payload_hash',payload_hash,'stream_checksum',source_hash,
  'projection_hash',payload_hash,'projection_checksum',projection_hash);
 fingerprint:=public.retail_event_hash(jsonb_build_object(
  'domain','aurum-pos-retail-sale-event','schema_version',3,'envelope',envelope));
 doc:=jsonb_build_object('record_kind','retail_sale_event','storage_version',1,'envelope',envelope,
  'previous_source_checksum',position.previous_checksum,
  'previous_projection_checksum',position.previous_projection_checksum,
  'event_fingerprint',fingerprint);
 PERFORM public.store_retail_checkout_event(p_tenant,p_sale,p_op,
  convert_to(public.retail_event_canonical_json(doc),'UTF8'),position.previous_checksum,
  position.previous_projection_checksum,fingerprint);
END $function$;
"""

COMPLETE_SQL = r"""
CREATE FUNCTION public.complete_edge_physical_cash_v1(p_operation UUID,p_activation UUID,
 p_epoch BIGINT,p_actor UUID,p_payload JSONB,p_wire_hash TEXT)
RETURNS JSONB LANGUAGE plpgsql VOLATILE SECURITY INVOKER
SET search_path=pg_catalog,pg_temp SET row_security=on AS $function$
DECLARE
 core JSONB; replay JSONB; draft JSONB; inputs JSONB; quote JSONB; plan JSONB;
 scope JSONB; tenant UUID; branch UUID; register UUID; sale_id UUID; cash_hash TEXT;
 identity_id UUID; complete_hash TEXT; result JSONB; receipt JSONB; wrapper JSONB;
 sale_row public.sale%ROWTYPE; item JSONB; line JSONB; lines JSONB:='[]'; receipt_lines JSONB:='[]';
 payment JSONB; label TEXT; name TEXT; seq BIGINT; number TEXT; completed TEXT; total NUMERIC;
 stock_date DATE; report_timezone TEXT;
BEGIN
 IF CURRENT_USER<>'aurum_edge_cash_owner' OR jsonb_typeof(p_payload) IS DISTINCT FROM 'object'
  OR p_payload-ARRAY['profile','kind','scope','confirmation','plan']<>'{}'::JSONB
  OR NOT p_payload ?& ARRAY['profile','kind','scope','confirmation','plan']
  OR p_payload->>'profile' IS DISTINCT FROM 'retail_cash.complete.v1'
  OR p_payload->>'kind' IS DISTINCT FROM 'physical_cash'
  OR p_wire_hash IS DISTINCT FROM encode(sha256(convert_to(
    public.edge_canonical_jsonb(p_payload),'UTF8')),'hex')
  OR octet_length(convert_to(public.edge_canonical_jsonb(p_payload),'UTF8'))>1048576 THEN
  RAISE EXCEPTION 'Native physical command envelope invalid' USING ERRCODE='22023'; END IF;
 core:=p_payload-'plan';
 replay:=core||jsonb_build_object('profile','retail_cash.replay.v1');
 wrapper:=public.replay_edge_retail_cash_v1(p_operation,p_activation,p_epoch,p_actor,replay,
  encode(sha256(convert_to(public.edge_canonical_jsonb(replay),'UTF8')),'hex'));
 IF wrapper IS NOT NULL THEN RETURN wrapper; END IF;
 IF current_setting('transaction_isolation')<>'read committed' THEN
  RAISE EXCEPTION 'Native physical execution requires read committed isolation'
   USING ERRCODE='25001'; END IF;
 core:=core||jsonb_build_object('profile','retail_cash.draft.v1');
 draft:=public.lock_edge_retail_cash_draft_v1(p_operation,p_activation,p_epoch,p_actor,core,
  encode(sha256(convert_to(public.edge_canonical_jsonb(core),'UTF8')),'hex'));
 inputs:=draft->'inputs'; quote:=inputs->'saved_quote'; plan:=p_payload->'plan';
 scope:=p_payload->'scope'; tenant:=(scope->>'tenant_id')::UUID;
 branch:=(scope->>'branch_id')::UUID; register:=(scope->>'register_id')::UUID;
 sale_id:=(p_payload->'confirmation'->>'sale_id')::UUID;
 IF jsonb_typeof(plan) IS DISTINCT FROM 'object'
  OR plan-ARRAY['schema_version','quote','stock_request_hash','movements']<>'{}'::JSONB
  OR NOT plan ?& ARRAY['schema_version','quote','stock_request_hash','movements']
  OR plan->'schema_version' IS DISTINCT FROM '1'::JSONB
  OR plan->'quote' IS DISTINCT FROM quote OR plan->>'stock_request_hash' IS NULL
  OR plan->>'stock_request_hash' !~ '^[0-9a-f]{64}$'
  OR NOT public.native_edge_physical_actor_v1(tenant,branch,register,p_actor) THEN
  RAISE EXCEPTION 'Native physical plan or actor invalid' USING ERRCODE='23514'; END IF;
 PERFORM public.lock_native_physical_gates_v1(tenant,branch,register,p_actor);
 SELECT coalesce(settings.report_timezone,'Asia/Dushanbe') INTO STRICT report_timezone
  FROM public.tenant_settings settings WHERE tenant_id=tenant;
 stock_date:=(clock_timestamp() AT TIME ZONE report_timezone)::DATE;
 IF stock_date IS DISTINCT FROM (inputs->>'stock_date')::DATE THEN
  RAISE EXCEPTION 'Native physical stock date changed' USING ERRCODE='23514'; END IF;
 SELECT * INTO STRICT sale_row FROM public.sale WHERE tenant_id=tenant AND id=sale_id;
 SELECT request_hash INTO STRICT cash_hash FROM public.pos_unit_cash_intent
  WHERE tenant_id=tenant AND id=p_operation;
 IF sale_row.operation_id IS NULL AND sale_row.operation_hash IS NULL THEN
  UPDATE public.sale SET operation_id=p_operation,operation_hash=cash_hash
   WHERE tenant_id=tenant AND id=sale_id AND status='draft';
 ELSIF (sale_row.operation_id,sale_row.operation_hash) IS DISTINCT FROM (p_operation,cash_hash) THEN
  RAISE EXCEPTION 'Native sale operation already bound' USING ERRCODE='23505'; END IF;
 total:=(quote->>'total_amount')::NUMERIC;
 INSERT INTO public.unit_stock_cart_command_v2(tenant_id,id,branch_id,actor_id,request_hash,
  retail_version,currency,created_by,checkout_sale_id)
 VALUES(tenant,p_operation,branch,p_actor,plan->>'stock_request_hash',1,'TJS',p_actor,sale_id);
 FOR item IN SELECT value FROM jsonb_array_elements(quote->'lines') LOOP
  INSERT INTO public.unit_stock_cart_line_v2(tenant_id,command_id,line_id,position,catalog_id,
   packaging,sale_format,quantity,base_units,created_by,original_retail_unit_price,
   gross_amount,discount_amount,net_amount)
  VALUES(tenant,p_operation,(item->>'id')::UUID,(item->>'position')::INTEGER-1,
   (item->>'catalog_id')::UUID,item->'packaging',item->>'sale_format',
   (item->>'quantity')::BIGINT,(item->>'base_units')::BIGINT,p_actor,
   (item->>'original_retail_unit_price')::NUMERIC,(item->>'gross_amount')::NUMERIC,
   (item->>'discount_amount')::NUMERIC,(item->>'net_amount')::NUMERIC);
 END LOOP;
 PERFORM public.edge_physical_allocate_v1(tenant,p_operation,inputs->'physical_stock',
  stock_date,plan->'movements');
 SELECT receipt_seq,receipt_number INTO STRICT seq,number
  FROM public.allocate_register_receipt(tenant,register);
 completed:=replace(to_char(clock_timestamp() AT TIME ZONE 'UTC',
  'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),'.000000Z','Z');
 FOR item IN SELECT value FROM jsonb_array_elements(quote->'lines') LOOP
  SELECT brand_name INTO STRICT name FROM public.tenant_catalog
   WHERE tenant_id=tenant AND id=(item->>'catalog_id')::UUID;
  line:=item||jsonb_build_object('name',name); lines:=lines||jsonb_build_array(line);
  label:=CASE item->>'sale_format' WHEN 'pack' THEN 'уп.' WHEN 'blister' THEN 'блист.' ELSE
   CASE item->'packaging'->>'unit' WHEN 'tablet' THEN 'табл.' WHEN 'capsule' THEN 'капс.'
    WHEN 'ampoule' THEN 'амп.' WHEN 'sachet' THEN 'саше' WHEN 'suppository' THEN 'супп.' END END;
  receipt_lines:=receipt_lines||jsonb_build_array(
   (line-ARRAY['id','catalog_id'])||jsonb_build_object('unit_label',label));
 END LOOP;
 payment:=jsonb_build_object('id',p_operation,'method','cash','amount',
  to_char(total,'FM999999999990.00'),'currency','TJS');
 result:=jsonb_build_object('schema_version',3,'document_kind','physical_checkout',
  'tenant_id',tenant,'branch_id',branch,'register_id',register,'shift_id',sale_row.shift_id,
  'cashier_id',p_actor,'event_id',p_operation,'sale_id',sale_id,'operation_id',p_operation,
  'created_at',replace(to_char(sale_row.created_at AT TIME ZONE 'UTC',
    'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),'.000000Z','Z'),'completed_at',completed,
  'receipt_number',number,'receipt_sequence',seq,'is_test',false,'stock_command_id',p_operation,
  'lines',lines,'payments',jsonb_build_array(payment),
  'total_amount',to_char(total,'FM999999999990.00'),
  'currency','TJS');
 receipt:=jsonb_build_object('schema_version',3,'document_kind','physical_receipt',
  'receipt_number',number,'receipt_sequence',seq,'completed_at',completed,'is_test',false,
  'lines',receipt_lines,'payments',jsonb_build_array(payment-ARRAY['id','currency']),
  'total_amount',to_char(total,'FM999999999990.00'),'currency','TJS');
 UPDATE public.sale SET total_amount=total WHERE tenant_id=tenant AND id=sale_id;
 INSERT INTO public.pos_physical_checkout(tenant_id,id,sale_id,branch_id,register_id,shift_id,
  cashier_user_id,cash_intent_id,stock_command_id,payment_id,payment_journal_version,
  request_hash,result_payload,receipt_payload,created_by)
 VALUES(tenant,p_operation,sale_id,branch,register,sale_row.shift_id,p_actor,p_operation,
  p_operation,p_operation,2,cash_hash,result,receipt,p_actor);
 INSERT INTO public.sale_payment(id,tenant_id,sale_id,payment_method,amount,currency,
  operation_id,operation_hash)
 VALUES(p_operation,tenant,sale_id,'cash',total,'TJS',p_operation,cash_hash);
 INSERT INTO public.pos_unit_checkout_payment_component(tenant_id,id,physical_checkout_id,
  sale_id,method,amount,currency,created_by)
 VALUES(tenant,p_operation,p_operation,sale_id,'cash',total,'TJS',p_actor);
 UPDATE public.sale SET status='completed',completed_at=completed::TIMESTAMPTZ,
  receipt_number=number,receipt_seq=seq,receipt_snapshot=receipt
 WHERE tenant_id=tenant AND id=sale_id;
 PERFORM public.assert_physical_checkout(tenant,sale_id,true);
 PERFORM public.emit_native_physical_retail_event_v1(tenant,sale_id,p_operation);
 complete_hash:=encode(sha256(convert_to(
  public.edge_canonical_jsonb(p_payload-'plan'),'UTF8')),'hex');
 wrapper:=jsonb_build_object('profile','retail_cash.completed.v1',
  'command_type','sale.cash.complete',
  'kind','physical_cash','scope',scope,'confirmation',p_payload->'confirmation','result',result,
  'operation_id',p_operation,'sale_id',sale_id,'receipt_number',number,
  'total_amount',to_char(total,'FM999999999990.00'),'currency','TJS');
 SELECT id INTO STRICT identity_id FROM public.edge_cash_node_identity
 WHERE database_role=SESSION_USER;
 INSERT INTO public.edge_cash_command(tenant_id,branch_id,operation_id,edge_identity_id,
  activation_id,edge_node_id,writer_epoch,register_id,cashier_user_id,sale_id,sale_status,
  receipt_number,total_amount,currency,command_type,request_hash,
  result_payload,result_hash,created_by)
 VALUES(tenant,branch,p_operation,identity_id,p_activation,(scope->>'edge_node_id')::UUID,p_epoch,
  register,p_actor,sale_id,'completed',number,total,'TJS','sale.cash.complete',
  complete_hash,wrapper,
  encode(sha256(convert_to(public.edge_canonical_jsonb(wrapper),'UTF8')),'hex'),p_actor);
 SET CONSTRAINTS ALL IMMEDIATE;
 IF NOT public.native_edge_physical_actor_v1(tenant,branch,register,p_actor)
   OR stock_date IS DISTINCT FROM (clock_timestamp() AT TIME ZONE report_timezone)::DATE THEN
  RAISE EXCEPTION 'Native physical authority changed' USING ERRCODE='42501'; END IF;
 RETURN wrapper;
END $function$;
"""

# Shared historical functions also run for app/support/catalog sessions, which
# intentionally have no EXECUTE on private native helpers. Definer functions can
# use CASE to avoid the runtime call. Invoker functions additionally require a
# separate PL/pgSQL statement: expression initialization checks every call's ACL.
NATIVE_SESSION = "SESSION_USER ~ '^aurum_edge_node_[0-9a-f]{32}$'"
CONTEXT = (
    f"(CASE WHEN {NATIVE_SESSION} THEN public.native_edge_physical_context_v1() " "ELSE false END)"
)
ACTOR_INVOCER = "v_native_physical"
CLOUD_WRITER = """epoch.state='active' AND epoch.capability='cloud_full'
  AND epoch.allowed_register_id IS NULL
  AND node.node_kind='cloud' AND node.mode='cloud_writer' AND node.status='active'"""
NATIVE_WRITER = """epoch.state='active' AND epoch.capability='cash_sale_v1'
  AND epoch.allowed_register_id=identity.register_id
  AND node.node_kind='edge' AND node.mode='edge_writer' AND node.status='active'"""


def _cloud_or_native(register: str, physical: str) -> str:
    return f"""(({CLOUD_WRITER}) OR ({physical} AND {CONTEXT}
  AND epoch.capability='cash_sale_v1' AND epoch.state='active'
  AND epoch.allowed_register_id={register}
  AND node.node_kind='edge' AND node.mode='edge_writer' AND node.status='active'))"""


INVOKER_PRELUDE = f"""BEGIN
 IF current_user='aurum_edge_cash_owner' AND {NATIVE_SESSION} THEN
  v_native_physical:=public.native_edge_physical_context_v1();
 END IF;
"""
PATCHES: tuple[tuple[str, str, str], ...] = (
    *(
        (signature, old, new)
        for signature in (
            "trg_unit_cart_v2_actor()",
            "trg_physical_checkout_guard()",
            "trg_guard_sale_immutability()",
        )
        for old, new in (
            ("DECLARE", "DECLARE\n v_native_physical BOOLEAN:=false;"),
            ("BEGIN\n", INVOKER_PRELUDE),
        )
    ),
    (
        "allocate_register_receipt(uuid,uuid)",
        """    p_tenant_id,
    v_branch_id,
    p_register_id,
    false
  );""",
        f"""    p_tenant_id,
    v_branch_id,
    p_register_id,
    CASE WHEN {NATIVE_SESSION} THEN public.native_edge_physical_actor_v1(
      p_tenant_id,v_branch_id,p_register_id,public.current_app_user_id())
    ELSE false END
  );""",
    ),
    (
        "trg_unit_cart_v2_actor()",
        "current_user<>'aurum_app'",
        f"(current_user<>'aurum_app' AND NOT {ACTOR_INVOCER})",
    ),
    (
        "trg_physical_stock_checkout_binding()",
        "session_user<>'aurum_app'",
        f"(session_user<>'aurum_app' AND NOT {CONTEXT})",
    ),
    (
        "trg_physical_checkout_guard()",
        "current_user<>'aurum_app'",
        f"(current_user<>'aurum_app' AND NOT {ACTOR_INVOCER})",
    ),
    (
        "trg_guard_sale_immutability()",
        "current_user = 'aurum_app' AND TG_OP = 'UPDATE'",
        f"(current_user = 'aurum_app' OR {ACTOR_INVOCER}) AND TG_OP = 'UPDATE'",
    ),
    (
        "trg_unit_cart_payment_boundary()",
        """WHERE tenant_id=v_tenant AND checkout_sale_id=v_sale_id) THEN
  IF session_user<>'aurum_app'""",
        f"""WHERE tenant_id=v_tenant AND checkout_sale_id=v_sale_id) THEN
  IF (session_user<>'aurum_app' AND NOT {CONTEXT})""",
    ),
    (
        "trg_unit_checkout_payment_component_guard()",
        "session_user<>'aurum_app'",
        f"(session_user<>'aurum_app' AND NOT {CONTEXT})",
    ),
    (
        "trg_capture_retail_checkout_origin()",
        "IF SESSION_USER<>'aurum_app' THEN RETURN NEW; END IF;",
        f"IF SESSION_USER<>'aurum_app' AND NOT (TG_TABLE_NAME='pos_physical_checkout' "
        f"AND {CONTEXT}) THEN RETURN NEW; END IF;",
    ),
    (
        "trg_capture_retail_checkout_origin()",
        "NEW.tenant_id,NEW.branch_id,NEW.register_id,false);",
        f"NEW.tenant_id,NEW.branch_id,NEW.register_id,{CONTEXT});",
    ),
    (
        "trg_capture_retail_checkout_origin()",
        CLOUD_WRITER,
        _cloud_or_native("NEW.register_id", "TG_TABLE_NAME='pos_physical_checkout'"),
    ),
    (
        "trg_retail_checkout_origin_guard()",
        "SESSION_USER<>'aurum_app'",
        f"(SESSION_USER<>'aurum_app' AND NOT {CONTEXT})",
    ),
    (
        "trg_retail_checkout_origin_guard()",
        CLOUD_WRITER,
        _cloud_or_native("NEW.register_id", "NEW.source_kind='physical'"),
    ),
    (
        "trg_retail_event_capture()",
        "SESSION_USER='aurum_app' AND CURRENT_USER='aurum_schema_owner'",
        f"(SESSION_USER='aurum_app' OR (NEW.source_kind='physical' AND {CONTEXT})) "
        "AND CURRENT_USER='aurum_schema_owner'",
    ),
    (
        "assert_retail_event_source(uuid,uuid,uuid)",
        "SESSION_USER<>'aurum_app'",
        f"(SESSION_USER<>'aurum_app' AND NOT {CONTEXT})",
    ),
    (
        "assert_retail_event_source(uuid,uuid,uuid)",
        "epoch.state='active' AND epoch.capability='cloud_full'"
        " AND epoch.allowed_register_id IS NULL\n"
        "   AND node.node_kind='cloud' AND node.mode='cloud_writer' AND node.status='active'",
        _cloud_or_native("origin.register_id", "origin.source_kind='physical'"),
    ),
    (
        "store_retail_checkout_event(uuid,uuid,uuid,bytea,text,text,text)",
        "SESSION_USER<>'aurum_app'",
        f"(SESSION_USER<>'aurum_app' AND NOT {CONTEXT})",
    ),
    (
        "reserve_retail_checkout_event_position(uuid,uuid,uuid)",
        " RETURN QUERY SELECT * FROM public.reserve_sync_event_position"
        "(p_tenant,origin.branch_id);",
        f""" IF origin.source_kind='physical' AND {CONTEXT} THEN
  RETURN QUERY UPDATE public.sync_stream stream SET last_sequence=stream.last_sequence+1
   WHERE stream.tenant_id=p_tenant AND stream.branch_id=origin.branch_id
   RETURNING stream.id,stream.writer_node_id,stream.writer_epoch,stream.last_sequence,
    stream.current_checksum,stream.current_projection_checksum;
 ELSE
  RETURN QUERY SELECT * FROM public.reserve_sync_event_position(p_tenant,origin.branch_id);
 END IF;""",
    ),
    (
        "trg_physical_stock_capture()",
        "IF SESSION_USER NOT IN ('aurum_app','aurum_catalog_worker')"
        " THEN unsupported:=true; END IF;",
        f"IF SESSION_USER NOT IN ('aurum_app','aurum_catalog_worker') "
        f"AND NOT (kind='dispense' AND {CONTEXT}) THEN unsupported:=true; END IF;",
    ),
    (
        "trg_physical_stock_capture()",
        """e.state='active' AND e.capability='cloud_full'
    AND e.allowed_register_id IS NULL AND n.node_kind='cloud' AND n.mode='cloud_writer'
    AND n.status='active'""",
        f"""((e.state='active' AND e.capability='cloud_full'
    AND e.allowed_register_id IS NULL AND n.node_kind='cloud' AND n.mode='cloud_writer'
    AND n.status='active') OR (kind='dispense' AND {CONTEXT}
    AND e.state='active' AND e.capability='cash_sale_v1'
    AND n.node_kind='edge' AND n.mode='edge_writer' AND n.status='active'
    AND e.allowed_register_id=n.register_id))""",
    ),
    (
        "emit_physical_stock_source(uuid,uuid)",
        "PERFORM public.assert_current_branch_writer(saved.tenant_id,saved.branch_id,NULL,false);",
        f"""IF saved.source_kind='dispense' AND {CONTEXT} THEN
  PERFORM public.assert_current_branch_writer(saved.tenant_id,saved.branch_id,
   (SELECT sale.register_id FROM public.unit_stock_cart_command_v2 command
    JOIN public.sale sale ON sale.tenant_id=command.tenant_id AND sale.id=command.checkout_sale_id
    WHERE command.tenant_id=saved.tenant_id AND command.id=saved.source_id),true);
 ELSE
  PERFORM public.assert_current_branch_writer(saved.tenant_id,saved.branch_id,NULL,false);
 END IF;""",
    ),
)


def _patch(signature: str, old: str, new: str) -> None:
    connection = op.get_bind()
    definition = connection.exec_driver_sql(
        f"SELECT pg_get_functiondef('public.{signature}'::regprocedure)"
    ).scalar_one()
    if not isinstance(definition, str) or definition.count(old) != 1:
        raise RuntimeError(f"Native physical historical function changed: {signature}")
    connection.exec_driver_sql(definition.replace(old, new, 1))


FUNCTIONS = (
    "edge_physical_uuid5_v1(uuid,text)",
    "edge_physical_pack_take_v1(jsonb,jsonb,text,bigint)",
    "edge_physical_post_v1(uuid,uuid,uuid,uuid,uuid,uuid,"
    "text,integer,bigint,bigint,jsonb,jsonb,jsonb)",
    "edge_physical_allocate_v1(uuid,uuid,jsonb,date,jsonb)",
    "native_edge_physical_actor_v1(uuid,uuid,uuid,uuid)",
    "native_edge_physical_context_v1()",
    "lock_native_physical_gates_v1(uuid,uuid,uuid,uuid)",
    "emit_native_physical_retail_event_v1(uuid,uuid,uuid)",
    "complete_edge_physical_cash_v1(uuid,uuid,bigint,uuid,jsonb,text)",
)
CORE = (
    "unit_pack_v2_remaining(jsonb,jsonb)",
    "unit_pack_v2_initial_state(jsonb)",
    "unit_pack_v2_assert_transition(jsonb,jsonb,jsonb,text,bigint)",
    "unit_incoming_uuid5(text)",
    "assert_unit_cart_v2_complete(uuid,uuid)",
    "allocate_register_receipt(uuid,uuid)",
    "assert_retail_event_source(uuid,uuid,uuid)",
    "reserve_retail_checkout_event_position(uuid,uuid,uuid)",
    "store_retail_checkout_event(uuid,uuid,uuid,bytea,text,text,text)",
    "retail_event_hash(jsonb)",
    "retail_event_canonical_json(jsonb)",
)
INSERT_COLUMNS = {
    "unit_stock_cart_command_v2": (
        "tenant_id,id,branch_id,actor_id,request_hash,retail_version,currency,"
        "created_by,checkout_sale_id"
    ),
    "unit_stock_cart_line_v2": (
        "tenant_id,command_id,line_id,position,catalog_id,packaging,sale_format,quantity,"
        "base_units,created_by,original_retail_unit_price,gross_amount,discount_amount,net_amount"
    ),
    "unit_stock_movement_v2": (
        "id,tenant_id,branch_id,catalog_id,command_id,line_id,lot_id,pack_id,kind,entry_order,"
        "closed_delta,units_taken,sealed_start,sealed_count,before_state,after_state,created_by"
    ),
    "pos_physical_checkout": (
        "tenant_id,id,sale_id,branch_id,register_id,shift_id,cashier_user_id,cash_intent_id,"
        "stock_command_id,payment_id,payment_journal_version,request_hash,result_payload,"
        "receipt_payload,created_by"
    ),
    "pos_unit_checkout_payment_component": (
        "tenant_id,id,physical_checkout_id,sale_id,method,amount,currency,created_by"
    ),
}
READ_COLUMNS = {
    "pos_unit_cash_intent": "request_hash",
    "unit_stock_lot_v2": "initial_closed_packs,initial_opened_packs",
    "unit_stock_cart_command_v2": "tenant_id,id,branch_id,actor_id,checkout_sale_id",
    "unit_stock_cart_line_v2": (
        "tenant_id,command_id,line_id,position,catalog_id,packaging,sale_format,base_units"
    ),
}


def _scope(table: str) -> str:
    if table == "unit_stock_cart_line_v2":
        return f"""tenant_id=public.current_tenant_id() AND {CONTEXT} AND EXISTS(
          SELECT 1 FROM public.unit_stock_cart_command_v2 command
          WHERE command.tenant_id={table}.tenant_id AND command.id={table}.command_id
           AND command.checkout_sale_id IS NOT NULL)"""
    if table == "pos_unit_checkout_payment_component":
        return f"""tenant_id=public.current_tenant_id() AND {CONTEXT}
          AND mixed_checkout_id IS NULL AND physical_checkout_id IS NOT NULL
          AND EXISTS(SELECT 1 FROM public.pos_physical_checkout marker
            WHERE marker.tenant_id={table}.tenant_id AND marker.id={table}.physical_checkout_id
             AND marker.sale_id={table}.sale_id)"""
    return f"""tenant_id=public.current_tenant_id() AND {CONTEXT} AND EXISTS(
      SELECT 1 FROM public.edge_cash_node_identity identity
      WHERE identity.database_role=SESSION_USER AND identity.tenant_id={table}.tenant_id
       AND identity.branch_id={table}.branch_id)"""


def _grants(*, reverse: bool = False) -> None:
    for table, columns in INSERT_COLUMNS.items():
        action = f"REVOKE INSERT ({columns}) ON public.{table} FROM aurum_edge_cash_owner"
        if not reverse:
            action = f"GRANT INSERT ({columns}) ON public.{table} TO aurum_edge_cash_owner"
        op.execute(action)
    for table, columns in READ_COLUMNS.items():
        action = f"REVOKE SELECT ({columns}) ON public.{table} FROM aurum_edge_cash_owner"
        if not reverse:
            action = f"GRANT SELECT ({columns}) ON public.{table} TO aurum_edge_cash_owner"
        op.execute(action)
    for signature in CORE:
        action = "REVOKE EXECUTE" if reverse else "GRANT EXECUTE"
        direction = "FROM" if reverse else "TO"
        op.execute(f"{action} ON FUNCTION public.{signature} {direction} aurum_edge_cash_owner")


DISPATCH_MARKER = "  IF p_operation_id IS NULL"
DISPATCH_COMPLETE = """  IF p_payload->>'profile'='retail_cash.complete.v1' THEN
    RETURN public.complete_edge_physical_cash_v1(
      p_operation_id,p_activation_id,p_writer_epoch,p_cashier_user_id,p_payload,p_request_hash);
  END IF;
""" + DISPATCH_MARKER


def _dispatcher(*, reverse: bool = False) -> None:
    connection = op.get_bind()
    signature = "dispatch_edge_cash_sale_v1(uuid,uuid,bigint,uuid,jsonb,text)"
    execute = connection.exec_driver_sql(
        f"SELECT has_function_privilege('aurum_edge_cash_owner','public.{signature}','EXECUTE')"
    ).scalar_one()
    if connection.exec_driver_sql(
        "SELECT has_schema_privilege('aurum_edge_cash_owner','public','CREATE')"
    ).scalar_one():
        raise RuntimeError("Native owner unexpectedly has schema creation rights")
    op.execute("GRANT CREATE ON SCHEMA public TO aurum_edge_cash_owner")
    op.execute("RESET ROLE")
    op.execute("SET LOCAL ROLE aurum_edge_cash_owner")
    if not execute:
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{signature} TO aurum_edge_cash_owner")
    old, new = (
        (DISPATCH_COMPLETE, DISPATCH_MARKER) if reverse else (DISPATCH_MARKER, DISPATCH_COMPLETE)
    )
    _patch(signature, old, new)
    if not execute:
        op.execute(f"REVOKE EXECUTE ON FUNCTION public.{signature} FROM aurum_edge_cash_owner")
    op.execute("RESET ROLE")
    op.execute("SET LOCAL ROLE aurum_schema_owner")
    op.execute("REVOKE CREATE ON SCHEMA public FROM aurum_edge_cash_owner")


def upgrade() -> None:
    for sql in (ACTOR_SQL, PACK_SQL, POST_SQL, EVENT_SQL, COMPLETE_SQL):
        for statement in sql.split("$function$;"):
            if statement.strip():
                op.get_bind().exec_driver_sql(
                    statement.replace(
                        "SET search_path=pg_catalog,pg_temp AS $function$",
                        "SET search_path=pg_catalog,pg_temp SET row_security=on AS $function$",
                    )
                    + "$function$;"
                )
    for signature in FUNCTIONS:
        op.execute(
            f"REVOKE ALL ON FUNCTION public.{signature} "
            "FROM PUBLIC,aurum_app,aurum_support,aurum_edge_cash_executor,aurum_edge_cash_owner"
        )
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{signature} TO aurum_edge_cash_owner")
    for signature, old, new in PATCHES:
        _patch(signature, old, new)
    for table in INSERT_COLUMNS:
        op.execute(
            f"CREATE POLICY edge_physical_execution_insert ON public.{table} "
            f"FOR INSERT TO aurum_edge_cash_owner WITH CHECK({_scope(table)})"
        )
    for table in ("unit_stock_cart_command_v2", "unit_stock_cart_line_v2"):
        op.execute(
            f"CREATE POLICY edge_physical_execution_read ON public.{table} "
            f"FOR SELECT TO aurum_edge_cash_owner USING({_scope(table)})"
        )
    _grants()
    _dispatcher()


def downgrade() -> None:
    op.execute(
        "LOCK TABLE public.pos_retail_checkout_origin,public.edge_cash_command "
        "IN ACCESS EXCLUSIVE MODE"
    )
    op.execute("""
      DO $guard$ BEGIN
        IF EXISTS(SELECT 1 FROM public.pos_retail_checkout_origin origin
          JOIN public.sync_node node ON node.id=origin.origin_node_id
            AND node.tenant_id=origin.tenant_id AND node.branch_id=origin.branch_id
          WHERE origin.source_kind='physical' AND node.node_kind='edge') THEN
          RAISE EXCEPTION 'Native physical cash history prevents downgrade' USING ERRCODE='55000';
        END IF;
      END $guard$;
    """)
    _dispatcher(reverse=True)
    for signature, old, new in reversed(PATCHES):
        _patch(signature, new, old)
    for table in INSERT_COLUMNS:
        op.execute(f"DROP POLICY edge_physical_execution_insert ON public.{table}")
    for table in ("unit_stock_cart_command_v2", "unit_stock_cart_line_v2"):
        op.execute(f"DROP POLICY edge_physical_execution_read ON public.{table}")
    _grants(reverse=True)
    for signature in reversed(FUNCTIONS):
        op.execute(f"DROP FUNCTION public.{signature}")
