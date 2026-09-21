"""Transactional rehearsal for the Edge inventory rollup migration."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from alembic.migration import MigrationContext
from alembic.operations import Operations


async def test_inventory_rollup_migration_roundtrip_on_disposable_database(
    maintenance_engine: AsyncEngine,
) -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "0149_optimize_edge_read_projections.py"
    )
    spec = importlib.util.spec_from_file_location("edge_inventory_rollup_rehearsal", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def rehearse(connection: Connection) -> None:
        database = str(connection.scalar(text("SELECT current_database()")))
        assert database.endswith("_test"), "Migration rehearsal requires a disposable test database"
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        with Operations.context(MigrationContext.configure(connection)):
            module.downgrade()
            assert (
                connection.scalar(text("SELECT to_regclass('public.sync_read_inventory_rollup')"))
                is None
            )
            assert (
                connection.scalar(
                    text("SELECT to_regprocedure(" "'public.search_sync_read_catalog(uuid,text)')")
                )
                is None
            )
            module.upgrade()
            assert connection.scalar(
                text("SELECT to_regclass('public.sync_read_inventory_rollup')")
            )
            assert connection.scalar(
                text(
                    "SELECT relrowsecurity AND relforcerowsecurity "
                    "FROM pg_class WHERE oid = 'public.sync_read_inventory_rollup'::regclass"
                )
            )
            assert connection.scalar(
                text(
                    "SELECT to_regprocedure("
                    "'public.search_sync_read_catalog(uuid,text)') IS NOT NULL"
                )
            )

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()
