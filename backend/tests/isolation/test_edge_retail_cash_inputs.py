"""Native input snapshots expose narrow columns and retain the read-only boundary."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

HELPER = "public.lock_edge_retail_cash_inputs_v1(uuid,uuid,bigint,uuid,jsonb,text)"
PHYSICAL_COLUMNS = {
    "unit_stock_lot_v2": {
        "id",
        "tenant_id",
        "branch_id",
        "catalog_id",
        "expires_at",
        "created_at",
        "packaging",
        "closed_packs",
        "is_blocked",
    },
    "unit_stock_opened_pack_v2": {
        "id",
        "tenant_id",
        "branch_id",
        "catalog_id",
        "lot_id",
        "opened_at",
        "state",
        "remaining_units",
    },
}


async def test_native_input_helper_is_private_invoker_with_fixed_search_path(
    maintenance_engine: AsyncEngine,
) -> None:
    async with maintenance_engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT prosecdef,provolatile::TEXT,proconfig,"
                    "has_function_privilege('aurum_edge_cash_owner',oid,'EXECUTE'),"
                    "has_function_privilege('aurum_edge_cash_executor',oid,'EXECUTE'),"
                    "has_function_privilege('aurum_app',oid,'EXECUTE'),"
                    "has_function_privilege('aurum_support',oid,'EXECUTE'),"
                    "EXISTS(SELECT 1 FROM "
                    "aclexplode(COALESCE(proacl,acldefault('f',proowner))) acl "
                    "WHERE acl.grantee=0 AND acl.privilege_type='EXECUTE') "
                    f"FROM pg_proc WHERE oid='{HELPER}'::REGPROCEDURE"
                )
            )
        ).one()
    assert tuple(row[:2]) == (False, "v")
    assert set(row[2]) == {"search_path=pg_catalog, pg_temp", "row_security=on"}
    assert tuple(row[3:]) == (True, False, False, False, False)


async def test_native_physical_input_columns_exclude_purchase_cost_and_writes(
    maintenance_engine: AsyncEngine,
) -> None:
    async with maintenance_engine.connect() as connection:
        rows = (await connection.execute(text("""
            SELECT relation.relname,attribute.attname,
              has_column_privilege('aurum_edge_cash_owner',relation.oid,attribute.attnum,
                'SELECT') AS can_select,
              has_column_privilege('aurum_edge_cash_owner',relation.oid,attribute.attnum,
                'UPDATE') AS can_update,
              has_column_privilege('aurum_edge_cash_owner',relation.oid,attribute.attnum,
                'INSERT') AS can_insert,
              has_column_privilege('aurum_edge_cash_owner',relation.oid,attribute.attnum,
                'UPDATE WITH GRANT OPTION') AS grantable,
              has_column_privilege('aurum_edge_cash_executor',relation.oid,attribute.attnum,
                'SELECT,INSERT,UPDATE,REFERENCES') AS executor_access
            FROM pg_attribute attribute JOIN pg_class relation ON relation.oid=attribute.attrelid
            WHERE relation.oid IN ('public.unit_stock_lot_v2'::REGCLASS,
              'public.unit_stock_opened_pack_v2'::REGCLASS)
              AND attribute.attnum>0 AND NOT attribute.attisdropped
        """))).mappings().all()
    for table, expected in PHYSICAL_COLUMNS.items():
        scoped = [row for row in rows if row["relname"] == table]
        assert {row["attname"] for row in scoped if row["can_select"]} == expected
        assert {row["attname"] for row in scoped if row["can_update"]} == {"id"}
    assert all(not row["can_insert"] and not row["grantable"] for row in rows)
    assert all(not row["executor_access"] for row in rows)


async def test_native_input_update_policies_are_restrictive_and_scope_bound(
    maintenance_engine: AsyncEngine,
) -> None:
    async with maintenance_engine.connect() as connection:
        rows = (await connection.execute(text("""
            SELECT relation.relname,policy.polpermissive,policy.polcmd::TEXT,
              pg_get_expr(policy.polqual,policy.polrelid) AS using_expression,
              pg_get_expr(policy.polwithcheck,policy.polrelid) AS check_expression,
              policy.polroles=ARRAY[(SELECT oid FROM pg_roles
                WHERE rolname='aurum_edge_cash_owner')] AS owner_only
            FROM pg_policy policy JOIN pg_class relation ON relation.oid=policy.polrelid
            WHERE policy.polname='edge_cash_inputs_no_write'
        """))).mappings().all()
    assert {row["relname"] for row in rows} == {
        "tenant_catalog",
        "unit_stock_lot_v2",
        "unit_stock_opened_pack_v2",
    }
    for row in rows:
        assert row["polpermissive"] is False
        assert row["polcmd"] == "w"
        assert row["check_expression"] == "false"
        assert row["owner_only"] is True
        assert "SESSION_USER" in row["using_expression"]
        assert "database_role_oid" in row["using_expression"]
        assert "tenant_id" in row["using_expression"]


@pytest.mark.parametrize("role", ["aurum_app", "aurum_support"])
async def test_nonowner_cannot_invoke_native_input_helper(
    role: str,
) -> None:
    # A migrator deliberately cannot SET ROLE to runtime identities. Connect
    # through the same disposable URLs already validated by conftest instead.
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
                            "SELECT public.lock_edge_retail_cash_inputs_v1("
                            ":operation,:activation,2,:actor,'{}'::JSONB,:hash)"
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


async def test_executor_is_nologin_and_cannot_execute_input_helper(
    maintenance_engine: AsyncEngine,
) -> None:
    async with maintenance_engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT rolcanlogin,has_function_privilege(rolname,:helper,'EXECUTE') "
                    "FROM pg_roles WHERE rolname='aurum_edge_cash_executor'"
                ),
                {"helper": HELPER},
            )
        ).one()
    assert tuple(row) == (False, False)
    # Its real per-node LOGIN descendants also prove direct-call refusal in
    # test-edge-cash-dispatcher.sh; the group role itself cannot log in.
