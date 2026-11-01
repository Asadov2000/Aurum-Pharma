"""Rehearse replay DDL in a rolled-back disposable CI database transaction."""

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
HELPER = "public.replay_edge_retail_cash_v1(uuid,uuid,bigint,uuid,jsonb,text)"
PREVIOUS_HELPERS = (
    "public.prepare_edge_retail_cash_v1(uuid,uuid,bigint,uuid,jsonb,text)",
    "public.lock_edge_retail_cash_inputs_v1(uuid,uuid,bigint,uuid,jsonb,text)",
    "public.lock_edge_retail_cash_draft_v1(uuid,uuid,bigint,uuid,jsonb,text)",
)
VALIDATORS = (
    "public.assert_physical_checkout(uuid,uuid,boolean)",
    "public.assert_mixed_checkout(uuid,uuid,boolean)",
)
COLUMNS_SQL = text("""
    SELECT relation.relname,attribute.attname,grantee.rolname,acl.privilege_type,
      acl.grantor,acl.is_grantable
    FROM pg_attribute attribute JOIN pg_class relation ON relation.oid=attribute.attrelid
    CROSS JOIN LATERAL aclexplode(attribute.attacl) acl
    JOIN pg_roles grantee ON grantee.oid=acl.grantee
    WHERE relation.oid IN ('public.pos_physical_checkout'::REGCLASS,
      'public.pos_mixed_checkout'::REGCLASS)
      AND attribute.attnum>0 AND NOT attribute.attisdropped
    ORDER BY relation.relname,attribute.attnum,grantee.rolname,acl.privilege_type,acl.grantor
""")
POLICIES_SQL = text("""
    SELECT polrelid,polname,polroles,polcmd::TEXT,polpermissive,
      pg_get_expr(polqual,polrelid),pg_get_expr(polwithcheck,polrelid)
    FROM pg_policy WHERE polname='edge_cash_replay_read'
    ORDER BY polrelid,polname
""")


def _migration() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "alembic/versions/0234_edge_retail_cash_replay.py"
    spec = importlib.util.spec_from_file_location("edge_retail_replay_0234", path)
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
    assert connection.scalar(text("SELECT to_regprocedure(:helper)"), {"helper": HELPER}) is None
    assert not connection.execute(POLICIES_SQL).all()
    columns = connection.execute(COLUMNS_SQL).all()
    assert all(row[2] != "aurum_edge_cash_owner" for row in columns)
    for validator in VALIDATORS:
        assert (
            connection.scalar(
                text("SELECT has_function_privilege('aurum_edge_cash_owner',:validator,'EXECUTE')"),
                {"validator": validator},
            )
            is False
        )


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
    previous = {signature: _routine(connection, signature) for signature in PREVIOUS_HELPERS}
    validators = {signature: _routine(connection, signature) for signature in VALIDATORS}
    helper = _routine(connection, HELPER)
    columns = connection.execute(COLUMNS_SQL).all()
    policies = connection.execute(POLICIES_SQL).all()
    assert "retail_cash.replay.v1" in str(dispatcher[5])
    with Operations.context(MigrationContext.configure(connection)):
        _migration().downgrade()
        old = _routine(connection, DISPATCHER)
        assert old[:5] == dispatcher[:5]
        assert "retail_cash.replay.v1" not in str(old[5])
        assert "retail_cash.draft.v1" in str(old[5])
        _assert_downgraded(connection)
        for signature, snapshot in previous.items():
            assert _routine(connection, signature) == snapshot
        for signature, snapshot in validators.items():
            old_validator = _routine(connection, signature)
            assert old_validator[:2] == snapshot[:2]
            assert old_validator[3:] == snapshot[3:]
        _migration().upgrade()
    assert _routine(connection, DISPATCHER) == dispatcher
    assert _routine(connection, HELPER)[1:] == helper[1:]
    for signature, snapshot in (previous | validators).items():
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
async def test_native_replay_roundtrip_preserves_existing_dispatcher_and_helpers(
    maintenance_engine: AsyncEngine,
    owner_execute: bool,
) -> None:
    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(lambda sync: _roundtrip(sync, owner_execute))
        finally:
            await transaction.rollback()
