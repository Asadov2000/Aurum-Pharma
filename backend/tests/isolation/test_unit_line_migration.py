"""Prepared line migration rollback rehearsal on disposable databases only."""

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
from tests.domains.pos.test_unit_line import post, seed_line_context


def _migration(filename: str = "0165_prepared_unit_sale_lines.py") -> ModuleType:
    source = Path(__file__).resolve().parents[2] / "alembic/versions" / filename
    spec = importlib.util.spec_from_file_location("prepared_unit_line_migration", source)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


async def test_line_migration_roundtrip_or_preserves_recorded_history(
    maintenance_engine: AsyncEngine,
) -> None:
    migration = _migration()

    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        with Operations.context(MigrationContext.configure(connection)):
            if connection.scalar(text("SELECT EXISTS(SELECT 1 FROM pos_unit_sale_command)")):
                with pytest.raises(DBAPIError, match="pos_unit_line_nonempty_downgrade_forbidden"):
                    with connection.begin_nested():
                        migration.downgrade()
                return
            line_oid = connection.scalar(text("SELECT 'public.pos_unit_sale_line'::regclass::oid"))
            savepoint = connection.begin_nested()
            try:
                # Revision 0169 references these tables even when no history exists.
                # Roll back the rehearsal to restore every later schema extension.
                _migration("0169_prepared_unit_return_quarantine.py").downgrade()
                migration.downgrade()
                assert (
                    connection.scalar(text("SELECT to_regclass('public.pos_unit_sale_command')"))
                    is None
                )
                assert (
                    connection.scalar(text("SELECT to_regclass('public.pos_unit_sale_line')"))
                    is None
                )
                migration.upgrade()
                assert (
                    connection.scalar(text("SELECT to_regclass('public.pos_unit_sale_command')"))
                    is not None
                )
                assert (
                    connection.scalar(text("SELECT to_regclass('public.pos_unit_sale_line')"))
                    is not None
                )
                assert connection.scalar(text("""
                    SELECT count(*) FROM pg_class
                    WHERE oid IN ('public.pos_unit_sale_line'::regclass,
                      'public.pos_unit_sale_command'::regclass)
                      AND relrowsecurity AND relforcerowsecurity
                """)) == 2
            finally:
                savepoint.rollback()
            assert (
                connection.scalar(text("SELECT 'public.pos_unit_sale_line'::regclass::oid"))
                == line_oid
            )
            assert connection.scalar(text("""
                SELECT count(*) FROM pg_constraint
                WHERE conrelid = 'public.pos_unit_return_command'::regclass
                  AND confrelid = 'public.pos_unit_sale_line'::regclass
                  AND contype = 'f'
            """)) == 1
            assert connection.scalar(text("""
                SELECT count(*) FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'pos_unit_sale_command'
                  AND column_name = 'stock_source_kind'
            """)) == 1

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()


async def test_nonempty_line_downgrade_refuses_and_preserves_line_and_stock(
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        async with session.begin():
            assert str(await session.scalar(text("SELECT current_database()"))).endswith("_test")
            await session.execute(text("SELECT set_config('app.support_session','true',true)"))
            context = await seed_line_context(session)
            result = await post(session, context, quantity=7)
    migration = _migration()

    def refuse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            with pytest.raises(
                DBAPIError, match="pos_unit_line_nonempty_downgrade_forbidden"
            ) as error:
                async with connection.begin_nested():
                    await connection.run_sync(refuse)
            assert error.value.orig.sqlstate == "55000"
            assert (
                await connection.scalar(
                    text("SELECT net_amount FROM pos_unit_sale_line WHERE id=:id"),
                    {"id": result.snapshot.id},
                )
                == result.snapshot.net_amount
            )
            assert (
                await connection.scalar(
                    text("SELECT sum(cost_amount) FROM unit_stock_entry WHERE operation_id=:id"),
                    {"id": result.snapshot.stock_operation_id},
                )
                == result.snapshot.original_cost_amount
            )
        finally:
            await transaction.rollback()
