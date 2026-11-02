"""The native monetary writer remains private, scoped and unable to export costs."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from tests.isolation.test_edge_physical_cash_execution_migration import NEW_HELPERS

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
    },
    "unit_stock_cart_line_v2": {
        "tenant_id",
        "command_id",
        "line_id",
        "position",
        "catalog_id",
        "packaging",
        "sale_format",
        "quantity",
        "base_units",
        "created_by",
        "original_retail_unit_price",
        "gross_amount",
        "discount_amount",
        "net_amount",
    },
    "unit_stock_movement_v2": {
        "id",
        "tenant_id",
        "branch_id",
        "catalog_id",
        "command_id",
        "line_id",
        "lot_id",
        "pack_id",
        "kind",
        "entry_order",
        "closed_delta",
        "units_taken",
        "sealed_start",
        "sealed_count",
        "before_state",
        "after_state",
        "created_by",
    },
    "pos_physical_checkout": {
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
        "sale_id",
        "method",
        "amount",
        "currency",
        "created_by",
    },
}
DEFINERS = {
    "edge_physical_uuid5_v1(uuid,text)",
    "native_edge_physical_actor_v1(uuid,uuid,uuid,uuid)",
    "native_edge_physical_context_v1()",
    "lock_native_physical_gates_v1(uuid,uuid,uuid,uuid)",
}


async def test_native_physical_helpers_are_private_and_have_fixed_execution_context(
    maintenance_engine: AsyncEngine,
) -> None:
    async with maintenance_engine.connect() as connection:
        for signature in NEW_HELPERS:
            row = (
                await connection.execute(
                    text("""
                SELECT pg_get_userbyid(proowner),prosecdef,proconfig,
                  has_function_privilege('aurum_edge_cash_owner',oid,'EXECUTE'),
                  has_function_privilege('aurum_edge_cash_executor',oid,'EXECUTE'),
                  has_function_privilege('aurum_app',oid,'EXECUTE'),
                  has_function_privilege('aurum_support',oid,'EXECUTE'),
                  EXISTS(SELECT 1 FROM aclexplode(coalesce(proacl,acldefault('f',proowner))) acl
                    WHERE grantee=0 AND privilege_type='EXECUTE'),
                  EXISTS(SELECT 1 FROM aclexplode(coalesce(proacl,acldefault('f',proowner))) acl
                    WHERE grantee<>(SELECT oid FROM pg_roles WHERE rolname='aurum_schema_owner')
                      AND is_grantable)
                FROM pg_proc WHERE oid=to_regprocedure(:signature)
            """),
                    {"signature": "public." + signature},
                )
            ).one()
            assert row[0] == "aurum_schema_owner"
            assert row[1] is (signature in DEFINERS)
            assert set(row[2]) == {"search_path=pg_catalog, pg_temp", "row_security=on"}
            assert tuple(row[3:]) == (True, False, False, False, False, False)


async def test_native_physical_insert_grants_are_exact_columns_not_broad_table_access(
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
                    'INSERT WITH GRANT OPTION') AS grantable,
                  has_column_privilege('aurum_edge_cash_executor',relation.oid,attribute.attnum,
                    'SELECT,INSERT,UPDATE,REFERENCES') AS executor_access
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
            assert all(not row["grantable"] and not row["executor_access"] for row in rows)
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


async def test_native_physical_policies_require_tenant_and_actual_native_context(
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
            WHERE policy.polname IN
              ('edge_physical_execution_insert','edge_physical_execution_read')
        """))).mappings().all()
    assert {(row["relname"], row["polcmd"]) for row in rows} == {
        *((table, "a") for table in INSERT_COLUMNS),
        ("unit_stock_cart_command_v2", "r"),
        ("unit_stock_cart_line_v2", "r"),
    }
    for row in rows:
        assert row["relrowsecurity"] and row["relforcerowsecurity"] and row["owner_only"]
        assert row["polpermissive"]
        expression = row["check_expression"] if row["polcmd"] == "a" else row["using_expression"]
        assert "tenant_id" in expression and "current_tenant_id()" in expression
        assert "native_edge_physical_context_v1()" in expression
        assert "support_session" not in expression
        if row["relname"] in {
            "unit_stock_cart_command_v2",
            "unit_stock_movement_v2",
            "pos_physical_checkout",
        }:
            assert "SESSION_USER" in expression and "edge_cash_node_identity" in expression


@pytest.mark.parametrize("role", ["aurum_app", "aurum_support"])
async def test_application_logins_cannot_call_private_native_physical_writer(role: str) -> None:
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
                            "SELECT public.complete_edge_physical_cash_v1("
                            ":operation,:activation,1,:actor,'{}'::jsonb,:hash)"
                        ),
                        {
                            "operation": uuid4(),
                            "activation": uuid4(),
                            "actor": uuid4(),
                            "hash": "a" * 64,
                        },
                    )
            assert getattr(denied.value.orig, "sqlstate", None) == "42501"
    finally:
        await engine.dispose()


async def test_owner_cannot_forge_native_identity_or_read_purchase_costs(
    maintenance_engine: AsyncEngine,
) -> None:
    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            assert str(await connection.scalar(text("SELECT current_database()"))).endswith("_test")
            assert await connection.scalar(text("SELECT session_user")) == "aurum_migrator"
            await connection.execute(text("RESET ROLE"))
            await connection.execute(text("SET LOCAL ROLE aurum_edge_cash_owner"))
            for name in ("tenant_id", "branch_id", "user_id", "edge_node_id"):
                await connection.execute(
                    text("SELECT set_config(:name,:value,true)"),
                    {"name": "app." + name, "value": str(uuid4())},
                )
            assert (
                await connection.scalar(text("SELECT public.native_edge_physical_context_v1()"))
                is False
            )
            for sql in (
                "SELECT purchase_cost_per_pack FROM public.unit_stock_lot_v2 LIMIT 0",
                "SELECT initial_opened_cost FROM public.unit_stock_lot_v2 LIMIT 0",
                "SELECT cost_amount FROM public.unit_stock_movement_v2 LIMIT 0",
                "INSERT INTO public.unit_stock_cart_command_v2 "
                "(tenant_id,id,branch_id,actor_id,request_hash,retail_version,currency) "
                "VALUES(gen_random_uuid(),gen_random_uuid(),gen_random_uuid(),"
                "gen_random_uuid(),repeat('a',64),2,'TJS')",
            ):
                with pytest.raises(DBAPIError) as denied:
                    async with connection.begin_nested():
                        await connection.execute(text(sql))
                assert getattr(denied.value.orig, "sqlstate", None) == "42501"
        finally:
            await transaction.rollback()
