"""Atomically execute native mixed legacy/physical cash checkouts.

Revision ID: 0236
Revises: 0235
"""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0236"
down_revision = "0235"
branch_labels = None
depends_on = None


def _history() -> ModuleType:
    path = Path(__file__).with_name("0235_edge_physical_cash_execution.py")
    spec = spec_from_file_location("aurum_0236_physical_history", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load native physical execution history")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _replace(source: str, old: str, new: str, *, count: int = 1) -> str:
    if source.count(old) != count:
        raise RuntimeError("Native mixed historical SQL changed")
    return source.replace(old, new, count)


physical = _history()
CONTEXT: str = physical.CONTEXT
NATIVE_SESSION: str = physical.NATIVE_SESSION

LEGACY_SQL = r"""
CREATE FUNCTION public.edge_mixed_legacy_stock_v1(
 p_tenant UUID,p_command UUID,p_sale UUID,p_snapshot JSONB,p_date DATE)
RETURNS VOID LANGUAGE plpgsql VOLATILE SECURITY INVOKER
SET search_path=pg_catalog,pg_temp SET row_security=on AS $function$
DECLARE
 catalog UUID; batch_row RECORD; batch_ids UUID[]; branch UUID;
 needed NUMERIC; taken NUMERIC; expected JSONB; selected JSONB;
BEGIN
 SELECT branch_id INTO STRICT branch FROM public.sale
  WHERE tenant_id=p_tenant AND id=p_sale;
 SELECT coalesce(array_agg((value->>'id')::UUID),'{}'::UUID[]) INTO batch_ids
  FROM jsonb_array_elements(p_snapshot);
 IF NOT EXISTS(SELECT 1 FROM public.sale_item WHERE tenant_id=p_tenant AND sale_id=p_sale)
  OR EXISTS(SELECT 1 FROM public.sale_item WHERE tenant_id=p_tenant AND sale_id=p_sale
    AND (parent_sale_item_id IS NOT NULL OR currency<>'TJS' OR qty<=0)) THEN
  RAISE EXCEPTION 'Native mixed legacy cart invalid' USING ERRCODE='23514'; END IF;
 -- The candidate universe comes from the coordinator's fresh locked snapshot,
 -- never a caller-provided batch allowlist or Python's selected allocation.
 FOR catalog IN SELECT DISTINCT catalog_id FROM public.sale_item
  WHERE tenant_id=p_tenant AND sale_id=p_sale ORDER BY catalog_id LOOP
  SELECT sum(quantity),jsonb_object_agg(batch_id::TEXT,quantity)
   INTO needed,selected FROM (
    SELECT batch_id,sum(qty) AS quantity FROM public.sale_item
    WHERE tenant_id=p_tenant AND sale_id=p_sale AND catalog_id=catalog GROUP BY batch_id
   ) selected_batches;
  expected:='{}'::JSONB;
  FOR batch_row IN SELECT id,qty_remaining FROM public.batch
   WHERE tenant_id=p_tenant AND branch_id=branch AND catalog_id=catalog
    AND id=ANY(batch_ids) AND NOT is_blocked AND expires_at>p_date AND qty_remaining>0
   ORDER BY expires_at,created_at,id LOOP
   IF needed=0 THEN EXIT; END IF;
   taken:=least(batch_row.qty_remaining,needed);
   expected:=expected||jsonb_build_object(batch_row.id::TEXT,taken);
   needed:=needed-taken;
  END LOOP;
  IF needed<>0 OR selected IS DISTINCT FROM expected THEN
   RAISE EXCEPTION 'Native mixed legacy FEFO allocation stale' USING ERRCODE='23514'; END IF;
 END LOOP;
 -- Preserve the established one immutable movement per selected batch contract.
 FOR batch_row IN SELECT batch_id,sum(qty) AS quantity FROM public.sale_item
  WHERE tenant_id=p_tenant AND sale_id=p_sale GROUP BY batch_id ORDER BY batch_id LOOP
  INSERT INTO public.batch_movement(id,tenant_id,batch_id,movement_type,qty_delta,
   source_table,source_id,operation_key,created_by)
  VALUES(gen_random_uuid(),p_tenant,batch_row.batch_id,'sale',-batch_row.quantity,
   'sale',p_sale,'pos:sale:'||p_sale||':sale:'||batch_row.batch_id,
   public.current_app_user_id());
 END LOOP;
END $function$;
"""


def _event_sql() -> str:
    sql: str = physical.EVENT_SQL
    for old, new, count in (
        (
            "SET search_path=pg_catalog,pg_temp AS $function$",
            "SET search_path=pg_catalog,pg_temp SET row_security=on AS $function$",
            1,
        ),
        ("emit_native_physical_retail_event_v1", "emit_native_mixed_retail_event_v1", 1),
        ("FROM public.pos_physical_checkout", "FROM public.pos_mixed_checkout", 1),
        ("pos.sale.completed.v3", "pos.sale.completed.v4", 2),
        ("'schema_version',3", "'schema_version',4", 3),
    ):
        sql = _replace(sql, old, new, count=count)
    return sql


def _complete_sql() -> str:
    sql: str = physical.COMPLETE_SQL
    for old, new in (
        ("complete_edge_physical_cash_v1", "complete_edge_mixed_cash_v1"),
        (
            "p_payload->>'kind' IS DISTINCT FROM 'physical_cash'",
            "p_payload->>'kind' IS DISTINCT FROM 'mixed_cash'",
        ),
        (
            "stock_date DATE; report_timezone TEXT;",
            "stock_date DATE; report_timezone TEXT; stock_position INTEGER:=0;",
        ),
        (
            " total:=(quote->>'total_amount')::NUMERIC;",
            """ IF NOT EXISTS(SELECT 1 FROM jsonb_array_elements(quote->'lines') quoted
    WHERE quoted->>'inventory_kind'='legacy_pack')
  OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(quote->'lines') quoted
    WHERE quoted->>'inventory_kind'='physical_unit')
  OR EXISTS(SELECT 1 FROM jsonb_array_elements(quote->'lines') quoted
    WHERE quoted->>'inventory_kind' NOT IN ('legacy_pack','physical_unit')) THEN
  RAISE EXCEPTION 'Native mixed requires legacy and physical positions'
   USING ERRCODE='23514'; END IF;
 PERFORM public.edge_mixed_legacy_stock_v1(tenant,p_operation,sale_id,
  inputs->'legacy_stock',stock_date);
 total:=(quote->>'total_amount')::NUMERIC;""",
        ),
        (
            "created_by,checkout_sale_id)\n VALUES(tenant,p_operation,branch,p_actor,"
            "plan->>'stock_request_hash',1,'TJS',p_actor,sale_id);",
            "created_by,mixed_checkout_sale_id)\n VALUES(tenant,p_operation,branch,p_actor,"
            "plan->>'stock_request_hash',1,'TJS',p_actor,sale_id);",
        ),
        (
            " FOR item IN SELECT value FROM jsonb_array_elements(quote->'lines') LOOP\n"
            "  INSERT INTO public.unit_stock_cart_line_v2",
            " FOR item IN SELECT value FROM jsonb_array_elements(quote->'lines')\n"
            "  WHERE value->>'inventory_kind'='physical_unit' LOOP\n"
            "  INSERT INTO public.unit_stock_cart_line_v2",
        ),
        ("(item->>'position')::INTEGER-1,", "stock_position,"),
        (
            "   (item->>'discount_amount')::NUMERIC,(item->>'net_amount')::NUMERIC);\n END LOOP;",
            "   (item->>'discount_amount')::NUMERIC,(item->>'net_amount')::NUMERIC);\n"
            "  stock_position:=stock_position+1;\n END LOOP;",
        ),
        (
            "  label:=CASE item->>'sale_format' WHEN 'pack' THEN 'уп.'",
            "  label:=CASE WHEN item->>'inventory_kind'='legacy_pack' THEN 'уп.' ELSE\n"
            "   CASE item->>'sale_format' WHEN 'pack' THEN 'уп.'",
        ),
        (
            "WHEN 'suppository' THEN 'супп.' END END;",
            "WHEN 'suppository' THEN 'супп.' END END END;",
        ),
        (
            "   (line-ARRAY['id','catalog_id'])||jsonb_build_object('unit_label',label));",
            """   CASE WHEN item->>'inventory_kind'='legacy_pack' THEN
    (line-ARRAY['id','catalog_id','batch_id'])||
     jsonb_build_object('sale_format','pack','unit_label',label)
   ELSE (line-ARRAY['id','catalog_id'])||jsonb_build_object('unit_label',label) END);""",
        ),
        (
            "'schema_version',3,'document_kind','physical_checkout'",
            "'schema_version',4,'document_kind','mixed_checkout'",
        ),
        (
            "'schema_version',3,'document_kind','physical_receipt'",
            "'schema_version',4,'document_kind','mixed_receipt'",
        ),
        ("INSERT INTO public.pos_physical_checkout", "INSERT INTO public.pos_mixed_checkout"),
        (
            "INSERT INTO public.pos_unit_checkout_payment_component"
            "(tenant_id,id,physical_checkout_id,",
            "INSERT INTO public.pos_unit_checkout_payment_component"
            "(tenant_id,id,mixed_checkout_id,",
        ),
        (
            "public.assert_physical_checkout(tenant,sale_id,true)",
            "public.assert_mixed_checkout(tenant,sale_id,true)",
        ),
        ("public.emit_native_physical_retail_event_v1", "public.emit_native_mixed_retail_event_v1"),
        ("'kind','physical_cash','scope',scope", "'kind','mixed_cash','scope',scope"),
    ):
        sql = _replace(sql, old, new)
    return sql


EVENT_SQL = _event_sql()
COMPLETE_SQL = _complete_sql()
FUNCTIONS = (
    "edge_mixed_legacy_stock_v1(uuid,uuid,uuid,jsonb,date)",
    "emit_native_mixed_retail_event_v1(uuid,uuid,uuid)",
    "complete_edge_mixed_cash_v1(uuid,uuid,bigint,uuid,jsonb,text)",
)


def _patches() -> tuple[tuple[str, str, str], ...]:
    patches = [
        (
            "trg_mixed_checkout_guard()",
            "DECLARE",
            "DECLARE\n v_native_mixed BOOLEAN:=false;",
        ),
        (
            "trg_mixed_checkout_guard()",
            "BEGIN\n",
            f"""BEGIN
 IF current_user='aurum_edge_cash_owner' AND {NATIVE_SESSION} THEN
  v_native_mixed:=public.native_edge_physical_context_v1();
 END IF;
""",
        ),
        (
            "trg_mixed_checkout_guard()",
            "current_user<>'aurum_app'",
            "(current_user<>'aurum_app' AND NOT v_native_mixed)",
        ),
        (
            "trg_mixed_stock_checkout_binding()",
            "session_user<>'aurum_app'",
            f"(session_user<>'aurum_app' AND NOT {CONTEXT})",
        ),
        (
            "trg_guard_sale_immutability()",
            """      INTO v_physical_cash;
  END IF;
  IF current_user = 'aurum_app' AND TG_OP = 'UPDATE' THEN""",
            """      INTO v_physical_cash;
    SELECT EXISTS(SELECT 1 FROM public.unit_stock_cart_command_v2
      WHERE tenant_id=NEW.tenant_id AND mixed_checkout_sale_id=NEW.id)
      INTO v_mixed_cash;
  END IF;
  IF current_user = 'aurum_app' AND TG_OP = 'UPDATE' THEN""",
        ),
        (
            "trg_unit_cart_payment_boundary()",
            """WHERE tenant_id=v_tenant AND mixed_checkout_sale_id=v_sale_id) THEN
  IF session_user<>'aurum_app'""",
            f"""WHERE tenant_id=v_tenant AND mixed_checkout_sale_id=v_sale_id) THEN
  IF (session_user<>'aurum_app' AND NOT {CONTEXT})""",
        ),
        (
            "trg_guard_sale_immutability()",
            """          AND m.cashier_user_id=public.current_app_user_id()
          AND session_user='aurum_app' AND NOT public.is_support_session()""",
            """          AND m.cashier_user_id=public.current_app_user_id()
          AND (session_user='aurum_app' OR v_native_physical)
          AND NOT public.is_support_session()""",
        ),
    ]
    # Patch the exact post-0235 clauses, retaining its current invoker/definer
    # metadata and avoiding another broad app/mixed/refund probe expansion.
    adaptations = {
        "trg_capture_retail_checkout_origin()": (
            "TG_TABLE_NAME='pos_physical_checkout'",
            "TG_TABLE_NAME IN ('pos_physical_checkout','pos_mixed_checkout')",
        ),
        "trg_retail_checkout_origin_guard()": (
            "NEW.source_kind='physical'",
            "NEW.source_kind IN ('physical','mixed')",
        ),
        "trg_retail_event_capture()": (
            "NEW.source_kind='physical'",
            "NEW.source_kind IN ('physical','mixed')",
        ),
        "assert_retail_event_source(uuid,uuid,uuid)": (
            "origin.source_kind='physical'",
            "origin.source_kind IN ('physical','mixed')",
        ),
        "reserve_retail_checkout_event_position(uuid,uuid,uuid)": (
            "origin.source_kind='physical'",
            "origin.source_kind IN ('physical','mixed')",
        ),
        "emit_physical_stock_source(uuid,uuid)": (
            "sale.id=command.checkout_sale_id",
            "sale.id=coalesce(command.checkout_sale_id,command.mixed_checkout_sale_id)",
        ),
    }
    for signature, _, replacement in physical.PATCHES:
        if signature in adaptations:
            old, new = adaptations[signature]
            if old in replacement:
                patches.append((signature, replacement, _replace(replacement, old, new)))
    if len(patches) != 14:
        raise RuntimeError("Native mixed historical patch inventory changed")
    return tuple(patches)


PATCHES = _patches()
INSERT_COLUMNS = {
    "unit_stock_cart_command_v2": "mixed_checkout_sale_id",
    "pos_mixed_checkout": (
        "tenant_id,id,sale_id,branch_id,register_id,shift_id,cashier_user_id,cash_intent_id,"
        "stock_command_id,payment_id,payment_journal_version,request_hash,result_payload,"
        "receipt_payload,created_by"
    ),
    "pos_unit_checkout_payment_component": "mixed_checkout_id",
}


def _grants(*, reverse: bool = False) -> None:
    for table, columns in INSERT_COLUMNS.items():
        action = "REVOKE INSERT" if reverse else "GRANT INSERT"
        direction = "FROM" if reverse else "TO"
        op.execute(f"{action} ({columns}) ON public.{table} {direction} aurum_edge_cash_owner")
    action = "REVOKE SELECT" if reverse else "GRANT SELECT"
    direction = "FROM" if reverse else "TO"
    op.execute(
        f"{action} (mixed_checkout_sale_id) ON public.unit_stock_cart_command_v2 "
        f"{direction} aurum_edge_cash_owner"
    )


def _scope(table: str) -> str:
    if table == "unit_stock_cart_line_v2":
        return f"""tenant_id=public.current_tenant_id() AND {CONTEXT} AND EXISTS(
          SELECT 1 FROM public.unit_stock_cart_command_v2 command
          WHERE command.tenant_id={table}.tenant_id AND command.id={table}.command_id
           AND command.checkout_sale_id IS NULL AND command.mixed_checkout_sale_id IS NOT NULL)"""
    if table == "pos_unit_checkout_payment_component":
        return f"""tenant_id=public.current_tenant_id() AND {CONTEXT}
          AND physical_checkout_id IS NULL AND mixed_checkout_id IS NOT NULL
          AND EXISTS(SELECT 1 FROM public.pos_mixed_checkout marker
            WHERE marker.tenant_id={table}.tenant_id AND marker.id={table}.mixed_checkout_id
             AND marker.sale_id={table}.sale_id)"""
    return f"""tenant_id=public.current_tenant_id() AND {CONTEXT}
      AND cashier_user_id=public.current_app_user_id() AND EXISTS(
       SELECT 1 FROM public.edge_cash_node_identity identity
       WHERE identity.database_role=SESSION_USER AND identity.tenant_id={table}.tenant_id
        AND identity.branch_id={table}.branch_id AND identity.register_id={table}.register_id)"""


DISPATCH_SIGNATURE = "dispatch_edge_cash_sale_v1(uuid,uuid,bigint,uuid,jsonb,text)"
DISPATCH_OLD = """  IF p_payload->>'profile'='retail_cash.complete.v1' THEN
    RETURN public.complete_edge_physical_cash_v1("""
DISPATCH_NEW = """  IF p_payload->>'profile'='retail_cash.complete.v1' THEN
    IF p_payload->>'kind'='mixed_cash' THEN
      RETURN public.complete_edge_mixed_cash_v1(
        p_operation_id,p_activation_id,p_writer_epoch,p_cashier_user_id,p_payload,p_request_hash);
    END IF;
    RETURN public.complete_edge_physical_cash_v1("""


def _dispatcher(*, reverse: bool = False) -> None:
    connection = op.get_bind()
    execute = connection.exec_driver_sql(
        f"SELECT has_function_privilege('aurum_edge_cash_owner','public.{DISPATCH_SIGNATURE}',"
        "'EXECUTE')"
    ).scalar_one()
    op.execute("GRANT CREATE ON SCHEMA public TO aurum_edge_cash_owner")
    op.execute("RESET ROLE")
    op.execute("SET LOCAL ROLE aurum_edge_cash_owner")
    if not execute:
        op.execute(
            f"GRANT EXECUTE ON FUNCTION public.{DISPATCH_SIGNATURE} TO aurum_edge_cash_owner"
        )
    old, new = (DISPATCH_NEW, DISPATCH_OLD) if reverse else (DISPATCH_OLD, DISPATCH_NEW)
    physical._patch(DISPATCH_SIGNATURE, old, new)
    if not execute:
        op.execute(
            f"REVOKE EXECUTE ON FUNCTION public.{DISPATCH_SIGNATURE} FROM aurum_edge_cash_owner"
        )
    op.execute("RESET ROLE")
    op.execute("SET LOCAL ROLE aurum_schema_owner")
    op.execute("REVOKE CREATE ON SCHEMA public FROM aurum_edge_cash_owner")


def upgrade() -> None:
    for sql in (LEGACY_SQL, EVENT_SQL, COMPLETE_SQL):
        op.get_bind().exec_driver_sql(sql)
    for signature in FUNCTIONS:
        op.execute(f"ALTER FUNCTION public.{signature} OWNER TO aurum_schema_owner")
        op.execute(
            f"REVOKE ALL ON FUNCTION public.{signature} "
            "FROM PUBLIC,aurum_app,aurum_support,aurum_edge_cash_executor,aurum_edge_cash_owner"
        )
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{signature} TO aurum_edge_cash_owner")
    for signature, old, new in PATCHES:
        physical._patch(signature, old, new)
    for table in (
        "unit_stock_cart_line_v2",
        "pos_mixed_checkout",
        "pos_unit_checkout_payment_component",
    ):
        op.execute(
            f"CREATE POLICY edge_mixed_execution_insert ON public.{table} "
            f"FOR INSERT TO aurum_edge_cash_owner WITH CHECK({_scope(table)})"
        )
    op.execute(
        "CREATE POLICY edge_mixed_execution_read ON public.unit_stock_cart_line_v2 "
        f"FOR SELECT TO aurum_edge_cash_owner USING({_scope('unit_stock_cart_line_v2')})"
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
          WHERE origin.source_kind='mixed' AND node.node_kind='edge') THEN
          RAISE EXCEPTION 'Native mixed cash history prevents downgrade' USING ERRCODE='55000';
        END IF;
      END $guard$;
    """)
    _dispatcher(reverse=True)
    for signature, old, new in reversed(PATCHES):
        physical._patch(signature, new, old)
    for table in (
        "unit_stock_cart_line_v2",
        "pos_mixed_checkout",
        "pos_unit_checkout_payment_component",
    ):
        op.execute(f"DROP POLICY edge_mixed_execution_insert ON public.{table}")
    op.execute("DROP POLICY edge_mixed_execution_read ON public.unit_stock_cart_line_v2")
    _grants(reverse=True)
    for signature in reversed(FUNCTIONS):
        op.execute(f"DROP FUNCTION public.{signature}")
