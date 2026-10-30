"""Rehearse refund DDL only in rolled-back disposable-test transactions."""

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


def migration() -> ModuleType:
    path = Path(__file__).resolve().parents[3] / "alembic/versions/0230_retail_refund_events.py"
    spec = importlib.util.spec_from_file_location("retail_refund_event_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def schema_facts(connection: Connection, tables: tuple[str, ...]) -> dict[str, list[object]]:
    return {
        "relations": list(
            connection.execute(
                text(
                    "SELECT relname,relrowsecurity,relforcerowsecurity,relowner::regrole::text "
                    "FROM pg_class WHERE relname=ANY(:tables) ORDER BY relname"
                ),
                {"tables": list(tables)},
            ).tuples()
        ),
        "policies": list(
            connection.execute(
                text(
                    "SELECT tablename,policyname,roles,cmd,qual,with_check FROM pg_policies "
                    "WHERE tablename=ANY(:tables) ORDER BY tablename,policyname"
                ),
                {"tables": list(tables)},
            ).tuples()
        ),
        "grants": list(
            connection.execute(
                text(
                    "SELECT table_name,grantee,privilege_type "
                    "FROM information_schema.role_table_grants "
                    "WHERE table_name=ANY(:tables) ORDER BY table_name,grantee,privilege_type"
                ),
                {"tables": list(tables)},
            ).tuples()
        ),
        "columns": list(
            connection.execute(
                text(
                    "SELECT table_name,column_name,grantee,privilege_type "
                    "FROM information_schema.role_column_grants WHERE table_name=ANY(:tables) "
                    "ORDER BY table_name,column_name,grantee,privilege_type"
                ),
                {"tables": list(tables)},
            ).tuples()
        ),
        "triggers": list(
            connection.execute(
                text(
                    "SELECT c.relname,t.tgname,t.tgenabled,pg_get_triggerdef(t.oid) "
                    "FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
                    "WHERE c.relname=ANY(:tables) AND NOT t.tgisinternal "
                    "ORDER BY c.relname,t.tgname"
                ),
                {"tables": list(tables)},
            ).tuples()
        ),
    }


async def test_empty_0230_roundtrip_restores_exact_policies_grants_and_column_permissions(
    maintenance_engine: AsyncEngine,
) -> None:
    module = migration()

    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT current_user")) == "aurum_schema_owner"
        for table in module.TABLES:
            assert connection.scalar(text(f"SELECT count(*) FROM {table}")) == 0
        before = schema_facts(connection, module.TABLES)
        with Operations.context(MigrationContext.configure(connection)):
            module.downgrade()
            for table in module.TABLES:
                assert connection.scalar(text("SELECT to_regclass(:name)"), {"name": table}) is None
            module.upgrade()
        assert schema_facts(connection, module.TABLES) == before

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()


@pytest.mark.parametrize("protected", ["enabled_gate", "anchor"])
async def test_0230_downgrade_cannot_destroy_enabled_or_protected_history(
    db_session: AsyncSession, maintenance_engine: AsyncEngine, protected: str
) -> None:
    context = runtime(db_session)
    module = migration()

    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT current_user")) == "aurum_schema_owner"
        values = {
            "tenant": context.stock.tenant_id,
            "branch": context.stock.branch_id,
            "event": uuid4(),
            "node": uuid4(),
            "origin": uuid4(),
            "hash": "a" * 64,
        }
        if protected == "enabled_gate":
            connection.execute(
                text(
                    "INSERT INTO sync_retail_refund_producer_gate(tenant_id,id,enabled) "
                    "VALUES(:tenant,:branch,true)"
                ),
                values,
            )
        else:
            connection.execute(
                text(
                    "INSERT INTO sync_retail_refund_anchor(event_id,tenant_id,branch_id,"
                    "target_edge_node_id,origin_node_id,writer_epoch,sequence,"
                    "previous_source_checksum,previous_projection_checksum,event_fingerprint) "
                    "VALUES(:event,:tenant,:branch,:node,:origin,1,1,:hash,:hash,:hash)"
                ),
                values,
            )
        before = schema_facts(connection, module.TABLES)
        with pytest.raises(DBAPIError) as denied:
            with (
                connection.begin_nested(),
                Operations.context(MigrationContext.configure(connection)),
            ):
                module.downgrade()
        assert getattr(denied.value.orig, "sqlstate", None) == "55000"
        assert "retail_refund_downgrade_requires_empty_history" in str(denied.value.orig)
        assert schema_facts(connection, module.TABLES) == before

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()
