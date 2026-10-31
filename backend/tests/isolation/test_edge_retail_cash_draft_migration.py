"""Roundtrip the current-draft helper only inside a disposable CI transaction."""

from __future__ import annotations

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
HELPER = "public.lock_edge_retail_cash_draft_v1(uuid,uuid,bigint,uuid,jsonb,text)"
PREPARE = "public.prepare_edge_retail_cash_v1(uuid,uuid,bigint,uuid,jsonb,text)"
INPUTS = "public.lock_edge_retail_cash_inputs_v1(uuid,uuid,bigint,uuid,jsonb,text)"
COLUMNS_SQL = text("""
    SELECT relation.relname,attribute.attname,
      has_column_privilege('aurum_edge_cash_owner',relation.oid,attribute.attnum,'SELECT'),
      has_column_privilege('aurum_edge_cash_owner',relation.oid,attribute.attnum,'UPDATE'),
      has_column_privilege('aurum_edge_cash_owner',relation.oid,attribute.attnum,'INSERT')
    FROM pg_attribute attribute JOIN pg_class relation ON relation.oid=attribute.attrelid
    WHERE relation.oid IN ('public.pos_unit_cart_item'::REGCLASS,'public.sale_item'::REGCLASS,
      'public.prescription_log'::REGCLASS,'public.pos_unit_payment_plan_binding'::REGCLASS,
      'public.pos_unit_payment_plan_binding_cancel'::REGCLASS)
      AND attribute.attnum>0 AND NOT attribute.attisdropped
    ORDER BY relation.relname,attribute.attnum
""")
POLICIES_SQL = text("""
    SELECT polrelid,polname,polroles,polcmd::TEXT,polpermissive,
      pg_get_expr(polqual,polrelid),pg_get_expr(polwithcheck,polrelid)
    FROM pg_policy WHERE polname LIKE 'edge_cash_draft_%'
    ORDER BY polrelid,polname
""")


def _migration() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "alembic/versions/0233_edge_retail_cash_draft.py"
    spec = importlib.util.spec_from_file_location("edge_retail_draft_0233", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _routine(connection: Connection, signature: str) -> tuple[object, ...]:
    return tuple(
        connection.execute(
            text(
                "SELECT oid,proowner,proacl,prosecdef,proconfig,pg_get_functiondef(oid) "
                "FROM pg_proc WHERE oid=to_regprocedure(:signature)"
            ),
            {"signature": signature},
        ).one()
    )


def _assert_downgraded(connection: Connection) -> None:
    rows = connection.execute(COLUMNS_SQL).all()
    added_tables = {
        "pos_unit_cart_item",
        "pos_unit_payment_plan_binding",
        "pos_unit_payment_plan_binding_cancel",
    }
    assert all(not any(row[2:]) for row in rows if row[0] in added_tables)
    # Legacy SELECT rights existed before 0233 and must survive its downgrade.
    legacy = [row for row in rows if row[0] in {"sale_item", "prescription_log"}]
    assert legacy and all(row[2] and not row[3] for row in legacy)
    assert not connection.execute(POLICIES_SQL).all()


def _roundtrip(connection: Connection, owner_execute: bool) -> None:
    assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
    assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
    connection.execute(text("RESET ROLE"))
    connection.execute(text("SET LOCAL ROLE aurum_edge_cash_owner"))
    privilege = "GRANT" if owner_execute else "REVOKE"
    direction = "TO" if owner_execute else "FROM"
    connection.execute(
        text(f"{privilege} EXECUTE ON FUNCTION {DISPATCHER} {direction} aurum_edge_cash_owner")
    )
    connection.execute(text("RESET ROLE"))
    connection.execute(text("SET LOCAL ROLE aurum_schema_owner"))
    dispatcher = _routine(connection, DISPATCHER)
    helpers = {signature: _routine(connection, signature) for signature in (PREPARE, INPUTS)}
    helper = _routine(connection, HELPER)
    columns = connection.execute(COLUMNS_SQL).all()
    policies = connection.execute(POLICIES_SQL).all()
    assert "retail_cash.draft.v1" in str(dispatcher[5])
    with Operations.context(MigrationContext.configure(connection)):
        _migration().downgrade()
        old = _routine(connection, DISPATCHER)
        assert old[:5] == dispatcher[:5]
        assert "retail_cash.draft.v1" not in str(old[5])
        assert "retail_cash.inputs.v1" in str(old[5])
        assert connection.scalar(text(f"SELECT to_regprocedure('{HELPER}')")) is None
        _assert_downgraded(connection)
        for signature, snapshot in helpers.items():
            assert _routine(connection, signature) == snapshot
        _migration().upgrade()
    assert _routine(connection, DISPATCHER) == dispatcher
    assert _routine(connection, HELPER)[1:] == helper[1:]
    for signature, snapshot in helpers.items():
        assert _routine(connection, signature) == snapshot
    assert connection.execute(COLUMNS_SQL).all() == columns
    assert connection.execute(POLICIES_SQL).all() == policies
    assert (
        connection.scalar(
            text("SELECT has_schema_privilege('aurum_edge_cash_owner','public','CREATE')")
        )
        is False
    )


@pytest.mark.parametrize("owner_execute", [False, True])
async def test_native_draft_roundtrip_preserves_inputs_preparation_and_dispatcher(
    maintenance_engine: AsyncEngine,
    owner_execute: bool,
) -> None:
    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(lambda sync: _roundtrip(sync, owner_execute))
        finally:
            await transaction.rollback()
