"""Transactional rehearsal for the Edge inventory page index migration."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from alembic.migration import MigrationContext
from alembic.operations import Operations


async def test_inventory_page_index_migration_roundtrip_on_disposable_database(
    maintenance_engine: AsyncEngine,
) -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "0150_optimize_edge_inventory_page_reads.py"
    )
    spec = importlib.util.spec_from_file_location("edge_inventory_page_index_rehearsal", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def rehearse(connection: Connection) -> None:
        database = str(connection.scalar(text("SELECT current_database()")))
        assert database.endswith("_test"), "Migration rehearsal requires a disposable test database"
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        index_name = "public.ix_sync_read_inventory_projection_available"
        with Operations.context(MigrationContext.configure(connection)):
            module.downgrade()
            old_definition = str(
                connection.scalar(
                    text("SELECT pg_get_indexdef(to_regclass(:name))"),
                    {"name": index_name},
                )
            )
            assert "snapshot_id) WHERE (qty_remaining >" in old_definition
            module.upgrade()
            new_definition = str(
                connection.scalar(
                    text("SELECT pg_get_indexdef(to_regclass(:name))"),
                    {"name": index_name},
                )
            )
            assert "snapshot_id, batch_id) WHERE (qty_remaining >" in new_definition

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()
