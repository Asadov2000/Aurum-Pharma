"""Lock current native retail inputs without executing a sale or moving stock.

Revision ID: 0232
Revises: 0231
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0232"
down_revision: str | Sequence[str] | None = "0231"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SIGNATURE = "public.dispatch_edge_cash_sale_v1(uuid,uuid,bigint,uuid,jsonb,text)"
HELPER = "public.lock_edge_retail_cash_inputs_v1(uuid,uuid,bigint,uuid,jsonb,text)"
LOT_COLUMNS = (
    "id,tenant_id,branch_id,catalog_id,expires_at,created_at,packaging,closed_packs,is_blocked"
)
PACK_COLUMNS = "id,tenant_id,branch_id,catalog_id,lot_id,opened_at,state,remaining_units"
ORIGINAL_START = "  IF pg_catalog.jsonb_typeof(p_payload) <> 'object'\n    OR ("
INPUTS_START = """  IF p_payload ->> 'profile' = 'retail_cash.inputs.v1' THEN
    RETURN public.lock_edge_retail_cash_inputs_v1(
      p_operation_id,p_activation_id,p_writer_epoch,p_cashier_user_id,p_payload,p_request_hash
    );
  END IF;
  IF pg_catalog.jsonb_typeof(p_payload) <> 'object'
    OR ("""


def _patch_dispatcher(*, reverse: bool = False) -> None:
    definition = (
        op.get_bind()
        .exec_driver_sql(f"SELECT pg_catalog.pg_get_functiondef('{SIGNATURE}'::regprocedure)")
        .scalar_one()
    )
    old, new = (INPUTS_START, ORIGINAL_START) if reverse else (ORIGINAL_START, INPUTS_START)
    if not isinstance(definition, str) or definition.count(old) != 1:
        raise RuntimeError("Native Edge dispatcher preparation body changed")
    if (
        op.get_bind()
        .exec_driver_sql("SELECT has_schema_privilege('aurum_edge_cash_owner','public','CREATE')")
        .scalar_one()
    ):
        raise RuntimeError("Native Edge owner unexpectedly has schema creation rights")
    owner_can_execute = (
        op.get_bind()
        .exec_driver_sql(
            f"SELECT has_function_privilege('aurum_edge_cash_owner','{SIGNATURE}','EXECUTE')"
        )
        .scalar_one()
    )
    # As in 0231, replacement retains OID, owner and the exact pre-existing ACL.
    # Temporary CREATE/owner EXECUTE privileges do not survive this transaction.
    op.execute("GRANT CREATE ON SCHEMA public TO aurum_edge_cash_owner")
    op.execute("RESET ROLE")
    op.execute("SET LOCAL ROLE aurum_edge_cash_owner")
    if not owner_can_execute:
        op.execute(f"GRANT EXECUTE ON FUNCTION {SIGNATURE} TO aurum_edge_cash_owner")
    op.get_bind().exec_driver_sql(definition.replace(old, new, 1))
    if not owner_can_execute:
        op.execute(f"REVOKE EXECUTE ON FUNCTION {SIGNATURE} FROM aurum_edge_cash_owner")
    op.execute("RESET ROLE")
    op.execute("SET LOCAL ROLE aurum_schema_owner")
    op.execute("REVOKE CREATE ON SCHEMA public FROM aurum_edge_cash_owner")


INPUTS_SQL = r"""
CREATE FUNCTION public.lock_edge_retail_cash_inputs_v1(
 p_operation_id UUID,p_activation_id UUID,p_writer_epoch BIGINT,p_cashier_user_id UUID,
 p_payload JSONB,p_request_hash TEXT
) RETURNS JSONB LANGUAGE plpgsql VOLATILE SECURITY INVOKER
SET search_path=pg_catalog,pg_temp SET row_security=on AS $function$
DECLARE
 v_prepare_payload JSONB; v_prepare_hash TEXT; v_preparation JSONB; v_scope JSONB;
 v_quote JSONB; v_catalogs JSONB; v_physical JSONB; v_legacy JSONB; v_result JSONB;
 v_tenant UUID; v_branch UUID; v_catalog_ids UUID[];
 v_physical_ids UUID[]; v_legacy_ids UUID[];
 v_lot_ids UUID[]:=ARRAY[]::UUID[]; v_pack_ids UUID[]:=ARRAY[]::UUID[];
 v_batch_ids UUID[]:=ARRAY[]::UUID[]; v_row RECORD;
 v_count BIGINT:=0; v_stock_date DATE; v_input_bytes BIGINT;
BEGIN
 IF CURRENT_USER<>'aurum_edge_cash_owner'
   OR pg_catalog.jsonb_typeof(p_payload) IS DISTINCT FROM 'object'
   OR p_payload->>'profile' IS DISTINCT FROM 'retail_cash.inputs.v1'
   OR pg_catalog.octet_length(p_payload::TEXT)>8192
   OR p_request_hash IS NULL OR p_request_hash !~ '^[0-9a-f]{64}$'
   OR p_request_hash IS DISTINCT FROM pg_catalog.encode(pg_catalog.sha256(
       pg_catalog.convert_to(public.edge_canonical_jsonb(p_payload),'UTF8')),'hex')
 THEN RAISE EXCEPTION 'Invalid native retail inputs envelope' USING ERRCODE='22023'; END IF;
 -- Reuse every 0231 identity, scope, authorization, intent and writer check.
 -- Only the profile and its canonical hash change inside this trusted function.
 v_prepare_payload:=p_payload||pg_catalog.jsonb_build_object('profile','retail_cash.prepare.v1');
 v_prepare_hash:=pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
   public.edge_canonical_jsonb(v_prepare_payload),'UTF8')),'hex');
 v_preparation:=public.prepare_edge_retail_cash_v1(
   p_operation_id,p_activation_id,p_writer_epoch,p_cashier_user_id,
   v_prepare_payload,v_prepare_hash);
 v_scope:=v_preparation->'scope';
 v_tenant:=(v_scope->>'tenant_id')::UUID; v_branch:=(v_scope->>'branch_id')::UUID;
 SELECT quote_payload INTO v_quote FROM public.pos_unit_cash_intent
 WHERE tenant_id=v_tenant AND id=p_operation_id
   AND branch_id=v_branch AND sale_id=(v_preparation->>'sale_id')::UUID;
 IF pg_catalog.jsonb_typeof(v_quote->'lines') IS DISTINCT FROM 'array'
   OR pg_catalog.jsonb_array_length(v_quote->'lines') NOT BETWEEN 1 AND 200
 THEN RAISE EXCEPTION 'Native retail saved quote unavailable' USING ERRCODE='55000'; END IF;
 SELECT pg_catalog.array_agg(DISTINCT (line->>'catalog_id')::UUID
     ORDER BY (line->>'catalog_id')::UUID),
   pg_catalog.array_agg(DISTINCT (line->>'catalog_id')::UUID
     ORDER BY (line->>'catalog_id')::UUID) FILTER(WHERE line->>'inventory_kind'='physical_unit'),
   pg_catalog.array_agg(DISTINCT (line->>'catalog_id')::UUID
     ORDER BY (line->>'catalog_id')::UUID) FILTER(WHERE line->>'inventory_kind'='legacy_pack')
 INTO v_catalog_ids,v_physical_ids,v_legacy_ids
 FROM pg_catalog.jsonb_array_elements(v_quote->'lines') line;
 IF pg_catalog.cardinality(v_catalog_ids)>200 THEN
  RAISE EXCEPTION 'Native retail catalog input limit exceeded' USING ERRCODE='54000'; END IF;
 -- Conservative UTF-8 budget includes every variable JSON cell before aggregation.
 -- Fixed allowances cover bounded UUID/date/number fields and JSON key overhead.
 v_input_bytes:=pg_catalog.octet_length(v_preparation::TEXT)
   +pg_catalog.octet_length(v_quote::TEXT)+512;
 IF v_input_bytes>1048576 THEN
  RAISE EXCEPTION 'Native retail input payload limit exceeded' USING ERRCODE='54000'; END IF;

 -- Deterministic catalog -> physical lots -> opened packs -> legacy batch locks.
 -- Row locks reserve these facts only within the caller's current transaction.
 FOR v_row IN SELECT catalog.id,
     pg_catalog.octet_length(catalog.unit_sale_preparation::TEXT) AS json_bytes,
     pg_catalog.octet_length(catalog.currency) AS currency_bytes
   FROM public.tenant_catalog catalog
   WHERE catalog.tenant_id=v_tenant AND catalog.id=ANY(v_catalog_ids)
   ORDER BY catalog.id FOR NO KEY UPDATE LOOP
  v_count:=v_count+1;
  v_input_bytes:=v_input_bytes+COALESCE(v_row.json_bytes,4)+v_row.currency_bytes+512;
  IF v_input_bytes>1048576 THEN
   RAISE EXCEPTION 'Native retail input payload limit exceeded' USING ERRCODE='54000'; END IF;
 END LOOP;
 IF v_count IS DISTINCT FROM pg_catalog.cardinality(v_catalog_ids)::BIGINT THEN
  RAISE EXCEPTION 'Native retail catalog inputs unavailable' USING ERRCODE='55000'; END IF;
 FOR v_row IN SELECT lot.id,pg_catalog.octet_length(lot.packaging::TEXT) AS json_bytes
   FROM public.unit_stock_lot_v2 lot
   WHERE lot.tenant_id=v_tenant AND lot.branch_id=v_branch
     AND lot.catalog_id=ANY(v_physical_ids)
   ORDER BY lot.id LIMIT 4097 FOR NO KEY UPDATE LOOP
  v_lot_ids:=pg_catalog.array_append(v_lot_ids,v_row.id);
  v_input_bytes:=v_input_bytes+v_row.json_bytes+512;
  IF v_input_bytes>1048576 THEN
   RAISE EXCEPTION 'Native retail input payload limit exceeded' USING ERRCODE='54000'; END IF;
 END LOOP;
 IF pg_catalog.cardinality(v_lot_ids)>4096 THEN
  RAISE EXCEPTION 'Native retail physical input limit exceeded' USING ERRCODE='54000'; END IF;
 FOR v_row IN SELECT pack.id,pg_catalog.octet_length(pack.state::TEXT) AS json_bytes
   FROM public.unit_stock_opened_pack_v2 pack
   WHERE pack.tenant_id=v_tenant AND pack.branch_id=v_branch AND pack.lot_id=ANY(v_lot_ids)
     AND pack.catalog_id=ANY(v_physical_ids) AND pack.remaining_units>0
   ORDER BY pack.id LIMIT 16385 FOR NO KEY UPDATE LOOP
  v_pack_ids:=pg_catalog.array_append(v_pack_ids,v_row.id);
  v_input_bytes:=v_input_bytes+v_row.json_bytes+160;
  IF v_input_bytes>1048576 THEN
   RAISE EXCEPTION 'Native retail input payload limit exceeded' USING ERRCODE='54000'; END IF;
 END LOOP;
 IF pg_catalog.cardinality(v_pack_ids)>16384 THEN
  RAISE EXCEPTION 'Native retail opened input limit exceeded' USING ERRCODE='54000'; END IF;
 FOR v_row IN SELECT batch.id FROM public.batch batch
   WHERE batch.tenant_id=v_tenant AND batch.branch_id=v_branch
     AND batch.catalog_id=ANY(v_legacy_ids)
   ORDER BY batch.id LIMIT 4097 FOR NO KEY UPDATE LOOP
  v_batch_ids:=pg_catalog.array_append(v_batch_ids,v_row.id);
  v_input_bytes:=v_input_bytes+512;
  IF v_input_bytes>1048576 THEN
   RAISE EXCEPTION 'Native retail input payload limit exceeded' USING ERRCODE='54000'; END IF;
 END LOOP;
 IF pg_catalog.cardinality(v_batch_ids)>4096 THEN
  RAISE EXCEPTION 'Native retail legacy input limit exceeded' USING ERRCODE='54000'; END IF;

 SELECT COALESCE(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
   'id',catalog.id,'tenant_id',catalog.tenant_id,
   'base_price',pg_catalog.to_char(catalog.base_price,'FM999999999990.00'),
   'unit_sale_preparation',catalog.unit_sale_preparation,'dispensing_type',catalog.dispensing_type,
   'currency',catalog.currency,'is_active',catalog.is_active,
   'deleted_at',pg_catalog.to_char(catalog.deleted_at AT TIME ZONE 'UTC',
     'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')) ORDER BY catalog.id),'[]'::JSONB) INTO v_catalogs
 FROM public.tenant_catalog catalog
 WHERE catalog.tenant_id=v_tenant AND catalog.id=ANY(v_catalog_ids);
 SELECT COALESCE(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
   'id',lot.id,'tenant_id',lot.tenant_id,'branch_id',lot.branch_id,'catalog_id',lot.catalog_id,
   'expires_at',lot.expires_at,'created_at',pg_catalog.to_char(lot.created_at AT TIME ZONE 'UTC',
     'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
   'packaging',lot.packaging,'closed_packs',lot.closed_packs,'is_blocked',lot.is_blocked,
   'opened_packs',(SELECT COALESCE(pg_catalog.jsonb_agg(pack.state||pg_catalog.jsonb_build_object(
       'id',pack.id,'opened_at',pg_catalog.to_char(pack.opened_at AT TIME ZONE 'UTC',
         'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')) ORDER BY pack.id),'[]'::JSONB)
     FROM public.unit_stock_opened_pack_v2 pack
     WHERE pack.tenant_id=v_tenant AND pack.branch_id=v_branch AND pack.lot_id=lot.id
       AND pack.catalog_id=lot.catalog_id AND pack.id=ANY(v_pack_ids)))
   ORDER BY lot.id),'[]'::JSONB) INTO v_physical
 FROM public.unit_stock_lot_v2 lot
 WHERE lot.tenant_id=v_tenant AND lot.branch_id=v_branch AND lot.id=ANY(v_lot_ids);
 SELECT COALESCE(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
   'id',batch.id,'tenant_id',batch.tenant_id,'branch_id',batch.branch_id,
   'catalog_id',batch.catalog_id,'expires_at',batch.expires_at,
   'created_at',pg_catalog.to_char(batch.created_at AT TIME ZONE 'UTC',
     'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
   'qty_remaining',pg_catalog.to_char(batch.qty_remaining,'FM999999999990.000'),
   'sale_price',pg_catalog.to_char(batch.sale_price,'FM999999999990.00'),
   'is_blocked',batch.is_blocked) ORDER BY batch.id),'[]'::JSONB) INTO v_legacy
 FROM public.batch batch WHERE batch.tenant_id=v_tenant AND batch.branch_id=v_branch
   AND batch.id=ANY(v_batch_ids);

 -- Permission and writer state can change while this transaction waits for stock.
 IF v_preparation IS DISTINCT FROM public.prepare_edge_retail_cash_v1(
   p_operation_id,p_activation_id,p_writer_epoch,p_cashier_user_id,
   v_prepare_payload,v_prepare_hash) THEN
  RAISE EXCEPTION 'Native retail preparation changed' USING ERRCODE='55000'; END IF;
 SELECT (pg_catalog.clock_timestamp() AT TIME ZONE COALESCE(
   settings.report_timezone,'Asia/Dushanbe'))::DATE INTO v_stock_date
 FROM public.tenant_settings settings WHERE settings.tenant_id=v_tenant;
 IF v_stock_date IS NULL THEN
  RAISE EXCEPTION 'Native retail stock date unavailable' USING ERRCODE='55000'; END IF;
 v_result:=pg_catalog.jsonb_build_object('schema_version',1,'state','locked_inputs_not_executed',
   'preparation',v_preparation,'stock_date',v_stock_date,'saved_quote',v_quote,
   'catalogs',v_catalogs,'physical_stock',v_physical,'legacy_stock',v_legacy);
 IF pg_catalog.octet_length(v_result::TEXT)>1048576 THEN
  RAISE EXCEPTION 'Native retail input payload limit exceeded' USING ERRCODE='54000'; END IF;
 RETURN v_result;
END
$function$;
"""


def _scope(table: str, *, branch: bool) -> str:
    branch_clause = f"AND identity.branch_id={table}.branch_id" if branch else ""
    return f"""EXISTS(
      SELECT 1 FROM public.edge_cash_node_identity identity
      JOIN pg_catalog.pg_roles role
        ON role.rolname=identity.database_role AND role.oid=identity.database_role_oid
      WHERE identity.database_role=SESSION_USER
        AND identity.database_role='aurum_edge_node_'
          ||pg_catalog.replace(identity.edge_node_id::TEXT,'-','')
        AND identity.tenant_id={table}.tenant_id {branch_clause})"""


def upgrade() -> None:
    op.get_bind().exec_driver_sql(INPUTS_SQL)
    op.execute(f"REVOKE ALL ON FUNCTION {HELPER} FROM PUBLIC,aurum_app,aurum_support")
    op.execute(f"REVOKE ALL ON FUNCTION {HELPER} FROM aurum_edge_cash_executor")
    op.execute(f"GRANT EXECUTE ON FUNCTION {HELPER} TO aurum_edge_cash_owner")
    for table, columns in (
        ("unit_stock_lot_v2", LOT_COLUMNS),
        ("unit_stock_opened_pack_v2", PACK_COLUMNS),
    ):
        scope = _scope(table, branch=True)
        op.execute(f"GRANT SELECT ({columns}) ON public.{table} TO aurum_edge_cash_owner")
        op.execute(f"GRANT UPDATE (id) ON public.{table} TO aurum_edge_cash_owner")
        op.execute(
            f"CREATE POLICY edge_cash_inputs_read ON public.{table} "
            f"FOR SELECT TO aurum_edge_cash_owner USING ({scope})"
        )
        op.execute(
            f"CREATE POLICY edge_cash_inputs_lock ON public.{table} "
            f"FOR UPDATE TO aurum_edge_cash_owner USING ({scope}) WITH CHECK(false)"
        )
        # A restrictive check also blocks DML if a permissive policy is added later.
        op.execute(
            f"CREATE POLICY edge_cash_inputs_no_write ON public.{table} AS RESTRICTIVE "
            f"FOR UPDATE TO aurum_edge_cash_owner USING ({scope}) WITH CHECK(false)"
        )
    # PostgreSQL requires any UPDATE-column privilege for SELECT FOR NO KEY UPDATE.
    # The existing public catalog policy is permissive: only a restrictive false
    # WITH CHECK reliably forbids actual UPDATE while still permitting row locks.
    op.execute("GRANT UPDATE (id) ON public.tenant_catalog TO aurum_edge_cash_owner")
    scope = _scope("tenant_catalog", branch=False)
    op.execute(
        "CREATE POLICY edge_cash_inputs_no_write ON public.tenant_catalog AS RESTRICTIVE "
        f"FOR UPDATE TO aurum_edge_cash_owner USING ({scope}) WITH CHECK(false)"
    )
    _patch_dispatcher()


def downgrade() -> None:
    _patch_dispatcher(reverse=True)
    op.execute("DROP POLICY edge_cash_inputs_no_write ON public.tenant_catalog")
    op.execute("REVOKE UPDATE (id) ON public.tenant_catalog FROM aurum_edge_cash_owner")
    for table, columns in (
        ("unit_stock_lot_v2", LOT_COLUMNS),
        ("unit_stock_opened_pack_v2", PACK_COLUMNS),
    ):
        for policy in (
            "edge_cash_inputs_read",
            "edge_cash_inputs_lock",
            "edge_cash_inputs_no_write",
        ):
            op.execute(f"DROP POLICY {policy} ON public.{table}")
        op.execute(f"REVOKE SELECT ({columns}) ON public.{table} FROM aurum_edge_cash_owner")
        op.execute(f"REVOKE UPDATE (id) ON public.{table} FROM aurum_edge_cash_owner")
    op.execute(f"DROP FUNCTION {HELPER}")
