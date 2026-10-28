"""Retail migration retains historical rows and refuses discarding priced history."""

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
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.inventory.test_unit_retail_ledger_v2 import (
    dispense_retail,
    retail_line,
    retail_source,
)
from tests.isolation.test_physical_cash_checkout_migration import (
    downgrade_cash_for_roundtrip,
    run_historical_rehearsal,
    upgrade_cash_after_roundtrip,
)
from tests.isolation.test_unit_physical_block_migration import migration as blocking_migration

db_session = receiving_session_fixture


def _migration() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / (
        "alembic/versions/0178_unit_blister_retail_snapshot.py"
    )
    spec = importlib.util.spec_from_file_location("physical_retail_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


async def test_retail_migration_roundtrip_preserves_unpriced_history(
    maintenance_engine: AsyncEngine,
) -> None:
    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        with Operations.context(MigrationContext.configure(connection)):
            original = {
                table: (
                    connection.execute(
                        text(
                            f"SELECT to_jsonb(row) FROM {table} row "
                            "ORDER BY tenant_id,command_id,line_id"
                        )
                        if table == "unit_stock_cart_line_v2"
                        else text(f"SELECT to_jsonb(row) FROM {table} row ORDER BY tenant_id,id")
                    )
                    .scalars()
                    .all()
                )
                for table in ("unit_stock_cart_command_v2", "unit_stock_cart_line_v2")
            }
            known = connection.scalar(
                text(
                    "SELECT EXISTS(SELECT 1 FROM unit_stock_cart_command_v2 "
                    "WHERE retail_version IS NOT NULL)"
                )
            )
            if known:
                with pytest.raises(DBAPIError) as error:
                    with connection.begin_nested():
                        _migration().downgrade()
                assert error.value.orig.sqlstate == "55000"
            elif connection.scalar(
                text("SELECT EXISTS(SELECT 1 FROM unit_stock_lot_v2 WHERE blocking_revision>0)")
            ):
                with pytest.raises(DBAPIError) as error:
                    with connection.begin_nested():
                        blocking_migration().downgrade()
                assert error.value.orig.sqlstate == "55000"
            else:
                downgrade_cash_for_roundtrip()
                blocking_migration().downgrade()
                _migration().downgrade()
                _migration().upgrade()
                blocking_migration().upgrade()
                upgrade_cash_after_roundtrip()
            for table, before in original.items():
                query = (
                    f"SELECT to_jsonb(row) FROM {table} row ORDER BY tenant_id,command_id,line_id"
                    if table == "unit_stock_cart_line_v2"
                    else f"SELECT to_jsonb(row) FROM {table} row ORDER BY tenant_id,id"
                )
                assert connection.execute(text(query)).scalars().all() == before

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(run_historical_rehearsal, rehearse)
        finally:
            await transaction.rollback()


async def test_downgrade_refuses_priced_history_without_changing_snapshots(
    db_session: AsyncSession, maintenance_engine: AsyncEngine
) -> None:
    source = await retail_source(db_session)
    result = await dispense_retail(db_session, source, (retail_line(source, "pack", 1),))
    await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    await db_session.commit()

    def refuse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        with Operations.context(MigrationContext.configure(connection)):
            query = text(
                "SELECT to_jsonb(row) FROM unit_stock_cart_line_v2 row WHERE command_id=:id"
            )
            before = connection.execute(query, {"id": result.command_id}).scalars().all()
            assert before and before[0]["net_amount"] == 60
            with pytest.raises(DBAPIError) as error:
                with connection.begin_nested():
                    _migration().downgrade()
            assert error.value.orig.sqlstate == "55000"
            assert connection.execute(query, {"id": result.command_id}).scalars().all() == before
            assert (
                connection.scalar(
                    text("SELECT retail_version FROM unit_stock_cart_command_v2 WHERE id=:id"),
                    {"id": result.command_id},
                )
                == 1
            )

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(refuse)
        finally:
            await transaction.rollback()
