"""Native mixed cash DDL preserves the established financial security boundary."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from alembic.migration import MigrationContext
from alembic.operations import Operations
from tests.isolation.test_edge_physical_cash_execution_migration import (
    DISPATCHER,
    relation_security,
    routines,
)
from tests.isolation.test_physical_cash_checkout_migration import (
    _native_replay_security_snapshot,
    at_native_revision,
)

NEW_HELPERS = (
    "edge_mixed_legacy_stock_v1(uuid,uuid,uuid,jsonb,date)",
    "emit_native_mixed_retail_event_v1(uuid,uuid,uuid)",
    "complete_edge_mixed_cash_v1(uuid,uuid,bigint,uuid,jsonb,text)",
)


def migration() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[2] / "alembic/versions/0236_edge_mixed_cash_execution.py"
    )
    spec = importlib.util.spec_from_file_location("mixed_cash_execution_0236", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_native_history_blocks_downgrade(connection: Connection) -> None:
    """The late disposable proof calls this after genuine committed mixed sales."""
    assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
    assert connection.scalar(text("""
        SELECT EXISTS(SELECT 1 FROM public.pos_retail_checkout_origin origin
        JOIN public.sync_node node ON node.tenant_id=origin.tenant_id
          AND node.id=origin.origin_node_id AND node.branch_id=origin.branch_id
        JOIN public.edge_cash_command anchor ON anchor.tenant_id=origin.tenant_id
          AND anchor.operation_id=origin.id AND anchor.edge_node_id=node.id
        WHERE origin.source_kind='mixed' AND node.node_kind='edge'
          AND anchor.sale_status='completed' AND anchor.result_payload->>'kind'='mixed_cash')
    """)) is True
    before = _native_replay_security_snapshot(connection)
    try:
        with connection.begin_nested():
            with Operations.context(MigrationContext.configure(connection)):
                migration().downgrade()
    except DBAPIError as error:
        assert getattr(error.orig, "sqlstate", None) == "55000"
    else:
        raise AssertionError("Genuine native mixed financial history allowed downgrade")
    assert _native_replay_security_snapshot(connection) == before


def roundtrip(connection: Connection, owner_execute: bool) -> None:
    assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
    assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
    with Operations.context(MigrationContext.configure(connection)):
        migration().downgrade()
        connection.execute(text("RESET ROLE"))
        connection.execute(text("SET LOCAL ROLE aurum_edge_cash_owner"))
        action, direction = ("GRANT", "TO") if owner_execute else ("REVOKE", "FROM")
        connection.execute(
            text(f"{action} EXECUTE ON FUNCTION {DISPATCHER} {direction} aurum_edge_cash_owner")
        )
        connection.execute(text("RESET ROLE"))
        connection.execute(text("SET LOCAL ROLE aurum_schema_owner"))
        historical = _native_replay_security_snapshot(connection)
        assert all(
            connection.scalar(
                text("SELECT to_regprocedure(:signature)"), {"signature": "public." + signature}
            )
            is None
            for signature in NEW_HELPERS
        )
        migration().upgrade()
        upgraded = routines(connection)
        upgraded_relations = relation_security(connection)
        assert all(signature in upgraded for signature in NEW_HELPERS)
        dispatcher = str(upgraded[DISPATCHER.removeprefix("public.")][-1])
        assert "complete_edge_mixed_cash_v1" in dispatcher
        assert "complete_edge_physical_cash_v1" in dispatcher
        migration().downgrade()
        # Includes all historical OIDs, function bodies/security/config/ACLs,
        # relation and column privileges, policies and tenant RLS metadata.
        assert _native_replay_security_snapshot(connection) == historical
        migration().upgrade()
        restored = routines(connection)
    assert restored.keys() == upgraded.keys()
    for signature, original in upgraded.items():
        assert (
            restored[signature][1 if signature in NEW_HELPERS else 0 :]
            == original[1 if signature in NEW_HELPERS else 0 :]
        )
    assert relation_security(connection) == upgraded_relations
    assert (
        connection.scalar(
            text("SELECT has_function_privilege('aurum_edge_cash_owner',:dispatcher,'EXECUTE')"),
            {"dispatcher": DISPATCHER},
        )
        is owner_execute
    )
    assert (
        connection.scalar(
            text("SELECT has_schema_privilege('aurum_edge_cash_owner','public','CREATE')")
        )
        is False
    )


@pytest.mark.parametrize("owner_execute", [False, True])
async def test_mixed_execution_empty_roundtrip_preserves_all_historical_security(
    maintenance_engine: AsyncEngine,
    owner_execute: bool,
) -> None:
    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:

            def rehearse(sync: Connection) -> None:
                with at_native_revision(sync, "0236"):
                    roundtrip(sync, owner_execute)

            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()
