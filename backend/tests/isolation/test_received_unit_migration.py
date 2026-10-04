"""Received source policy remains protected through disposable migration rehearsals."""

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
from tests.domains.pos.test_received_unit_line import (
    post_received,
    received_dispense,
    seed_received,
)

db_session = receiving_session_fixture


def _migration() -> ModuleType:
    source = (
        Path(__file__).resolve().parents[2] / "alembic/versions/0167_received_unit_line_sources.py"
    )
    spec = importlib.util.spec_from_file_location("received_unit_sources_migration", source)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


async def test_received_migration_roundtrip_or_preserves_received_history(
    maintenance_engine: AsyncEngine,
) -> None:
    migration = _migration()

    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        with Operations.context(MigrationContext.configure(connection)):
            history_exists = connection.scalar(
                text(
                    "SELECT EXISTS(SELECT 1 FROM pos_unit_sale_command "
                    "WHERE stock_source_kind='received') "
                    "OR EXISTS(SELECT 1 FROM unit_stock_operation "
                    "WHERE stock_source_kind='received')"
                )
            )
            if history_exists:
                with pytest.raises(
                    DBAPIError,
                    match="unit_received_nonempty_downgrade_forbidden",
                ) as error:
                    with connection.begin_nested():
                        migration.downgrade()
                assert error.value.orig.sqlstate == "55000"
                return
            migration.downgrade()
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.columns "
                        "WHERE table_schema='public' AND column_name='stock_source_kind' "
                        "AND table_name IN ('pos_unit_sale_command','unit_stock_operation')"
                    )
                )
                == 0
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT position('stock_source_kind' IN pg_get_functiondef("
                        "'public.trg_pos_unit_line_complete()'::regprocedure))"
                    )
                )
                == 0
            )
            for role in ("aurum_app", "aurum_support"):
                assert not connection.scalar(
                    text(
                        "SELECT has_function_privilege("
                        ":role,'public.trg_pos_unit_line_complete()','EXECUTE')"
                    ),
                    {"role": role},
                )
            migration.upgrade()
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.columns "
                        "WHERE table_schema='public' AND column_name='stock_source_kind' "
                        "AND table_name IN ('pos_unit_sale_command','unit_stock_operation') "
                        "AND is_nullable='NO'"
                    )
                )
                == 2
            )
            assert connection.scalar(
                text(
                    "SELECT has_function_privilege('aurum_app',"
                    "'public.unit_lot_has_received_source(uuid,uuid,uuid,uuid)','EXECUTE')"
                )
            )
            assert not connection.scalar(
                text(
                    "SELECT has_function_privilege('aurum_support',"
                    "'public.unit_lot_has_received_source(uuid,uuid,uuid,uuid)','EXECUTE')"
                )
            )

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()


@pytest.mark.parametrize("post_line", [True, False])
async def test_received_line_or_direct_stock_history_blocks_downgrade_without_data_loss(
    db_session: AsyncSession,
    maintenance_engine: AsyncEngine,
    post_line: bool,
) -> None:
    incoming = await seed_received(db_session)
    if post_line:
        posted = await post_received(db_session, incoming)
        operation_id = posted.snapshot.stock_operation_id
    else:
        dispensed = await received_dispense(db_session, incoming, 1)
        operation_id = dispensed.operation_id
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
            with pytest.raises(
                DBAPIError,
                match="unit_received_nonempty_downgrade_forbidden",
            ) as error:
                async with connection.begin_nested():
                    await connection.run_sync(refuse)
            assert error.value.orig.sqlstate == "55000"
            assert (
                await connection.scalar(
                    text("SELECT stock_source_kind FROM unit_stock_operation WHERE id=:id"),
                    {"id": operation_id},
                )
                == "received"
            )
            assert (
                await connection.scalar(
                    text("SELECT sum(cost_amount) FROM unit_stock_entry WHERE operation_id=:id"),
                    {"id": operation_id},
                )
                is not None
            )
        finally:
            await transaction.rollback()
