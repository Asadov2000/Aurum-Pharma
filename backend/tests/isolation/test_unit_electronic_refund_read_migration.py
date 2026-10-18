"""Refund read policies are reversible without altering grants, facts or old guards."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from alembic.migration import MigrationContext
from alembic.operations import Operations
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.pos.test_unit_electronic_refund_preview import original

db_session = receiving_session_fixture


def migration() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / (
        "alembic/versions/0213_unit_electronic_refund_read.py"
    )
    spec = importlib.util.spec_from_file_location("unit_electronic_refund_read", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


POLICIES = text(
    "SELECT tablename,policyname,roles,cmd,qual,with_check FROM pg_policies "
    "WHERE schemaname='public' AND tablename IN "
    "('pos_unit_checkout_payment_component','pos_unit_refund_payment_component') "
    "ORDER BY tablename,policyname"
)
RELATIONS = text(
    "SELECT relname,relowner,relacl,relrowsecurity,relforcerowsecurity FROM pg_class "
    "WHERE relnamespace='public'::regnamespace AND relname IN "
    "('pos_unit_checkout_payment_component','pos_unit_refund_payment_component') ORDER BY relname"
)
FUNCTIONS = text(
    "SELECT proname,pg_get_functiondef(oid),proowner,proacl,prosecdef,proconfig "
    "FROM pg_proc WHERE pronamespace='public'::regnamespace "
    "AND proowner='aurum_schema_owner'::regrole ORDER BY proname,oid::regprocedure::text"
)


@pytest.mark.parametrize("with_history", [False, True])
async def test_refund_read_policy_roundtrip_preserves_existing_security_and_history(
    maintenance_engine: AsyncEngine, db_session: AsyncSession, with_history: bool
) -> None:
    assert str(await db_session.scalar(text("SELECT current_database()"))).endswith("_test")
    if with_history:
        source = await original(db_session)
        assert await db_session.scalar(
            text("SELECT count(*) FROM pos_unit_checkout_payment_component WHERE sale_id=:id"),
            {"id": source.sale_id},
        ) == 1
        await db_session.commit()

    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        before_policies = connection.execute(POLICIES).all()
        new_policies = [row for row in before_policies if row.policyname == migration().POLICY]
        assert len(new_policies) == 2
        assert all(row.cmd == "SELECT" and row.roles == ["aurum_app"] for row in new_policies)
        assert all(row.with_check is None for row in new_policies)
        before_relations = connection.execute(RELATIONS).all()
        before_functions = connection.execute(FUNCTIONS).all()
        originals: dict[str, list[object]] = {}
        for table in ("pos_unit_checkout_payment_component", "pos_unit_refund_payment_component"):
            history = connection.execute(
                text(f"SELECT to_jsonb(row) FROM {table} row ORDER BY tenant_id,id")
            )
            originals[table] = list(history.scalars())
        with Operations.context(MigrationContext.configure(connection)):
            migration().downgrade()
            assert connection.execute(POLICIES).all() == [
                row for row in before_policies if row.policyname != migration().POLICY
            ]
            assert connection.execute(RELATIONS).all() == before_relations
            migration().upgrade()
        assert connection.execute(POLICIES).all() == before_policies
        assert connection.execute(RELATIONS).all() == before_relations
        assert connection.execute(FUNCTIONS).all() == before_functions
        for table, rows in originals.items():
            history = connection.execute(
                text(f"SELECT to_jsonb(row) FROM {table} row ORDER BY tenant_id,id")
            )
            assert list(history.scalars()) == rows

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()
