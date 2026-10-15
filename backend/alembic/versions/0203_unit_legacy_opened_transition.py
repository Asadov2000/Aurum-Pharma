"""Transfer counted opened packs with an explicit initial physical and cost basis.

Revision ID: 0203
Revises: 0202
"""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType

revision = "0203"
down_revision = "0202"
branch_labels = None
depends_on = None


def _history() -> ModuleType:
    spec = spec_from_file_location(
        "legacy_closed_history", Path(__file__).with_name("0201_unit_legacy_closed_transition.py")
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Closed transfer migration is unavailable")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sql(sql: str) -> None:
    _history()._sql(sql)


def _replace(signature: str, old: str, new: str) -> None:
    _history()._replace(signature, old, new)


STATE_SQL = """
CREATE FUNCTION public.unit_legacy_opening_state(p_pack JSONB) RETURNS JSONB
LANGUAGE SQL IMMUTABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT jsonb_build_object(
   'intact_blisters',coalesce((SELECT jsonb_agg(value ORDER BY value)
     FROM jsonb_array_elements(p_pack->'intact_blisters')),'[]'::JSONB),
   'opened_blisters',coalesce((SELECT jsonb_agg(value ORDER BY value->'number')
     FROM jsonb_array_elements(p_pack->'opened_blisters')),'[]'::JSONB),
   'loose_units',p_pack->'loose_units')
$$;

CREATE FUNCTION public.unit_legacy_opening_time(p_value TEXT) RETURNS TIMESTAMPTZ
LANGUAGE plpgsql IMMUTABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE v_time TIMESTAMPTZ;
BEGIN
 v_time:=CASE WHEN right(p_value,1)='Z' THEN p_value::TIMESTAMPTZ ELSE
   (left(p_value,length(p_value)-6)::TIMESTAMP AT TIME ZONE 'UTC')
     - make_interval(mins => CASE WHEN substr(p_value,length(p_value)-5,1)='-' THEN -1 ELSE 1 END
       *(substr(p_value,length(p_value)-4,2)::INTEGER*60+right(p_value,2)::INTEGER)) END;
 IF v_time<'0001-01-01T00:00:00Z'::TIMESTAMPTZ
   OR v_time>'9999-12-31T23:59:59.999999Z'::TIMESTAMPTZ THEN
   RAISE EXCEPTION 'legacy_opening_time_outside_runtime_range' USING ERRCODE='23514';
 END IF;
 RETURN v_time;
END $$;

CREATE FUNCTION public.trg_unit_opening_pack_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE j public.unit_legacy_transition%ROWTYPE; v_pack JSONB; v_ordinal BIGINT; v_p INTEGER; v_units BIGINT;
BEGIN
 IF TG_OP<>'INSERT' OR session_user<>'aurum_app' THEN
   RAISE EXCEPTION 'unit_opening_history_immutable' USING ERRCODE='55000';
 END IF;
 SELECT * INTO j FROM public.unit_legacy_transition
   WHERE tenant_id=NEW.tenant_id AND id=NEW.transition_id AND initialization_version=2;
 SELECT value,ordinal INTO v_pack,v_ordinal FROM (
   SELECT value,row_number() OVER(ORDER BY (value->>'id')::UUID) AS ordinal
   FROM public.unit_legacy_reconciliation_record r,
     LATERAL jsonb_array_elements(r.count->'opened_packs')
   WHERE r.tenant_id=NEW.tenant_id AND r.id=j.record_id) packs
   WHERE (value->>'id')::UUID=NEW.id;
 IF j.id IS NULL OR v_pack IS NULL OR j.created_by IS DISTINCT FROM public.current_app_user_id()
   OR (NEW.branch_id,NEW.catalog_id,NEW.lot_id,NEW.created_by,NEW.created_at)
     IS DISTINCT FROM (j.branch_id,j.catalog_id,j.lot_id,j.created_by,j.created_at)
   OR NEW.pack_ordinal IS DISTINCT FROM v_ordinal
   OR NEW.state IS DISTINCT FROM public.unit_legacy_opening_state(v_pack)
   OR NEW.opened_at IS DISTINCT FROM public.unit_legacy_opening_time(v_pack->>'opened_at') THEN
   RAISE EXCEPTION 'unit_opening_source_invalid' USING ERRCODE='23514';
 END IF;
 v_p:=(j.packaging->>'units_per_pack')::INTEGER;
 v_units:=public.unit_pack_v2_remaining(NEW.state,j.packaging);
 IF NEW.remaining_units IS DISTINCT FROM v_units OR v_units<=0 OR NEW.cost_amount IS DISTINCT FROM
   (j.purchase_cost_per_pack-div(j.purchase_cost_per_pack*100*(v_p-v_units),v_p)/100) THEN
   RAISE EXCEPTION 'unit_opening_cost_invalid' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION public.trg_unit_opening_pack_apply() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 INSERT INTO public.unit_stock_opened_pack_v2(tenant_id,id,branch_id,catalog_id,lot_id,
   pack_ordinal,opened_at,state,remaining_units,created_at,updated_at,created_by,updated_by)
 VALUES(NEW.tenant_id,NEW.id,NEW.branch_id,NEW.catalog_id,NEW.lot_id,NEW.pack_ordinal,
   NEW.opened_at,NEW.state,NEW.remaining_units,NEW.created_at,NEW.created_at,NEW.created_by,NEW.created_by);
 RETURN NEW;
END $$;

CREATE FUNCTION public.trg_unit_initial_opened_basis_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE j public.unit_legacy_transition%ROWTYPE; r public.unit_legacy_reconciliation_record%ROWTYPE;
BEGIN
 IF NEW.initial_opened_packs=0 AND NEW.initial_opened_units=0 AND NEW.initial_opened_cost=0 THEN
   RETURN NEW;
 END IF;
 SELECT * INTO j FROM public.unit_legacy_transition
   WHERE tenant_id=NEW.tenant_id AND lot_id=NEW.id AND initialization_version=2;
 SELECT * INTO r FROM public.unit_legacy_reconciliation_record WHERE tenant_id=j.tenant_id AND id=j.record_id;
 IF j.id IS NULL OR NEW.initial_opened_packs IS DISTINCT FROM jsonb_array_length(r.count->'opened_packs')::BIGINT
   OR NEW.initial_opened_units IS DISTINCT FROM j.base_units-j.quantity*(j.packaging->>'units_per_pack')::BIGINT
   OR NEW.initial_opened_cost IS DISTINCT FROM j.total_cost-j.quantity*j.purchase_cost_per_pack THEN
   RAISE EXCEPTION 'unit_initial_opened_basis_invalid' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
"""

OPEN_COMPLETE_SQL = """
CREATE FUNCTION public.assert_unit_legacy_opened_complete(p_tenant UUID,p_item UUID)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE j public.unit_legacy_transition%ROWTYPE; l public.unit_stock_lot_v2%ROWTYPE;
 r public.unit_legacy_reconciliation_record%ROWTYPE;
BEGIN
 SELECT * INTO j FROM public.unit_legacy_transition WHERE tenant_id=p_tenant AND source_item_id=p_item;
 SELECT * INTO l FROM public.unit_stock_lot_v2 WHERE tenant_id=p_tenant AND id=j.lot_id;
 SELECT * INTO r FROM public.unit_legacy_reconciliation_record WHERE tenant_id=p_tenant AND id=j.record_id;
 IF j.id IS NULL OR l.id IS NULL OR j.initialization_version<>2
   OR j.legacy_quantity IS DISTINCT FROM r.legacy_quantity
   OR j.quantity IS DISTINCT FROM (r.count->>'closed_packs')::BIGINT
   OR j.packaging IS DISTINCT FROM r.count->'packaging'
   OR l.initial_opened_packs IS DISTINCT FROM jsonb_array_length(r.count->'opened_packs')::BIGINT
   OR l.initial_closed_packs IS DISTINCT FROM j.quantity OR l.packaging IS DISTINCT FROM j.packaging
   OR (l.branch_id,l.catalog_id,l.source_item_id,l.purchase_cost_per_pack,l.expires_at,l.cost_version,l.created_at)
     IS DISTINCT FROM(j.branch_id,j.catalog_id,p_item,j.purchase_cost_per_pack,j.expires_at,1,j.created_at)
   OR l.initial_opened_packs<>(SELECT count(*) FROM public.unit_stock_opening_pack_v2
     WHERE tenant_id=p_tenant AND transition_id=j.id AND lot_id=l.id)
   OR l.initial_opened_units<>(SELECT coalesce(sum(remaining_units),0) FROM public.unit_stock_opening_pack_v2
     WHERE tenant_id=p_tenant AND transition_id=j.id AND lot_id=l.id)
   OR l.initial_opened_cost<>(SELECT coalesce(sum(cost_amount),0) FROM public.unit_stock_opening_pack_v2
     WHERE tenant_id=p_tenant AND transition_id=j.id AND lot_id=l.id)
   OR j.total_cost<>l.initial_closed_packs*l.purchase_cost_per_pack+l.initial_opened_cost
   OR j.base_units<>l.initial_closed_packs*(j.packaging->>'units_per_pack')::BIGINT+l.initial_opened_units
   OR EXISTS(SELECT 1 FROM public.unit_stock_opening_pack_v2 o LEFT JOIN public.unit_stock_opened_pack_v2 p
     ON p.tenant_id=o.tenant_id AND p.id=o.id AND p.lot_id=o.lot_id
     WHERE o.tenant_id=p_tenant AND o.transition_id=j.id
       AND (p.id IS NULL OR p.pack_ordinal<>o.pack_ordinal OR p.opened_at<>o.opened_at))
   OR NOT EXISTS(SELECT 1 FROM public.batch b WHERE b.tenant_id=p_tenant AND b.id=j.batch_id
     AND b.qty_remaining=0 AND b.branch_id=j.branch_id AND b.catalog_id=j.catalog_id)
   OR NOT EXISTS(SELECT 1 FROM public.incoming_item i JOIN public.incoming_document d
     ON d.tenant_id=i.tenant_id AND d.id=i.document_id WHERE i.tenant_id=p_tenant AND i.id=p_item
     AND i.created_batch_id=j.batch_id AND i.created_unit_lot_id IS NULL AND i.created_unit_lot_v2_id IS NULL
     AND d.id=j.source_document_id AND d.status='accepted' AND d.branch_id=j.branch_id
     AND i.catalog_id=j.catalog_id AND i.purchase_price=j.purchase_cost_per_pack)
   OR NOT EXISTS(SELECT 1 FROM public.unit_stock_operation_v2 o WHERE o.tenant_id=p_tenant
     AND o.id=j.operation_id AND o.source_item_id=p_item AND o.quantity=j.quantity+l.initial_opened_packs
     AND o.base_units=j.base_units AND o.request_hash=j.request_hash AND o.packaging=j.packaging
     AND o.created_at=j.created_at)
   OR NOT EXISTS(SELECT 1 FROM public.unit_stock_entry_v2 e WHERE e.tenant_id=p_tenant
     AND e.id=j.entry_id AND e.operation_id=j.operation_id AND e.lot_id=l.id
     AND e.closed_delta=j.quantity AND e.base_units=j.base_units AND e.source_item_id=p_item AND e.created_at=j.created_at)
   OR NOT EXISTS(SELECT 1 FROM public.batch_movement m WHERE m.id=j.movement_id AND m.tenant_id=p_tenant
     AND m.batch_id=j.batch_id AND m.source_table='unit_legacy_transition' AND m.source_id=j.id
     AND m.movement_type='correction' AND m.qty_delta=-j.legacy_quantity AND m.created_at=j.created_at)
   OR l.closed_packs<>j.quantity+(SELECT coalesce(sum(closed_delta),0) FROM public.unit_stock_movement_v2
     WHERE tenant_id=p_tenant AND lot_id=l.id)
   OR j.base_units<>l.closed_packs*(j.packaging->>'units_per_pack')::BIGINT
     +(SELECT coalesce(sum(remaining_units),0) FROM public.unit_stock_opened_pack_v2 WHERE tenant_id=p_tenant AND lot_id=l.id)
     +(SELECT coalesce(sum(units_taken),0) FROM public.unit_stock_movement_v2 WHERE tenant_id=p_tenant AND lot_id=l.id) THEN
   RAISE EXCEPTION 'legacy_opened_transition_incomplete' USING ERRCODE='23514';
 END IF;
END $$;
"""


def _opened_post_sql() -> str:
    sql = _history().POST_SQL
    replacements = (
        (
            "CREATE FUNCTION public.post_unit_legacy_transition(",
            "CREATE FUNCTION public.post_unit_legacy_opened_transition(",
        ),
        (
            "v_cost NUMERIC;",
            "v_cost NUMERIC; v_open_count BIGINT; v_open_units BIGINT:=0; v_open_cost NUMERIC:=0; v_pack JSONB; v_state JSONB; v_remaining BIGINT; v_p INTEGER;",
        ),
        (
            "r.count->'opened_packs'<>'[]'::JSONB",
            "r.count->'opened_packs'='[]'::JSONB",
        ),
        (
            "OR b.qty_remaining<=0 OR b.qty_remaining<>trunc(b.qty_remaining)",
            "OR b.qty_remaining<=0",
        ),
        (
            "   OR (r.count->>'closed_packs')::NUMERIC IS DISTINCT FROM b.qty_remaining THEN",
            "   THEN",
        ),
        (
            " v_packs:=b.qty_remaining::BIGINT;\n v_units:=v_packs::NUMERIC*(r.count->'packaging'->>'units_per_pack')::INTEGER;\n v_cost:=v_packs::NUMERIC*i.purchase_price;",
            """
 v_packs:=(r.count->>'closed_packs')::BIGINT;
 v_p:=(r.count->'packaging'->>'units_per_pack')::INTEGER;
 v_open_count:=jsonb_array_length(r.count->'opened_packs');
 FOR v_pack IN SELECT value FROM jsonb_array_elements(r.count->'opened_packs') LOOP
   v_state:=public.unit_legacy_opening_state(v_pack);
   v_remaining:=public.unit_pack_v2_remaining(v_state,r.count->'packaging');
   v_open_units:=v_open_units+v_remaining;
   v_open_cost:=v_open_cost+i.purchase_price-div(i.purchase_price*100*(v_p-v_remaining),v_p)/100;
 END LOOP;
 v_units:=v_packs::NUMERIC*v_p+v_open_units;
 v_cost:=b.qty_remaining*i.purchase_price;
 IF v_units IS DISTINCT FROM b.qty_remaining*v_p OR v_cost<>trunc(v_cost,2)
   OR v_cost IS DISTINCT FROM v_packs*i.purchase_price+v_open_cost THEN
   RAISE EXCEPTION 'legacy_opened_exact_basis_mismatch' USING ERRCODE='23514';
 END IF;""",
        ),
        (
            "purchase_cost_per_pack,total_cost,expires_at,request_hash,created_by,created_at)",
            "purchase_cost_per_pack,total_cost,expires_at,request_hash,created_by,created_at,initialization_version,legacy_quantity)",
        ),
        (
            "public.current_app_user_id(),clock_timestamp()) RETURNING * INTO j;",
            "public.current_app_user_id(),clock_timestamp(),2,b.qty_remaining) RETURNING * INTO j;",
        ),
        (
            "aurum-legacy-transition:1:",
            "aurum-legacy-transition:2:",
        ),
        (
            "j.packaging,'initialize','pack',j.quantity,j.base_units",
            "j.packaging,'initialize','pack',j.quantity+v_open_count,j.base_units",
        ),
        (
            "packaging,expires_at,initial_closed_packs,purchase_cost_per_pack,cost_version,created_by)",
            "packaging,expires_at,initial_closed_packs,purchase_cost_per_pack,cost_version,created_by,initial_opened_packs,initial_opened_units,initial_opened_cost)",
        ),
        (
            "j.packaging,j.expires_at,j.quantity,j.purchase_cost_per_pack,1,j.created_by);",
            "j.packaging,j.expires_at,j.quantity,j.purchase_cost_per_pack,1,j.created_by,v_open_count,v_open_units,v_open_cost);",
        ),
        (
            "'correction',-j.quantity::NUMERIC,",
            "'correction',-j.legacy_quantity,",
        ),
        (
            " PERFORM public.assert_unit_legacy_transition_complete(p_tenant,i.id);",
            """
 INSERT INTO public.unit_stock_opening_pack_v2(tenant_id,id,branch_id,catalog_id,lot_id,transition_id,
   pack_ordinal,opened_at,state,remaining_units,cost_amount,created_by,created_at)
 SELECT j.tenant_id,(value->>'id')::UUID,j.branch_id,j.catalog_id,j.lot_id,j.id,
   row_number() OVER(ORDER BY (value->>'id')::UUID),public.unit_legacy_opening_time(value->>'opened_at'),
   public.unit_legacy_opening_state(value),
   public.unit_pack_v2_remaining(public.unit_legacy_opening_state(value),j.packaging),
   j.purchase_cost_per_pack-div(j.purchase_cost_per_pack*100*(v_p-
     public.unit_pack_v2_remaining(public.unit_legacy_opening_state(value),j.packaging)),v_p)/100,
   j.created_by,j.created_at
 FROM jsonb_array_elements(r.count->'opened_packs');
 PERFORM public.assert_unit_legacy_transition_complete(p_tenant,i.id);""",
        ),
    )
    for old, new in replacements:
        if sql.count(old) != 1:
            raise RuntimeError(
                "Opened transfer source template differs from its closed predecessor"
            )
        sql = sql.replace(old, new, 1)
    return sql


POST_ANCHOR = "BEGIN\n PERFORM public.assert_unit_legacy_transition_actor(p_tenant,p_branch);"
POST_BRANCH = """BEGIN
 IF EXISTS(SELECT 1 FROM public.unit_legacy_reconciliation_record WHERE tenant_id=p_tenant
   AND branch_id=p_branch AND batch_id=p_batch AND id=p_record AND count->'opened_packs'<>'[]'::JSONB) THEN
   RETURN QUERY SELECT * FROM public.post_unit_legacy_opened_transition(p_tenant,p_branch,p_batch,p_record,p_command);
   RETURN;
 END IF;
 PERFORM public.assert_unit_legacy_transition_actor(p_tenant,p_branch);"""
COMPLETE_ANCHOR = (
    "BEGIN\n SELECT * INTO j FROM public.unit_legacy_transition "
    "WHERE tenant_id=p_tenant AND source_item_id=p_item;"
)
COMPLETE_BRANCH = """BEGIN
 IF EXISTS(SELECT 1 FROM public.unit_legacy_transition WHERE tenant_id=p_tenant AND source_item_id=p_item
   AND initialization_version=2) THEN
   PERFORM public.assert_unit_legacy_opened_complete(p_tenant,p_item);RETURN;
 END IF;
 SELECT * INTO j FROM public.unit_legacy_transition WHERE tenant_id=p_tenant AND source_item_id=p_item;"""
PROJECTION_ANCHOR = " IF NOT EXISTS(SELECT 1 FROM public.unit_stock_movement_v2"
PROJECTION_BRANCH = """ IF EXISTS(SELECT 1 FROM public.unit_stock_opening_pack_v2 o
   WHERE o.tenant_id=NEW.tenant_id AND o.id=NEW.id AND o.lot_id=NEW.lot_id
     AND o.pack_ordinal=NEW.pack_ordinal AND o.opened_at=NEW.opened_at) THEN
   IF TG_OP='INSERT' AND NOT EXISTS(SELECT 1 FROM public.unit_stock_opening_pack_v2 o
     WHERE o.tenant_id=NEW.tenant_id AND o.id=NEW.id AND o.state=NEW.state
       AND o.remaining_units=NEW.remaining_units AND o.created_at=NEW.created_at) THEN
     RAISE EXCEPTION 'unit_opening_projection_invalid' USING ERRCODE='23514';
   END IF;
   RETURN NEW;
 END IF;
 IF NOT EXISTS(SELECT 1 FROM public.unit_stock_movement_v2"""


def _patches(*, upgrade: bool) -> None:
    history = _history()
    # Provenance remains strict per source; the generic quantity bound now admits
    # initially partial physical containers, without weakening receipt validation.
    changes = [
        (
            "public.post_unit_legacy_transition(uuid,uuid,uuid,uuid,uuid)",
            POST_ANCHOR,
            POST_BRANCH,
        ),
        (
            "public.assert_unit_legacy_transition_complete(uuid,uuid)",
            COMPLETE_ANCHOR,
            COMPLETE_BRANCH,
        ),
        (
            "public.trg_unit_legacy_transition_movement()",
            "-j.quantity::NUMERIC",
            "-coalesce(j.legacy_quantity,j.quantity::NUMERIC)",
        ),
        (
            "public.unit_legacy_transition_row_matches(text,jsonb)",
            "(p_row->>'quantity')::BIGINT IS DISTINCT FROM j.quantity",
            "(p_row->>'quantity')::BIGINT IS DISTINCT FROM j.quantity+CASE WHEN j.initialization_version=2 THEN\n       (SELECT jsonb_array_length(count->'opened_packs') FROM public.unit_legacy_reconciliation_record\n         WHERE tenant_id=j.tenant_id AND id=j.record_id) ELSE 0 END",
        ),
        (
            "public.trg_unit_pack_v2_projection_guard()",
            "NEW.pack_ordinal>l.initial_closed_packs",
            "NEW.pack_ordinal>l.initial_closed_packs+l.initial_opened_packs",
        ),
        (
            "public.trg_unit_pack_v2_projection_guard()",
            PROJECTION_ANCHOR,
            PROJECTION_BRANCH,
        ),
        (
            "public.trg_unit_movement_v2_guard()",
            "v_next:=l.initial_closed_packs-l.closed_packs+1;",
            "v_next:=l.initial_closed_packs+l.initial_opened_packs-l.closed_packs+1;",
        ),
        (
            "public.trg_unit_movement_v2_cost_snapshot()",
            "v_start::NUMERIC+v_count-1>l.initial_closed_packs",
            "v_start::NUMERIC+v_count-1>l.initial_closed_packs+l.initial_opened_packs",
        ),
        (
            "public.trg_unit_movement_v2_cost_snapshot()",
            "v_start>l.initial_closed_packs",
            "v_start>l.initial_closed_packs+l.initial_opened_packs",
        ),
        (
            "public.pos_stock_on_date_for_report(uuid,timestamptz,uuid)",
            "l.initial_closed_packs*l.purchase_cost_per_pack-coalesce(s.cost_amount,0)",
            "l.initial_closed_packs*l.purchase_cost_per_pack+l.initial_opened_cost-coalesce(s.cost_amount,0)",
        ),
        (
            "public.audit_redact_table_snapshot(text,jsonb)",
            history.AUDIT_BRANCH,
            """IF p_table_name='unit_stock_opening_pack_v2' THEN RETURN v_result-'cost_amount'; END IF;
      """
            + history.AUDIT_BRANCH,
        ),
        (
            "public.audit_redact_table_snapshot(text,jsonb)",
            "IF v_result IS NULL THEN RETURN NULL; END IF;",
            "IF v_result IS NULL THEN RETURN NULL; END IF;\n      IF p_table_name='unit_stock_lot_v2' THEN v_result:=v_result-'initial_opened_cost'; END IF;",
        ),
    ]
    for signature, old, new in changes if upgrade else reversed(changes):
        _replace(signature, old if upgrade else new, new if upgrade else old)


FUNCTIONS = (
    "unit_legacy_opening_state(jsonb)",
    "unit_legacy_opening_time(text)",
    "trg_unit_opening_pack_guard()",
    "trg_unit_opening_pack_apply()",
    "trg_unit_initial_opened_basis_guard()",
    "assert_unit_legacy_opened_complete(uuid,uuid)",
    "post_unit_legacy_opened_transition(uuid,uuid,uuid,uuid,uuid)",
)


def upgrade() -> None:
    _sql(
        """
      ALTER TABLE public.unit_stock_lot_v2
        ADD COLUMN initial_opened_packs BIGINT NOT NULL DEFAULT 0 CHECK(initial_opened_packs>=0),
        ADD COLUMN initial_opened_units BIGINT NOT NULL DEFAULT 0 CHECK(initial_opened_units>=0),
        ADD COLUMN initial_opened_cost NUMERIC(14,2) NOT NULL DEFAULT 0 CHECK(initial_opened_cost>=0),
        DROP CONSTRAINT unit_stock_lot_v2_initial_closed_packs_check,
        ADD CONSTRAINT unit_stock_lot_v2_initial_closed_packs_check CHECK(initial_closed_packs BETWEEN 0 AND 99999999999),
        ADD CONSTRAINT ck_unit_lot_initial_capacity CHECK(initial_closed_packs+initial_opened_packs BETWEEN 1 AND 99999999999);
      ALTER TABLE public.unit_legacy_transition
        ADD COLUMN initialization_version INTEGER NOT NULL DEFAULT 1 CHECK(initialization_version IN (1,2)),
        ADD COLUMN legacy_quantity NUMERIC(14,3),
        DROP CONSTRAINT unit_legacy_transition_quantity_check,
        DROP CONSTRAINT unit_legacy_transition_check,
        DROP CONSTRAINT unit_legacy_transition_check1,
        ADD CONSTRAINT unit_legacy_transition_quantity_check CHECK(quantity BETWEEN 0 AND 99999999999),
        ADD CONSTRAINT unit_legacy_transition_check CHECK(base_units::NUMERIC=coalesce(legacy_quantity,quantity::NUMERIC)*(packaging->>'units_per_pack')::INTEGER),
        ADD CONSTRAINT unit_legacy_transition_check1 CHECK(total_cost=coalesce(legacy_quantity,quantity::NUMERIC)*purchase_cost_per_pack),
        ADD CONSTRAINT ck_unit_legacy_initialization_version CHECK(
          (initialization_version=1 AND legacy_quantity IS NULL AND quantity>0)
          OR (initialization_version=2 AND legacy_quantity IS NOT NULL AND legacy_quantity>0));
      ALTER TABLE public.unit_stock_operation_v2 DROP CONSTRAINT unit_stock_operation_v2_check,
        ADD CONSTRAINT unit_stock_operation_v2_check CHECK(base_units::NUMERIC<=quantity::NUMERIC*(packaging->>'units_per_pack')::INTEGER);
      ALTER TABLE public.unit_stock_entry_v2 DROP CONSTRAINT unit_stock_entry_v2_closed_delta_check,
        ADD CONSTRAINT unit_stock_entry_v2_closed_delta_check CHECK(closed_delta BETWEEN 0 AND 99999999999);
      CREATE TABLE public.unit_stock_opening_pack_v2 (
        tenant_id UUID NOT NULL,id UUID NOT NULL,branch_id UUID NOT NULL,catalog_id UUID NOT NULL,
        lot_id UUID NOT NULL,transition_id UUID NOT NULL,pack_ordinal BIGINT NOT NULL CHECK(pack_ordinal>0),
        opened_at TIMESTAMPTZ NOT NULL,state JSONB NOT NULL,remaining_units BIGINT NOT NULL CHECK(remaining_units>0),
        cost_amount NUMERIC(14,2) NOT NULL CHECK(cost_amount>=0),created_by UUID NOT NULL,created_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,lot_id,pack_ordinal),
        FOREIGN KEY(tenant_id,lot_id,catalog_id) REFERENCES public.unit_stock_lot_v2(tenant_id,id,catalog_id),
        FOREIGN KEY(tenant_id,transition_id) REFERENCES public.unit_legacy_transition(tenant_id,id));
      ALTER TABLE public.unit_stock_opening_pack_v2 OWNER TO aurum_schema_owner;
      ALTER TABLE public.unit_stock_opening_pack_v2 ENABLE ROW LEVEL SECURITY;
      ALTER TABLE public.unit_stock_opening_pack_v2 FORCE ROW LEVEL SECURITY;
      REVOKE ALL ON public.unit_stock_opening_pack_v2 FROM PUBLIC,aurum_app,aurum_support;
      GRANT SELECT ON public.unit_stock_opening_pack_v2 TO aurum_app,aurum_support;
      CREATE POLICY schema_owner ON public.unit_stock_opening_pack_v2 TO aurum_schema_owner USING(true) WITH CHECK(true);
      CREATE POLICY support_read ON public.unit_stock_opening_pack_v2 FOR SELECT TO aurum_support USING(true);
      CREATE POLICY tenant_read ON public.unit_stock_opening_pack_v2 FOR SELECT TO aurum_app USING(
        tenant_id=public.current_tenant_id()
        AND public.tenant_actor_has_scoped_permission(tenant_id,'batches.view',branch_id)
        AND public.tenant_actor_has_scoped_permission(tenant_id,'batches.view_costs',branch_id));
        """
    )
    for sql in (STATE_SQL, OPEN_COMPLETE_SQL, _opened_post_sql()):
        _sql(sql)
    for signature in FUNCTIONS:
        _sql(
            f"ALTER FUNCTION public.{signature} OWNER TO aurum_schema_owner; "
            f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC,aurum_app,aurum_support;"
        )
    _patches(upgrade=True)
    _sql(
        """
      CREATE TRIGGER a00_tenant_access BEFORE INSERT OR UPDATE OR DELETE ON public.unit_stock_opening_pack_v2
        FOR EACH ROW EXECUTE FUNCTION public.trg_enforce_tenant_access('inventory');
      CREATE TRIGGER a10_unit_opening_pack_guard BEFORE INSERT OR UPDATE OR DELETE ON public.unit_stock_opening_pack_v2
        FOR EACH ROW EXECUTE FUNCTION public.trg_unit_opening_pack_guard();
      CREATE TRIGGER b10_unit_opening_pack_apply AFTER INSERT ON public.unit_stock_opening_pack_v2
        FOR EACH ROW EXECUTE FUNCTION public.trg_unit_opening_pack_apply();
      CREATE TRIGGER z90_audit AFTER INSERT OR UPDATE OR DELETE ON public.unit_stock_opening_pack_v2
        FOR EACH ROW EXECUTE FUNCTION public.trg_audit_log();
      CREATE TRIGGER a09_unit_initial_opened_basis_guard BEFORE INSERT ON public.unit_stock_lot_v2
        FOR EACH ROW EXECUTE FUNCTION public.trg_unit_initial_opened_basis_guard();
        """
    )


def downgrade() -> None:
    _sql(
        """
      LOCK TABLE public.unit_legacy_transition,public.unit_stock_lot_v2,public.unit_stock_opening_pack_v2 IN ACCESS EXCLUSIVE MODE;
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.unit_stock_opening_pack_v2)
          OR EXISTS(SELECT 1 FROM public.unit_legacy_transition WHERE initialization_version=2)
          OR EXISTS(SELECT 1 FROM public.unit_stock_lot_v2 WHERE initial_opened_packs<>0 OR initial_opened_units<>0 OR initial_opened_cost<>0) THEN
          RAISE EXCEPTION 'legacy_opened_history_exists' USING ERRCODE='55000';
        END IF;
      END $$;
      DROP TRIGGER a09_unit_initial_opened_basis_guard ON public.unit_stock_lot_v2;
        """
    )
    _patches(upgrade=False)
    _sql("DROP TABLE public.unit_stock_opening_pack_v2;")
    for signature in reversed(FUNCTIONS):
        _sql(f"DROP FUNCTION public.{signature};")
    _sql(
        """
      ALTER TABLE public.unit_stock_lot_v2 DROP CONSTRAINT ck_unit_lot_initial_capacity,
        DROP COLUMN initial_opened_packs,DROP COLUMN initial_opened_units,DROP COLUMN initial_opened_cost,
        DROP CONSTRAINT unit_stock_lot_v2_initial_closed_packs_check,
        ADD CONSTRAINT unit_stock_lot_v2_initial_closed_packs_check CHECK(initial_closed_packs BETWEEN 1 AND 99999999999);
      ALTER TABLE public.unit_legacy_transition DROP CONSTRAINT ck_unit_legacy_initialization_version,
        DROP CONSTRAINT unit_legacy_transition_check,DROP CONSTRAINT unit_legacy_transition_check1,
        DROP CONSTRAINT unit_legacy_transition_quantity_check,DROP COLUMN initialization_version,DROP COLUMN legacy_quantity,
        ADD CONSTRAINT unit_legacy_transition_quantity_check CHECK(quantity BETWEEN 1 AND 99999999999),
        ADD CONSTRAINT unit_legacy_transition_check CHECK(base_units::NUMERIC=quantity::NUMERIC*(packaging->>'units_per_pack')::INTEGER),
        ADD CONSTRAINT unit_legacy_transition_check1 CHECK(total_cost=quantity::NUMERIC*purchase_cost_per_pack);
      ALTER TABLE public.unit_stock_operation_v2 DROP CONSTRAINT unit_stock_operation_v2_check,
        ADD CONSTRAINT unit_stock_operation_v2_check CHECK(base_units::NUMERIC=quantity::NUMERIC*(packaging->>'units_per_pack')::INTEGER);
      ALTER TABLE public.unit_stock_entry_v2 DROP CONSTRAINT unit_stock_entry_v2_closed_delta_check,
        ADD CONSTRAINT unit_stock_entry_v2_closed_delta_check CHECK(closed_delta BETWEEN 1 AND 99999999999);
        """
    )
