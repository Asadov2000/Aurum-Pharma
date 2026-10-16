"""Journal explicitly selected physical warehouse losses outside sales.

Revision ID: 0204
Revises: 0203
"""

from __future__ import annotations

import re

from alembic import op
from sqlalchemy import text

revision = "0204"
down_revision = "0203"
branch_labels = None
depends_on = None


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


def _definition(signature: str) -> str:
    definition = op.get_bind().scalar(
        text("SELECT pg_get_functiondef(CAST(:name AS regprocedure))"), {"name": signature}
    )
    if not isinstance(definition, str):
        raise RuntimeError(f"Physical write-off requires function {signature}")
    return definition


NORMALIZE_SQL = """
CREATE FUNCTION public.physical_writeoff_selection(p_value JSONB) RETURNS JSONB
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$
DECLARE p JSONB; b JSONB; packs JSONB:='[]'; blisters JSONB; ids UUID[]:='{}';
 numbers BIGINT[]; pid UUID; n BIGINT; u BIGINT; closed BIGINT; loose BIGINT;
BEGIN
 IF p_value IS NULL OR jsonb_typeof(p_value)<>'object'
   OR p_value-ARRAY['closed_packs','opened_packs']<>'{}'
   OR jsonb_typeof(p_value->'closed_packs') IS DISTINCT FROM 'number'
   OR (p_value->>'closed_packs') !~ '^[0-9]+$'
   OR jsonb_typeof(p_value->'opened_packs') IS DISTINCT FROM 'array' THEN
   RAISE EXCEPTION 'physical_writeoff_selection_invalid' USING ERRCODE='23514';
 END IF;
 closed:=(p_value->>'closed_packs')::BIGINT;
 IF closed>99999999999 THEN
   RAISE EXCEPTION 'physical_writeoff_quantity_limit' USING ERRCODE='23514';
 END IF;
 FOR p IN SELECT value FROM jsonb_array_elements(p_value->'opened_packs') LOOP
   IF jsonb_typeof(p)<>'object' OR p-ARRAY['pack_id','blisters','loose_units']<>'{}'
     OR jsonb_typeof(p->'pack_id') IS DISTINCT FROM 'string'
     OR jsonb_typeof(p->'blisters') IS DISTINCT FROM 'array'
     OR jsonb_typeof(p->'loose_units') IS DISTINCT FROM 'number'
     OR (p->>'loose_units') !~ '^[0-9]+$' THEN
     RAISE EXCEPTION 'physical_writeoff_pack_invalid' USING ERRCODE='23514';
   END IF;
   pid:=(p->>'pack_id')::UUID; loose:=(p->>'loose_units')::BIGINT;
   IF pid=ANY(ids) OR loose>99999999999 THEN
     RAISE EXCEPTION 'physical_writeoff_pack_duplicate_or_limit' USING ERRCODE='23514';
   END IF;
   ids:=array_append(ids,pid); numbers:='{}'; blisters:='[]';
   FOR b IN SELECT value FROM jsonb_array_elements(p->'blisters') LOOP
     IF jsonb_typeof(b)<>'object' OR b-ARRAY['number','units']<>'{}'
       OR jsonb_typeof(b->'number') IS DISTINCT FROM 'number'
       OR jsonb_typeof(b->'units') IS DISTINCT FROM 'number'
       OR (b->>'number') !~ '^[0-9]+$' OR (b->>'units') !~ '^[0-9]+$' THEN
       RAISE EXCEPTION 'physical_writeoff_blister_invalid' USING ERRCODE='23514';
     END IF;
     n:=(b->>'number')::BIGINT;u:=(b->>'units')::BIGINT;
     IF n<1 OR n>10000 OR u<1 OR u>99999999999 OR n=ANY(numbers) THEN
       RAISE EXCEPTION 'physical_writeoff_blister_duplicate_or_limit' USING ERRCODE='23514';
     END IF;
     numbers:=array_append(numbers,n);
     blisters:=blisters||jsonb_build_array(jsonb_build_object('number',n,'units',u));
   END LOOP;
   IF loose=0 AND blisters='[]' THEN
     RAISE EXCEPTION 'physical_writeoff_empty_pack' USING ERRCODE='23514';
   END IF;
   SELECT coalesce(jsonb_agg(value ORDER BY (value->>'number')::BIGINT),'[]') INTO blisters
     FROM jsonb_array_elements(blisters);
   packs:=packs||jsonb_build_array(jsonb_build_object('pack_id',pid,'blisters',blisters,'loose_units',loose));
 END LOOP;
 IF closed=0 AND packs='[]' THEN
   RAISE EXCEPTION 'physical_writeoff_empty_selection' USING ERRCODE='23514';
 END IF;
 SELECT coalesce(jsonb_agg(value ORDER BY (value->>'pack_id')::UUID),'[]') INTO packs
   FROM jsonb_array_elements(packs);
 RETURN jsonb_build_object('closed_packs',closed,'opened_packs',packs);
EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range THEN
 RAISE EXCEPTION 'physical_writeoff_selection_invalid' USING ERRCODE='23514';
END $$;

CREATE FUNCTION public.assert_physical_writeoff_actor(p_tenant UUID,p_branch UUID,p_write BOOLEAN)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE policy public.tenant_access_policy%ROWTYPE; v_status TEXT;
BEGIN
 IF session_user<>'aurum_app' OR p_tenant IS NULL OR p_branch IS NULL
   OR p_tenant IS DISTINCT FROM public.current_tenant_id()
   OR public.is_support_session() OR public.is_tenant_support_session()
   OR NOT EXISTS(SELECT 1 FROM public.app_user WHERE id=public.current_app_user_id() AND status='active')
   OR NOT coalesce(public.tenant_actor_has_scoped_permission(p_tenant,'batches.view',p_branch),false)
   OR NOT coalesce(public.tenant_actor_has_scoped_permission(p_tenant,'batches.write_off',p_branch),false) THEN
   RAISE EXCEPTION 'physical_writeoff_actor_denied' USING ERRCODE='42501';
 END IF;
 IF NOT p_write THEN RETURN; END IF;
 IF current_setting('transaction_isolation')<>'read committed' THEN
   RAISE EXCEPTION 'physical_writeoff_isolation_invalid' USING ERRCODE='23514';
 END IF;
 PERFORM pg_advisory_xact_lock_shared(hashtextextended('tenant-access:'||p_tenant,0));
 SELECT t.status INTO v_status FROM public.tenant t WHERE id=p_tenant;
 SELECT * INTO policy FROM public.tenant_access_policy WHERE tenant_id=p_tenant;
 IF NOT FOUND OR v_status IS NULL OR v_status IN ('readonly','suspended','archived') OR (
   (policy.expires_at IS NULL OR policy.expires_at>statement_timestamp())
   AND (policy.mode='readonly' OR 'inventory'=ANY(policy.blocked_modules))) THEN
   RAISE EXCEPTION 'physical_writeoff_access_denied' USING ERRCODE='42501';
 END IF;
 PERFORM public.assert_current_branch_writer(p_tenant,p_branch,NULL,false);
END $$;

CREATE FUNCTION public.physical_writeoff_result(p_tenant UUID,p_operation UUID) RETURNS JSONB
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT jsonb_build_object('operation_id',id,'branch_id',branch_id,'lot_id',lot_id,
   'reason',reason,'comment',comment,'base_units',base_units::TEXT,
   'closed_packs',closed_packs::TEXT,
   'created_at',to_char(created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'))
 FROM public.physical_writeoff_command WHERE tenant_id=p_tenant AND id=p_operation
$$;

CREATE FUNCTION public.physical_writeoff_status(p_tenant UUID,p_branch UUID,p_operation UUID)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 PERFORM public.assert_physical_writeoff_actor(p_tenant,p_branch,false);
 IF NOT EXISTS(SELECT 1 FROM public.physical_writeoff_command
   WHERE tenant_id=p_tenant AND id=p_operation AND branch_id=p_branch
     AND actor_id=public.current_app_user_id()) THEN RETURN NULL; END IF;
 RETURN public.physical_writeoff_result(p_tenant,p_operation);
END $$;
"""


GUARD_SQL = """
CREATE FUNCTION public.trg_physical_writeoff_immutable() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 IF TG_OP<>'INSERT' THEN
   RAISE EXCEPTION 'physical_writeoff_history_immutable' USING ERRCODE='55000';
 END IF;
 IF NEW.created_by IS DISTINCT FROM public.current_app_user_id() THEN
   RAISE EXCEPTION 'physical_writeoff_actor_denied' USING ERRCODE='42501';
 END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION public.trg_physical_writeoff_entry() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE c public.physical_writeoff_command%ROWTYPE; l public.unit_stock_lot_v2%ROWTYPE;
 p public.unit_stock_opened_pack_v2%ROWTYPE; size INTEGER; offset_units BIGINT; amount NUMERIC;
BEGIN
 SELECT * INTO c FROM public.physical_writeoff_command
   WHERE tenant_id=NEW.tenant_id AND id=NEW.operation_id;
 PERFORM 1 FROM public.tenant_catalog WHERE tenant_id=NEW.tenant_id AND id=NEW.catalog_id FOR NO KEY UPDATE;
 SELECT * INTO l FROM public.unit_stock_lot_v2 WHERE tenant_id=NEW.tenant_id AND id=NEW.lot_id FOR NO KEY UPDATE;
 IF c.id IS NULL OR l.id IS NULL OR l.is_blocked OR c.actor_id IS DISTINCT FROM public.current_app_user_id()
   OR (NEW.branch_id,NEW.lot_id,NEW.created_at) IS DISTINCT FROM (c.branch_id,c.lot_id,c.created_at)
   OR (NEW.branch_id,NEW.catalog_id) IS DISTINCT FROM (l.branch_id,l.catalog_id)
   OR l.cost_version<>1 OR l.purchase_cost_per_pack<0 THEN
   RAISE EXCEPTION 'physical_writeoff_entry_source_invalid' USING ERRCODE='23514';
 END IF;
 size:=(l.packaging->>'units_per_pack')::INTEGER;
 IF NEW.pack_id IS NULL THEN
   IF NEW.closed_delta>=0 OR -NEW.closed_delta>l.closed_packs
     OR NEW.sealed_count IS DISTINCT FROM -NEW.closed_delta
     OR NEW.sealed_start IS DISTINCT FROM l.initial_closed_packs+l.initial_opened_packs-l.closed_packs+1
     OR NEW.units_taken::NUMERIC<>-NEW.closed_delta::NUMERIC*size
     OR NEW.before_state IS NOT NULL OR NEW.after_state IS NOT NULL THEN
     RAISE EXCEPTION 'physical_writeoff_sealed_invalid' USING ERRCODE='23514';
   END IF;
   offset_units:=0;amount:=-NEW.closed_delta::NUMERIC*l.purchase_cost_per_pack;
 ELSE
   SELECT * INTO p FROM public.unit_stock_opened_pack_v2
     WHERE tenant_id=NEW.tenant_id AND id=NEW.pack_id AND lot_id=l.id FOR NO KEY UPDATE;
   IF p.id IS NULL OR NEW.closed_delta<>0 OR NEW.sealed_start IS NOT NULL OR NEW.sealed_count IS NOT NULL
     OR NEW.before_state IS DISTINCT FROM p.state
     OR p.remaining_units<>public.unit_pack_v2_remaining(p.state,l.packaging) THEN
     RAISE EXCEPTION 'physical_writeoff_opened_invalid' USING ERRCODE='23514';
   END IF;
   PERFORM public.unit_pack_v2_assert_transition(NEW.before_state,NEW.after_state,l.packaging,'unit',NEW.units_taken);
   offset_units:=size-p.remaining_units;
   amount:=(div(l.purchase_cost_per_pack*100*(offset_units+NEW.units_taken),size)
     -div(l.purchase_cost_per_pack*100*offset_units,size))/100;
 END IF;
 IF (NEW.cost_unit_offset,NEW.cost_amount,NEW.cost_original_pack_cost,NEW.cost_version)
   IS DISTINCT FROM (offset_units,amount,l.purchase_cost_per_pack,1) THEN
   RAISE EXCEPTION 'physical_writeoff_cost_invalid' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION public.trg_physical_writeoff_apply() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE packaging JSONB;
BEGIN
 IF NEW.closed_delta<>0 THEN
   UPDATE public.unit_stock_lot_v2 SET closed_packs=closed_packs+NEW.closed_delta,
     updated_at=NEW.created_at,updated_by=NEW.created_by WHERE tenant_id=NEW.tenant_id AND id=NEW.lot_id;
 ELSE
   SELECT l.packaging INTO packaging FROM public.unit_stock_lot_v2 l WHERE tenant_id=NEW.tenant_id AND id=NEW.lot_id;
   UPDATE public.unit_stock_opened_pack_v2 SET state=NEW.after_state,
     remaining_units=public.unit_pack_v2_remaining(NEW.after_state,packaging),
     updated_at=NEW.created_at,updated_by=NEW.created_by WHERE tenant_id=NEW.tenant_id AND id=NEW.pack_id;
 END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION public.trg_physical_writeoff_complete() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE c public.physical_writeoff_command%ROWTYPE; item UUID;
 total_units BIGINT; total_closed BIGINT; total_amount NUMERIC;
BEGIN
 SELECT * INTO c FROM public.physical_writeoff_command WHERE tenant_id=NEW.tenant_id AND id=NEW.id;
 SELECT sum(units_taken)::BIGINT,-sum(closed_delta)::BIGINT,sum(cost_amount)
   INTO total_units,total_closed,total_amount FROM public.physical_writeoff_entry
   WHERE tenant_id=c.tenant_id AND operation_id=c.id;
 IF (c.base_units,c.closed_packs,c.total_cost) IS DISTINCT FROM (total_units,total_closed,total_amount) THEN
   RAISE EXCEPTION 'physical_writeoff_incomplete' USING ERRCODE='23514';
 END IF;
 SELECT source_item_id INTO item FROM public.unit_stock_lot_v2 WHERE tenant_id=c.tenant_id AND id=c.lot_id;
 PERFORM public.assert_unit_incoming_v2_source(c.tenant_id,item,true);
 RETURN NEW;
END $$;
"""


WRITER_SQL = """
CREATE FUNCTION public.record_physical_writeoff(p_tenant UUID,p_branch UUID,p_operation UUID,
 p_actor UUID,p_lot UUID,p_reason TEXT,p_comment TEXT,p_selection JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE c public.physical_writeoff_command%ROWTYPE; l public.unit_stock_lot_v2%ROWTYPE;
 p public.unit_stock_opened_pack_v2%ROWTYPE; selection JSONB; chosen JSONB; b JSONB; value JSONB;
 entries JSONB:='[]'; entry JSONB; after_state JSONB; intact JSONB; opened JSONB; selected JSONB;
 hash TEXT; catalog UUID; size INTEGER; blister_size INTEGER; number INTEGER; taken BIGINT; remaining BIGINT;
 closed BIGINT; units BIGINT; total_units BIGINT; loose BIGINT; amount NUMERIC; total_cost NUMERIC;
 offset_units BIGINT; timestamp_at TIMESTAMPTZ; position INTEGER:=0;
BEGIN
 PERFORM public.assert_physical_writeoff_actor(p_tenant,p_branch,true);
 IF p_operation IS NULL OR p_lot IS NULL OR p_actor IS DISTINCT FROM public.current_app_user_id() THEN
   RAISE EXCEPTION 'physical_writeoff_actor_denied' USING ERRCODE='42501';
 END IF;
 IF p_reason IS NULL OR p_reason NOT IN ('expired','damaged','spoiled','theft','other')
   OR length(p_comment)>2000 THEN
   RAISE EXCEPTION 'physical_writeoff_reason_invalid' USING ERRCODE='23514';
 END IF;
 selection:=public.physical_writeoff_selection(p_selection);
 hash:=encode(sha256(convert_to(jsonb_build_object('branch',p_branch,'lot',p_lot,
   'actor',p_actor,'reason',p_reason,'comment',p_comment,'selection',selection)::TEXT,'UTF8')),'hex');
 PERFORM pg_advisory_xact_lock(hashtextextended('physical-writeoff:'||p_tenant||':'||p_operation,0));
 SELECT * INTO c FROM public.physical_writeoff_command WHERE tenant_id=p_tenant AND id=p_operation;
 IF c.id IS NOT NULL THEN
   IF c.request_hash IS DISTINCT FROM hash THEN
     RAISE EXCEPTION 'physical_writeoff_idempotency_conflict' USING ERRCODE='23505';
   END IF;
   RETURN public.physical_writeoff_result(p_tenant,p_operation);
 END IF;
 SELECT catalog_id INTO catalog FROM public.unit_stock_lot_v2
   WHERE tenant_id=p_tenant AND branch_id=p_branch AND id=p_lot;
 IF catalog IS NULL THEN RAISE EXCEPTION 'physical_writeoff_lot_not_found' USING ERRCODE='P0002'; END IF;
 PERFORM 1 FROM public.tenant_catalog WHERE tenant_id=p_tenant AND id=catalog FOR NO KEY UPDATE;
 SELECT * INTO l FROM public.unit_stock_lot_v2
   WHERE tenant_id=p_tenant AND branch_id=p_branch AND id=p_lot FOR NO KEY UPDATE;
 IF l.is_blocked THEN RAISE EXCEPTION 'physical_writeoff_lot_blocked' USING ERRCODE='23514'; END IF;
 PERFORM public.assert_unit_incoming_v2_source(p_tenant,l.source_item_id,true);
 size:=(l.packaging->>'units_per_pack')::INTEGER;
 blister_size:=(l.packaging->>'units_per_blister')::INTEGER;
 closed:=(selection->>'closed_packs')::BIGINT;
 IF closed>l.closed_packs OR l.cost_version<>1 OR l.purchase_cost_per_pack<0 THEN
   RAISE EXCEPTION 'physical_writeoff_sealed_overdraw_or_cost' USING ERRCODE='23514';
 END IF;
 total_units:=closed*size;total_cost:=closed::NUMERIC*l.purchase_cost_per_pack;
 IF closed>0 THEN
   entries:=entries||jsonb_build_array(jsonb_build_object('pack_id',NULL,'closed_delta',-closed,
     'sealed_start',l.initial_closed_packs+l.initial_opened_packs-l.closed_packs+1,'sealed_count',closed,
     'units_taken',total_units,'cost_unit_offset',0,'cost_amount',total_cost));
 END IF;
 FOR chosen IN SELECT x.value FROM jsonb_array_elements(selection->'opened_packs') x LOOP
   SELECT * INTO p FROM public.unit_stock_opened_pack_v2
     WHERE tenant_id=p_tenant AND lot_id=p_lot AND id=(chosen->>'pack_id')::UUID FOR NO KEY UPDATE;
   IF p.id IS NULL OR p.remaining_units<=0 THEN
     RAISE EXCEPTION 'physical_writeoff_pack_unavailable' USING ERRCODE='23514';
   END IF;
   loose:=(chosen->>'loose_units')::BIGINT;intact:='[]';opened:='[]';units:=0;
   IF blister_size IS NULL THEN
     IF chosen->'blisters'<>'[]' OR loose>p.remaining_units THEN
       RAISE EXCEPTION 'physical_writeoff_loose_invalid' USING ERRCODE='23514';
     END IF;
     units:=loose;
     after_state:=jsonb_build_object('intact_blisters',intact,'opened_blisters',opened,'loose_units',p.remaining_units-loose);
   ELSE
     IF loose<>0 THEN RAISE EXCEPTION 'physical_writeoff_blister_identity_required' USING ERRCODE='23514'; END IF;
     FOR b IN SELECT x.value FROM jsonb_array_elements(chosen->'blisters') x LOOP
       number:=(b->>'number')::INTEGER;
       IF NOT p.state->'intact_blisters' @> jsonb_build_array(number)
         AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(p.state->'opened_blisters') x
           WHERE (x.value->>'number')::INTEGER=number) THEN
         RAISE EXCEPTION 'physical_writeoff_blister_unavailable' USING ERRCODE='23514';
       END IF;
     END LOOP;
     FOR value IN SELECT x.value FROM jsonb_array_elements(p.state->'intact_blisters') x LOOP
       number:=value::TEXT::INTEGER;
       SELECT x.value INTO selected FROM jsonb_array_elements(chosen->'blisters') x
         WHERE (x.value->>'number')::INTEGER=number;
       taken:=coalesce((selected->>'units')::BIGINT,0);remaining:=blister_size-taken;
       IF remaining<0 THEN RAISE EXCEPTION 'physical_writeoff_blister_overdraw' USING ERRCODE='23514'; END IF;
       units:=units+taken;
       IF taken=0 THEN intact:=intact||jsonb_build_array(number);
       ELSIF remaining>0 THEN opened:=opened||jsonb_build_array(jsonb_build_object('number',number,'remaining_units',remaining)); END IF;
     END LOOP;
     FOR value IN SELECT x.value FROM jsonb_array_elements(p.state->'opened_blisters') x LOOP
       number:=(value->>'number')::INTEGER;
       SELECT x.value INTO selected FROM jsonb_array_elements(chosen->'blisters') x
         WHERE (x.value->>'number')::INTEGER=number;
       taken:=coalesce((selected->>'units')::BIGINT,0);remaining:=(value->>'remaining_units')::BIGINT-taken;
       IF remaining<0 THEN RAISE EXCEPTION 'physical_writeoff_blister_overdraw' USING ERRCODE='23514'; END IF;
       units:=units+taken;
       IF remaining>0 THEN opened:=opened||jsonb_build_array(jsonb_build_object('number',number,'remaining_units',remaining)); END IF;
     END LOOP;
     SELECT coalesce(jsonb_agg(x.value ORDER BY (x.value->>'number')::INTEGER),'[]') INTO opened FROM jsonb_array_elements(opened) x;
     SELECT coalesce(jsonb_agg(x.value ORDER BY x.value::TEXT::INTEGER),'[]') INTO intact FROM jsonb_array_elements(intact) x;
     after_state:=jsonb_build_object('intact_blisters',intact,'opened_blisters',opened,'loose_units',0);
   END IF;
   PERFORM public.unit_pack_v2_assert_transition(p.state,after_state,l.packaging,'unit',units);
   offset_units:=size-p.remaining_units;
   amount:=(div(l.purchase_cost_per_pack*100*(offset_units+units),size)
     -div(l.purchase_cost_per_pack*100*offset_units,size))/100;
   total_units:=total_units+units;total_cost:=total_cost+amount;
   entries:=entries||jsonb_build_array(jsonb_build_object('pack_id',p.id,'closed_delta',0,
     'before_state',p.state,'after_state',after_state,'units_taken',units,
     'cost_unit_offset',offset_units,'cost_amount',amount));
 END LOOP;
 IF total_units NOT BETWEEN 1 AND 99999999999 OR total_cost NOT BETWEEN 0 AND 999999999999.99 THEN
   RAISE EXCEPTION 'physical_writeoff_total_limit' USING ERRCODE='23514';
 END IF;
 -- Permissions or the actor may have changed while waiting for physical locks.
 PERFORM public.assert_physical_writeoff_actor(p_tenant,p_branch,true);
 -- Lock waits must not backdate a removal ahead of the source state it consumed.
 timestamp_at:=clock_timestamp();
 INSERT INTO public.physical_writeoff_command(tenant_id,id,branch_id,lot_id,actor_id,reason,comment,
   selection,request_hash,base_units,closed_packs,total_cost,created_at,created_by)
 VALUES(p_tenant,p_operation,p_branch,p_lot,p_actor,p_reason,p_comment,selection,hash,total_units,closed,total_cost,timestamp_at,p_actor);
 FOR entry IN SELECT x.value FROM jsonb_array_elements(entries) x LOOP
   INSERT INTO public.physical_writeoff_entry(tenant_id,operation_id,entry_order,branch_id,catalog_id,
     lot_id,pack_id,sealed_start,sealed_count,closed_delta,units_taken,before_state,after_state,
     cost_unit_offset,cost_amount,cost_original_pack_cost,cost_version,created_at,created_by)
   VALUES(p_tenant,p_operation,position,p_branch,l.catalog_id,p_lot,(entry->>'pack_id')::UUID,
     (entry->>'sealed_start')::BIGINT,(entry->>'sealed_count')::BIGINT,(entry->>'closed_delta')::BIGINT,
     (entry->>'units_taken')::BIGINT,entry->'before_state',entry->'after_state',
     (entry->>'cost_unit_offset')::BIGINT,(entry->>'cost_amount')::NUMERIC,l.purchase_cost_per_pack,1,timestamp_at,p_actor);
   position:=position+1;
 END LOOP;
 PERFORM public.assert_unit_incoming_v2_source(p_tenant,l.source_item_id,true);
 RETURN public.physical_writeoff_result(p_tenant,p_operation);
END $$;
"""


FUNCTIONS = (
    "physical_writeoff_selection(jsonb)",
    "assert_physical_writeoff_actor(uuid,uuid,boolean)",
    "physical_writeoff_result(uuid,uuid)",
    "physical_writeoff_status(uuid,uuid,uuid)",
    "trg_physical_writeoff_immutable()",
    "trg_physical_writeoff_entry()",
    "trg_physical_writeoff_apply()",
    "trg_physical_writeoff_complete()",
    "record_physical_writeoff(uuid,uuid,uuid,uuid,uuid,text,text,jsonb)",
)


def _patch_balances(*, upgrade: bool) -> None:
    signatures = (
        "public.trg_unit_stock_v2_guard()",
        "public.assert_unit_incoming_v2_source(uuid,uuid,boolean)",
        "public.assert_unit_legacy_transition_complete(uuid,uuid)",
        "public.assert_unit_legacy_opened_complete(uuid,uuid)",
    )
    pattern = re.compile(
        r"\(SELECT coalesce\(sum\((closed_delta|units_taken)\),0\)\s+"
        r"FROM public\.unit_stock_movement_v2\s+WHERE tenant_id=(OLD\.tenant_id|p_tenant) "
        r"AND lot_id=(OLD\.id|l\.id)\)"
    )
    for signature in signatures:
        sql = _definition(signature)
        matches = list(pattern.finditer(sql))
        if len(matches) != (1 if "trg_unit_stock_v2_guard" in signature else 2):
            raise RuntimeError(f"Unexpected physical conservation guard: {signature}")
        for match in reversed(matches):
            addition = (
                f"+(SELECT coalesce(sum({match[1]}),0) FROM public.physical_writeoff_entry "
                f"WHERE tenant_id={match[2]} AND lot_id={match[3]})"
            )
            if upgrade:
                sql = sql[: match.end()] + addition + sql[match.end() :]
            else:
                if sql[match.end() : match.end() + len(addition)] != addition:
                    raise RuntimeError(f"Missing physical write-off conservation: {signature}")
                sql = sql[: match.end()] + sql[match.end() + len(addition) :]
        op.get_bind().exec_driver_sql(sql)


def _patch_reports_audit(*, upgrade: bool) -> None:
    # Existing report grouping and permissions also apply to the write-off union.
    report_old = "FROM sources l JOIN public.unit_stock_movement_v2 m"
    report_new = """FROM sources l JOIN (
            SELECT tenant_id,lot_id,branch_id,catalog_id,closed_delta,units_taken,cost_amount,cost_version,created_at
              FROM public.unit_stock_movement_v2
            UNION ALL
            SELECT tenant_id,lot_id,branch_id,catalog_id,closed_delta,units_taken,cost_amount,cost_version,created_at
              FROM public.physical_writeoff_entry
          ) m"""
    audit_old = "IF v_result IS NULL THEN RETURN NULL; END IF;"
    audit_new = audit_old + """
      IF p_table_name IN ('physical_writeoff_command','physical_writeoff_entry') THEN
        RETURN v_result-ARRAY['total_cost','cost_amount','cost_original_pack_cost',
          'cost_unit_offset','cost_version','comment'];
      END IF;"""
    for signature, before, after in (
        ("public.pos_stock_on_date_for_report(uuid,timestamptz,uuid)", report_old, report_new),
        ("public.audit_redact_table_snapshot(text,jsonb)", audit_old, audit_new),
    ):
        sql = _definition(signature)
        old, new = (before, after) if upgrade else (after, before)
        if sql.count(old) != 1:
            raise RuntimeError(f"Unexpected physical write-off integration: {signature}")
        op.get_bind().exec_driver_sql(sql.replace(old, new, 1))


def upgrade() -> None:
    _sql("""
      CREATE TABLE public.physical_writeoff_command (
        tenant_id UUID NOT NULL,id UUID NOT NULL,branch_id UUID NOT NULL,lot_id UUID NOT NULL,
        actor_id UUID NOT NULL,reason TEXT NOT NULL CHECK(reason IN ('expired','damaged','spoiled','theft','other')),
        comment TEXT CHECK(length(comment)<=2000),selection JSONB NOT NULL,
        request_hash TEXT NOT NULL CHECK(request_hash ~ '^[0-9a-f]{64}$'),
        base_units BIGINT NOT NULL CHECK(base_units BETWEEN 1 AND 99999999999),
        closed_packs BIGINT NOT NULL CHECK(closed_packs BETWEEN 0 AND 99999999999),
        total_cost NUMERIC(14,2) NOT NULL CHECK(total_cost>=0),
        created_at TIMESTAMPTZ NOT NULL,created_by UUID NOT NULL,
        PRIMARY KEY(tenant_id,id),
        FOREIGN KEY(tenant_id,lot_id) REFERENCES public.unit_stock_lot_v2(tenant_id,id)
      );
      CREATE TABLE public.physical_writeoff_entry (
        id UUID NOT NULL DEFAULT gen_random_uuid(),
        tenant_id UUID NOT NULL,operation_id UUID NOT NULL,entry_order INTEGER NOT NULL CHECK(entry_order>=0),
        branch_id UUID NOT NULL,catalog_id UUID NOT NULL,lot_id UUID NOT NULL,pack_id UUID,
        sealed_start BIGINT CHECK(sealed_start>0),sealed_count BIGINT CHECK(sealed_count>0),
        closed_delta BIGINT NOT NULL CHECK(closed_delta<=0),
        units_taken BIGINT NOT NULL CHECK(units_taken BETWEEN 1 AND 99999999999),
        before_state JSONB,after_state JSONB,cost_unit_offset BIGINT NOT NULL CHECK(cost_unit_offset BETWEEN 0 AND 9999),
        cost_amount NUMERIC(14,2) NOT NULL CHECK(cost_amount>=0),
        cost_original_pack_cost NUMERIC(14,2) NOT NULL CHECK(cost_original_pack_cost>=0),
        cost_version INTEGER NOT NULL CHECK(cost_version=1),created_at TIMESTAMPTZ NOT NULL,created_by UUID NOT NULL,
        PRIMARY KEY(tenant_id,operation_id,entry_order),
        FOREIGN KEY(tenant_id,operation_id) REFERENCES public.physical_writeoff_command(tenant_id,id),
        FOREIGN KEY(tenant_id,lot_id,branch_id,catalog_id)
          REFERENCES public.unit_stock_lot_v2(tenant_id,id,branch_id,catalog_id),
        FOREIGN KEY(tenant_id,pack_id) REFERENCES public.unit_stock_opened_pack_v2(tenant_id,id)
      );
      CREATE INDEX ix_physical_writeoff_lot ON public.physical_writeoff_entry(tenant_id,lot_id,created_at);
    """)
    for table in ("physical_writeoff_command", "physical_writeoff_entry"):
        _sql(f"""
          ALTER TABLE public.{table} OWNER TO aurum_schema_owner;
          ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY;
          ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY;
          REVOKE ALL ON public.{table} FROM PUBLIC,aurum_app,aurum_support;
          GRANT SELECT ON public.{table} TO aurum_support;
          CREATE POLICY schema_owner ON public.{table} TO aurum_schema_owner USING(true) WITH CHECK(true);
          CREATE POLICY support_read ON public.{table} FOR SELECT TO aurum_support USING(true);
        """)
    for sql in (NORMALIZE_SQL, GUARD_SQL, WRITER_SQL):
        _sql(sql)
    for signature in FUNCTIONS:
        _sql(f"""
          ALTER FUNCTION public.{signature} OWNER TO aurum_schema_owner;
          REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC,aurum_app,aurum_support;
        """)
    for signature in (FUNCTIONS[3], FUNCTIONS[-1]):
        _sql(f"GRANT EXECUTE ON FUNCTION public.{signature} TO aurum_app;")
    _patch_balances(upgrade=True)
    _patch_reports_audit(upgrade=True)
    for table in ("physical_writeoff_command", "physical_writeoff_entry"):
        _sql(f"""
          CREATE TRIGGER a00_tenant_access BEFORE INSERT OR UPDATE OR DELETE ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION public.trg_enforce_tenant_access('inventory');
          CREATE TRIGGER a10_physical_writeoff_immutable BEFORE INSERT OR UPDATE OR DELETE ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION public.trg_physical_writeoff_immutable();
          CREATE TRIGGER z90_audit AFTER INSERT OR UPDATE OR DELETE ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION public.trg_audit_log();
        """)
    _sql("""
      CREATE TRIGGER a11_physical_writeoff_entry BEFORE INSERT ON public.physical_writeoff_entry
        FOR EACH ROW EXECUTE FUNCTION public.trg_physical_writeoff_entry();
      CREATE TRIGGER b10_physical_writeoff_apply AFTER INSERT ON public.physical_writeoff_entry
        FOR EACH ROW EXECUTE FUNCTION public.trg_physical_writeoff_apply();
      CREATE CONSTRAINT TRIGGER physical_writeoff_complete AFTER INSERT ON public.physical_writeoff_command
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.trg_physical_writeoff_complete();
    """)


def downgrade() -> None:
    # Dropping history while keeping its stock effects would corrupt provenance.
    # A downgrade is permitted only while this migration has no posted commands.
    _sql("""
      LOCK TABLE public.physical_writeoff_command,public.physical_writeoff_entry IN ACCESS EXCLUSIVE MODE;
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.physical_writeoff_command) THEN
          RAISE EXCEPTION 'physical_writeoff_downgrade_requires_empty_history' USING ERRCODE='55000';
        END IF;
      END $$;
    """)
    _patch_reports_audit(upgrade=False)
    _patch_balances(upgrade=False)
    _sql("DROP TABLE public.physical_writeoff_entry; DROP TABLE public.physical_writeoff_command;")
    for signature in reversed(FUNCTIONS):
        _sql(f"DROP FUNCTION public.{signature};")
