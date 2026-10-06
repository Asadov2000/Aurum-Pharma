"""Rehearse historical composition rollback only in a disposable database."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.domains.incoming.repository import IncomingRepository
from app.domains.incoming.service import IncomingService
from tests.domains.incoming.test_unit_acceptance import (
    db_session as receiving_session_fixture,
)
from tests.domains.incoming.test_unit_acceptance import seed_prepared_incoming
from tests.domains.inventory.test_unit_ledger import PACKAGING
from tests.isolation.test_unit_dispensing_v2_migration import _migration as _dispensing_migration
from tests.isolation.test_unit_incoming_v2_migration import _migration as _physical_migration

db_session = receiving_session_fixture


def _migration() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[2] / "alembic/versions/0174_incoming_blister_snapshot.py"
    )
    spec = importlib.util.spec_from_file_location("incoming_blister_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


async def test_blister_migration_roundtrip_or_preserves_historical_sources(
    maintenance_engine: AsyncEngine,
) -> None:
    migration = _migration()

    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        with Operations.context(MigrationContext.configure(connection)):
            nonempty = connection.scalar(text("""
                SELECT EXISTS(SELECT 1 FROM incoming_item
                  WHERE unit_packaging_snapshot_v2 IS NOT NULL)
                  OR EXISTS(SELECT 1 FROM batch WHERE unit_packaging_snapshot_v2 IS NOT NULL)
            """))
            if nonempty:
                with pytest.raises(DBAPIError) as error:
                    with connection.begin_nested():
                        migration.downgrade()
                assert error.value.orig.sqlstate == "55000"
                return
            physical_migration = _physical_migration()
            dispensing_migration = _dispensing_migration()
            dispensing_migration.downgrade()
            physical_migration.downgrade()
            migration.downgrade()
            assert connection.scalar(text("""
                SELECT count(*) FROM information_schema.columns
                WHERE table_schema='public' AND table_name IN ('incoming_item','batch')
                  AND column_name='unit_packaging_snapshot_v2'
            """)) == 0
            migration.upgrade()
            physical_migration.upgrade()
            dispensing_migration.upgrade()
            assert connection.scalar(text("""
                SELECT count(*) FROM information_schema.columns
                WHERE table_schema='public' AND table_name IN ('incoming_item','batch')
                  AND column_name='unit_packaging_snapshot_v2'
                  AND is_nullable='YES' AND udt_name='jsonb'
            """)) == 2

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()


async def test_nonempty_blister_downgrade_preserves_both_sources(
    db_session: AsyncSession, maintenance_engine: AsyncEngine
) -> None:
    incoming = await seed_prepared_incoming(db_session)
    await IncomingService(IncomingRepository(db_session)).accept(incoming.document_id)
    item_id = incoming.item_ids[0]
    batch_id = await db_session.scalar(
        text("SELECT created_batch_id FROM incoming_item WHERE id=:id"), {"id": item_id}
    )
    assert batch_id is not None
    await db_session.commit()
    migration = _migration()

    def refuse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            with pytest.raises(DBAPIError) as error:
                async with connection.begin_nested():
                    await connection.run_sync(refuse)
            assert error.value.orig.sqlstate == "55000"
            for table, identifier in (("incoming_item", item_id), ("batch", batch_id)):
                assert await connection.scalar(
                    text(f"SELECT unit_packaging_snapshot_v2 FROM {table} WHERE id=:id"),
                    {"id": identifier},
                ) == {**PACKAGING.model_dump(), "version": 2, "units_per_blister": None}
        finally:
            await transaction.rollback()
