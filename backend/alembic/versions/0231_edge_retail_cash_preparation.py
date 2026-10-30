"""Read saved retail cash evidence through the native Edge boundary, without execution.

Revision ID: 0231
Revises: 0230
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0231"
down_revision: str | Sequence[str] | None = "0230"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SIGNATURE = "public.dispatch_edge_cash_sale_v1(uuid,uuid,bigint,uuid,jsonb,text)"
HELPER = "public.prepare_edge_retail_cash_v1(uuid,uuid,bigint,uuid,jsonb,text)"
INTENT_COLUMNS = (
    "tenant_id,id,branch_id,sale_id,register_id,shift_id,cashier_user_id,"
    "revision_hash,expected_total_amount,cash_tendered,currency,quote_payload"
)
ORIGINAL_START = "BEGIN\n  IF p_operation_id IS NULL"
PREPARATION_START = """BEGIN
  IF p_payload ->> 'profile' = 'retail_cash.prepare.v1' THEN
    RETURN public.prepare_edge_retail_cash_v1(
      p_operation_id,p_activation_id,p_writer_epoch,p_cashier_user_id,p_payload,p_request_hash
    );
  END IF;
  IF p_operation_id IS NULL"""


def _patch_dispatcher(*, reverse: bool = False) -> None:
    definition = (
        op.get_bind()
        .exec_driver_sql(f"SELECT pg_catalog.pg_get_functiondef('{SIGNATURE}'::regprocedure)")
        .scalar_one()
    )
    old, new = (
        (PREPARATION_START, ORIGINAL_START) if reverse else (ORIGINAL_START, PREPARATION_START)
    )
    if not isinstance(definition, str) or definition.count(old) != 1:
        raise RuntimeError("Native Edge dispatcher historical body changed")
    if (
        op.get_bind()
        .exec_driver_sql("SELECT has_schema_privilege('aurum_edge_cash_owner','public','CREATE')")
        .scalar_one()
    ):
        raise RuntimeError("Native Edge owner unexpectedly has schema creation rights")
    # CREATE OR REPLACE retains the public function's OID, owner and exact ACL.
    # PostgreSQL requires schema CREATE even for replacement. Its temporary grant
    # exists only in the migration transaction and never survives migration success.
    owner_can_execute = (
        op.get_bind()
        .exec_driver_sql(
            f"SELECT has_function_privilege('aurum_edge_cash_owner','{SIGNATURE}','EXECUTE')"
        )
        .scalar_one()
    )
    op.execute("GRANT CREATE ON SCHEMA public TO aurum_edge_cash_owner")
    op.execute("RESET ROLE")
    op.execute("SET LOCAL ROLE aurum_edge_cash_owner")
    # Repeated role bootstrap may revoke the owner's own EXECUTE. PostgreSQL's
    # PL/pgSQL validator still requires it when replacing an owned function.
    # Restore the exact absence of that privilege after validation completes.
    if not owner_can_execute:
        op.execute(f"GRANT EXECUTE ON FUNCTION {SIGNATURE} TO aurum_edge_cash_owner")
    op.get_bind().exec_driver_sql(definition.replace(old, new, 1))
    if not owner_can_execute:
        op.execute(f"REVOKE EXECUTE ON FUNCTION {SIGNATURE} FROM aurum_edge_cash_owner")
    op.execute("RESET ROLE")
    op.execute("SET LOCAL ROLE aurum_schema_owner")
    op.execute("REVOKE CREATE ON SCHEMA public FROM aurum_edge_cash_owner")


PREPARE_SQL = r"""
CREATE FUNCTION public.prepare_edge_retail_cash_v1(
 p_operation_id UUID,p_activation_id UUID,p_writer_epoch BIGINT,p_cashier_user_id UUID,
 p_payload JSONB,p_request_hash TEXT
) RETURNS JSONB LANGUAGE plpgsql VOLATILE SECURITY INVOKER
SET search_path=pg_catalog,pg_temp SET row_security=on AS $function$
DECLARE
 v_identity public.edge_cash_node_identity%ROWTYPE;
 v_stream public.sync_stream%ROWTYPE; v_node public.sync_node%ROWTYPE;
 v_activation public.sync_writer_activation%ROWTYPE;
 v_epoch public.sync_writer_epoch%ROWTYPE;
 v_sale public.sale%ROWTYPE; v_shift public.shift%ROWTYPE;
 v_intent RECORD; v_scope JSONB; v_confirmation JSONB;
 v_physical_count INTEGER; v_legacy_count INTEGER; v_line_count INTEGER;
BEGIN
 IF CURRENT_USER<>'aurum_edge_cash_owner' OR p_operation_id IS NULL
   OR (pg_catalog.get_byte(pg_catalog.uuid_send(p_operation_id),6)>>4)<>4
   OR (pg_catalog.get_byte(pg_catalog.uuid_send(p_operation_id),8)&192)<>128
   OR p_activation_id IS NULL OR p_writer_epoch IS NULL OR p_writer_epoch<=0
   OR p_cashier_user_id IS NULL OR p_request_hash IS NULL
   OR p_request_hash !~ '^[0-9a-f]{64}$'
   OR pg_catalog.jsonb_typeof(p_payload) IS DISTINCT FROM 'object'
 THEN RAISE EXCEPTION 'Invalid native retail preparation envelope' USING ERRCODE='22023'; END IF;
 IF pg_catalog.octet_length(p_payload::TEXT)>8192
   OR NOT p_payload ?& ARRAY['profile','kind','scope','confirmation']
   OR p_payload-ARRAY['profile','kind','scope','confirmation']<>'{}'::JSONB
   OR p_payload->>'profile' IS DISTINCT FROM 'retail_cash.prepare.v1'
   OR COALESCE(p_payload->>'kind','') NOT IN ('physical_cash','mixed_cash')
   OR pg_catalog.jsonb_typeof(p_payload->'scope') IS DISTINCT FROM 'object'
   OR pg_catalog.jsonb_typeof(p_payload->'confirmation') IS DISTINCT FROM 'object'
 THEN RAISE EXCEPTION 'Invalid native retail preparation payload' USING ERRCODE='22023'; END IF;
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
   OR pg_catalog.jsonb_typeof(v_confirmation->'expected_revision_hash') IS DISTINCT FROM 'string'
   OR COALESCE(v_confirmation->>'expected_revision_hash','') !~ '^[0-9a-f]{64}$'
   OR pg_catalog.jsonb_typeof(v_confirmation->'expected_total_amount') IS DISTINCT FROM 'string'
   OR pg_catalog.jsonb_typeof(v_confirmation->'cash_tendered') IS DISTINCT FROM 'string'
   OR COALESCE(v_confirmation->>'expected_total_amount','') !~ '^(0|[1-9][0-9]{0,11})\.[0-9]{2}$'
   OR COALESCE(v_confirmation->>'cash_tendered','') !~ '^(0|[1-9][0-9]{0,11})\.[0-9]{2}$'
 THEN RAISE EXCEPTION 'Invalid native retail preparation fields' USING ERRCODE='22023'; END IF;
 IF (v_confirmation->>'expected_total_amount')::NUMERIC<=0
   OR (v_confirmation->>'cash_tendered')::NUMERIC
       <(v_confirmation->>'expected_total_amount')::NUMERIC
   OR p_request_hash IS DISTINCT FROM pg_catalog.encode(pg_catalog.sha256(
       pg_catalog.convert_to(public.edge_canonical_jsonb(p_payload),'UTF8')),'hex')
 THEN RAISE EXCEPTION 'Native retail preparation hash or amounts differ' USING ERRCODE='22023';
 END IF;

 -- The same immutable per-node binding and role restrictions as the legacy dispatcher.
 SELECT identity.* INTO v_identity FROM public.edge_cash_node_identity AS identity
 JOIN pg_catalog.pg_roles AS role
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
  RAISE EXCEPTION 'Native retail database identity unavailable' USING ERRCODE='42501'; END IF;
 v_scope:=pg_catalog.jsonb_build_object('activation_id',p_activation_id,
   'tenant_id',v_identity.tenant_id,'branch_id',v_identity.branch_id,
   'edge_node_id',v_identity.edge_node_id,'register_id',v_identity.register_id,
   'writer_epoch',p_writer_epoch,'user_id',p_cashier_user_id,'capability','cash_sale_v1');
 IF v_scope IS DISTINCT FROM p_payload->'scope' THEN
  RAISE EXCEPTION 'Native retail scope differs from binding' USING ERRCODE='42501'; END IF;
 PERFORM pg_catalog.set_config('app.tenant_id',v_identity.tenant_id::TEXT,true);
 PERFORM pg_catalog.set_config('app.branch_id',v_identity.branch_id::TEXT,true);
 PERFORM pg_catalog.set_config('app.user_id',p_cashier_user_id::TEXT,true);
 PERFORM pg_catalog.set_config('app.edge_node_id',v_identity.edge_node_id::TEXT,true);
 PERFORM pg_catalog.pg_advisory_xact_lock(
   pg_catalog.hashtextextended(p_operation_id::TEXT,0));

 SELECT stream.* INTO v_stream FROM public.sync_stream stream
 WHERE stream.tenant_id=v_identity.tenant_id AND stream.branch_id=v_identity.branch_id
 FOR UPDATE;
 SELECT node.* INTO v_node FROM public.sync_node node
 WHERE node.id=v_identity.edge_node_id AND node.tenant_id=v_identity.tenant_id
   AND node.branch_id=v_identity.branch_id AND node.register_id=v_identity.register_id
 FOR UPDATE;
 SELECT activation.* INTO v_activation FROM public.sync_writer_activation activation
 WHERE activation.activation_id=p_activation_id AND activation.tenant_id=v_identity.tenant_id
   AND activation.branch_id=v_identity.branch_id AND activation.writer_epoch=p_writer_epoch
   AND activation.writer_node_id=v_identity.edge_node_id
   AND activation.allowed_register_id=v_identity.register_id FOR UPDATE;
 SELECT epoch.* INTO v_epoch FROM public.sync_writer_epoch epoch
 WHERE epoch.activation_id=p_activation_id AND epoch.tenant_id=v_identity.tenant_id
   AND epoch.branch_id=v_identity.branch_id AND epoch.writer_epoch=p_writer_epoch
   AND epoch.writer_node_id=v_identity.edge_node_id
   AND epoch.allowed_register_id=v_identity.register_id FOR UPDATE;
 IF v_stream.id IS NULL OR v_node.id IS NULL OR v_activation.activation_id IS NULL
   OR v_epoch.activation_id IS NULL
   OR v_stream.writer_node_id IS DISTINCT FROM v_identity.edge_node_id
   OR v_stream.writer_epoch IS DISTINCT FROM p_writer_epoch
   OR v_node.status<>'active' OR v_node.node_kind<>'edge' OR v_node.mode<>'edge_writer'
   OR v_activation.state<>'activated' OR v_activation.capability<>'cash_sale_v1'
   OR v_epoch.state<>'active' OR v_epoch.capability<>'cash_sale_v1'
   OR EXISTS(SELECT 1 FROM public.sync_writer_activation pending
     WHERE pending.tenant_id=v_identity.tenant_id AND pending.branch_id=v_identity.branch_id
       AND pending.state IN ('prepared','ready'))
 THEN RAISE EXCEPTION 'Native retail writer unavailable' USING ERRCODE='55000'; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.tenant tenant
      WHERE tenant.id=v_identity.tenant_id AND tenant.status IN ('trial','active'))
   OR NOT EXISTS(SELECT 1 FROM public.register register JOIN public.branch branch
      ON branch.id=register.branch_id AND branch.tenant_id=register.tenant_id
      WHERE register.id=v_identity.register_id AND register.tenant_id=v_identity.tenant_id
        AND register.branch_id=v_identity.branch_id AND register.is_active AND branch.is_active)
   OR NOT EXISTS(SELECT 1 FROM public.tenant_settings settings
      WHERE settings.tenant_id=v_identity.tenant_id AND settings.pos_payment_methods ? 'cash')
 THEN RAISE EXCEPTION 'Native retail pharmacy unavailable' USING ERRCODE='55000'; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.app_user app_user
   JOIN public.tenant_membership membership ON membership.user_id=app_user.id
     AND membership.tenant_id=v_identity.tenant_id AND membership.status='active'
   JOIN public.user_assignment assignment ON assignment.membership_id=membership.id
     AND assignment.user_id=app_user.id AND assignment.tenant_id=membership.tenant_id
     AND assignment.is_active
     AND (assignment.branch_id IS NULL OR assignment.branch_id=v_identity.branch_id)
   JOIN public.role assigned_role ON assigned_role.id=assignment.role_id AND assigned_role.is_active
   JOIN public.role_permission role_permission ON role_permission.role_id=assigned_role.id
     AND role_permission.permission_code='pos.sell'
   JOIN public.permission permission ON permission.code=role_permission.permission_code
     AND permission.is_active
   WHERE app_user.id=p_cashier_user_id AND app_user.status='active')
 THEN RAISE EXCEPTION 'Native retail cashier unavailable' USING ERRCODE='42501'; END IF;

 SELECT sale.* INTO v_sale FROM public.sale sale
 WHERE sale.tenant_id=v_identity.tenant_id AND sale.id=(v_confirmation->>'sale_id')::UUID
   AND sale.branch_id=v_identity.branch_id AND sale.register_id=v_identity.register_id
   AND sale.cashier_user_id=p_cashier_user_id FOR UPDATE;
 IF v_sale.id IS NULL OR v_sale.status<>'draft' OR v_sale.sale_type<>'sale'
   OR v_sale.parent_sale_id IS NOT NULL OR v_sale.is_test OR v_sale.currency<>'TJS'
 THEN RAISE EXCEPTION 'Native retail draft unavailable' USING ERRCODE='55000'; END IF;
 SELECT shift.* INTO v_shift FROM public.shift shift
 WHERE shift.id=v_sale.shift_id AND shift.tenant_id=v_identity.tenant_id
   AND shift.branch_id=v_identity.branch_id AND shift.register_id=v_identity.register_id
   AND shift.opened_by_user_id=p_cashier_user_id FOR UPDATE;
 IF v_shift.id IS NULL OR v_shift.status<>'open' OR v_shift.currency<>'TJS'
   OR EXISTS(SELECT 1 FROM public.sale_payment
     WHERE tenant_id=v_identity.tenant_id AND sale_id=v_sale.id)
   OR EXISTS(SELECT 1 FROM public.pos_payment_attempt
     WHERE tenant_id=v_identity.tenant_id AND sale_id=v_sale.id)
 THEN RAISE EXCEPTION 'Native retail shift or payment unavailable' USING ERRCODE='55000'; END IF;

 -- Intent UPDATE/DELETE is forbidden by its immutable trigger. The parent sale lock
 -- also excludes concurrent intent insertion and cart edits: no new UPDATE grant.
 SELECT tenant_id,id,branch_id,sale_id,register_id,shift_id,cashier_user_id,
   revision_hash,expected_total_amount,cash_tendered,currency,quote_payload
 INTO v_intent FROM public.pos_unit_cash_intent
 WHERE tenant_id=v_identity.tenant_id AND id=p_operation_id
   AND branch_id=v_identity.branch_id AND sale_id=v_sale.id
   AND register_id=v_identity.register_id AND shift_id=v_shift.id
   AND cashier_user_id=p_cashier_user_id;
 IF NOT FOUND THEN
  RAISE EXCEPTION 'Native retail saved confirmation unavailable' USING ERRCODE='42501'; END IF;
 v_confirmation:=pg_catalog.jsonb_build_object('schema_version',2,'operation_id',v_intent.id,
   'sale_id',v_intent.sale_id,'expected_revision_hash',v_intent.revision_hash,
   'expected_total_amount',pg_catalog.to_char(v_intent.expected_total_amount,'FM999999999990.00'),
   'cash_tendered',pg_catalog.to_char(v_intent.cash_tendered,'FM999999999990.00'),
   'currency',v_intent.currency);
 IF v_confirmation IS DISTINCT FROM p_payload->'confirmation' THEN
  RAISE EXCEPTION 'Native retail saved confirmation differs' USING ERRCODE='23505'; END IF;
 SELECT pg_catalog.count(*) FILTER(WHERE line->>'inventory_kind'='physical_unit'),
   pg_catalog.count(*) FILTER(WHERE line->>'inventory_kind'='legacy_pack'),pg_catalog.count(*)
 INTO v_physical_count,v_legacy_count,v_line_count
 FROM pg_catalog.jsonb_array_elements(v_intent.quote_payload->'lines') line;
 IF v_physical_count=0 OR v_line_count NOT BETWEEN 1 AND 200
   OR v_line_count<>v_physical_count+v_legacy_count
   OR (p_payload->>'kind'='physical_cash' AND v_legacy_count<>0)
   OR (p_payload->>'kind'='mixed_cash' AND v_legacy_count=0)
 THEN RAISE EXCEPTION 'Native retail saved cart kind differs' USING ERRCODE='23505'; END IF;
 -- No repricing, stock movement, cash acceptance, receipt, ledger or event occurs.
 RETURN pg_catalog.jsonb_build_object('schema_version',1,'state','prepared_not_executed',
   'kind',p_payload->>'kind','scope',v_scope,'sale_id',v_sale.id,'shift_id',v_shift.id,
   'saved_confirmation',v_confirmation);
END
$function$;
"""


def upgrade() -> None:
    op.get_bind().exec_driver_sql(PREPARE_SQL)
    op.execute(f"REVOKE ALL ON FUNCTION {HELPER} FROM PUBLIC,aurum_app,aurum_support")
    op.execute(f"REVOKE ALL ON FUNCTION {HELPER} FROM aurum_edge_cash_executor")
    op.execute(f"GRANT EXECUTE ON FUNCTION {HELPER} TO aurum_edge_cash_owner")
    op.execute(
        f"GRANT SELECT ({INTENT_COLUMNS}) ON public.pos_unit_cash_intent "
        "TO aurum_edge_cash_owner"
    )
    op.execute("""
      CREATE POLICY edge_cash_saved_confirmation ON public.pos_unit_cash_intent
      FOR SELECT TO aurum_edge_cash_owner USING (
        EXISTS(SELECT 1 FROM public.edge_cash_node_identity identity
          JOIN pg_catalog.pg_roles role ON role.rolname=identity.database_role
            AND role.oid=identity.database_role_oid
          WHERE identity.database_role=SESSION_USER
            AND identity.database_role='aurum_edge_node_'
              ||pg_catalog.replace(identity.edge_node_id::TEXT,'-','')
            AND identity.tenant_id=pos_unit_cash_intent.tenant_id
            AND identity.branch_id=pos_unit_cash_intent.branch_id
            AND identity.register_id=pos_unit_cash_intent.register_id)
      )
    """)
    _patch_dispatcher()


def downgrade() -> None:
    _patch_dispatcher(reverse=True)
    op.execute("DROP POLICY edge_cash_saved_confirmation ON public.pos_unit_cash_intent")
    op.execute(
        f"REVOKE SELECT ({INTENT_COLUMNS}) ON public.pos_unit_cash_intent "
        "FROM aurum_edge_cash_owner"
    )
    op.execute(f"DROP FUNCTION {HELPER}")
