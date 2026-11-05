"""Rehearse inventory-event DDL only inside rolled-back disposable-test transactions."""

import importlib.util
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from alembic.migration import MigrationContext
from alembic.operations import Operations
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture
TABLES = (
    "sync_physical_stock_producer_gate",
    "sync_physical_stock_source",
    "sync_physical_stock_event",
    "sync_physical_stock_anchor",
    "sync_physical_stock_projection",
    "sync_physical_stock_baseline",
)


def migration() -> ModuleType:
    path = Path(__file__).resolve().parents[3] / "alembic/versions/0229_physical_stock_events.py"
    spec = importlib.util.spec_from_file_location("physical_stock_event_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def require_test_owner(connection: Connection) -> None:
    assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
    assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
    assert connection.scalar(text("SELECT current_user")) == "aurum_schema_owner"


def schema_facts(connection: Connection) -> dict[str, object]:
    return {
        "tables": list(
            connection.execute(
                text(
                    "SELECT relname,relrowsecurity,relforcerowsecurity,relacl::text "
                    "FROM pg_class WHERE relname=ANY(:tables) ORDER BY relname"
                ),
                {"tables": list(TABLES)},
            ).tuples()
        ),
        "policies": list(
            connection.execute(
                text(
                    "SELECT tablename,policyname,roles,cmd,qual,with_check FROM pg_policies "
                    "WHERE tablename=ANY(:tables) ORDER BY tablename,policyname"
                ),
                {"tables": list(TABLES)},
            ).tuples()
        ),
        "revision": list(
            connection.execute(
                text(
                    "SELECT column_name,data_type,is_nullable,column_default "
                    "FROM information_schema.columns "
                    "WHERE table_name='tenant_catalog' AND column_name='physical_config_revision'"
                )
            ).tuples()
        ),
        "redactor": connection.scalar(
            text(
                "SELECT pg_get_functiondef("
                "'public.audit_redact_table_snapshot(text,jsonb)'::regprocedure)"
            )
        ),
        "stream_transaction": list(
            connection.execute(
                text(
                    "SELECT column_name,data_type,is_nullable,column_default "
                    "FROM information_schema.columns WHERE table_name='sync_stream' "
                    "AND column_name='last_reservation_transaction_id'"
                )
            ).tuples()
        ),
    }


async def test_empty_0229_roundtrip_restores_rls_grants_revision_and_audit_redaction(
    maintenance_engine: AsyncEngine,
) -> None:
    module = migration()

    def rehearse(connection: Connection) -> None:
        require_test_owner(connection)
        for table in TABLES:
            assert connection.scalar(text(f"SELECT count(*) FROM {table}")) == 0
        before = schema_facts(connection)
        with Operations.context(MigrationContext.configure(connection)):
            module.downgrade()
            for table in TABLES:
                assert (
                    connection.scalar(text("SELECT to_regclass(:table)"), {"table": table}) is None
                )
            assert schema_facts(connection)["revision"] == []
            assert schema_facts(connection)["stream_transaction"] == []
            module.upgrade()
        assert schema_facts(connection) == before

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()


@pytest.mark.parametrize("protected", ["anchor", "baseline"])
async def test_0229_nonempty_downgrade_preserves_protected_history_and_schema(
    db_session: AsyncSession,
    maintenance_engine: AsyncEngine,
    protected: str,
) -> None:
    context = runtime(db_session)
    module = migration()

    def refuse(connection: Connection) -> None:
        require_test_owner(connection)
        values = {
            "tenant": context.stock.tenant_id,
            "branch": context.stock.branch_id,
            "node": uuid4(),
            "snapshot": uuid4(),
            "origin": uuid4(),
            "hash": "a" * 64,
        }
        if protected == "anchor":
            connection.execute(
                text(
                    "INSERT INTO sync_physical_stock_anchor(event_id,tenant_id,branch_id,"
                    "target_edge_node_id,origin_node_id,writer_epoch,sequence,previous_source_checksum,"
                    "previous_projection_checksum,event_fingerprint) "
                    "VALUES(:snapshot,:tenant,:branch,:node,:origin,1,1,:hash,:hash,:hash)"
                ),
                values,
            )
        else:
            connection.execute(
                text(
                    "INSERT INTO sync_physical_stock_baseline(tenant_id,branch_id,"
                    "target_edge_node_id,"
                    "snapshot_id,snapshot_hash,physical_records_hash,configuration_hash,origin_node_id,"
                    "writer_epoch,sequence,source_checksum,projection_checksum) "
                    "VALUES(:tenant,:branch,:node,:snapshot,:hash,:hash,:hash,:origin,1,0,:hash,:hash)"
                ),
                values,
            )
        table = f"sync_physical_stock_{protected}"
        before = connection.scalar(text(f"SELECT count(*) FROM {table}"))
        schema = schema_facts(connection)
        with pytest.raises(DBAPIError) as denied:
            with connection.begin_nested():
                with Operations.context(MigrationContext.configure(connection)):
                    module.downgrade()
        assert getattr(denied.value.orig, "sqlstate", None) == "55000"
        assert connection.scalar(text(f"SELECT count(*) FROM {table}")) == before
        assert schema_facts(connection) == schema

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(refuse)
        finally:
            await transaction.rollback()
