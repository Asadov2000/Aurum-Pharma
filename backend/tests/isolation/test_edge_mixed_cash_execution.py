"""Mixed native money keeps private helpers, exact column grants and tenant RLS."""

import hashlib

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.domains.pos.edge_mixed_cash_execution import mixed_completion_payload
from app.domains.pos.edge_mixed_cash_plan import build_edge_mixed_cash_plan
from tests.domains.pos.test_edge_retail_cash_repricing import draft_example

HELPERS = (
    "edge_mixed_legacy_stock_v1(uuid,uuid,uuid,jsonb,date)",
    "emit_native_mixed_retail_event_v1(uuid,uuid,uuid)",
    "complete_edge_mixed_cash_v1(uuid,uuid,bigint,uuid,jsonb,text)",
)
INSERT_COLUMNS = {
    "unit_stock_cart_command_v2": {
        "tenant_id",
        "id",
        "branch_id",
        "actor_id",
        "request_hash",
        "retail_version",
        "currency",
        "created_by",
        "checkout_sale_id",
        "mixed_checkout_sale_id",
    },
    "pos_mixed_checkout": {
        "tenant_id",
        "id",
        "sale_id",
        "branch_id",
        "register_id",
        "shift_id",
        "cashier_user_id",
        "cash_intent_id",
        "stock_command_id",
        "payment_id",
        "payment_journal_version",
        "request_hash",
        "result_payload",
        "receipt_payload",
        "created_by",
    },
    "pos_unit_checkout_payment_component": {
        "tenant_id",
        "id",
        "physical_checkout_id",
        "mixed_checkout_id",
        "sale_id",
        "method",
        "amount",
        "currency",
        "created_by",
    },
}
SELECT_COLUMNS = {
    "unit_stock_cart_command_v2": {
        "tenant_id",
        "id",
        "branch_id",
        "actor_id",
        "checkout_sale_id",
        "mixed_checkout_sale_id",
    },
    "pos_mixed_checkout": {
        "tenant_id",
        "id",
        "sale_id",
        "branch_id",
        "register_id",
        "shift_id",
        "cashier_user_id",
        "cash_intent_id",
        "payment_plan_intent_id",
        "stock_command_id",
        "payment_id",
        "payment_journal_version",
        "request_hash",
        "result_payload",
        "receipt_payload",
    },
}
RUNTIME_ROLES = (
    "aurum_app",
    "aurum_support",
    "aurum_edge_cash_executor",
    "aurum_worker",
    "aurum_catalog_worker",
    "aurum_billing_worker",
    "aurum_mailer",
)


async def test_mixed_helpers_are_private_invokers_with_fixed_search_path(
    maintenance_engine: AsyncEngine,
) -> None:
    async with maintenance_engine.connect() as connection:
        for signature in HELPERS:
            row = (
                await connection.execute(
                    text("""
                SELECT pg_get_userbyid(proowner),prosecdef,provolatile::TEXT,proconfig,
                  has_function_privilege('aurum_edge_cash_owner',oid,'EXECUTE'),
                  EXISTS(SELECT 1 FROM aclexplode(coalesce(proacl,acldefault('f',proowner))) acl
                    WHERE acl.grantee=0 AND acl.privilege_type='EXECUTE'),
                  EXISTS(SELECT 1 FROM aclexplode(coalesce(proacl,acldefault('f',proowner))) acl
                    WHERE acl.grantee<>proowner AND acl.is_grantable)
                FROM pg_proc WHERE oid=to_regprocedure(:signature)
            """),
                    {"signature": "public." + signature},
                )
            ).one()
            assert tuple(row[:3]) == ("aurum_schema_owner", False, "v")
            assert set(row[3]) == {"search_path=pg_catalog, pg_temp", "row_security=on"}
            assert tuple(row[4:]) == (True, False, False)
            for role in RUNTIME_ROLES:
                assert (
                    await connection.scalar(
                        text("SELECT has_function_privilege(:role,:signature,'EXECUTE')"),
                        {"role": role, "signature": "public." + signature},
                    )
                    is False
                )


async def test_mixed_writer_has_exact_insert_and_select_columns_without_broad_grants(
    maintenance_engine: AsyncEngine,
) -> None:
    async with maintenance_engine.connect() as connection:
        for table, expected in INSERT_COLUMNS.items():
            rows = (
                (
                    await connection.execute(
                        text("""
                SELECT attribute.attname,
                  has_column_privilege('aurum_edge_cash_owner',relation.oid,attribute.attnum,
                    'INSERT') AS can_insert,
                  has_column_privilege('aurum_edge_cash_owner',relation.oid,attribute.attnum,
                    'SELECT') AS can_select,
                  has_column_privilege('aurum_edge_cash_owner',relation.oid,attribute.attnum,
                    'INSERT WITH GRANT OPTION') AS insert_grantable,
                  has_column_privilege('aurum_edge_cash_owner',relation.oid,attribute.attnum,
                    'SELECT WITH GRANT OPTION') AS select_grantable,
                  has_column_privilege('aurum_edge_cash_executor',relation.oid,attribute.attnum,
                    'SELECT,INSERT,UPDATE,REFERENCES') AS executor_access,
                  EXISTS(SELECT 1 FROM pg_roles node WHERE node.rolname
                    ~ '^aurum_edge_node_[0-9a-f]{32}$'
                    AND has_column_privilege(node.oid,relation.oid,attribute.attnum,
                      'SELECT,INSERT,UPDATE,REFERENCES')) AS native_access
                FROM pg_class relation JOIN pg_attribute attribute
                  ON attribute.attrelid=relation.oid
                WHERE relation.oid=to_regclass(:table) AND attribute.attnum>0
                  AND NOT attribute.attisdropped
            """),
                        {"table": "public." + table},
                    )
                )
                .mappings()
                .all()
            )
            assert {row["attname"] for row in rows if row["can_insert"]} == expected
            if table in SELECT_COLUMNS:
                assert {row["attname"] for row in rows if row["can_select"]} == SELECT_COLUMNS[
                    table
                ]
            assert all(
                not row["insert_grantable"]
                and not row["select_grantable"]
                and not row["executor_access"]
                and not row["native_access"]
                for row in rows
            )
            assert (
                await connection.scalar(
                    text(
                        "SELECT has_table_privilege('aurum_edge_cash_owner',:table,"
                        "'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')"
                    ),
                    {"table": "public." + table},
                )
                is False
            )


async def test_mixed_rls_requires_actual_native_context_and_exclusive_mixed_links(
    maintenance_engine: AsyncEngine,
) -> None:
    async with maintenance_engine.connect() as connection:
        rows = (await connection.execute(text("""
            SELECT relation.relname,relation.relrowsecurity,relation.relforcerowsecurity,
              policy.polname,policy.polcmd::TEXT,policy.polpermissive,
              policy.polroles=ARRAY[(SELECT oid FROM pg_roles
                WHERE rolname='aurum_edge_cash_owner')] AS owner_only,
              pg_get_expr(policy.polqual,policy.polrelid) AS using_expression,
              pg_get_expr(policy.polwithcheck,policy.polrelid) AS check_expression
            FROM pg_policy policy JOIN pg_class relation ON relation.oid=policy.polrelid
            WHERE policy.polname IN ('edge_mixed_execution_insert','edge_mixed_execution_read')
        """))).mappings().all()
    assert {(row["relname"], row["polcmd"]) for row in rows} == {
        ("unit_stock_cart_line_v2", "a"),
        ("pos_mixed_checkout", "a"),
        ("pos_unit_checkout_payment_component", "a"),
        ("unit_stock_cart_line_v2", "r"),
    }
    for row in rows:
        assert row["relrowsecurity"] and row["relforcerowsecurity"]
        assert row["owner_only"] and row["polpermissive"]
        expression = row["using_expression"] if row["polcmd"] == "r" else row["check_expression"]
        assert "current_tenant_id()" in expression and "tenant_id" in expression
        assert "native_edge_physical_context_v1()" in expression
        assert "support_session" not in expression
        if row["relname"] == "unit_stock_cart_line_v2":
            assert "checkout_sale_id IS NULL" in expression
            assert "mixed_checkout_sale_id IS NOT NULL" in expression
        elif row["relname"] == "pos_unit_checkout_payment_component":
            assert "physical_checkout_id IS NULL" in expression
            assert "mixed_checkout_id IS NOT NULL" in expression
            assert "pos_mixed_checkout" in expression and "sale_id" in expression
        else:
            for required in (
                "SESSION_USER",
                "edge_cash_node_identity",
                "branch_id",
                "register_id",
                "cashier_user_id",
                "current_app_user_id()",
            ):
                assert required in expression


@pytest.mark.parametrize("role", ["aurum_app", "aurum_support"])
async def test_real_application_logins_cannot_invoke_private_mixed_coordinator(role: str) -> None:
    settings = get_settings()
    url = settings.DATABASE_URL_APP if role == "aurum_app" else settings.DATABASE_URL_SUPPORT
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            assert await connection.scalar(text("SELECT session_user")) == role
            with pytest.raises(DBAPIError) as denied:
                async with connection.begin_nested():
                    await connection.execute(
                        text(
                            "SELECT public.complete_edge_mixed_cash_v1("
                            "NULL::uuid,NULL::uuid,1,NULL::uuid,'{}'::jsonb,repeat('a',64))"
                        )
                    )
            assert getattr(denied.value.orig, "sqlstate", None) == "42501"
    finally:
        await engine.dispose()


async def test_private_owner_cannot_forge_native_session_or_export_legacy_costs(
    maintenance_engine: AsyncEngine,
) -> None:
    command, draft = draft_example("mixed_cash")
    payload = mixed_completion_payload(command, build_edge_mixed_cash_plan(draft))
    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            assert str(await connection.scalar(text("SELECT current_database()"))).endswith("_test")
            assert await connection.scalar(text("SELECT session_user")) == "aurum_migrator"
            await connection.execute(text("RESET ROLE"))
            await connection.execute(text("SET LOCAL ROLE aurum_edge_cash_owner"))
            for name, value in (
                ("tenant_id", command.scope.tenant_id),
                ("branch_id", command.scope.branch_id),
                ("user_id", command.scope.user_id),
                ("edge_node_id", command.scope.edge_node_id),
            ):
                await connection.execute(
                    text("SELECT set_config(:name,:value,true)"),
                    {"name": "app." + name, "value": str(value)},
                )
            assert (
                await connection.scalar(text("SELECT public.native_edge_physical_context_v1()"))
                is False
            )
            with pytest.raises(DBAPIError) as denied:
                async with connection.begin_nested():
                    await connection.execute(
                        text(
                            "SELECT public.complete_edge_mixed_cash_v1("
                            ":operation,:activation,:epoch,:actor,CAST(:payload AS jsonb),:hash)"
                        ),
                        {
                            "operation": command.confirmation.operation_id,
                            "activation": command.scope.activation_id,
                            "epoch": command.scope.writer_epoch,
                            "actor": command.scope.user_id,
                            "payload": payload,
                            "hash": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                        },
                    )
            assert getattr(denied.value.orig, "sqlstate", None) == "42501"
            for sql in (
                "SELECT purchase_price FROM public.batch LIMIT 0",
                "UPDATE public.pos_mixed_checkout SET request_hash=request_hash",
                "INSERT INTO public.pos_unit_checkout_payment_component "
                "(tenant_id,id,mixed_checkout_id,sale_id,method,amount,currency) "
                "VALUES(gen_random_uuid(),gen_random_uuid(),gen_random_uuid(),"
                "gen_random_uuid(),'cash',1.00,'TJS')",
            ):
                with pytest.raises(DBAPIError) as rejected:
                    async with connection.begin_nested():
                        await connection.execute(text(sql))
                assert getattr(rejected.value.orig, "sqlstate", None) == "42501"
        finally:
            await transaction.rollback()
