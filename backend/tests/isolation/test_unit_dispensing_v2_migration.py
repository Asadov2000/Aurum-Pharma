"""Physical dispensing rollback rehearsals never discard live history."""

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
from tests.domains.incoming.test_unit_acceptance import (
    db_session as receiving_session_fixture,
)
from tests.domains.inventory.test_unit_ledger_v2_dispensing import (
    DISPENSING_TABLES,
    cart_line,
    dispense,
    receive_source,
)

db_session = receiving_session_fixture


def _migration() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic/versions/0176_unit_blister_physical_dispensing.py"
    )
    spec = importlib.util.spec_from_file_location("physical_dispensing_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


async def test_dispensing_migration_roundtrip_or_preserves_stock_history(
    maintenance_engine: AsyncEngine,
) -> None:
    migration = _migration()

    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        with Operations.context(MigrationContext.configure(connection)):
            if connection.scalar(text("SELECT EXISTS(SELECT 1 FROM unit_stock_cart_command_v2)")):
                with pytest.raises(DBAPIError) as error:
                    with connection.begin_nested():
                        migration.downgrade()
                assert error.value.orig.sqlstate == "55000"
                return
            migration.downgrade()
            for table in DISPENSING_TABLES:
                assert (
                    connection.scalar(
                        text("SELECT to_regclass(:table)"), {"table": f"public.{table}"}
                    )
                    is None
                )
            assert not connection.scalar(
                text(
                    "SELECT has_column_privilege("
                    "'aurum_app','unit_stock_lot_v2','updated_at','UPDATE')"
                )
            )
            migration.upgrade()
            for table in DISPENSING_TABLES:
                assert (
                    connection.scalar(
                        text(
                            "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
                            "WHERE oid=to_regclass(:table)"
                        ),
                        {"table": f"public.{table}"},
                    )
                    is True
                )

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()


async def test_downgrade_preserves_receiving_history_without_dispensing(
    db_session: AsyncSession,
    maintenance_engine: AsyncEngine,
) -> None:
    source = await receive_source(db_session)
    await db_session.commit()
    migration = _migration()

    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        with Operations.context(MigrationContext.configure(connection)):
            # Other tests may have committed physical sales in this disposable DB.
            nonempty = connection.scalar(
                text("SELECT EXISTS(SELECT 1 FROM unit_stock_cart_command_v2)")
            )
            if nonempty:
                with pytest.raises(DBAPIError) as error:
                    with connection.begin_nested():
                        migration.downgrade()
                assert error.value.orig.sqlstate == "55000"
            else:
                migration.downgrade()
            assert (
                connection.scalar(
                    text("SELECT closed_packs FROM unit_stock_lot_v2 WHERE id=:id"),
                    {"id": source.lot_id},
                )
                == 2
            )
            assert (
                connection.scalar(
                    text("SELECT created_unit_lot_v2_id FROM incoming_item WHERE id=:id"),
                    {"id": source.incoming.item_ids[0]},
                )
                == source.lot_id
            )
            if not nonempty:
                migration.upgrade()

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()


async def test_nonempty_downgrade_keeps_real_opened_pack_and_journal(
    db_session: AsyncSession,
    maintenance_engine: AsyncEngine,
) -> None:
    source = await receive_source(db_session)
    result = await dispense(db_session, source, (cart_line(source, "unit", 3),))
    await db_session.commit()
    migration = _migration()

    def refuse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            with pytest.raises(DBAPIError) as error:
                async with connection.begin_nested():
                    await connection.run_sync(refuse)
            assert error.value.orig.sqlstate == "55000"
            assert (
                await connection.scalar(
                    text("SELECT remaining_units FROM unit_stock_opened_pack_v2 WHERE lot_id=:id"),
                    {"id": source.lot_id},
                )
                == 27
            )
            assert await connection.scalar(
                text("SELECT count(*) FROM unit_stock_movement_v2 WHERE command_id=:id"),
                {"id": result.command_id},
            ) == len(result.movements)
        finally:
            await transaction.rollback()
