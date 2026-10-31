"""Read the current native retail draft and redacted Rx links under locks.

Revision ID: 0233
Revises: 0232
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0233"
down_revision: str | Sequence[str] | None = "0232"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SIGNATURE = "public.dispatch_edge_cash_sale_v1(uuid,uuid,bigint,uuid,jsonb,text)"
HELPER = "public.lock_edge_retail_cash_draft_v1(uuid,uuid,bigint,uuid,jsonb,text)"
UNIT_COLUMNS = (
    "id,tenant_id,branch_id,sale_id,catalog_id,position,sale_format,"
    "physical_packaging,quantity,discount_amount"
)
BINDING_COLUMNS = "id,tenant_id,branch_id,sale_id"
ORIGINAL_START = "  IF pg_catalog.jsonb_typeof(p_payload) <> 'object'\n    OR ("
DRAFT_START = """  IF p_payload ->> 'profile' = 'retail_cash.draft.v1' THEN
    RETURN public.lock_edge_retail_cash_draft_v1(
      p_operation_id,p_activation_id,p_writer_epoch,p_cashier_user_id,p_payload,p_request_hash
    );
  END IF;
  IF pg_catalog.jsonb_typeof(p_payload) <> 'object'
    OR ("""


def _patch_dispatcher(*, reverse: bool = False) -> None:
    connection = op.get_bind()
    definition = connection.exec_driver_sql(
        f"SELECT pg_catalog.pg_get_functiondef('{SIGNATURE}'::regprocedure)"
    ).scalar_one()
    old, new = (DRAFT_START, ORIGINAL_START) if reverse else (ORIGINAL_START, DRAFT_START)
    if not isinstance(definition, str) or definition.count(old) != 1:
        raise RuntimeError("Native Edge dispatcher draft body changed")
    if connection.exec_driver_sql(
        "SELECT has_schema_privilege('aurum_edge_cash_owner','public','CREATE')"
    ).scalar_one():
        raise RuntimeError("Native Edge owner unexpectedly has schema creation rights")
    owner_can_execute = connection.exec_driver_sql(
        f"SELECT has_function_privilege('aurum_edge_cash_owner','{SIGNATURE}','EXECUTE')"
    ).scalar_one()
    # Preserve the dispatcher OID, owner and exact pre-existing execution ACL.
    op.execute("GRANT CREATE ON SCHEMA public TO aurum_edge_cash_owner")
    op.execute("RESET ROLE")
    op.execute("SET LOCAL ROLE aurum_edge_cash_owner")
    if not owner_can_execute:
        op.execute(f"GRANT EXECUTE ON FUNCTION {SIGNATURE} TO aurum_edge_cash_owner")
    connection.exec_driver_sql(definition.replace(old, new, 1))
    if not owner_can_execute:
        op.execute(f"REVOKE EXECUTE ON FUNCTION {SIGNATURE} FROM aurum_edge_cash_owner")
    op.execute("RESET ROLE")
    op.execute("SET LOCAL ROLE aurum_schema_owner")
    op.execute("REVOKE CREATE ON SCHEMA public FROM aurum_edge_cash_owner")


DRAFT_SQL = r"""
CREATE FUNCTION public.lock_edge_retail_cash_draft_v1(
 p_operation_id UUID,p_activation_id UUID,p_writer_epoch BIGINT,p_cashier_user_id UUID,
 p_payload JSONB,p_request_hash TEXT
) RETURNS JSONB LANGUAGE plpgsql VOLATILE SECURITY INVOKER
SET search_path=pg_catalog,pg_temp SET row_security=on AS $function$
DECLARE
 v_prepare_payload JSONB; v_prepare_hash TEXT; v_preparation JSONB;
 v_inputs_payload JSONB; v_inputs_hash TEXT; v_inputs JSONB; v_scope JSONB;
 v_tenant UUID; v_sale UUID; v_row RECORD;
 v_unit_ids UUID[]:=ARRAY[]::UUID[]; v_legacy_ids UUID[]:=ARRAY[]::UUID[];
 v_rx_ids UUID[]:=ARRAY[]::UUID[];
 v_units JSONB; v_legacy JSONB; v_refs JSONB; v_result JSONB;
 v_bytes BIGINT:=1024;
BEGIN
 IF CURRENT_USER<>'aurum_edge_cash_owner'
   OR pg_catalog.jsonb_typeof(p_payload) IS DISTINCT FROM 'object'
   OR p_payload->>'profile' IS DISTINCT FROM 'retail_cash.draft.v1'
   OR pg_catalog.octet_length(p_payload::TEXT)>8192
   OR p_request_hash IS NULL OR p_request_hash !~ '^[0-9a-f]{64}$'
   OR p_request_hash IS DISTINCT FROM pg_catalog.encode(pg_catalog.sha256(
       pg_catalog.convert_to(public.edge_canonical_jsonb(p_payload),'UTF8')),'hex')
 THEN RAISE EXCEPTION 'Invalid native retail draft envelope' USING ERRCODE='22023'; END IF;
 v_prepare_payload:=p_payload||pg_catalog.jsonb_build_object('profile','retail_cash.prepare.v1');
 v_prepare_hash:=pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
   public.edge_canonical_jsonb(v_prepare_payload),'UTF8')),'hex');
 v_preparation:=public.prepare_edge_retail_cash_v1(
   p_operation_id,p_activation_id,p_writer_epoch,p_cashier_user_id,
   v_prepare_payload,v_prepare_hash);
 v_scope:=v_preparation->'scope';
 v_tenant:=(v_scope->>'tenant_id')::UUID;
 v_sale:=(v_preparation->>'sale_id')::UUID;
 IF EXISTS(SELECT 1 FROM public.pos_unit_payment_plan_binding binding
   WHERE binding.tenant_id=v_tenant AND binding.sale_id=v_sale
     AND NOT EXISTS(SELECT 1 FROM public.pos_unit_payment_plan_binding_cancel cancelled
       WHERE cancelled.tenant_id=binding.tenant_id AND cancelled.id=binding.id))
 THEN RAISE EXCEPTION 'Native retail draft has active payment binding'
   USING ERRCODE='55000'; END IF;

 -- The parent Sale lock excludes inserts/edits guarded by the current cart rules.
 -- Capture all current rows, not only the identities in the historical quote.
 FOR v_row IN SELECT item.id,
     pg_catalog.octet_length(item.physical_packaging::TEXT) AS json_bytes,
     pg_catalog.octet_length(item.sale_format) AS format_bytes
   FROM public.pos_unit_cart_item item
   WHERE item.tenant_id=v_tenant AND item.sale_id=v_sale
   ORDER BY item.id LIMIT 201 FOR NO KEY UPDATE LOOP
  v_unit_ids:=pg_catalog.array_append(v_unit_ids,v_row.id);
  v_bytes:=v_bytes+COALESCE(v_row.json_bytes,4)+6*v_row.format_bytes+768;
  IF v_bytes>1048576 THEN RAISE EXCEPTION 'Native retail draft payload limit exceeded'
    USING ERRCODE='54000'; END IF;
 END LOOP;
 FOR v_row IN SELECT item.id,pg_catalog.octet_length(item.currency) AS currency_bytes
   FROM public.sale_item item WHERE item.tenant_id=v_tenant AND item.sale_id=v_sale
   ORDER BY item.id LIMIT 201 FOR NO KEY UPDATE LOOP
  v_legacy_ids:=pg_catalog.array_append(v_legacy_ids,v_row.id);
  v_bytes:=v_bytes+6*v_row.currency_bytes+1024;
  IF v_bytes>1048576 THEN RAISE EXCEPTION 'Native retail draft payload limit exceeded'
    USING ERRCODE='54000'; END IF;
 END LOOP;
 IF pg_catalog.cardinality(v_unit_ids)+pg_catalog.cardinality(v_legacy_ids)
     NOT BETWEEN 1 AND 200
 THEN RAISE EXCEPTION 'Native retail current cart limit exceeded' USING ERRCODE='54000'; END IF;

 -- Some legacy/global Rx writers do not share the newer parent-sale guard.
 -- Hold actual eligible rows so coverage cannot be removed during this check.
 -- Sensitive detail columns are used only in this predicate and never returned.
 FOR v_row IN SELECT log.id FROM public.prescription_log log
   WHERE log.tenant_id=v_tenant AND log.sale_id=v_sale AND (
     pg_catalog.regexp_replace(log.prescription_number,'[[:space:]]','','g')<>'' OR
     pg_catalog.regexp_replace(log.doctor_name,'[[:space:]]','','g')<>'' OR
     pg_catalog.regexp_replace(log.doctor_license,'[[:space:]]','','g')<>'' OR
     pg_catalog.regexp_replace(log.patient_name,'[[:space:]]','','g')<>'' OR
     pg_catalog.regexp_replace(log.notes,'[[:space:]]','','g')<>'')
   ORDER BY log.id LIMIT 4097 FOR NO KEY UPDATE LOOP
  v_rx_ids:=pg_catalog.array_append(v_rx_ids,v_row.id);
  v_bytes:=v_bytes+160;
  IF v_bytes>1048576 THEN RAISE EXCEPTION 'Native retail draft payload limit exceeded'
    USING ERRCODE='54000'; END IF;
 END LOOP;
 IF pg_catalog.cardinality(v_rx_ids)>4096 THEN
  RAISE EXCEPTION 'Native retail prescription reference limit exceeded' USING ERRCODE='54000';
 END IF;
 v_inputs_payload:=p_payload||pg_catalog.jsonb_build_object('profile','retail_cash.inputs.v1');
 v_inputs_hash:=pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
   public.edge_canonical_jsonb(v_inputs_payload),'UTF8')),'hex');
 v_inputs:=public.lock_edge_retail_cash_inputs_v1(
   p_operation_id,p_activation_id,p_writer_epoch,p_cashier_user_id,v_inputs_payload,v_inputs_hash);
 -- Reject the combined budget before building cart arrays; never return a prefix.
 IF v_bytes+pg_catalog.octet_length(v_inputs::TEXT)>1048576 THEN
  RAISE EXCEPTION 'Native retail draft payload limit exceeded' USING ERRCODE='54000'; END IF;

 SELECT COALESCE(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
   'id',item.id,'tenant_id',item.tenant_id,'branch_id',item.branch_id,
   'sale_id',item.sale_id,'catalog_id',item.catalog_id,'position',item.position,
   'sale_format',item.sale_format,'physical_packaging',item.physical_packaging,
   'quantity',item.quantity,
   'discount_amount',pg_catalog.to_char(item.discount_amount,'FM999999999990.00'))
   ORDER BY item.position,item.id),'[]'::JSONB) INTO v_units
 FROM public.pos_unit_cart_item item WHERE item.tenant_id=v_tenant
   AND item.sale_id=v_sale AND item.id=ANY(v_unit_ids);
 SELECT COALESCE(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
   'id',item.id,'tenant_id',item.tenant_id,'sale_id',item.sale_id,
   'catalog_id',item.catalog_id,'batch_id',item.batch_id,'position',item.position,
   'quantity',pg_catalog.to_char(item.qty,'FM999999999990.000'),
   'unit_price',pg_catalog.to_char(item.unit_price,'FM999999999990.00'),
   'discount_amount',pg_catalog.to_char(item.discount_amount,'FM999999999990.00'),
   'total_price',pg_catalog.to_char(item.total_price,'FM999999999990.00'),
   'currency',item.currency,'parent_sale_item_id',item.parent_sale_item_id)
   ORDER BY item.position,item.id),'[]'::JSONB) INTO v_legacy
 FROM public.sale_item item WHERE item.tenant_id=v_tenant
   AND item.sale_id=v_sale AND item.id=ANY(v_legacy_ids);
 SELECT COALESCE(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
   'sale_item_id',log.sale_item_id,'unit_cart_item_id',log.unit_cart_item_id)
   ORDER BY log.id),'[]'::JSONB) INTO v_refs
 FROM public.prescription_log log WHERE log.tenant_id=v_tenant
   AND log.sale_id=v_sale AND log.id=ANY(v_rx_ids);

 IF v_preparation IS DISTINCT FROM public.prepare_edge_retail_cash_v1(
   p_operation_id,p_activation_id,p_writer_epoch,p_cashier_user_id,
   v_prepare_payload,v_prepare_hash) OR v_preparation IS DISTINCT FROM v_inputs->'preparation'
 THEN RAISE EXCEPTION 'Native retail draft admission changed' USING ERRCODE='55000'; END IF;
 IF EXISTS(SELECT 1 FROM public.pos_unit_payment_plan_binding binding
   WHERE binding.tenant_id=v_tenant AND binding.sale_id=v_sale
     AND NOT EXISTS(SELECT 1 FROM public.pos_unit_payment_plan_binding_cancel cancelled
       WHERE cancelled.tenant_id=binding.tenant_id AND cancelled.id=binding.id))
 THEN RAISE EXCEPTION 'Native retail draft has active payment binding'
   USING ERRCODE='55000'; END IF;
 v_result:=pg_catalog.jsonb_build_object('schema_version',1,'state','current_draft_not_executed',
   'inputs',v_inputs,'unit_items',v_units,'legacy_items',v_legacy,'rx_refs',v_refs);
 IF pg_catalog.octet_length(v_result::TEXT)>1048576 THEN
  RAISE EXCEPTION 'Native retail draft payload limit exceeded' USING ERRCODE='54000'; END IF;
 RETURN v_result;
END
$function$;
"""


def _scope(table: str) -> str:
    return f"""EXISTS(
      SELECT 1 FROM public.edge_cash_node_identity identity
      JOIN pg_catalog.pg_roles role
        ON role.rolname=identity.database_role AND role.oid=identity.database_role_oid
      JOIN public.sale draft ON draft.tenant_id=identity.tenant_id
        AND draft.branch_id=identity.branch_id AND draft.register_id=identity.register_id
      WHERE identity.database_role=SESSION_USER
        AND identity.database_role='aurum_edge_node_'
          ||pg_catalog.replace(identity.edge_node_id::TEXT,'-','')
        AND draft.tenant_id={table}.tenant_id AND draft.id={table}.sale_id)"""


def upgrade() -> None:
    op.get_bind().exec_driver_sql(DRAFT_SQL)
    op.execute(f"REVOKE ALL ON FUNCTION {HELPER} FROM PUBLIC,aurum_app,aurum_support")
    op.execute(f"REVOKE ALL ON FUNCTION {HELPER} FROM aurum_edge_cash_executor")
    op.execute(f"GRANT EXECUTE ON FUNCTION {HELPER} TO aurum_edge_cash_owner")
    op.execute(
        f"GRANT SELECT ({UNIT_COLUMNS}) ON public.pos_unit_cart_item TO aurum_edge_cash_owner"
    )
    for table in ("pos_unit_payment_plan_binding", "pos_unit_payment_plan_binding_cancel"):
        op.execute(f"GRANT SELECT ({BINDING_COLUMNS}) ON public.{table} TO aurum_edge_cash_owner")
    for table in (
        "pos_unit_cart_item",
        "sale_item",
        "prescription_log",
        "pos_unit_payment_plan_binding",
        "pos_unit_payment_plan_binding_cancel",
    ):
        scope = _scope(table)
        op.execute(
            f"CREATE POLICY edge_cash_draft_read ON public.{table} "
            f"FOR SELECT TO aurum_edge_cash_owner USING ({scope})"
        )
        if table in {"pos_unit_cart_item", "sale_item", "prescription_log"}:
            op.execute(f"GRANT UPDATE (id) ON public.{table} TO aurum_edge_cash_owner")
            op.execute(
                f"CREATE POLICY edge_cash_draft_lock ON public.{table} "
                f"FOR UPDATE TO aurum_edge_cash_owner USING ({scope}) WITH CHECK(false)"
            )
            op.execute(
                f"CREATE POLICY edge_cash_draft_no_write ON public.{table} AS RESTRICTIVE "
                f"FOR UPDATE TO aurum_edge_cash_owner USING ({scope}) WITH CHECK(false)"
            )
    _patch_dispatcher()


def downgrade() -> None:
    _patch_dispatcher(reverse=True)
    for table in (
        "pos_unit_cart_item",
        "sale_item",
        "prescription_log",
        "pos_unit_payment_plan_binding",
        "pos_unit_payment_plan_binding_cancel",
    ):
        op.execute(f"DROP POLICY edge_cash_draft_read ON public.{table}")
        if table in {"pos_unit_cart_item", "sale_item", "prescription_log"}:
            op.execute(f"DROP POLICY edge_cash_draft_lock ON public.{table}")
            op.execute(f"DROP POLICY edge_cash_draft_no_write ON public.{table}")
            op.execute(f"REVOKE UPDATE (id) ON public.{table} FROM aurum_edge_cash_owner")
    op.execute(
        f"REVOKE SELECT ({UNIT_COLUMNS}) ON public.pos_unit_cart_item FROM aurum_edge_cash_owner"
    )
    for table in ("pos_unit_payment_plan_binding", "pos_unit_payment_plan_binding_cancel"):
        op.execute(
            f"REVOKE SELECT ({BINDING_COLUMNS}) ON public.{table} FROM aurum_edge_cash_owner"
        )
    op.execute(f"DROP FUNCTION {HELPER}")
