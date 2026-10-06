"""Cost migration preserves immutable legacy rows and refuses losing known costs."""

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
from app.core.errors import BusinessRuleError
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import set_receiving_context
from tests.domains.inventory.test_unit_cost_ledger_v2 import costs, priced_source
from tests.domains.inventory.test_unit_ledger_v2_dispensing import cart_line, dispense

db_session = receiving_session_fixture
COST_FIELDS = (
    "cost_version",
    "cost_pack_ordinal_start",
    "cost_pack_count",
    "cost_unit_offset",
    "cost_base_units",
    "cost_original_pack_cost",
    "cost_amount",
)


def _migration() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / (
        "alembic/versions/0177_unit_blister_movement_cost.py"
    )
    spec = importlib.util.spec_from_file_location("physical_cost_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _retail_migration() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / (
        "alembic/versions/0178_unit_blister_retail_snapshot.py"
    )
    spec = importlib.util.spec_from_file_location("physical_retail_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def downgrade_cost_for_roundtrip(connection: Connection) -> None:
    """Outer migration is removed only when its persisted history can be retained."""
    _retail_migration().downgrade()
    _migration().downgrade()


def upgrade_cost_after_roundtrip(connection: Connection) -> None:
    _migration().upgrade()
    _retail_migration().upgrade()


def _roundtrip_or_refuse_known_costs(connection: Connection) -> None:
    with Operations.context(MigrationContext.configure(connection)):
        known = connection.scalar(
            text(
                "SELECT EXISTS(SELECT 1 FROM unit_stock_movement_v2 "
                "WHERE cost_version IS NOT NULL)"
            )
        )
        if known:
            with pytest.raises(DBAPIError) as error:
                with connection.begin_nested():
                    downgrade_cost_for_roundtrip(connection)
            assert error.value.orig.sqlstate == "55000"
        else:
            downgrade_cost_for_roundtrip(connection)
            upgrade_cost_after_roundtrip(connection)


async def test_cost_migration_roundtrip_or_refuses_known_history(
    maintenance_engine: AsyncEngine,
) -> None:
    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        with Operations.context(MigrationContext.configure(connection)):
            rows_before = (
                connection.execute(
                    text("SELECT to_jsonb(m) FROM unit_stock_movement_v2 m ORDER BY tenant_id,id")
                )
                .scalars()
                .all()
            )
            known = connection.scalar(
                text(
                    "SELECT EXISTS(SELECT 1 FROM unit_stock_movement_v2 "
                    "WHERE cost_version IS NOT NULL)"
                )
            )
            if known:
                with pytest.raises(DBAPIError) as error:
                    with connection.begin_nested():
                        downgrade_cost_for_roundtrip(connection)
                assert error.value.orig.sqlstate == "55000"
            else:
                downgrade_cost_for_roundtrip(connection)
                upgrade_cost_after_roundtrip(connection)
            assert (
                connection.execute(
                    text("SELECT to_jsonb(m) FROM unit_stock_movement_v2 m ORDER BY tenant_id,id")
                )
                .scalars()
                .all()
                == rows_before
            )

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()


async def test_legacy_cost_remains_unknown_without_backfill_or_replay_recalculation(
    db_session: AsyncSession,
    maintenance_engine: AsyncEngine,
) -> None:
    source = await priced_source(db_session)
    await db_session.commit()

    async def set_cost_trigger(*, mode: str) -> str:
        # This serial fixture runs only in the disposable test database. Separate
        # committed DDL lets the real app login create pre-0177 history without
        # granting the migrator permission to impersonate the app.
        async with maintenance_engine.begin() as owner:
            assert str(await owner.scalar(text("SELECT current_database()"))).endswith("_test")
            trigger = (
                await owner.execute(
                    text(
                        "SELECT tgname,tgenabled::text AS tgenabled FROM pg_trigger "
                        "WHERE tgrelid='unit_stock_movement_v2'::regclass "
                        "AND tgfoid='trg_unit_movement_v2_cost_snapshot()'::regprocedure "
                        "AND NOT tgisinternal"
                    )
                )
            ).one()
            assert isinstance(trigger.tgname, str) and trigger.tgname.isidentifier()
            action = {"O": "ENABLE", "A": "ENABLE ALWAYS", "R": "ENABLE REPLICA", "D": "DISABLE"}[
                mode
            ]
            await owner.execute(
                text(f'ALTER TABLE unit_stock_movement_v2 {action} TRIGGER "{trigger.tgname}"')
            )
            assert isinstance(trigger.tgenabled, str)
            return trigger.tgenabled

    original_mode = await set_cost_trigger(mode="D")
    try:
        await set_receiving_context(db_session, source.incoming.stock, source.incoming.actor_id)
        lines = (cart_line(source, "unit", 7),)
        result = await dispense(db_session, source, lines)
        await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        with pytest.raises(BusinessRuleError, match="unknown"):
            await costs(db_session, source, result.command_id)
        assert await dispense(db_session, source, lines, command_id=result.command_id) == result
        await db_session.commit()
    finally:
        # Release app locks even on failure before restoring transactional DDL.
        await db_session.rollback()
        await set_cost_trigger(mode=original_mode)

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            assert str(await connection.scalar(text("SELECT current_database()"))).endswith("_test")
            original = (
                (
                    await connection.execute(
                        text(
                            "SELECT to_jsonb(m) FROM unit_stock_movement_v2 m WHERE command_id=:id "
                            "ORDER BY entry_order"
                        ),
                        {"id": result.command_id},
                    )
                )
                .scalars()
                .all()
            )
            assert any(row["units_taken"] > 0 for row in original)
            assert all(row[field] is None for row in original for field in COST_FIELDS)

            await connection.run_sync(_roundtrip_or_refuse_known_costs)
            current = (
                (
                    await connection.execute(
                        text(
                            "SELECT to_jsonb(m) FROM unit_stock_movement_v2 m WHERE command_id=:id "
                            "ORDER BY entry_order"
                        ),
                        {"id": result.command_id},
                    )
                )
                .scalars()
                .all()
            )
            assert current == original
        finally:
            await transaction.rollback()
    await set_receiving_context(db_session, source.incoming.stock, source.incoming.actor_id)
    with pytest.raises(BusinessRuleError, match="unknown"):
        await costs(db_session, source, result.command_id)
