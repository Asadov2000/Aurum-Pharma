"""Physical blocking rollback never discards a past restriction."""

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
from app.domains.inventory.unit_dispensing_v2_repository import UnitDispensingV2Repository
from app.domains.inventory.unit_physical_blocking import PhysicalLotBlockingService
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import (
    seed_receiving_actor,
    set_receiving_context,
)
from tests.domains.inventory.test_unit_ledger_v2_dispensing import receive_source

db_session = receiving_session_fixture


def migration() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / (
        "alembic/versions/0180_unit_physical_lot_blocking.py"
    )
    spec = importlib.util.spec_from_file_location("physical_block_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_blocking_roundtrip_preserves_functions_acl_and_untouched_stock(
    maintenance_engine: AsyncEngine,
) -> None:
    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        with Operations.context(MigrationContext.configure(connection)):
            query = text("SELECT to_jsonb(row) FROM unit_stock_lot_v2 row ORDER BY tenant_id,id")
            before = connection.execute(query).scalars().all()
            if any(row["blocking_revision"] or row["is_blocked"] for row in before):
                with pytest.raises(DBAPIError) as error:
                    with connection.begin_nested():
                        migration().downgrade()
                assert getattr(error.value.orig, "sqlstate", None) == "55000"
                return
            guard_query = text(
                "SELECT proname,pg_get_functiondef(oid),proowner,proacl,prosecdef "
                "FROM pg_proc WHERE oid IN ('trg_unit_stock_v2_guard()'::regprocedure,"
                "'trg_unit_movement_v2_guard()'::regprocedure) ORDER BY proname"
            )
            original = connection.execute(guard_query).all()
            migration().downgrade()
            assert not connection.scalar(
                text(
                    "SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE "
                    "table_name='unit_stock_lot_v2' AND column_name='is_blocked')"
                )
            )
            for function in connection.execute(guard_query):
                assert "l.is_blocked" not in function[1]
                assert "NEW.blocking_revision" not in function[1]
            migration().upgrade()
            assert connection.execute(query).scalars().all() == before
            assert connection.execute(guard_query).all() == original

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()


async def test_even_unblocked_history_prevents_downgrade(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> None:
    source = await receive_source(db_session)
    await db_session.commit()
    actor = await seed_receiving_actor(
        db_engine,
        maintenance_engine,
        source.incoming.stock,
        permissions=("batches.view", "batches.update"),
    )
    await set_receiving_context(db_session, source.incoming.stock, actor)
    service = PhysicalLotBlockingService(UnitDispensingV2Repository(db_session))
    for blocked in (True, False):
        await service.set_blocked(
            tenant_id=source.incoming.stock.tenant_id,
            branch_id=source.incoming.stock.branch_id,
            lot_id=source.lot_id,
            actor_id=actor,
            allowed_branch_ids={source.incoming.stock.branch_id},
            is_blocked=blocked,
        )
    await db_session.commit()

    def refuse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        query = text("SELECT to_jsonb(row) FROM unit_stock_lot_v2 row WHERE id=:id")
        before = connection.scalar(query, {"id": source.lot_id})
        assert before["blocking_revision"] == 2 and before["is_blocked"] is False
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(DBAPIError) as error:
                with connection.begin_nested():
                    migration().downgrade()
            assert getattr(error.value.orig, "sqlstate", None) == "55000"
        assert connection.scalar(query, {"id": source.lot_id}) == before

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(refuse)
        finally:
            await transaction.rollback()
