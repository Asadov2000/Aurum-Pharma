"""The recovery function has narrow ACLs and reversible, history-preserving DDL."""

import importlib.util
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from alembic.migration import MigrationContext
from alembic.operations import Operations

SIGNATURE = "public.mixed_customer_return_disposition_status(uuid,uuid,uuid)"


def migration() -> ModuleType:
    directory = Path(__file__).resolve().parents[2] / "alembic/versions"
    spec = importlib.util.spec_from_file_location(
        "mixed_disposition_status_migration", directory / "0194_mixed_disposition_status.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_status_is_stable_fixed_owner_app_only_and_support_cannot_execute(
    maintenance_engine: AsyncEngine, db_engine: AsyncEngine
) -> None:
    async with maintenance_engine.connect() as connection:
        row = (
            (
                await connection.execute(
                    text("""
                      SELECT pg_get_userbyid(proowner) AS owner,prosecdef,
                        provolatile::TEXT,proconfig,
                        has_function_privilege('aurum_app',oid,'EXECUTE') AS app_execute,
                        has_function_privilege('aurum_app',oid,
                          'EXECUTE WITH GRANT OPTION') AS grantable,
                        has_function_privilege('aurum_support',oid,'EXECUTE') AS support_execute,
                        EXISTS(SELECT 1 FROM aclexplode(proacl) a
                          WHERE a.grantee=0) AS public_access
                      FROM pg_proc WHERE oid=to_regprocedure(:signature)
                    """),
                    {"signature": SIGNATURE},
                )
            )
            .mappings()
            .one()
        )
        assert row["owner"] == "aurum_schema_owner" and row["prosecdef"] is True
        assert row["provolatile"] == "s" and row["proconfig"] == ["search_path=pg_catalog, pg_temp"]
        assert row["app_execute"] is True
        assert not row["grantable"] and not row["support_execute"] and not row["public_access"]
    async with db_engine.connect() as support:
        assert await support.scalar(text("SELECT session_user")) == "aurum_support"
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        with pytest.raises(DBAPIError) as error:
            await support.execute(
                text("SELECT public.mixed_customer_return_disposition_status(:t,:b,:d)"),
                {"t": uuid4(), "b": uuid4(), "d": uuid4()},
            )
        assert getattr(error.value.orig, "sqlstate", None) == "42501"


async def test_status_roundtrip_preserves_exact_journal_history_and_previous_function_acls(
    maintenance_engine: AsyncEngine,
) -> None:
    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        history = text(
            "SELECT to_jsonb(d) FROM mixed_customer_return_disposition d ORDER BY tenant_id,id"
        )
        definitions = text("""
          SELECT proname,pg_get_functiondef(oid),proacl,proowner FROM pg_proc
          WHERE pronamespace='public'::regnamespace AND proname IN (
            'mixed_customer_return_disposition_status','record_mixed_customer_return_disposition',
            'mixed_customer_return_disposition_history','mixed_customer_return_queue',
            'audit_redact_table_snapshot') ORDER BY proname
        """)
        before_history = connection.execute(history).all()
        before_functions = connection.execute(definitions).all()
        with Operations.context(MigrationContext.configure(connection)):
            migration().downgrade()
            assert connection.scalar(text("SELECT to_regprocedure(:s)"), {"s": SIGNATURE}) is None
            assert connection.execute(history).all() == before_history
            migration().upgrade()
        assert connection.execute(history).all() == before_history
        assert connection.execute(definitions).all() == before_functions

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()
