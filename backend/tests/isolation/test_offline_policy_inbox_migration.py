"""Rehearse the inactive inbox migration only in the disposable database."""

import importlib.util
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from alembic.migration import MigrationContext
from alembic.operations import Operations


async def test_offline_policy_inbox_migration_roundtrip(maintenance_engine: AsyncEngine) -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "0158_offline_policy_inbox.py"
    )
    spec = importlib.util.spec_from_file_location("offline_policy_inbox_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        grants_sql = text("""
            SELECT relname, has_table_privilege('aurum_schema_owner', oid, 'REFERENCES')
            FROM pg_class WHERE oid IN ('public.tenant'::regclass,
              'public.sync_writer_activation'::regclass, 'public.sync_node'::regclass,
              'public.register'::regclass) ORDER BY relname
        """)
        before = connection.execute(grants_sql).all()
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
            assert (
                connection.scalar(text("SELECT to_regclass('public.offline_policy_inbox')")) is None
            )
            migration.upgrade()
            assert connection.scalar(text("""
                SELECT relrowsecurity AND relforcerowsecurity FROM pg_class
                WHERE oid='public.offline_policy_inbox'::regclass
            """))
            for role, expected in (
                ("aurum_app", {"SELECT", "INSERT"}),
                ("aurum_support", {"SELECT"}),
            ):
                actual = set(
                    connection.scalars(
                        text("""
                    SELECT privilege FROM unnest(ARRAY[
                      'SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER'
                    ]) AS privilege
                    WHERE has_table_privilege(:role, 'public.offline_policy_inbox', privilege)
                """),
                        {"role": role},
                    )
                )
                assert actual == expected
            assert connection.execute(grants_sql).all() == before

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()
