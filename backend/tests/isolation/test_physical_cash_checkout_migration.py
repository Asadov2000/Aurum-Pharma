"""Cash binding rollback preserves old guards and never discards checkout history."""

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


def migration() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / ("alembic/versions/0181_physical_cash_checkout.py")
    spec = importlib.util.spec_from_file_location("physical_cash_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_cash_binding_roundtrip_preserves_stock_and_guard_acl(
    maintenance_engine: AsyncEngine,
) -> None:
    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        with Operations.context(MigrationContext.configure(connection)):
            history = connection.scalar(
                text(
                    "SELECT EXISTS(SELECT 1 FROM pos_physical_checkout) OR EXISTS(SELECT 1 "
                    "FROM unit_stock_cart_command_v2 WHERE checkout_sale_id IS NOT NULL)"
                )
            )
            if history:
                with pytest.raises(DBAPIError) as error:
                    with connection.begin_nested():
                        migration().downgrade()
                assert getattr(error.value.orig, "sqlstate", None) == "55000"
                return
            query = text(
                "SELECT to_jsonb(row) FROM unit_stock_cart_command_v2 row ORDER BY tenant_id,id"
            )
            before = connection.execute(query).scalars().all()
            guards = text(
                "SELECT proname,pg_get_functiondef(oid),proowner,proacl,prosecdef FROM pg_proc "
                "WHERE pronamespace='public'::regnamespace "
                "AND proname IN ('trg_unit_cart_payment_boundary','trg_guard_sale_immutability',"
                "'trg_unit_cart_guard','trg_unit_cart_v2_guard') ORDER BY proname"
            )
            original = connection.execute(guards).all()
            migration().downgrade()
            assert connection.scalar(text("SELECT to_regclass('pos_physical_checkout')")) is None
            assert not connection.scalar(
                text(
                    "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
                    "WHERE table_name='unit_stock_cart_command_v2' "
                    "AND column_name='checkout_sale_id')"
                )
            )
            migration().upgrade()
            assert connection.execute(query).scalars().all() == before
            assert connection.execute(guards).all() == original

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()
