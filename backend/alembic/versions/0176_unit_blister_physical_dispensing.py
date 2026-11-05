"""Journal physical carton, blister and unit dispensing without POS activation.

Revision ID: 0176
Revises: 0175
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from types import ModuleType

from alembic import op

revision: str = "0176"
down_revision: str | Sequence[str] | None = "0175"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
    "unit_stock_cart_command_v2",
    "unit_stock_cart_line_v2",
    "unit_stock_opened_pack_v2",
    "unit_stock_movement_v2",
)
FUNCTIONS = (
    "unit_pack_v2_remaining(jsonb,jsonb)",
    "unit_pack_v2_initial_state(jsonb)",
    "unit_pack_v2_assert_transition(jsonb,jsonb,jsonb,text,bigint)",
    "unit_cart_v2_fingerprint(uuid,uuid)",
    "assert_unit_cart_v2_complete(uuid,uuid)",
    "trg_unit_cart_v2_actor()",
    "trg_unit_cart_v2_guard()",
    "trg_unit_pack_v2_projection_guard()",
    "trg_unit_movement_v2_guard()",
    "trg_unit_movement_v2_apply()",
    "trg_unit_cart_v2_complete()",
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


def _history() -> ModuleType:
    # Reuse the checked loader and restore the exact accepted initialization guards.
    from importlib.util import module_from_spec, spec_from_file_location
    from pathlib import Path

    path = Path(__file__).with_name("0175_received_blister_initialization.py")
    spec = spec_from_file_location("aurum_0176_history", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load physical initialization migration")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


STATE_SQL = r"""
CREATE FUNCTION public.unit_pack_v2_remaining(p_state JSONB,p_packaging JSONB)
RETURNS BIGINT LANGUAGE plpgsql IMMUTABLE SECURITY DEFINER
SET search_path=pg_catalog,pg_temp AS $$
DECLARE
 v_p INTEGER:=(p_packaging->>'units_per_pack')::INTEGER;
 v_b INTEGER:=(p_packaging->>'units_per_blister')::INTEGER;
 v_loose INTEGER;
 v_total BIGINT;
 v_value JSONB;
 v_number INTEGER;
 v_remaining INTEGER;
 v_seen INTEGER[]:=ARRAY[]::INTEGER[];
 v_previous_number INTEGER:=0;
BEGIN
 IF p_state IS NULL OR jsonb_typeof(p_state)<>'object'
   OR NOT p_state ?& ARRAY['intact_blisters','opened_blisters','loose_units']
   OR p_state-ARRAY['intact_blisters','opened_blisters','loose_units']<>'{}'::JSONB
   OR jsonb_typeof(p_state->'intact_blisters')<>'array'
   OR jsonb_typeof(p_state->'opened_blisters')<>'array'
   OR jsonb_typeof(p_state->'loose_units')<>'number'
   OR NOT (p_state->>'loose_units' ~ '^[0-9]{1,5}$') THEN
   RAISE EXCEPTION 'unit_pack_v2_state_shape' USING ERRCODE='23514';
 END IF;
 v_loose:=(p_state->>'loose_units')::INTEGER;
 IF v_loose>v_p THEN
   RAISE EXCEPTION 'unit_pack_v2_capacity' USING ERRCODE='23514';
 END IF;
 IF v_b IS NULL THEN
   IF jsonb_array_length(p_state->'intact_blisters')<>0
     OR jsonb_array_length(p_state->'opened_blisters')<>0 THEN
     RAISE EXCEPTION 'unit_pack_v2_unknown_blisters' USING ERRCODE='23514';
   END IF;
   RETURN v_loose;
 END IF;
 IF v_loose<>0 THEN
   RAISE EXCEPTION 'unit_pack_v2_unidentified_units' USING ERRCODE='23514';
 END IF;
 v_total:=0;
 FOR v_value IN SELECT value FROM jsonb_array_elements(p_state->'intact_blisters') LOOP
   IF jsonb_typeof(v_value)<>'number' OR NOT(v_value::TEXT ~ '^[0-9]{1,5}$') THEN
     RAISE EXCEPTION 'unit_pack_v2_blister_number' USING ERRCODE='23514';
   END IF;
   v_number:=v_value::TEXT::INTEGER;
   IF v_number<=v_previous_number OR v_number>v_p/v_b OR v_number=ANY(v_seen) THEN
     RAISE EXCEPTION 'unit_pack_v2_blister_identity' USING ERRCODE='23514';
   END IF;
   v_seen:=array_append(v_seen,v_number);
   v_previous_number:=v_number;
   v_total:=v_total+v_b;
 END LOOP;
 v_previous_number:=0;
 FOR v_value IN SELECT value FROM jsonb_array_elements(p_state->'opened_blisters') LOOP
   IF jsonb_typeof(v_value)<>'object' OR NOT v_value ?& ARRAY['number','remaining_units']
     OR v_value-ARRAY['number','remaining_units']<>'{}'::JSONB
     OR jsonb_typeof(v_value->'number')<>'number'
     OR jsonb_typeof(v_value->'remaining_units')<>'number'
     OR NOT(v_value->>'number' ~ '^[0-9]{1,5}$')
     OR NOT(v_value->>'remaining_units' ~ '^[0-9]{1,5}$') THEN
     RAISE EXCEPTION 'unit_pack_v2_fragment_shape' USING ERRCODE='23514';
   END IF;
   v_number:=(v_value->>'number')::INTEGER;
   v_remaining:=(v_value->>'remaining_units')::INTEGER;
   IF v_number<=v_previous_number OR v_number>v_p/v_b OR v_number=ANY(v_seen)
     OR v_remaining<1 OR v_remaining>=v_b THEN
     RAISE EXCEPTION 'unit_pack_v2_fragment_identity' USING ERRCODE='23514';
   END IF;
   v_seen:=array_append(v_seen,v_number);
   v_previous_number:=v_number;
   v_total:=v_total+v_remaining;
 END LOOP;
 IF v_total>v_p THEN
   RAISE EXCEPTION 'unit_pack_v2_capacity' USING ERRCODE='23514';
 END IF;
 RETURN v_total;
END $$;

CREATE FUNCTION public.unit_pack_v2_initial_state(p_packaging JSONB)
RETURNS JSONB LANGUAGE SQL IMMUTABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT jsonb_build_object(
   'intact_blisters',CASE WHEN p_packaging->>'units_per_blister' IS NULL THEN '[]'::JSONB
     ELSE (SELECT jsonb_agg(n ORDER BY n) FROM generate_series(1,
       (p_packaging->>'units_per_pack')::INTEGER/
       (p_packaging->>'units_per_blister')::INTEGER) AS n) END,
   'opened_blisters','[]'::JSONB,
   'loose_units',CASE WHEN p_packaging->>'units_per_blister' IS NULL
     THEN (p_packaging->>'units_per_pack')::INTEGER ELSE 0 END)
$$;

CREATE FUNCTION public.unit_pack_v2_assert_transition(
 p_before JSONB,p_after JSONB,p_packaging JSONB,p_format TEXT,p_taken BIGINT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE
 v_b INTEGER:=(p_packaging->>'units_per_blister')::INTEGER;
 v_value JSONB;
 v_previous JSONB;
 v_number INTEGER;
BEGIN
 IF p_taken<=0 OR public.unit_pack_v2_remaining(p_before,p_packaging)
     -public.unit_pack_v2_remaining(p_after,p_packaging)<>p_taken THEN
   RAISE EXCEPTION 'unit_pack_v2_transition_quantity' USING ERRCODE='23514';
 END IF;
 IF v_b IS NULL THEN
   IF p_format<>'unit' THEN
     RAISE EXCEPTION 'unit_pack_v2_unknown_format' USING ERRCODE='23514';
   END IF;
   RETURN;
 END IF;
 FOR v_value IN SELECT value FROM jsonb_array_elements(p_after->'intact_blisters') LOOP
   IF NOT p_before->'intact_blisters' @> jsonb_build_array(v_value) THEN
     RAISE EXCEPTION 'unit_pack_v2_blister_restored' USING ERRCODE='23514';
   END IF;
 END LOOP;
 FOR v_value IN SELECT value FROM jsonb_array_elements(p_after->'opened_blisters') LOOP
   v_number:=(v_value->>'number')::INTEGER;
   SELECT value INTO v_previous FROM jsonb_array_elements(p_before->'opened_blisters')
     WHERE (value->>'number')::INTEGER=v_number;
   IF v_previous IS NULL THEN
     IF NOT p_before->'intact_blisters' @> jsonb_build_array(v_number) THEN
       RAISE EXCEPTION 'unit_pack_v2_fragment_restored' USING ERRCODE='23514';
     END IF;
   ELSIF (v_value->>'remaining_units')::INTEGER>(v_previous->>'remaining_units')::INTEGER THEN
     RAISE EXCEPTION 'unit_pack_v2_fragment_increased' USING ERRCODE='23514';
   END IF;
 END LOOP;
 IF p_format='blister' AND (
   p_before->'opened_blisters' IS DISTINCT FROM p_after->'opened_blisters'
   OR p_taken%v_b<>0) THEN
   RAISE EXCEPTION 'unit_pack_v2_fragment_sold_as_blister' USING ERRCODE='23514';
 END IF;
 IF p_format NOT IN ('blister','unit') THEN
   RAISE EXCEPTION 'unit_pack_v2_transition_format' USING ERRCODE='23514';
 END IF;
END $$;
"""

HASH_SQL = """
CREATE FUNCTION public.unit_cart_v2_fingerprint(p_tenant UUID,p_command UUID)
RETURNS TEXT LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE
 v_branch UUID;
 v_lines TEXT;
 v_payload TEXT;
BEGIN
 SELECT branch_id INTO v_branch FROM public.unit_stock_cart_command_v2
   WHERE tenant_id=p_tenant AND id=p_command;
 SELECT '['||string_agg(replace(json_build_array(
   line_id::TEXT,catalog_id::TEXT,(packaging->>'version')::INTEGER,
   packaging->>'unit',(packaging->>'units_per_pack')::INTEGER,
   (packaging->>'units_per_blister')::INTEGER,(packaging->>'dispense_step')::INTEGER,
   sale_format,quantity)::TEXT,' ',''),',' ORDER BY position)||']'
   INTO v_lines FROM public.unit_stock_cart_line_v2
   WHERE tenant_id=p_tenant AND command_id=p_command;
 v_payload:='["aurum-unit-cart-v2:1",'||to_json(p_tenant::TEXT)::TEXT||','||
   to_json(v_branch::TEXT)::TEXT||','||v_lines||']';
 RETURN encode(public.digest(convert_to(v_payload,'UTF8'),'sha256'),'hex');
END $$;
"""

GUARD_SQL = """
CREATE FUNCTION public.trg_unit_cart_v2_actor() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS $$
DECLARE v_branch UUID;
BEGIN
 IF TG_TABLE_NAME='unit_stock_cart_command_v2' THEN
   v_branch:=NEW.branch_id;
   IF NEW.actor_id IS DISTINCT FROM public.current_app_user_id() THEN
     RAISE EXCEPTION 'unit_cart_v2_actor_mismatch' USING ERRCODE='42501';
   END IF;
 ELSE
   SELECT branch_id INTO v_branch FROM public.unit_stock_cart_command_v2
     WHERE tenant_id=NEW.tenant_id AND id=NEW.command_id;
 END IF;
 IF current_user<>'aurum_app' OR public.is_support_session()
   OR public.current_app_user_id() IS NULL
   OR NEW.tenant_id IS DISTINCT FROM public.current_tenant_id()
   OR NEW.created_by IS DISTINCT FROM public.current_app_user_id() OR v_branch IS NULL
   OR NOT public.tenant_actor_has_scoped_permission(NEW.tenant_id,'pos.sell',v_branch) THEN
   RAISE EXCEPTION 'unit_cart_v2_actor_forbidden' USING ERRCODE='42501';
 END IF;
 NEW.created_at:=statement_timestamp();NEW.updated_at:=NEW.created_at;
 NEW.updated_by:=public.current_app_user_id();
 RETURN NEW;
END $$;

CREATE FUNCTION public.trg_unit_cart_v2_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE
 c public.unit_stock_cart_command_v2%ROWTYPE;
 v_expected NUMERIC;
BEGIN
 IF TG_OP<>'INSERT' THEN
   RAISE EXCEPTION 'unit_cart_v2_history_immutable' USING ERRCODE='55000';
 END IF;
 IF TG_TABLE_NAME='unit_stock_cart_command_v2' THEN RETURN NEW; END IF;
 SELECT * INTO c FROM public.unit_stock_cart_command_v2
   WHERE tenant_id=NEW.tenant_id AND id=NEW.command_id FOR SHARE;
 IF c.id IS NULL OR c.actor_id IS DISTINCT FROM public.current_app_user_id()
   OR EXISTS(SELECT 1 FROM public.unit_stock_movement_v2
     WHERE tenant_id=NEW.tenant_id AND command_id=NEW.command_id) THEN
   RAISE EXCEPTION 'unit_cart_v2_line_command_invalid' USING ERRCODE='23514';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM public.tenant_catalog
   WHERE tenant_id=NEW.tenant_id AND id=NEW.catalog_id AND is_active AND deleted_at IS NULL) THEN
   RAISE EXCEPTION 'unit_cart_v2_catalog_unavailable' USING ERRCODE='23514';
 END IF;
 IF NEW.sale_format='pack' THEN
   v_expected:=NEW.quantity::NUMERIC*(NEW.packaging->>'units_per_pack')::INTEGER;
 ELSIF NEW.sale_format='blister' THEN
   IF NEW.packaging->>'units_per_blister' IS NULL THEN
     RAISE EXCEPTION 'unit_cart_v2_unknown_blister' USING ERRCODE='23514';
   END IF;
   v_expected:=NEW.quantity::NUMERIC*(NEW.packaging->>'units_per_blister')::INTEGER;
 ELSE v_expected:=NEW.quantity;
 END IF;
 IF v_expected IS DISTINCT FROM NEW.base_units::NUMERIC OR v_expected>99999999999
   OR mod(v_expected,(NEW.packaging->>'dispense_step')::INTEGER)<>0 THEN
   RAISE EXCEPTION 'unit_cart_v2_line_quantity' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION public.trg_unit_pack_v2_projection_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE l public.unit_stock_lot_v2%ROWTYPE;
BEGIN
 IF TG_OP='DELETE' OR pg_trigger_depth()<2 THEN
   RAISE EXCEPTION 'unit_pack_v2_projection_requires_movement' USING ERRCODE='55000';
 END IF;
 SELECT * INTO l FROM public.unit_stock_lot_v2 WHERE tenant_id=NEW.tenant_id AND id=NEW.lot_id;
 IF l.id IS NULL OR NEW.pack_ordinal>l.initial_closed_packs OR
   (NEW.branch_id,NEW.catalog_id) IS DISTINCT FROM (l.branch_id,l.catalog_id)
   OR NEW.remaining_units<>public.unit_pack_v2_remaining(NEW.state,l.packaging) THEN
   RAISE EXCEPTION 'unit_pack_v2_projection_invalid' USING ERRCODE='23514';
 END IF;
 IF TG_OP='UPDATE' AND
   (to_jsonb(NEW)-ARRAY['state','remaining_units','updated_at','updated_by']) IS DISTINCT FROM
   (to_jsonb(OLD)-ARRAY['state','remaining_units','updated_at','updated_by']) THEN
   RAISE EXCEPTION 'unit_pack_v2_identity_immutable' USING ERRCODE='55000';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM public.unit_stock_movement_v2
   WHERE tenant_id=NEW.tenant_id AND pack_id=NEW.id AND kind='pack_open'
     AND sealed_start=NEW.pack_ordinal) THEN
   RAISE EXCEPTION 'unit_pack_v2_opening_missing' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION public.trg_unit_movement_v2_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE
 c public.unit_stock_cart_command_v2%ROWTYPE;
 r public.unit_stock_cart_line_v2%ROWTYPE;
 l public.unit_stock_lot_v2%ROWTYPE;
 p public.unit_stock_opened_pack_v2%ROWTYPE;
 v_p INTEGER;
 v_next BIGINT;
BEGIN
 IF TG_OP<>'INSERT' THEN
   RAISE EXCEPTION 'unit_movement_v2_history_immutable' USING ERRCODE='55000';
 END IF;
 SELECT * INTO c FROM public.unit_stock_cart_command_v2
   WHERE tenant_id=NEW.tenant_id AND id=NEW.command_id FOR SHARE;
 SELECT * INTO r FROM public.unit_stock_cart_line_v2 WHERE tenant_id=NEW.tenant_id
   AND command_id=NEW.command_id AND line_id=NEW.line_id;
 IF c.id IS NULL OR r.line_id IS NULL OR c.actor_id IS DISTINCT FROM public.current_app_user_id()
   OR (NEW.branch_id,NEW.catalog_id) IS DISTINCT FROM (c.branch_id,r.catalog_id)
   OR NEW.entry_order<>(SELECT coalesce(max(entry_order),-1)+1
     FROM public.unit_stock_movement_v2 WHERE tenant_id=NEW.tenant_id AND command_id=NEW.command_id)
 THEN
   RAISE EXCEPTION 'unit_movement_v2_command_invalid' USING ERRCODE='23514';
 END IF;
 IF EXISTS(SELECT 1 FROM public.unit_stock_movement_v2 m
   JOIN public.unit_stock_cart_line_v2 previous ON previous.tenant_id=m.tenant_id
     AND previous.command_id=m.command_id AND previous.line_id=m.line_id
   WHERE m.tenant_id=NEW.tenant_id AND m.command_id=NEW.command_id
     AND (CASE previous.sale_format WHEN 'pack' THEN 0 WHEN 'blister' THEN 1 ELSE 2 END)
       >(CASE r.sale_format WHEN 'pack' THEN 0 WHEN 'blister' THEN 1 ELSE 2 END)) THEN
   RAISE EXCEPTION 'unit_movement_v2_format_order' USING ERRCODE='23514';
 END IF;
 -- Catalog before lot matches the service lock order and blocks concurrent archival.
 PERFORM 1 FROM public.tenant_catalog WHERE tenant_id=NEW.tenant_id AND id=NEW.catalog_id
   AND is_active AND deleted_at IS NULL FOR NO KEY UPDATE;
 IF NOT FOUND THEN
   RAISE EXCEPTION 'unit_movement_v2_catalog_unavailable' USING ERRCODE='23514';
 END IF;
 SELECT * INTO l FROM public.unit_stock_lot_v2
   WHERE tenant_id=NEW.tenant_id AND id=NEW.lot_id FOR NO KEY UPDATE;
 IF l.id IS NULL OR (l.branch_id,l.catalog_id,l.packaging)
   IS DISTINCT FROM (NEW.branch_id,NEW.catalog_id,r.packaging)
   OR l.expires_at<=(statement_timestamp() AT TIME ZONE coalesce(
     (SELECT report_timezone FROM public.tenant_settings WHERE tenant_id=NEW.tenant_id),
     'Asia/Dushanbe'))::DATE THEN
   RAISE EXCEPTION 'unit_movement_v2_lot_unavailable' USING ERRCODE='23514';
 END IF;
 PERFORM public.assert_unit_incoming_v2_source(NEW.tenant_id,l.source_item_id,true);
 v_p:=(l.packaging->>'units_per_pack')::INTEGER;
 v_next:=l.initial_closed_packs-l.closed_packs+1;
 IF NEW.kind='pack_take' THEN
   IF NEW.closed_delta>=0 OR -NEW.closed_delta>l.closed_packs
     OR NEW.units_taken::NUMERIC<>-NEW.closed_delta::NUMERIC*v_p OR NEW.pack_id IS NOT NULL
     OR NEW.before_state IS NOT NULL OR NEW.after_state IS NOT NULL
     OR NEW.sealed_start IS DISTINCT FROM v_next
     OR NEW.sealed_count IS DISTINCT FROM -NEW.closed_delta THEN
     RAISE EXCEPTION 'unit_movement_v2_sealed_take_invalid' USING ERRCODE='23514';
   END IF;
 ELSIF NEW.kind='pack_open' THEN
   IF NEW.closed_delta<>-1 OR l.closed_packs<1 OR NEW.units_taken<>0
     OR NEW.before_state IS NOT NULL OR NEW.after_state IS DISTINCT FROM
       public.unit_pack_v2_initial_state(l.packaging) OR NEW.pack_id IS NULL
     OR NEW.sealed_start IS DISTINCT FROM v_next OR NEW.sealed_count IS DISTINCT FROM 1::BIGINT
     OR r.sale_format='pack' OR NEW.pack_id<>public.unit_incoming_uuid5(
       'aurum-unit-open-v2:'||NEW.tenant_id||':'||NEW.command_id||':'||NEW.lot_id||':'||v_next)
     OR EXISTS(SELECT 1 FROM public.unit_stock_opened_pack_v2
       WHERE tenant_id=NEW.tenant_id AND id=NEW.pack_id) THEN
     RAISE EXCEPTION 'unit_movement_v2_open_invalid' USING ERRCODE='23514';
   END IF;
 ELSE
   SELECT * INTO p FROM public.unit_stock_opened_pack_v2
     WHERE tenant_id=NEW.tenant_id AND id=NEW.pack_id FOR NO KEY UPDATE;
   IF p.id IS NULL OR (p.lot_id,p.branch_id,p.catalog_id)
     IS DISTINCT FROM (NEW.lot_id,NEW.branch_id,NEW.catalog_id)
     OR NEW.closed_delta<>0 OR NEW.sealed_start IS NOT NULL OR NEW.sealed_count IS NOT NULL
     OR NEW.before_state IS DISTINCT FROM p.state OR NEW.units_taken<=0
     OR (r.sale_format='unit'
       AND NEW.units_taken%(l.packaging->>'dispense_step')::INTEGER<>0)
     OR NEW.kind IS DISTINCT FROM r.sale_format||'_take'
     OR r.sale_format NOT IN ('unit','blister') THEN
     RAISE EXCEPTION 'unit_movement_v2_opened_take_invalid' USING ERRCODE='23514';
   END IF;
   PERFORM public.unit_pack_v2_assert_transition(
     NEW.before_state,NEW.after_state,l.packaging,r.sale_format,NEW.units_taken);
 END IF;
 IF NEW.units_taken+(SELECT coalesce(sum(units_taken),0) FROM public.unit_stock_movement_v2
   WHERE tenant_id=NEW.tenant_id AND command_id=NEW.command_id AND line_id=NEW.line_id)
     >r.base_units THEN
   RAISE EXCEPTION 'unit_movement_v2_line_overdrawn' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION public.trg_unit_movement_v2_apply() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE v_packaging JSONB;
BEGIN
 SELECT packaging INTO v_packaging FROM public.unit_stock_lot_v2
   WHERE tenant_id=NEW.tenant_id AND id=NEW.lot_id;
 IF NEW.closed_delta<>0 THEN
   UPDATE public.unit_stock_lot_v2 SET closed_packs=closed_packs+NEW.closed_delta,
     updated_at=statement_timestamp(),updated_by=public.current_app_user_id()
     WHERE tenant_id=NEW.tenant_id AND id=NEW.lot_id;
 END IF;
 IF NEW.kind='pack_open' THEN
   INSERT INTO public.unit_stock_opened_pack_v2
     (tenant_id,id,branch_id,catalog_id,lot_id,pack_ordinal,opened_at,state,remaining_units,
      created_at,updated_at,created_by,updated_by)
   VALUES(NEW.tenant_id,NEW.pack_id,NEW.branch_id,NEW.catalog_id,NEW.lot_id,NEW.sealed_start,
     NEW.created_at,NEW.after_state,public.unit_pack_v2_remaining(NEW.after_state,v_packaging),
     NEW.created_at,NEW.created_at,NEW.created_by,NEW.created_by);
 ELSIF NEW.kind IN ('blister_take','unit_take') THEN
   UPDATE public.unit_stock_opened_pack_v2 SET state=NEW.after_state,
     remaining_units=public.unit_pack_v2_remaining(NEW.after_state,v_packaging),
     updated_at=statement_timestamp(),updated_by=public.current_app_user_id()
     WHERE tenant_id=NEW.tenant_id AND id=NEW.pack_id;
 END IF;
 RETURN NEW;
END $$;
"""


COMPLETE_SQL = """
CREATE FUNCTION public.assert_unit_cart_v2_complete(p_tenant UUID,p_command UUID)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE
 c public.unit_stock_cart_command_v2%ROWTYPE;
 v_count INTEGER;
 v_first INTEGER;
 v_last INTEGER;
 v_total NUMERIC;
 v_lot RECORD;
BEGIN
 SELECT * INTO c FROM public.unit_stock_cart_command_v2
   WHERE tenant_id=p_tenant AND id=p_command;
 SELECT count(*),min(position),max(position),sum(base_units)
   INTO v_count,v_first,v_last,v_total FROM public.unit_stock_cart_line_v2
   WHERE tenant_id=p_tenant AND command_id=p_command;
 IF c.id IS NULL OR v_count<1 OR v_count>200 OR v_first<>0 OR v_last<>v_count-1
   OR v_total>99999999999
   OR c.request_hash IS DISTINCT FROM public.unit_cart_v2_fingerprint(p_tenant,p_command)
   OR EXISTS(SELECT 1 FROM public.unit_stock_cart_line_v2 r
     WHERE r.tenant_id=p_tenant AND r.command_id=p_command AND r.base_units<>
       (SELECT coalesce(sum(m.units_taken),0) FROM public.unit_stock_movement_v2 m
        WHERE m.tenant_id=p_tenant AND m.command_id=p_command AND m.line_id=r.line_id))
   OR EXISTS(SELECT 1 FROM public.unit_stock_movement_v2 opening
     WHERE opening.tenant_id=p_tenant AND opening.command_id=p_command AND opening.kind='pack_open'
       AND NOT EXISTS(SELECT 1 FROM public.unit_stock_movement_v2 taking
         WHERE taking.tenant_id=p_tenant AND taking.command_id=p_command
           AND taking.pack_id=opening.pack_id AND taking.units_taken>0)) THEN
   RAISE EXCEPTION 'unit_cart_v2_incomplete' USING ERRCODE='23514';
 END IF;
 FOR v_lot IN SELECT DISTINCT l.source_item_id FROM public.unit_stock_lot_v2 l
   JOIN public.unit_stock_movement_v2 m ON m.tenant_id=l.tenant_id AND m.lot_id=l.id
   WHERE m.tenant_id=p_tenant AND m.command_id=p_command LOOP
   PERFORM public.assert_unit_incoming_v2_source(p_tenant,v_lot.source_item_id,true);
 END LOOP;
END $$;

CREATE FUNCTION public.trg_unit_cart_v2_complete() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE v_command UUID;
BEGIN
 IF TG_TABLE_NAME='unit_stock_cart_command_v2' THEN v_command:=NEW.id;
 ELSIF TG_TABLE_NAME='unit_stock_opened_pack_v2' THEN
   SELECT command_id INTO v_command FROM public.unit_stock_movement_v2
     WHERE tenant_id=NEW.tenant_id AND pack_id=NEW.id AND kind='pack_open';
 ELSE v_command:=NEW.command_id;
 END IF;
 IF v_command IS NULL THEN
   RAISE EXCEPTION 'unit_cart_v2_projection_orphan' USING ERRCODE='23514';
 END IF;
 PERFORM public.assert_unit_cart_v2_complete(NEW.tenant_id,v_command);
 RETURN NULL;
END $$;
"""


def _historical_guards(*, dispensing: bool) -> None:
    history = _history()
    source_sql = history.PROVENANCE_SQL
    if dispensing:
        source_sql = history._replace(
            source_sql,
            "     OR l.closed_packs<>l.initial_closed_packs",
            """     OR l.closed_packs<>(SELECT coalesce(sum(closed_delta),0)
       FROM public.unit_stock_entry_v2 WHERE tenant_id=p_tenant AND lot_id=l.id)
       +(SELECT coalesce(sum(closed_delta),0) FROM public.unit_stock_movement_v2
         WHERE tenant_id=p_tenant AND lot_id=l.id)
     OR l.initial_closed_packs::NUMERIC*(l.packaging->>'units_per_pack')::INTEGER<>
       l.closed_packs::NUMERIC*(l.packaging->>'units_per_pack')::INTEGER
       +(SELECT coalesce(sum(remaining_units),0) FROM public.unit_stock_opened_pack_v2
         WHERE tenant_id=p_tenant AND lot_id=l.id)
       +(SELECT coalesce(sum(units_taken),0) FROM public.unit_stock_movement_v2
         WHERE tenant_id=p_tenant AND lot_id=l.id)""",
        )
        source_sql = history._replace(
            source_sql,
            "     AND NEW.closed_packs=(SELECT coalesce(sum(closed_delta),0)\n"
            "       FROM public.unit_stock_entry_v2 "
            "WHERE tenant_id=OLD.tenant_id AND lot_id=OLD.id)",
            """     AND NEW.closed_packs=(SELECT coalesce(sum(closed_delta),0)
       FROM public.unit_stock_entry_v2 WHERE tenant_id=OLD.tenant_id AND lot_id=OLD.id)
       +(SELECT coalesce(sum(closed_delta),0) FROM public.unit_stock_movement_v2
         WHERE tenant_id=OLD.tenant_id AND lot_id=OLD.id)""",
        )
    # CREATE OR REPLACE preserves the original ownership and private execution ACLs.
    _sql(source_sql.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION"))


def _tables() -> None:
    history = _history()
    identity = history._history("0162_prepared_unit_stock_ledger.py").IDENTITY
    audit = """
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      created_by UUID REFERENCES public.app_user(id),updated_by UUID REFERENCES public.app_user(id)
    """
    snapshot = history._history("0174_incoming_blister_snapshot.py").SNAPSHOT_CHECK
    snapshot = (
        snapshot.replace("    AND unit_packaging_preparation IS NOT NULL\n", "")
        .replace(
            "    AND (unit_packaging_snapshot_v2 - ARRAY['version', 'units_per_blister'])\n"
            "      = (unit_packaging_preparation - 'version')\n",
            "",
        )
        .replace("unit_packaging_snapshot_v2 IS NULL OR ", "")
        .replace("unit_packaging_snapshot_v2", "packaging")
    )
    _sql("""
      CREATE TEMP TABLE unit_dispense_v2_reference_grants AS
        SELECT oid::regclass::TEXT AS relation,
          has_table_privilege('aurum_schema_owner',oid,'REFERENCES') AS granted
        FROM pg_class WHERE oid IN ('public.tenant'::regclass,'public.branch'::regclass,
          'public.tenant_catalog'::regclass,'public.app_user'::regclass);
      GRANT REFERENCES ON public.tenant,public.branch,public.tenant_catalog,public.app_user
        TO aurum_schema_owner;
      ALTER TABLE public.unit_stock_lot_v2 ADD CONSTRAINT uq_unit_lot_v2_dispense_scope
        UNIQUE(tenant_id,id,branch_id,catalog_id);
    """)
    _sql(f"""
      CREATE TABLE public.unit_stock_cart_command_v2 (
        tenant_id UUID NOT NULL REFERENCES public.tenant(id),id UUID NOT NULL,
        branch_id UUID NOT NULL,actor_id UUID NOT NULL REFERENCES public.app_user(id),
        request_hash TEXT NOT NULL CHECK(request_hash ~ '^[0-9a-f]{{64}}$'),{audit},
        PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,id,branch_id),
        FOREIGN KEY(tenant_id,branch_id) REFERENCES public.branch(tenant_id,id)
      );
      CREATE TABLE public.unit_stock_cart_line_v2 (
        tenant_id UUID NOT NULL,command_id UUID NOT NULL,line_id UUID NOT NULL,
        position INTEGER NOT NULL CHECK(position BETWEEN 0 AND 199),catalog_id UUID NOT NULL,
        packaging JSONB NOT NULL CHECK({snapshot}),
        sale_format TEXT NOT NULL CHECK(sale_format IN ('pack','blister','unit')),
        quantity BIGINT NOT NULL CHECK(quantity BETWEEN 1 AND 99999999999),
        base_units BIGINT NOT NULL CHECK(base_units BETWEEN 1 AND 99999999999),{audit},
        PRIMARY KEY(tenant_id,command_id,line_id),UNIQUE(tenant_id,command_id,position),
        UNIQUE(tenant_id,command_id,line_id,catalog_id),
        FOREIGN KEY(tenant_id,command_id)
          REFERENCES public.unit_stock_cart_command_v2(tenant_id,id),
        FOREIGN KEY(tenant_id,catalog_id) REFERENCES public.tenant_catalog(tenant_id,id)
      );
      CREATE TABLE public.unit_stock_opened_pack_v2 (
        {identity},lot_id UUID NOT NULL,pack_ordinal BIGINT NOT NULL CHECK(pack_ordinal>0),
        opened_at TIMESTAMPTZ NOT NULL,state JSONB NOT NULL,
        remaining_units BIGINT NOT NULL CHECK(remaining_units BETWEEN 0 AND 10000),
        UNIQUE(tenant_id,id),UNIQUE(tenant_id,lot_id,pack_ordinal),
        UNIQUE(tenant_id,id,lot_id,branch_id,catalog_id),
        FOREIGN KEY(tenant_id,lot_id,branch_id,catalog_id)
          REFERENCES public.unit_stock_lot_v2(tenant_id,id,branch_id,catalog_id)
      );
      CREATE TABLE public.unit_stock_movement_v2 (
        {identity},command_id UUID NOT NULL,line_id UUID NOT NULL,lot_id UUID NOT NULL,
        pack_id UUID,kind TEXT NOT NULL
          CHECK(kind IN ('pack_open','pack_take','blister_take','unit_take')),
        entry_order INTEGER NOT NULL CHECK(entry_order>=0),
        closed_delta BIGINT NOT NULL CHECK(closed_delta BETWEEN -99999999999 AND 0),
        units_taken BIGINT NOT NULL CHECK(units_taken BETWEEN 0 AND 99999999999),
        before_state JSONB,after_state JSONB,sealed_start BIGINT,sealed_count BIGINT,
        UNIQUE(tenant_id,command_id,entry_order),
        FOREIGN KEY(tenant_id,command_id,branch_id)
          REFERENCES public.unit_stock_cart_command_v2(tenant_id,id,branch_id),
        FOREIGN KEY(tenant_id,command_id,line_id,catalog_id)
          REFERENCES public.unit_stock_cart_line_v2(tenant_id,command_id,line_id,catalog_id),
        FOREIGN KEY(tenant_id,lot_id,branch_id,catalog_id)
          REFERENCES public.unit_stock_lot_v2(tenant_id,id,branch_id,catalog_id),
        FOREIGN KEY(tenant_id,pack_id,lot_id,branch_id,catalog_id)
          REFERENCES public.unit_stock_opened_pack_v2(tenant_id,id,lot_id,branch_id,catalog_id)
          DEFERRABLE INITIALLY DEFERRED
      );
      CREATE UNIQUE INDEX uq_unit_movement_v2_open
        ON public.unit_stock_movement_v2(tenant_id,pack_id)
        WHERE kind='pack_open';
      CREATE INDEX ix_unit_movement_v2_lot ON public.unit_stock_movement_v2(tenant_id,lot_id);
      CREATE INDEX ix_unit_pack_v2_lot ON public.unit_stock_opened_pack_v2(tenant_id,lot_id);
      DO $$ DECLARE r RECORD; BEGIN
        FOR r IN SELECT * FROM pg_temp.unit_dispense_v2_reference_grants WHERE NOT granted LOOP
          EXECUTE format('REVOKE REFERENCES ON %s FROM aurum_schema_owner',r.relation);
        END LOOP;
      END $$;
      DROP TABLE pg_temp.unit_dispense_v2_reference_grants;
    """)


def _policies() -> None:
    for table in TABLES:
        scope = "branch_id"
        if table == "unit_stock_cart_line_v2":
            scope = (
                "(SELECT command.branch_id FROM public.unit_stock_cart_command_v2 command "
                f"WHERE command.tenant_id={table}.tenant_id "
                f"AND command.id={table}.command_id)"
            )
        read = (
            "tenant_id=public.current_tenant_id() AND ("
            f"public.tenant_actor_has_scoped_permission(tenant_id,'pos.sell',{scope}) OR "
            f"public.tenant_actor_has_scoped_permission(tenant_id,'batches.view',{scope}) OR "
            f"public.tenant_actor_has_scoped_permission(tenant_id,'incoming.finalize',{scope}))"
        )
        write = (
            "tenant_id=public.current_tenant_id() AND "
            f"public.tenant_actor_has_scoped_permission(tenant_id,'pos.sell',{scope})"
        )
        _sql(f"""
          ALTER TABLE public.{table} OWNER TO aurum_schema_owner;
          ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY;
          ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY;
          REVOKE ALL ON public.{table} FROM PUBLIC,aurum_app,aurum_support;
          GRANT SELECT ON public.{table} TO aurum_app,aurum_support;
          CREATE POLICY schema_owner ON public.{table} TO aurum_schema_owner
            USING(true) WITH CHECK(true);
          CREATE POLICY support ON public.{table} TO aurum_support USING(true) WITH CHECK(true);
          CREATE POLICY tenant_read ON public.{table} FOR SELECT TO aurum_app USING({read});
          CREATE TRIGGER a00_tenant_access BEFORE INSERT OR UPDATE OR DELETE ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION public.trg_enforce_tenant_access('inventory');
          CREATE TRIGGER z90_audit AFTER INSERT OR UPDATE OR DELETE ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION public.trg_audit_log();
        """)
        if table == "unit_stock_opened_pack_v2":
            _sql(f"""
              GRANT UPDATE(updated_at) ON public.{table} TO aurum_app;
              CREATE POLICY tenant_lock ON public.{table} FOR UPDATE TO aurum_app
                USING({write}) WITH CHECK({write});
              CREATE TRIGGER a10_unit_pack_v2_guard BEFORE INSERT OR UPDATE OR DELETE
                ON public.{table} FOR EACH ROW
                EXECUTE FUNCTION public.trg_unit_pack_v2_projection_guard();
            """)
            continue
        guard = (
            "trg_unit_movement_v2_guard"
            if table == "unit_stock_movement_v2"
            else "trg_unit_cart_v2_guard"
        )
        _sql(f"""
          GRANT INSERT ON public.{table} TO aurum_app,aurum_support;
          CREATE POLICY tenant_insert ON public.{table} FOR INSERT TO aurum_app
            WITH CHECK({write});
          CREATE TRIGGER a05_unit_cart_v2_actor BEFORE INSERT ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION public.trg_unit_cart_v2_actor();
          CREATE TRIGGER a10_unit_cart_v2_guard BEFORE INSERT OR UPDATE OR DELETE ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION public.{guard}();
          CREATE CONSTRAINT TRIGGER unit_cart_v2_complete AFTER INSERT ON public.{table}
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
            EXECUTE FUNCTION public.trg_unit_cart_v2_complete();
        """)
    _sql("""
      GRANT UPDATE(updated_at) ON public.unit_stock_lot_v2 TO aurum_app;
      CREATE POLICY tenant_dispensing_lock ON public.unit_stock_lot_v2 FOR UPDATE TO aurum_app
        USING(tenant_id=public.current_tenant_id() AND
          public.tenant_actor_has_scoped_permission(tenant_id,'pos.sell',branch_id))
        WITH CHECK(tenant_id=public.current_tenant_id() AND
          public.tenant_actor_has_scoped_permission(tenant_id,'pos.sell',branch_id));
      CREATE TRIGGER b10_unit_movement_v2_apply AFTER INSERT ON public.unit_stock_movement_v2
        FOR EACH ROW EXECUTE FUNCTION public.trg_unit_movement_v2_apply();
    """)


def upgrade() -> None:
    _tables()
    for sql in (STATE_SQL, HASH_SQL, GUARD_SQL, COMPLETE_SQL):
        _sql(sql)
    _historical_guards(dispensing=True)
    for function in FUNCTIONS:
        _sql(f"""
          ALTER FUNCTION public.{function} OWNER TO aurum_schema_owner;
          REVOKE ALL ON FUNCTION public.{function} FROM PUBLIC,aurum_app,aurum_support;
        """)
    _policies()


def downgrade() -> None:
    _sql("""
      LOCK TABLE public.unit_stock_cart_command_v2,public.unit_stock_cart_line_v2,
        public.unit_stock_opened_pack_v2,public.unit_stock_movement_v2,
        public.unit_stock_lot_v2,public.unit_stock_entry_v2 IN ACCESS EXCLUSIVE MODE;
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.unit_stock_cart_command_v2)
          OR EXISTS(SELECT 1 FROM public.unit_stock_cart_line_v2)
          OR EXISTS(SELECT 1 FROM public.unit_stock_opened_pack_v2)
          OR EXISTS(SELECT 1 FROM public.unit_stock_movement_v2) THEN
          RAISE EXCEPTION 'unit_dispensing_v2_nonempty_downgrade_forbidden' USING ERRCODE='55000';
        END IF;
      END $$;
      DROP POLICY tenant_dispensing_lock ON public.unit_stock_lot_v2;
      REVOKE UPDATE(updated_at) ON public.unit_stock_lot_v2 FROM aurum_app;
    """)
    _historical_guards(dispensing=False)
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE public.{table}")
    for function in reversed(FUNCTIONS):
        op.execute(f"DROP FUNCTION public.{function}")
    op.execute("ALTER TABLE public.unit_stock_lot_v2 DROP CONSTRAINT uq_unit_lot_v2_dispense_scope")
