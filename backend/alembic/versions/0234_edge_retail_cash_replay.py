"""Read an already anchored native retail cash result without executing a sale.

Revision ID: 0234
Revises: 0233
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0234"
down_revision: str | Sequence[str] | None = "0233"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SIGNATURE = "public.dispatch_edge_cash_sale_v1(uuid,uuid,bigint,uuid,jsonb,text)"
HELPER = "public.replay_edge_retail_cash_v1(uuid,uuid,bigint,uuid,jsonb,text)"
MARKER_COLUMNS = (
    "tenant_id,id,sale_id,branch_id,register_id,shift_id,cashier_user_id,cash_intent_id,"
    "payment_plan_intent_id,stock_command_id,payment_id,payment_journal_version,"
    "request_hash,result_payload,receipt_payload"
)
VALIDATORS = (
    "public.assert_physical_checkout(uuid,uuid,boolean)",
    "public.assert_mixed_checkout(uuid,uuid,boolean)",
)
ORIGINAL_START = "BEGIN\n  IF p_payload ->> 'profile' = 'retail_cash.prepare.v1' THEN"
REPLAY_START = """BEGIN
  IF p_payload ->> 'profile' = 'retail_cash.replay.v1' THEN
    RETURN public.replay_edge_retail_cash_v1(
      p_operation_id,p_activation_id,p_writer_epoch,p_cashier_user_id,p_payload,p_request_hash
    );
  END IF;
  IF p_payload ->> 'profile' = 'retail_cash.prepare.v1' THEN"""


def _patch_dispatcher(*, reverse: bool = False) -> None:
    connection = op.get_bind()
    definition = connection.exec_driver_sql(
        f"SELECT pg_catalog.pg_get_functiondef('{SIGNATURE}'::regprocedure)"
    ).scalar_one()
    old, new = (REPLAY_START, ORIGINAL_START) if reverse else (ORIGINAL_START, REPLAY_START)
    if not isinstance(definition, str) or definition.count(old) != 1:
        raise RuntimeError("Native Edge dispatcher replay body changed")
    if connection.exec_driver_sql(
        "SELECT has_schema_privilege('aurum_edge_cash_owner','public','CREATE')"
    ).scalar_one():
        raise RuntimeError("Native Edge owner unexpectedly has schema creation rights")
    owner_can_execute = connection.exec_driver_sql(
        f"SELECT has_function_privilege('aurum_edge_cash_owner','{SIGNATURE}','EXECUTE')"
    ).scalar_one()
    # Preserve the existing dispatcher OID, owner and exact execution ACL.
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


REPLAY_SQL = r"""
CREATE FUNCTION public.replay_edge_retail_cash_v1(
 p_operation_id UUID,p_activation_id UUID,p_writer_epoch BIGINT,p_cashier_user_id UUID,
 p_payload JSONB,p_request_hash TEXT
) RETURNS JSONB LANGUAGE plpgsql VOLATILE SECURITY INVOKER
SET search_path=pg_catalog,pg_temp SET row_security=on AS $function$
DECLARE
 v_identity public.edge_cash_node_identity%ROWTYPE;
 v_existing public.edge_cash_command%ROWTYPE;
 v_sale public.sale%ROWTYPE;
 v_intent RECORD; v_marker RECORD;
 v_scope JSONB; v_confirmation JSONB; v_saved_confirmation JSONB;
 v_complete_payload JSONB; v_request_hash TEXT; v_cash_hash TEXT; v_wrapper JSONB;
BEGIN
 IF CURRENT_USER<>'aurum_edge_cash_owner' OR p_operation_id IS NULL
   OR (pg_catalog.get_byte(pg_catalog.uuid_send(p_operation_id),6)>>4)<>4
   OR (pg_catalog.get_byte(pg_catalog.uuid_send(p_operation_id),8)&192)<>128
   OR p_activation_id IS NULL OR p_writer_epoch IS NULL OR p_writer_epoch<=0
   OR p_cashier_user_id IS NULL OR p_request_hash IS NULL
   OR p_request_hash !~ '^[0-9a-f]{64}$'
   OR pg_catalog.jsonb_typeof(p_payload) IS DISTINCT FROM 'object'
 THEN RAISE EXCEPTION 'Invalid native retail replay envelope' USING ERRCODE='22023'; END IF;
 IF pg_catalog.octet_length(p_payload::TEXT)>8192
   OR NOT p_payload ?& ARRAY['profile','kind','scope','confirmation']
   OR p_payload-ARRAY['profile','kind','scope','confirmation']<>'{}'::JSONB
   OR p_payload->>'profile' IS DISTINCT FROM 'retail_cash.replay.v1'
   OR COALESCE(p_payload->>'kind','') NOT IN ('physical_cash','mixed_cash')
   OR pg_catalog.jsonb_typeof(p_payload->'scope') IS DISTINCT FROM 'object'
   OR pg_catalog.jsonb_typeof(p_payload->'confirmation') IS DISTINCT FROM 'object'
 THEN RAISE EXCEPTION 'Invalid native retail replay payload' USING ERRCODE='22023'; END IF;
 v_scope:=p_payload->'scope'; v_confirmation:=p_payload->'confirmation';
 IF NOT v_scope ?& ARRAY['activation_id','tenant_id','branch_id','edge_node_id',
       'register_id','writer_epoch','user_id','capability']
   OR v_scope-ARRAY['activation_id','tenant_id','branch_id','edge_node_id',
       'register_id','writer_epoch','user_id','capability']<>'{}'::JSONB
   OR NOT v_confirmation ?& ARRAY['schema_version','operation_id','sale_id',
       'expected_revision_hash','expected_total_amount','cash_tendered','currency']
   OR v_confirmation-ARRAY['schema_version','operation_id','sale_id',
       'expected_revision_hash','expected_total_amount','cash_tendered','currency']<>'{}'::JSONB
   OR v_confirmation->'schema_version' IS DISTINCT FROM '2'::JSONB
   OR v_confirmation->>'operation_id' IS DISTINCT FROM p_operation_id::TEXT
   OR v_confirmation->>'currency' IS DISTINCT FROM 'TJS'
   OR pg_catalog.jsonb_typeof(v_confirmation->'sale_id') IS DISTINCT FROM 'string'
   OR COALESCE(v_confirmation->>'sale_id','')
       !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
   OR pg_catalog.jsonb_typeof(v_confirmation->'expected_revision_hash') IS DISTINCT FROM 'string'
   OR COALESCE(v_confirmation->>'expected_revision_hash','') !~ '^[0-9a-f]{64}$'
   OR pg_catalog.jsonb_typeof(v_confirmation->'expected_total_amount') IS DISTINCT FROM 'string'
   OR pg_catalog.jsonb_typeof(v_confirmation->'cash_tendered') IS DISTINCT FROM 'string'
   OR COALESCE(v_confirmation->>'expected_total_amount','') !~ '^(0|[1-9][0-9]{0,11})\.[0-9]{2}$'
   OR COALESCE(v_confirmation->>'cash_tendered','') !~ '^(0|[1-9][0-9]{0,11})\.[0-9]{2}$'
 THEN RAISE EXCEPTION 'Invalid native retail replay fields' USING ERRCODE='22023'; END IF;
 IF (v_confirmation->>'expected_total_amount')::NUMERIC<=0
   OR (v_confirmation->>'cash_tendered')::NUMERIC
       <(v_confirmation->>'expected_total_amount')::NUMERIC
   OR p_request_hash IS DISTINCT FROM pg_catalog.encode(pg_catalog.sha256(
       pg_catalog.convert_to(public.edge_canonical_jsonb(p_payload),'UTF8')),'hex')
 THEN RAISE EXCEPTION 'Native retail replay hash or amounts differ' USING ERRCODE='22023';
 END IF;

 -- As in the legacy dispatcher, wait for the operation before reading identity:
 -- revocation or a changed role binding must not survive an operation-lock wait.
 PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(p_operation_id::TEXT,0));
 -- Identity, not caller-supplied app settings, selects the original command scope.
 SELECT identity.* INTO v_identity FROM public.edge_cash_node_identity identity
 JOIN pg_catalog.pg_roles role
   ON role.rolname=identity.database_role AND role.oid=identity.database_role_oid
 WHERE identity.database_role=SESSION_USER
   AND identity.database_role='aurum_edge_node_'
     ||pg_catalog.replace(identity.edge_node_id::TEXT,'-','')
   AND role.rolcanlogin AND role.rolinherit AND role.rolconnlimit=1
   AND NOT role.rolsuper AND NOT role.rolcreatedb AND NOT role.rolcreaterole
   AND NOT role.rolreplication AND NOT role.rolbypassrls
   AND (SELECT pg_catalog.count(*) FROM pg_catalog.pg_auth_members membership
       WHERE membership.member=role.oid)=1
   AND EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members membership
     JOIN pg_catalog.pg_roles granted ON granted.oid=membership.roleid
     WHERE membership.member=role.oid AND granted.rolname='aurum_edge_cash_executor'
       AND NOT membership.admin_option AND membership.inherit_option
       AND NOT membership.set_option);
 IF v_identity.id IS NULL THEN
  RAISE EXCEPTION 'Native retail replay identity unavailable' USING ERRCODE='42501'; END IF;
 v_scope:=pg_catalog.jsonb_build_object('activation_id',p_activation_id,
   'tenant_id',v_identity.tenant_id,'branch_id',v_identity.branch_id,
   'edge_node_id',v_identity.edge_node_id,'register_id',v_identity.register_id,
   'writer_epoch',p_writer_epoch,'user_id',p_cashier_user_id,'capability','cash_sale_v1');
 IF v_scope IS DISTINCT FROM p_payload->'scope' THEN
  RAISE EXCEPTION 'Native retail replay scope differs from binding' USING ERRCODE='42501'; END IF;
 PERFORM pg_catalog.set_config('app.tenant_id',v_identity.tenant_id::TEXT,true);
 PERFORM pg_catalog.set_config('app.branch_id',v_identity.branch_id::TEXT,true);
 PERFORM pg_catalog.set_config('app.user_id',p_cashier_user_id::TEXT,true);
 PERFORM pg_catalog.set_config('app.edge_node_id',v_identity.edge_node_id::TEXT,true);
 SELECT command.* INTO v_existing FROM public.edge_cash_command command
 WHERE command.tenant_id=v_identity.tenant_id AND command.operation_id=p_operation_id;
 -- No fallback to an app result and no creation of a missing native anchor.
 IF v_existing.id IS NULL THEN RETURN NULL; END IF;
 v_complete_payload:=p_payload||pg_catalog.jsonb_build_object('profile','retail_cash.complete.v1');
 v_request_hash:=pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
   public.edge_canonical_jsonb(v_complete_payload),'UTF8')),'hex');
 IF v_existing.edge_identity_id IS DISTINCT FROM v_identity.id
   OR v_existing.branch_id IS DISTINCT FROM v_identity.branch_id
   OR v_existing.register_id IS DISTINCT FROM v_identity.register_id
   OR v_existing.edge_node_id IS DISTINCT FROM v_identity.edge_node_id
   OR v_existing.activation_id IS DISTINCT FROM p_activation_id
   OR v_existing.writer_epoch IS DISTINCT FROM p_writer_epoch
   OR v_existing.cashier_user_id IS DISTINCT FROM p_cashier_user_id
   OR v_existing.sale_id IS DISTINCT FROM (v_confirmation->>'sale_id')::UUID
   OR v_existing.command_type IS DISTINCT FROM 'sale.cash.complete'
   OR v_existing.sale_status IS DISTINCT FROM 'completed'
   OR v_existing.currency IS DISTINCT FROM 'TJS'
   OR v_existing.total_amount IS DISTINCT FROM (v_confirmation->>'expected_total_amount')::NUMERIC
   OR v_existing.request_hash IS DISTINCT FROM v_request_hash
   OR pg_catalog.octet_length(v_existing.result_payload::TEXT)>131072
   OR v_existing.result_payload->>'profile' IS DISTINCT FROM 'retail_cash.completed.v1'
   OR v_existing.result_payload->>'kind' IS DISTINCT FROM p_payload->>'kind'
   OR v_existing.result_payload->'scope' IS DISTINCT FROM v_scope
   OR v_existing.result_payload->'confirmation' IS DISTINCT FROM v_confirmation
   OR v_existing.result_hash IS DISTINCT FROM pg_catalog.encode(pg_catalog.sha256(
       pg_catalog.convert_to(public.edge_canonical_jsonb(v_existing.result_payload),'UTF8')),'hex')
 THEN RAISE EXCEPTION 'Native retail replay operation differs' USING ERRCODE='23505'; END IF;

 SELECT sale.* INTO v_sale FROM public.sale sale
 WHERE sale.tenant_id=v_identity.tenant_id AND sale.id=v_existing.sale_id
   AND sale.branch_id=v_identity.branch_id AND sale.register_id=v_identity.register_id
   AND sale.cashier_user_id=p_cashier_user_id;
 IF v_sale.id IS NULL OR v_sale.status<>'completed' OR v_sale.sale_type<>'sale'
   OR v_sale.parent_sale_id IS NOT NULL OR v_sale.is_test OR v_sale.currency<>'TJS'
   OR v_sale.operation_id IS DISTINCT FROM p_operation_id
   OR v_sale.receipt_number IS DISTINCT FROM v_existing.receipt_number
   OR v_sale.total_amount IS DISTINCT FROM v_existing.total_amount
 THEN RAISE EXCEPTION 'Native retail replay sale is inconsistent' USING ERRCODE='23514'; END IF;
 SELECT tenant_id,id,branch_id,sale_id,register_id,shift_id,cashier_user_id,
   revision_hash,expected_total_amount,cash_tendered,currency
 INTO v_intent FROM public.pos_unit_cash_intent
 WHERE tenant_id=v_identity.tenant_id AND id=p_operation_id
   AND branch_id=v_identity.branch_id AND sale_id=v_sale.id
   AND register_id=v_identity.register_id AND shift_id=v_sale.shift_id
   AND cashier_user_id=p_cashier_user_id;
 IF NOT FOUND THEN
  RAISE EXCEPTION 'Native retail replay confirmation unavailable' USING ERRCODE='23514'; END IF;
 v_saved_confirmation:=pg_catalog.jsonb_build_object('schema_version',2,'operation_id',v_intent.id,
   'sale_id',v_intent.sale_id,'expected_revision_hash',v_intent.revision_hash,
   'expected_total_amount',pg_catalog.to_char(v_intent.expected_total_amount,'FM999999999990.00'),
   'cash_tendered',pg_catalog.to_char(v_intent.cash_tendered,'FM999999999990.00'),
   'currency',v_intent.currency);
 IF v_saved_confirmation IS DISTINCT FROM v_confirmation THEN
  RAISE EXCEPTION 'Native retail replay confirmation differs' USING ERRCODE='23505'; END IF;
 v_cash_hash:=pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
   'aurum-cash-intent-v1:'||public.edge_canonical_jsonb(v_confirmation),'UTF8')),'hex');

 IF p_payload->>'kind'='physical_cash' THEN
  SELECT tenant_id,id,sale_id,branch_id,register_id,shift_id,cashier_user_id,cash_intent_id,
    payment_plan_intent_id,stock_command_id,payment_id,payment_journal_version,
    request_hash,result_payload,receipt_payload INTO v_marker
  FROM public.pos_physical_checkout WHERE tenant_id=v_identity.tenant_id AND id=p_operation_id;
 ELSE
  SELECT tenant_id,id,sale_id,branch_id,register_id,shift_id,cashier_user_id,cash_intent_id,
    payment_plan_intent_id,stock_command_id,payment_id,payment_journal_version,
    request_hash,result_payload,receipt_payload INTO v_marker
  FROM public.pos_mixed_checkout WHERE tenant_id=v_identity.tenant_id AND id=p_operation_id;
 END IF;
 IF NOT FOUND OR (v_marker.sale_id,v_marker.branch_id,v_marker.register_id,
       v_marker.shift_id,v_marker.cashier_user_id)
      IS DISTINCT FROM (v_sale.id,v_sale.branch_id,v_sale.register_id,
        v_sale.shift_id,p_cashier_user_id)
   OR v_marker.cash_intent_id IS DISTINCT FROM p_operation_id
   OR v_marker.payment_plan_intent_id IS NOT NULL
   OR v_marker.stock_command_id IS DISTINCT FROM p_operation_id
   OR v_marker.payment_id IS DISTINCT FROM p_operation_id
   OR v_marker.payment_journal_version NOT IN (1,2,3)
   OR v_marker.request_hash IS DISTINCT FROM v_cash_hash
   OR pg_catalog.octet_length(v_marker.result_payload::TEXT)>131072
   OR v_marker.result_payload->>'event_id' IS DISTINCT FROM p_operation_id::TEXT
   OR v_marker.result_payload->>'stock_command_id' IS DISTINCT FROM p_operation_id::TEXT
   OR v_marker.result_payload->'payments' IS DISTINCT FROM pg_catalog.jsonb_build_array(
     pg_catalog.jsonb_build_object('id',p_operation_id,'method','cash',
       'amount',pg_catalog.to_char(v_existing.total_amount,'FM999999999990.00'),'currency','TJS'))
 THEN RAISE EXCEPTION 'Native retail replay cash marker is inconsistent' USING ERRCODE='23514';
 END IF;
 -- Existing schema-owner validators inspect the immutable stock, receipt and
 -- payment journals. They neither reprice today's stock nor execute a sale.
 IF p_payload->>'kind'='physical_cash' THEN
  PERFORM public.assert_physical_checkout(v_identity.tenant_id,v_sale.id,true);
 ELSE
  PERFORM public.assert_mixed_checkout(v_identity.tenant_id,v_sale.id,true);
 END IF;
 v_wrapper:=pg_catalog.jsonb_build_object('profile','retail_cash.completed.v1',
   'command_type','sale.cash.complete','kind',p_payload->>'kind','scope',v_scope,
   'confirmation',v_saved_confirmation,'result',v_marker.result_payload,
   'operation_id',p_operation_id,'sale_id',v_sale.id,'receipt_number',v_sale.receipt_number,
   'total_amount',pg_catalog.to_char(v_sale.total_amount,'FM999999999990.00'),'currency','TJS');
 IF v_existing.result_payload IS DISTINCT FROM v_wrapper THEN
  RAISE EXCEPTION 'Native retail replay result is inconsistent' USING ERRCODE='23514'; END IF;
 -- Historical replay deliberately does not require today's writer, cashier,
 -- payment settings, catalog or an open shift. The lease kernel reauthorizes
 -- attempts separately; this profile never creates a native execution anchor.
 RETURN v_existing.result_payload;
END
$function$;
"""


def _scope(table: str) -> str:
    return f"""EXISTS(
      SELECT 1 FROM public.edge_cash_node_identity identity
      JOIN pg_catalog.pg_roles role
        ON role.rolname=identity.database_role AND role.oid=identity.database_role_oid
      WHERE identity.database_role=SESSION_USER
        AND identity.database_role='aurum_edge_node_'
          ||pg_catalog.replace(identity.edge_node_id::TEXT,'-','')
        AND identity.tenant_id={table}.tenant_id
        AND identity.branch_id={table}.branch_id
        AND identity.register_id={table}.register_id)
      AND {table}.cashier_user_id=public.current_app_user_id()"""


def upgrade() -> None:
    op.get_bind().exec_driver_sql(REPLAY_SQL)
    op.execute(f"REVOKE ALL ON FUNCTION {HELPER} FROM PUBLIC,aurum_app,aurum_support")
    op.execute(f"REVOKE ALL ON FUNCTION {HELPER} FROM aurum_edge_cash_executor")
    op.execute(f"GRANT EXECUTE ON FUNCTION {HELPER} TO aurum_edge_cash_owner")
    for validator in VALIDATORS:
        op.execute(f"GRANT EXECUTE ON FUNCTION {validator} TO aurum_edge_cash_owner")
    for table in ("pos_physical_checkout", "pos_mixed_checkout"):
        op.execute(f"GRANT SELECT ({MARKER_COLUMNS}) ON public.{table} TO aurum_edge_cash_owner")
        op.execute(
            f"CREATE POLICY edge_cash_replay_read ON public.{table} "
            f"FOR SELECT TO aurum_edge_cash_owner USING ({_scope(table)})"
        )
    _patch_dispatcher()


def downgrade() -> None:
    _patch_dispatcher(reverse=True)
    for table in ("pos_physical_checkout", "pos_mixed_checkout"):
        op.execute(f"DROP POLICY edge_cash_replay_read ON public.{table}")
        op.execute(f"REVOKE SELECT ({MARKER_COLUMNS}) ON public.{table} FROM aurum_edge_cash_owner")
    for validator in VALIDATORS:
        op.execute(f"REVOKE EXECUTE ON FUNCTION {validator} FROM aurum_edge_cash_owner")
    op.execute(f"DROP FUNCTION {HELPER}")
