"""Rehearse downgrade/upgrade transactionally on the disposable test database."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from alembic.migration import MigrationContext
from alembic.operations import Operations


async def test_local_auth_migration_roundtrip(maintenance_engine: AsyncEngine) -> None:
    source = (
        Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0154_local_auth_state.py"
    )
    spec = importlib.util.spec_from_file_location("local_auth_migration_rehearsal", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        references_before = connection.scalar(
            text("SELECT has_table_privilege('aurum_schema_owner', 'public.tenant', 'REFERENCES')")
        )
        with Operations.context(MigrationContext.configure(connection)):
            module.downgrade()
            for table in ("local_auth_guard", "local_auth_session"):
                assert (
                    connection.scalar(
                        text("SELECT to_regclass(:table)"), {"table": f"public.{table}"}
                    )
                    is None
                )
            module.upgrade()
            for table in ("local_auth_guard", "local_auth_session"):
                assert connection.scalar(
                    text(
                        "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
                        "WHERE oid = to_regclass(:table)"
                    ),
                    {"table": f"public.{table}"},
                )
                assert not connection.scalar(
                    text("SELECT has_table_privilege('aurum_app', :table, 'DELETE')"),
                    {"table": f"public.{table}"},
                )
            assert (
                connection.scalar(
                    text(
                        "SELECT has_table_privilege("
                        "'aurum_schema_owner', 'public.tenant', 'REFERENCES')"
                    )
                )
                == references_before
            )

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()
