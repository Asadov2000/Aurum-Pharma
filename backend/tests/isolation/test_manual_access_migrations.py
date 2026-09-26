"""Transactional rollback rehearsal, exclusively in the disposable test DB."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from alembic.migration import MigrationContext
from alembic.operations import Operations


def _migration(filename: str) -> ModuleType:
    source = Path(__file__).resolve().parents[2] / "alembic" / "versions" / filename
    spec = importlib.util.spec_from_file_location(f"manual_access_{source.stem}", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_manual_access_migrations_roundtrip(maintenance_engine: AsyncEngine) -> None:
    migrations = [
        _migration("0155_tenant_access_policy.py"),
        _migration("0156_owner_pharmacy_launch.py"),
        _migration("0157_retire_subscription_access.py"),
    ]

    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        with Operations.context(MigrationContext.configure(connection)):
            for migration in reversed(migrations):
                migration.downgrade()
            for table in ("tenant_access_policy", "tenant_launch_activation"):
                assert (
                    connection.scalar(
                        text("SELECT to_regclass(:table)"), {"table": f"public.{table}"}
                    )
                    is None
                )
            for migration in migrations:
                migration.upgrade()
            for table in ("tenant_access_policy", "tenant_launch_activation"):
                assert connection.scalar(
                    text("""
                    SELECT relrowsecurity AND relforcerowsecurity FROM pg_class
                    WHERE oid = to_regclass(:table)
                    """),
                    {"table": f"public.{table}"},
                )
            definition = str(connection.scalar(text("""
                SELECT pg_get_functiondef(
                  ('public.approve_billing_bank_payment'
                   || '(uuid,uuid,uuid,text,uuid,uuid,integer)')::regprocedure
                )
                """)))
            assert "UPDATE public.tenant" not in definition
            assert "v_access_restored := true" not in definition

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()
