"""Physical receiving migration rollback preserves history in disposable DBs."""

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
from tests.domains.incoming.test_unit_acceptance import (
    db_session as receiving_session_fixture,
)
from tests.domains.incoming.test_unit_acceptance_v2 import (
    TABLES_V2,
    accept_v2,
    seed_v2_incoming,
)
from tests.isolation.test_unit_dispensing_cost_v2_migration import (
    downgrade_cost_for_roundtrip,
    upgrade_cost_after_roundtrip,
)
from tests.isolation.test_unit_dispensing_v2_migration import _migration as _dispensing_migration

db_session = receiving_session_fixture


def _migration() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic/versions/0175_received_blister_initialization.py"
    )
    spec = importlib.util.spec_from_file_location("unit_incoming_v2_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def has_physical_history(connection: Connection) -> bool:
    return bool(connection.scalar(text("SELECT EXISTS(SELECT 1 FROM unit_stock_operation_v2)")))


async def test_physical_receiving_migration_roundtrip_or_preserves_sources(
    maintenance_engine: AsyncEngine,
) -> None:
    migration = _migration()

    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        with Operations.context(MigrationContext.configure(connection)):
            if has_physical_history(connection):
                with pytest.raises(DBAPIError) as error:
                    with connection.begin_nested():
                        migration.downgrade()
                assert error.value.orig.sqlstate == "55000"
                return
            dispensing_migration = _dispensing_migration()
            downgrade_cost_for_roundtrip(connection)
            dispensing_migration.downgrade()
            migration.downgrade()
            for table in TABLES_V2:
                assert (
                    connection.scalar(text("SELECT to_regclass(:table)"), {"table": table}) is None
                )
            assert connection.scalar(text("""
                SELECT count(*) FROM information_schema.columns WHERE table_name='incoming_item'
                  AND column_name='created_unit_lot_v2_id'
            """)) == 0
            # Revoke as the actual owner: an unrelated role cannot revoke its grants.
            # Owners retain grant options even after revoking ordinary REFERENCES.
            owners = dict(connection.execute(text("""
                SELECT relation.relname,owner.rolname FROM pg_class AS relation
                JOIN pg_roles AS owner ON owner.oid=relation.relowner
                WHERE relation.oid IN ('public.tenant'::regclass,'public.app_user'::regclass)
            """)).all())
            assert set(owners) == {"tenant", "app_user"}
            for table in ("tenant", "app_user"):
                assert owners[table] in {"aurum_schema_owner", "aurum_support"}
                connection.execute(text(f"SET LOCAL ROLE {owners[table]}"))
                connection.execute(
                    text(f"REVOKE REFERENCES ON public.{table} FROM aurum_schema_owner")
                )
            connection.execute(text("SET LOCAL ROLE aurum_schema_owner"))
            references = text(
                "SELECT has_table_privilege('aurum_schema_owner',:table,'REFERENCES')"
            )
            for table in ("public.tenant", "public.app_user"):
                assert connection.scalar(references, {"table": table}) is False
            migration.upgrade()
            connection.execute(text("SET LOCAL ROLE aurum_schema_owner"))
            for table in ("public.tenant", "public.app_user"):
                assert connection.scalar(references, {"table": table}) is False
            assert (
                connection.scalar(
                    text("""
                SELECT count(*) FROM pg_class WHERE relname=ANY(:tables)
                  AND relrowsecurity AND relforcerowsecurity
            """),
                    {"tables": list(TABLES_V2)},
                )
                == 3
            )
            assert connection.scalar(text("""
                SELECT count(*) FROM information_schema.columns WHERE table_name='incoming_item'
                  AND column_name='created_unit_lot_v2_id' AND udt_name='uuid' AND is_nullable='YES'
            """)) == 1
            dispensing_migration.upgrade()
            upgrade_cost_after_roundtrip(connection)

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()


async def test_nonempty_physical_downgrade_preserves_stock_and_binding(
    db_session: AsyncSession, maintenance_engine: AsyncEngine
) -> None:
    incoming = await seed_v2_incoming(db_session)
    await accept_v2(db_session, incoming)
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
            with pytest.raises(DBAPIError) as error:
                async with connection.begin_nested():
                    await connection.run_sync(refuse)
            assert error.value.orig.sqlstate == "55000"
            source_id = incoming.item_ids[0]
            lot_id = await connection.scalar(
                text("SELECT created_unit_lot_v2_id FROM incoming_item WHERE id=:id"),
                {"id": source_id},
            )
            assert lot_id is not None
            assert (
                await connection.scalar(
                    text("SELECT closed_packs FROM unit_stock_lot_v2 WHERE id=:id"), {"id": lot_id}
                )
                == 2
            )
            for table in TABLES_V2:
                assert (
                    await connection.scalar(
                        text(f"SELECT count(*) FROM {table} WHERE source_item_id=:id"),
                        {"id": source_id},
                    )
                    == 1
                )
        finally:
            await transaction.rollback()
