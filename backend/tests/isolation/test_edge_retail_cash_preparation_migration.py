"""Rehearse the latest native preparation boundary only in a disposable database."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from alembic.migration import MigrationContext
from alembic.operations import Operations

DISPATCHER = "public.dispatch_edge_cash_sale_v1(uuid,uuid,bigint,uuid,jsonb,text)"
HELPER = "public.prepare_edge_retail_cash_v1(uuid,uuid,bigint,uuid,jsonb,text)"
DISPATCHER_SNAPSHOT = text(
    "SELECT oid,proowner,proacl,prosecdef,proconfig,pg_get_functiondef(oid) FROM pg_proc "
    f"WHERE oid='{DISPATCHER}'::regprocedure"
)
INTENT_ACCESS = text(
    "SELECT attname,has_column_privilege('aurum_edge_cash_owner',attrelid,attnum,'SELECT'),"
    "has_column_privilege('aurum_edge_cash_owner',attrelid,attnum,'UPDATE') "
    "FROM pg_attribute WHERE attrelid='public.pos_unit_cash_intent'::regclass "
    "AND attnum>0 AND NOT attisdropped ORDER BY attnum"
)
INTENT_POLICY = text(
    "SELECT polroles,polcmd::TEXT,pg_get_expr(polqual,polrelid) FROM pg_policy "
    "WHERE polrelid='public.pos_unit_cash_intent'::regclass "
    "AND polname='edge_cash_saved_confirmation'"
)


def _migration() -> ModuleType:
    source = (
        Path(__file__).resolve().parents[2]
        / "alembic/versions/0231_edge_retail_cash_preparation.py"
    )
    spec = importlib.util.spec_from_file_location("edge_retail_preparation_0231", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _assert_private_helper(connection: Connection) -> None:
    helper = connection.execute(
        text(
            "SELECT prosecdef,proconfig,"
            "has_function_privilege('aurum_app',oid,'EXECUTE'),"
            "has_function_privilege('aurum_support',oid,'EXECUTE'),"
            "has_function_privilege('aurum_edge_cash_executor',oid,'EXECUTE'),"
            "has_function_privilege('aurum_edge_cash_owner',oid,'EXECUTE'),"
            "EXISTS(SELECT 1 FROM aclexplode(COALESCE(proacl,acldefault('f',proowner))) acl "
            "WHERE acl.grantee=0 AND acl.privilege_type='EXECUTE') "
            f"FROM pg_proc WHERE oid='{HELPER}'::regprocedure"
        )
    ).one()
    assert helper[0] is False
    assert set(helper[1]) == {"search_path=pg_catalog, pg_temp", "row_security=on"}
    assert tuple(helper[2:]) == (False, False, False, True, False)
    assert (
        connection.scalar(
            text("SELECT has_schema_privilege('aurum_edge_cash_owner','public','CREATE')")
        )
        is False
    )


@pytest.mark.parametrize("owner_execute", [False, True])
async def test_native_preparation_roundtrip_preserves_dispatcher_and_narrow_acl(
    maintenance_engine: AsyncEngine,
    owner_execute: bool,
) -> None:
    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        connection.execute(text("RESET ROLE"))
        connection.execute(text("SET LOCAL ROLE aurum_edge_cash_owner"))
        statement = (
            f"GRANT EXECUTE ON FUNCTION {DISPATCHER} TO aurum_edge_cash_owner"
            if owner_execute
            else f"REVOKE EXECUTE ON FUNCTION {DISPATCHER} FROM aurum_edge_cash_owner"
        )
        connection.execute(text(statement))
        connection.execute(text("RESET ROLE"))
        connection.execute(text("SET LOCAL ROLE aurum_schema_owner"))
        assert (
            connection.scalar(
                text(
                    "SELECT has_function_privilege('aurum_edge_cash_owner',"
                    f"'{DISPATCHER}','EXECUTE')"
                )
            )
            is owner_execute
        )
        before = connection.execute(DISPATCHER_SNAPSHOT).one()
        before_access = connection.execute(INTENT_ACCESS).all()
        before_policy = connection.execute(INTENT_POLICY).one()
        assert before_policy[1] == "r"
        assert "SESSION_USER" in before_policy[2]
        assert "database_role_oid" in before_policy[2]
        _assert_private_helper(connection)
        selected = {row[0] for row in before_access if row[1]}
        assert selected == set(_migration().INTENT_COLUMNS.split(","))
        assert not any(row[2] for row in before_access)
        assert before[3] is True
        assert "retail_cash.prepare.v1" in before[5]
        with Operations.context(MigrationContext.configure(connection)):
            _migration().downgrade()
            legacy = connection.execute(DISPATCHER_SNAPSHOT).one()
            assert legacy[:5] == before[:5]
            assert "retail_cash.prepare.v1" not in legacy[5]
            assert connection.scalar(text(f"SELECT to_regprocedure('{HELPER}')")) is None
            assert connection.execute(INTENT_POLICY).first() is None
            assert not any(row[1] or row[2] for row in connection.execute(INTENT_ACCESS))
            _migration().upgrade()
        assert connection.execute(DISPATCHER_SNAPSHOT).one() == before
        assert connection.execute(INTENT_ACCESS).all() == before_access
        assert connection.execute(INTENT_POLICY).one() == before_policy
        _assert_private_helper(connection)

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()
