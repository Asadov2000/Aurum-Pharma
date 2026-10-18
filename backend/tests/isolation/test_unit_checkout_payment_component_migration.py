"""Journal DDL is reversible only without new checkout history in the test DB."""

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from alembic.migration import MigrationContext
from alembic.operations import Operations
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import set_receiving_context
from tests.domains.pos.test_unit_checkout_payment_components import complete, journal, request_for
from tests.domains.pos.test_unit_return import runtime
from tests.isolation.test_physical_cash_checkout_migration import (
    downgrade_electronic_for_roundtrip,
    payment_component_migration,
    refund_payment_component_migration,
    upgrade_electronic_after_roundtrip,
)

db_session = receiving_session_fixture

HISTORY = text(
    "SELECT EXISTS(SELECT 1 FROM pos_unit_payment_attempt_binding) OR "
    "EXISTS(SELECT 1 FROM pos_physical_checkout WHERE payment_journal_version=3) OR "
    "EXISTS(SELECT 1 FROM pos_unit_refund_payment_component) OR "
    "EXISTS(SELECT 1 FROM pos_physical_cash_refund WHERE payment_journal_version=2) OR "
    "EXISTS(SELECT 1 FROM pos_mixed_cash_refund WHERE payment_journal_version=2) OR "
    "EXISTS(SELECT 1 FROM pos_unit_checkout_payment_component) OR "
    "EXISTS(SELECT 1 FROM pos_physical_checkout WHERE payment_journal_version=2) OR "
    "EXISTS(SELECT 1 FROM pos_mixed_checkout WHERE payment_journal_version=2)"
)
FUNCTIONS = text(
    "SELECT proname,pg_get_functiondef(oid),proowner,proacl,prosecdef,proconfig FROM pg_proc "
    "WHERE pronamespace='public'::regnamespace AND (proname LIKE '%unit_checkout_payment%' "
    "OR proname IN ('assert_physical_checkout','assert_mixed_checkout')) ORDER BY proname"
)
RELATION = text(
    "SELECT relowner,relacl,relrowsecurity,relforcerowsecurity FROM pg_class "
    "WHERE oid='public.pos_unit_checkout_payment_component'::regclass"
)
POLICIES = text(
    "SELECT policyname,roles,cmd,qual,with_check FROM pg_policies "
    "WHERE schemaname='public' AND tablename='pos_unit_checkout_payment_component' "
    "ORDER BY policyname"
)
COLUMNS = text(
    "SELECT table_name,column_name,data_type,is_nullable,column_default "
    "FROM information_schema.columns WHERE table_schema='public' AND "
    "(table_name='pos_unit_checkout_payment_component' OR "
    "(table_name IN ('pos_physical_checkout','pos_mixed_checkout') "
    "AND column_name='payment_journal_version')) ORDER BY table_name,column_name"
)


async def test_payment_component_roundtrip_preserves_security_and_parent_functions(
    maintenance_engine: AsyncEngine,
) -> None:
    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        queries = (FUNCTIONS, RELATION, POLICIES, COLUMNS)
        before = [connection.execute(query).all() for query in queries]
        with Operations.context(MigrationContext.configure(connection)):
            if connection.scalar(HISTORY):
                with pytest.raises(DBAPIError) as denied:
                    with connection.begin_nested():
                        downgrade_electronic_for_roundtrip()
                        refund_payment_component_migration().downgrade()
                        payment_component_migration().downgrade()
                assert getattr(denied.value.orig, "sqlstate", None) == "55000"
            else:
                downgrade_electronic_for_roundtrip()
                refund_payment_component_migration().downgrade()
                payment_component_migration().downgrade()
                relation = connection.scalar(
                    text("SELECT to_regclass('public.pos_unit_checkout_payment_component')")
                )
                assert relation is None
                assert connection.execute(COLUMNS).all() == []
                payment_component_migration().upgrade()
                refund_payment_component_migration().upgrade()
                upgrade_electronic_after_roundtrip()
        assert [connection.execute(query).all() for query in queries] == before

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()


@pytest.mark.parametrize("mixed", [False, True])
async def test_populated_payment_journal_refuses_downgrade_without_losing_history(
    db_session: AsyncSession, maintenance_engine: AsyncEngine, mixed: bool
) -> None:
    assert str(await db_session.scalar(text("SELECT current_database()"))).endswith("_test")
    request = await request_for(db_session, mixed)
    result = await complete(db_session, request, mixed)
    before = await journal(db_session)
    await db_session.commit()

    def refuse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(DBAPIError, match="downgrade_requires_empty_history") as denied:
                with connection.begin_nested():
                    payment_component_migration().downgrade()
            assert getattr(denied.value.orig, "sqlstate", None) == "55000"

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(refuse)
        finally:
            await transaction.rollback()
    context = runtime(db_session)
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await journal(db_session) == before
    assert await complete(db_session, request, mixed) == result
