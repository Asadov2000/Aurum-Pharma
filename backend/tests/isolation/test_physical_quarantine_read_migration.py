"""The quarantine projection grants no raw journal access and preserves history."""

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
from tests.isolation.test_runtime_role_privileges import (
    support_engine_privileges as support_engine_fixture,
)

support_engine = support_engine_fixture

SIGNATURE = "public.physical_customer_return_queue(uuid,uuid,text)"
HISTORY_TABLES = (
    "pos_physical_return_command",
    "pos_physical_return_line",
    "pos_physical_return_slice",
    "pos_physical_cash_refund",
    "pos_physical_zero_return",
)


def migration() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "alembic/versions/0187_physical_quarantine_read.py"
    spec = importlib.util.spec_from_file_location("physical_quarantine_read_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_queue_function_is_a_private_stable_cost_free_interface(
    maintenance_engine: AsyncEngine,
) -> None:
    async with maintenance_engine.connect() as connection:
        row = (
            (
                await connection.execute(
                    text("""
            SELECT pg_get_userbyid(p.proowner) AS owner,p.prosecdef,
              p.provolatile::TEXT AS provolatile,
              p.proconfig,pg_get_function_result(p.oid) AS result,
              has_function_privilege('aurum_app',p.oid,'EXECUTE') AS app_execute,
              has_function_privilege('aurum_app',p.oid,'EXECUTE WITH GRANT OPTION') AS app_grant,
              has_function_privilege('aurum_support',p.oid,'EXECUTE') AS support_execute,
              EXISTS(SELECT 1 FROM aclexplode(p.proacl) a WHERE a.grantee=0) AS public_access
            FROM pg_proc p WHERE p.oid=to_regprocedure(:signature)
        """),
                    {"signature": SIGNATURE},
                )
            )
            .mappings()
            .one()
        )
        assert row["owner"] == "aurum_schema_owner"
        assert row["prosecdef"] is True
        assert row["provolatile"] == "s"
        assert row["proconfig"] == ["search_path=pg_catalog, pg_temp"]
        assert row["app_execute"] is True
        assert row["app_grant"] is False
        assert row["support_execute"] is False
        assert row["public_access"] is False
        assert "cost" not in row["result"]
        assert "result_payload" not in row["result"]
        assert "receipt_payload" not in row["result"]


async def test_queue_read_migration_roundtrip_keeps_every_history_and_policy(
    maintenance_engine: AsyncEngine,
) -> None:
    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        snapshots = {
            table: connection.execute(
                text(f"SELECT to_jsonb(row) FROM public.{table} row ORDER BY to_jsonb(row)::TEXT")
            )
            .scalars()
            .all()
            for table in HISTORY_TABLES
        }
        policies = text("""
            SELECT policyname,tablename,roles,cmd,qual,with_check FROM pg_policies
            WHERE schemaname='public' AND tablename LIKE 'pos_physical_%'
            ORDER BY tablename,policyname
        """)
        before_policies = connection.execute(policies).all()
        definition = connection.scalar(
            text("SELECT pg_get_functiondef(to_regprocedure(:s))"), {"s": SIGNATURE}
        )
        with Operations.context(MigrationContext.configure(connection)):
            migration().downgrade()
            assert connection.scalar(text("SELECT to_regprocedure(:s)"), {"s": SIGNATURE}) is None
            migration().upgrade()
        assert (
            connection.scalar(
                text("SELECT pg_get_functiondef(to_regprocedure(:s))"), {"s": SIGNATURE}
            )
            == definition
        )
        assert connection.execute(policies).all() == before_policies
        for table, before in snapshots.items():
            after = (
                connection.execute(
                    text(
                        f"SELECT to_jsonb(row) FROM public.{table} row ORDER BY to_jsonb(row)::TEXT"
                    )
                )
                .scalars()
                .all()
            )
            assert after == before

    async with maintenance_engine.begin() as connection:
        await connection.run_sync(rehearse)


async def test_queue_rejects_untrusted_support_login(
    support_engine: AsyncEngine,
) -> None:
    async with support_engine.connect() as connection:
        with pytest.raises(DBAPIError) as error:
            await connection.execute(
                text("SELECT * FROM public.physical_customer_return_queue(NULL)")
            )
        assert getattr(error.value.orig, "sqlstate", None) == "42501"
