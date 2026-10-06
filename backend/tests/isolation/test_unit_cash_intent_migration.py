"""Cash intent downgrade rehearsal, exclusively in a disposable database."""

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
from tests.isolation.test_unit_cart_migration import migration as cart_migration


def migration() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "alembic/versions/0171_unit_cash_intents.py"
    spec = importlib.util.spec_from_file_location("unit_cash_intent_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_empty_roundtrip_or_nonempty_protection(maintenance_engine: AsyncEngine) -> None:
    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        with Operations.context(MigrationContext.configure(connection)):
            if connection.scalar(text("SELECT EXISTS(SELECT 1 FROM pos_unit_cash_intent)")):
                with pytest.raises(DBAPIError, match="unit_cash_intent_nonempty"):
                    with connection.begin_nested():
                        migration().downgrade()
                return
            cart_migration("0179_unit_physical_cart_intents.py").downgrade()
            migration().downgrade()
            assert connection.scalar(text("SELECT to_regclass('pos_unit_cash_intent')")) is None
            migration().upgrade()
            cart_migration("0179_unit_physical_cart_intents.py").upgrade()
            assert connection.scalar(
                text(
                    "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
                    "WHERE oid='pos_unit_cash_intent'::regclass"
                )
            )

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()
