"""Incoming stock provenance migration rehearsals never run against shared dev."""

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
from tests.domains.incoming.test_unit_acceptance import accept_prepared, seed_prepared_incoming
from tests.domains.incoming.test_unit_acceptance import (
    db_session as receiving_session_fixture,
)

db_session = receiving_session_fixture


def _migration() -> ModuleType:
    source = (
        Path(__file__).resolve().parents[2]
        / "alembic/versions/0166_incoming_unit_stock_provenance.py"
    )
    spec = importlib.util.spec_from_file_location("unit_incoming_provenance_migration", source)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


async def test_provenance_migration_roundtrip_or_preserves_bound_sources(
    maintenance_engine: AsyncEngine,
) -> None:
    migration = _migration()

    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        with Operations.context(MigrationContext.configure(connection)):
            if connection.scalar(
                text(
                    "SELECT EXISTS(SELECT 1 FROM incoming_item "
                    "WHERE created_unit_lot_id IS NOT NULL)"
                )
            ):
                with pytest.raises(DBAPIError, match="unit_incoming_nonempty_downgrade_forbidden"):
                    with connection.begin_nested():
                        migration.downgrade()
                return
            migration.downgrade()
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.columns "
                        "WHERE table_schema='public' AND table_name='incoming_item' "
                        "AND column_name='created_unit_lot_id'"
                    )
                )
                == 0
            )
            migration.upgrade()
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.columns "
                        "WHERE table_schema='public' AND table_name='incoming_item' "
                        "AND column_name='created_unit_lot_id' "
                        "AND udt_name='uuid' AND is_nullable='YES'"
                    )
                )
                == 1
            )

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()


async def test_nonempty_provenance_downgrade_preserves_document_and_lot(
    db_session: AsyncSession, maintenance_engine: AsyncEngine
) -> None:
    incoming = await seed_prepared_incoming(db_session)
    await accept_prepared(db_session, incoming)
    lot_id = await db_session.scalar(
        text("SELECT created_unit_lot_id FROM incoming_item WHERE id=:id"),
        {"id": incoming.item_ids[0]},
    )
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
                DBAPIError, match="unit_incoming_nonempty_downgrade_forbidden"
            ) as error:
                async with connection.begin_nested():
                    await connection.run_sync(refuse)
            assert error.value.orig.sqlstate == "55000"
            assert (
                await connection.scalar(
                    text("SELECT created_unit_lot_id FROM incoming_item WHERE id=:id"),
                    {"id": incoming.item_ids[0]},
                )
                == lot_id
            )
            assert (
                await connection.scalar(
                    text("SELECT closed_packs FROM unit_stock_lot WHERE id=:id"), {"id": lot_id}
                )
                == 2
            )
            assert (
                await connection.scalar(
                    text("SELECT status FROM incoming_document WHERE id=:id"),
                    {"id": incoming.document_id},
                )
                == "accepted"
            )
        finally:
            await transaction.rollback()
