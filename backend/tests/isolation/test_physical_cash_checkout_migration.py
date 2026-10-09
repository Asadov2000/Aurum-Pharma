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


def compatibility_migration() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / (
        "alembic/versions/0182_physical_cash_edge_compatibility.py"
    )
    spec = importlib.util.spec_from_file_location("physical_cash_edge_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def return_journal_migration() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / (
        "alembic/versions/0183_physical_return_journal.py"
    )
    spec = importlib.util.spec_from_file_location("physical_return_journal_dependency", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def downgrade_cash_for_roundtrip() -> None:
    cash_refund_migration().downgrade()
    return_journal_migration().downgrade()
    compatibility_migration().downgrade()
    migration().downgrade()


def upgrade_cash_after_roundtrip() -> None:
    migration().upgrade()
    compatibility_migration().upgrade()
    return_journal_migration().upgrade()
    cash_refund_migration().upgrade()


def cash_refund_migration() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / ("alembic/versions/0184_physical_cash_refund.py")
    spec = importlib.util.spec_from_file_location("physical_cash_refund_dependency", path)
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
                    "SELECT EXISTS(SELECT 1 FROM pos_physical_return_command) OR "
                    "EXISTS(SELECT 1 FROM pos_physical_checkout) OR EXISTS(SELECT 1 "
                    "FROM unit_stock_cart_command_v2 WHERE checkout_sale_id IS NOT NULL)"
                )
            )
            if history:
                with pytest.raises(DBAPIError) as error:
                    with connection.begin_nested():
                        downgrade_cash_for_roundtrip()
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
            downgrade_cash_for_roundtrip()
            assert connection.scalar(text("SELECT to_regclass('pos_physical_checkout')")) is None
            assert not connection.scalar(
                text(
                    "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
                    "WHERE table_name='unit_stock_cart_command_v2' "
                    "AND column_name='checkout_sale_id')"
                )
            )
            upgrade_cash_after_roundtrip()
            assert connection.execute(query).scalars().all() == before
            assert connection.execute(guards).all() == original

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()


async def test_edge_compatibility_roundtrip_preserves_guard_owner_and_acl(
    maintenance_engine: AsyncEngine,
) -> None:
    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        query = text(
            "SELECT pg_get_functiondef(oid),proowner,proacl,prosecdef "
            "FROM pg_proc WHERE oid='trg_guard_sale_immutability()'::regprocedure"
        )
        before = connection.execute(query).one()
        with Operations.context(MigrationContext.configure(connection)):
            history = connection.scalar(
                text("SELECT EXISTS(SELECT 1 FROM pos_physical_cash_refund)")
            )
            if history:
                with pytest.raises(DBAPIError) as error:
                    with connection.begin_nested():
                        cash_refund_migration().downgrade()
                assert getattr(error.value.orig, "sqlstate", None) == "55000"
                return
            cash_refund_migration().downgrade()
            compatibility_migration().downgrade()
            previous = connection.execute(query).one()
            assert "v_physical_cash BOOLEAN" not in previous[0]
            assert previous[1:] == before[1:]
            compatibility_migration().upgrade()
            cash_refund_migration().upgrade()
            assert connection.execute(query).one() == before

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()
