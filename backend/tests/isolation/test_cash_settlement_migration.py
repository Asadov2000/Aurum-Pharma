"""Rehearse cash schema rollback only in an isolated test database."""

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from alembic.migration import MigrationContext
from alembic.operations import Operations


async def test_cash_settlement_migration_roundtrip_or_preserves_history(
    maintenance_engine: AsyncEngine,
) -> None:
    path = Path(__file__).resolve().parents[2] / "alembic/versions/0159_aurum_cash_settlements.py"
    spec = importlib.util.spec_from_file_location("cash_settlement_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        with Operations.context(MigrationContext.configure(connection)):
            if connection.scalar(text("SELECT EXISTS(SELECT 1 FROM public.billing_cash_document)")):
                with pytest.raises(DBAPIError) as error:
                    with connection.begin_nested():
                        migration.downgrade()
                assert error.value.orig.sqlstate == "55000"
                return
            migration.downgrade()
            assert (
                connection.scalar(text("SELECT to_regclass('public.billing_cash_document')"))
                is None
            )
            migration.upgrade()
            assert connection.scalar(text("""
                SELECT relrowsecurity AND relforcerowsecurity FROM pg_class
                WHERE oid='public.billing_cash_document'::regclass
            """))
            assert not connection.scalar(text("""
                SELECT has_table_privilege('aurum_app','public.billing_cash_document','INSERT')
            """))

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()
