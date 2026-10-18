"""Electronic checkout DDL preserves old guards and refuses any new financial history."""

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from alembic.migration import MigrationContext
from alembic.operations import Operations
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import set_receiving_context
from tests.domains.pos.test_unit_electronic_checkout import (
    attempts,
    complete,
    confirm,
    financial_facts,
    prepared,
)
from tests.domains.pos.test_unit_return import runtime
from tests.isolation.test_physical_cash_checkout_migration import electronic_checkout_migration

db_session = receiving_session_fixture
HISTORY = text(
    "SELECT EXISTS(SELECT 1 FROM pos_unit_payment_attempt_binding) OR "
    "EXISTS(SELECT 1 FROM pos_physical_checkout WHERE payment_journal_version=3)"
)
FUNCTIONS = text(
    "SELECT proname,pg_get_functiondef(oid),proowner,proacl,prosecdef,proconfig "
    "FROM pg_proc WHERE pronamespace='public'::regnamespace "
    "AND proowner='aurum_schema_owner'::regrole ORDER BY proname,oid::regprocedure::text"
)
RELATIONS = text(
    "SELECT relname,relowner,relacl,relrowsecurity,relforcerowsecurity FROM pg_class "
    "WHERE relnamespace='public'::regnamespace "
    "AND relname IN ('pos_unit_payment_attempt_binding','pos_physical_checkout') ORDER BY relname"
)
POLICIES = text(
    "SELECT tablename,policyname,roles,cmd,qual,with_check FROM pg_policies "
    "WHERE schemaname='public' "
    "AND tablename IN ('pos_unit_payment_attempt_binding','pos_physical_checkout') "
    "ORDER BY tablename,policyname"
)
COLUMNS = text(
    "SELECT table_name,column_name,data_type,is_nullable,column_default "
    "FROM information_schema.columns WHERE table_schema='public' "
    "AND table_name IN ('pos_unit_payment_attempt_binding','pos_physical_checkout') "
    "ORDER BY table_name,column_name"
)


async def test_electronic_checkout_roundtrip_preserves_security_and_all_old_guards(
    maintenance_engine: AsyncEngine,
) -> None:
    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        queries = (FUNCTIONS, RELATIONS, POLICIES, COLUMNS)
        before = [connection.execute(query).all() for query in queries]
        with Operations.context(MigrationContext.configure(connection)):
            if connection.scalar(HISTORY):
                with pytest.raises(DBAPIError) as denied:
                    with connection.begin_nested():
                        electronic_checkout_migration().downgrade()
                assert getattr(denied.value.orig, "sqlstate", None) == "55000"
            else:
                electronic_checkout_migration().downgrade()
                relation = connection.scalar(
                    text("SELECT to_regclass('public.pos_unit_payment_attempt_binding')")
                )
                assert relation is None
                nullable = connection.scalar(
                    text(
                        "SELECT is_nullable FROM information_schema.columns "
                        "WHERE table_schema='public' AND table_name='pos_physical_checkout' "
                        "AND column_name='cash_intent_id'"
                    )
                )
                assert nullable == "NO"
                electronic_checkout_migration().upgrade()
        assert [connection.execute(query).all() for query in queries] == before

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()


@pytest.mark.parametrize("completed", [False, True])
async def test_attempt_or_completed_electronic_history_prevents_destructive_downgrade(
    db_session: AsyncSession, maintenance_engine: AsyncEngine, completed: bool
) -> None:
    assert str(await db_session.scalar(text("SELECT current_database()"))).endswith("_test")
    request, ids = await prepared(db_session)
    rows = await attempts(db_session, request, ids)
    if completed:
        await confirm(db_session, rows)
        await complete(db_session, request)
    before = await financial_facts(db_session)
    await db_session.commit()

    def refuse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(DBAPIError) as denied:
                with connection.begin_nested():
                    electronic_checkout_migration().downgrade()
            assert getattr(denied.value.orig, "sqlstate", None) == "55000"

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(refuse)
        finally:
            await transaction.rollback()
    context = runtime(db_session)
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await financial_facts(db_session) == before
