"""Mixed electronic DDL preserves ownership, old guards and all pending history."""

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from alembic.migration import MigrationContext
from alembic.operations import Operations
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import set_receiving_context
from tests.domains.pos.test_mixed_electronic_checkout import (
    attempts,
    complete,
    confirm,
    facts,
    prepared,
)
from tests.domains.pos.test_unit_return import runtime
from tests.isolation.test_physical_cash_checkout_migration import (
    mixed_electronic_checkout_migration,
)

db_session = receiving_session_fixture
HISTORY = text(
    "SELECT EXISTS(SELECT 1 FROM pos_mixed_checkout WHERE payment_journal_version=3) OR "
    "EXISTS(SELECT 1 FROM pos_unit_payment_attempt_binding b "
    "JOIN pos_unit_payment_plan_intent i ON i.tenant_id=b.tenant_id AND i.id=b.intent_id "
    "WHERE EXISTS(SELECT 1 FROM jsonb_array_elements(i.quote_payload->'lines') line "
    "WHERE line->>'inventory_kind'='legacy_pack'))"
)
FUNCTIONS = text(
    "SELECT proname,pg_get_functiondef(oid),proowner,proacl,prosecdef,proconfig "
    "FROM pg_proc WHERE pronamespace='public'::regnamespace "
    "AND proowner='aurum_schema_owner'::regrole ORDER BY proname,oid::regprocedure::text"
)
RELATIONS = text(
    "SELECT relname,relowner,relacl,relrowsecurity,relforcerowsecurity "
    "FROM pg_class WHERE relnamespace='public'::regnamespace AND relname IN "
    "('pos_mixed_checkout','unit_electronic_checkout_marker','pos_unit_payment_attempt_binding') "
    "ORDER BY relname"
)
COLUMNS = text(
    "SELECT table_name,column_name,data_type,is_nullable,column_default "
    "FROM information_schema.columns WHERE table_schema='public' AND table_name IN "
    "('pos_mixed_checkout','unit_electronic_checkout_marker') ORDER BY table_name,column_name"
)
POLICIES = text(
    "SELECT tablename,policyname,roles,cmd,qual,with_check FROM pg_policies "
    "WHERE schemaname='public' AND tablename IN "
    "('pos_mixed_checkout','pos_unit_payment_attempt_binding') ORDER BY tablename,policyname"
)


def assert_private_projection(connection: Connection) -> None:
    for role in ("aurum_app", "aurum_support"):
        assert (
            connection.scalar(
                text(
                    "SELECT has_table_privilege(:role,"
                    "'public.unit_electronic_checkout_marker','SELECT')"
                ),
                {"role": role},
            )
            is False
        )
        for signature in mixed_electronic_checkout_migration().NEW_FUNCTIONS:
            assert (
                connection.scalar(
                    text("SELECT has_function_privilege(:role,:signature,'EXECUTE')"),
                    {"role": role, "signature": "public." + signature},
                )
                is False
            )
    row = connection.execute(
        text(
            "SELECT prosecdef,pg_get_functiondef(oid) AS body FROM pg_proc "
            "WHERE oid='public.trg_mixed_stock_checkout_binding()'::regprocedure"
        )
    ).one()
    assert row.prosecdef is True
    assert "session_user<>'aurum_app'" in row.body
    assert "current_user<>'aurum_app'" not in row.body


async def test_mixed_electronic_roundtrip_preserves_acl_old_cash_and_physical_guards(
    maintenance_engine: AsyncEngine,
) -> None:
    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        queries = (FUNCTIONS, RELATIONS, COLUMNS, POLICIES)
        before = [connection.execute(query).all() for query in queries]
        assert_private_projection(connection)
        with Operations.context(MigrationContext.configure(connection)):
            if connection.scalar(HISTORY):
                with pytest.raises(DBAPIError, match="requires_empty_history") as denied:
                    with connection.begin_nested():
                        mixed_electronic_checkout_migration().downgrade()
                assert getattr(denied.value.orig, "sqlstate", None) == "55000"
            else:
                mixed_electronic_checkout_migration().downgrade()
                assert (
                    connection.scalar(
                        text("SELECT to_regclass('public.unit_electronic_checkout_marker')")
                    )
                    is None
                )
                assert (
                    connection.scalar(
                        text(
                            "SELECT prosecdef FROM pg_proc WHERE "
                            "oid='public.trg_mixed_stock_checkout_binding()'::regprocedure"
                        )
                    )
                    is False
                )
                mixed_electronic_checkout_migration().upgrade()
                assert_private_projection(connection)
        assert [connection.execute(query).all() for query in queries] == before

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()


@pytest.mark.parametrize("completed", [False, True])
async def test_pending_or_completed_mixed_attempts_block_destructive_downgrade(
    db_session: AsyncSession,
    maintenance_engine: AsyncEngine,
    completed: bool,
) -> None:
    assert str(await db_session.scalar(text("SELECT current_database()"))).endswith("_test")
    request, ids = await prepared(db_session)
    rows = await attempts(db_session, request, ids)
    if completed:
        await confirm(db_session, request, rows)
        await complete(db_session, request)
    before = await facts(db_session)
    await db_session.commit()

    def refuse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(DBAPIError, match="requires_empty_history") as denied:
                with connection.begin_nested():
                    mixed_electronic_checkout_migration().downgrade()
            assert getattr(denied.value.orig, "sqlstate", None) == "55000"
        assert_private_projection(connection)

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(refuse)
        finally:
            await transaction.rollback()
    context = runtime(db_session)
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await facts(db_session) == before
