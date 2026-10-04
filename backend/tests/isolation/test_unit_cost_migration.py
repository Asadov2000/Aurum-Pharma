"""Rehearse cost migration exclusively on disposable integration databases."""

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
from tests.domains.inventory.test_unit_cost_ledger import prepare_priced, take
from tests.domains.inventory.test_unit_ledger import seed_ledger_context


def _migration() -> ModuleType:
    source = (
        Path(__file__).resolve().parents[2] / "alembic/versions/0164_prepared_unit_stock_cost.py"
    )
    spec = importlib.util.spec_from_file_location("prepared_unit_cost_migration", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_cost_migration_roundtrip_or_refuses_to_discard_cost_history(
    maintenance_engine: AsyncEngine,
) -> None:
    migration = _migration()

    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        with Operations.context(MigrationContext.configure(connection)):
            nonempty = connection.scalar(
                text("SELECT EXISTS(SELECT 1 FROM unit_stock_lot WHERE cost_version IS NOT NULL)")
            )
            if nonempty:
                with pytest.raises(DBAPIError, match="unit_stock_priced_downgrade_forbidden"):
                    with connection.begin_nested():
                        migration.downgrade()
                return
            migration.downgrade()
            assert connection.scalar(text("""
                    SELECT count(*) FROM information_schema.columns
                    WHERE table_schema='public' AND table_name='unit_stock_entry'
                      AND column_name LIKE 'cost_%'
                """)) == 0
            migration.upgrade()
            assert connection.scalar(text("""
                    SELECT count(*) FROM information_schema.columns
                    WHERE table_schema='public' AND is_nullable='YES'
                      AND numeric_precision=14 AND numeric_scale=2
                      AND (table_name='unit_stock_lot' AND column_name='purchase_cost_per_pack'
                        OR table_name='unit_stock_entry'
                          AND column_name IN ('cost_original_pack_cost','cost_amount'))
                """)) == 3
            assert connection.scalar(text("""
                    SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal
                      AND tgname IN ('a20_unit_stock_cost_identity','a20_unit_stock_cost_snapshot')
                """)) == 3

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()


async def test_nonempty_cost_downgrade_preserves_original_pack_and_dispense_history(
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        async with session.begin():
            assert str(await session.scalar(text("SELECT current_database()"))).endswith("_test")
            await session.execute(text("SELECT set_config('app.support_session','true',true)"))
            context = await seed_ledger_context(session)
            prepared = await prepare_priced(session, context)
            result = await take(session, context, 1)
    migration = _migration()

    def refuse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            with pytest.raises(DBAPIError, match="unit_stock_priced_downgrade_forbidden") as error:
                async with connection.begin_nested():
                    await connection.run_sync(refuse)
            assert error.value.orig.sqlstate == "55000"
            assert (
                await connection.scalar(
                    text("SELECT purchase_cost_per_pack FROM unit_stock_lot WHERE id=:lot"),
                    {"lot": prepared.lot_id},
                )
                == result.cost_slices[0].original_pack_cost
            )
            assert (
                await connection.scalar(
                    text("SELECT cost_amount FROM unit_stock_entry WHERE id=:entry"),
                    {"entry": result.cost_slices[0].entry_id},
                )
                == result.cost_slices[0].amount
            )
        finally:
            await transaction.rollback()
