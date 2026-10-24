"""Posting migrations keep strict historical guards and forbid incomplete commits."""

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from alembic.migration import MigrationContext
from alembic.operations import Operations
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.pos.test_unit_electronic_refund_posting import reconciled
from tests.domains.pos.test_unit_return import runtime
from tests.isolation.test_physical_cash_checkout_migration import (
    electronic_refund_posting_migration,
    mixed_electronic_checkout_migration,
    mixed_electronic_refund_intent_migration,
    mixed_electronic_refund_preparation_migration,
)

db_session = receiving_session_fixture
TABLE = "pos_unit_electronic_refund_posting"


async def test_posting_roundtrip_restores_exact_historical_guards(
    maintenance_engine: AsyncEngine,
) -> None:
    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        migration = electronic_refund_posting_migration()
        signatures = [item[0] for item in migration.PATCHES]
        query = text("SELECT pg_get_functiondef(CAST(:signature AS regprocedure))")
        head_before = {
            sig: connection.scalar(query, {"signature": f"public.{sig}"}) for sig in signatures
        }
        with Operations.context(MigrationContext.configure(connection)):
            if connection.scalar(text(f"SELECT EXISTS(SELECT 1 FROM public.{TABLE})")):
                with pytest.raises(DBAPIError, match="downgrade_requires_empty_history"):
                    with connection.begin_nested():
                        migration.downgrade()
                return
            later_history = connection.scalar(
                text(
                    "SELECT EXISTS(SELECT 1 FROM public.pos_mixed_electronic_refund_intent) "
                    "OR EXISTS(SELECT 1 FROM public.pos_mixed_checkout "
                    "WHERE payment_journal_version=3) OR EXISTS("
                    "SELECT 1 FROM public.pos_unit_payment_attempt_binding b "
                    "JOIN public.pos_unit_payment_plan_intent i "
                    "ON i.tenant_id=b.tenant_id AND i.id=b.intent_id "
                    "WHERE EXISTS(SELECT 1 FROM "
                    "jsonb_array_elements(i.quote_payload->'lines') line "
                    "WHERE line->>'inventory_kind'='legacy_pack'))"
                )
            )
            if later_history:
                with pytest.raises(
                    DBAPIError,
                    match=(
                        "mixed_electronic_(refund_preparation|refund_intent|checkout)"
                        "_downgrade_requires_empty_history"
                    ),
                ) as error:
                    with connection.begin_nested():
                        mixed_electronic_refund_preparation_migration().downgrade()
                        mixed_electronic_refund_intent_migration().downgrade()
                        mixed_electronic_checkout_migration().downgrade()
                assert getattr(error.value.orig, "sqlstate", None) == "55000"
                assert {
                    sig: connection.scalar(query, {"signature": f"public.{sig}"})
                    for sig in signatures
                } == head_before
                return
            # Unwind later audit patches and dependencies before historical 0218 text.
            mixed_electronic_refund_preparation_migration().downgrade()
            mixed_electronic_refund_intent_migration().downgrade()
            mixed_electronic_checkout_migration().downgrade()
            before0218 = {
                sig: connection.scalar(query, {"signature": f"public.{sig}"}) for sig in signatures
            }
            migration.downgrade()
            assert connection.scalar(text("SELECT to_regclass(:name)"), {"name": TABLE}) is None
            downgraded = {
                sig: connection.scalar(query, {"signature": f"public.{sig}"}) for sig in signatures
            }
            assert all(before0218[sig] != downgraded[sig] for sig in signatures)
            migration.upgrade()
            assert {
                sig: connection.scalar(query, {"signature": f"public.{sig}"}) for sig in signatures
            } == before0218
            mixed_electronic_checkout_migration().upgrade()
            mixed_electronic_refund_intent_migration().upgrade()
            mixed_electronic_refund_preparation_migration().upgrade()
        assert {
            sig: connection.scalar(query, {"signature": f"public.{sig}"}) for sig in signatures
        } == head_before
        for role, allowed in (("aurum_app", {"SELECT", "INSERT"}), ("aurum_support", {"SELECT"})):
            for privilege in (
                "SELECT",
                "INSERT",
                "UPDATE",
                "DELETE",
                "TRUNCATE",
                "REFERENCES",
                "TRIGGER",
            ):
                assert connection.scalar(
                    text("SELECT has_table_privilege(:role,:table,:privilege)"),
                    {"role": role, "table": f"public.{TABLE}", "privilege": privilege},
                ) is (privilege in allowed)
            for function in migration.FUNCTIONS:
                assert not connection.scalar(
                    text("SELECT has_function_privilege(:role,:function,'EXECUTE')"),
                    {"role": role, "function": f"public.{function}"},
                )
        assert connection.scalar(
            text(
                "SELECT relrowsecurity AND relforcerowsecurity "
                "AND relowner='aurum_schema_owner'::regrole FROM pg_class "
                f"WHERE oid='public.{TABLE}'::regclass"
            )
        )

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()


async def test_raw_marker_alone_cannot_commit_without_financial_posting(
    db_session: AsyncSession,
) -> None:
    request, shift_id = await reconciled(db_session)
    context = runtime(db_session)
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    f"INSERT INTO {TABLE} "
                    "(tenant_id,id,parent_sale_id,sale_id,branch_id,register_id,shift_id,"
                    "actor_id,created_by) SELECT :tenant,:operation,:parent,gen_random_uuid(),"
                    ":branch,register_id,:shift,:actor,:actor FROM sale WHERE id=:parent"
                ),
                {
                    "tenant": context.stock.tenant_id,
                    "operation": request.operation_id,
                    "parent": request.request.parent_sale_id,
                    "branch": context.stock.branch_id,
                    "shift": shift_id,
                    "actor": context.actor_id,
                },
            )
            await db_session.execute(
                text("SET CONSTRAINTS unit_electronic_refund_posting_complete IMMEDIATE")
            )
    assert (
        await db_session.scalar(
            text(f"SELECT count(*) FROM {TABLE} WHERE id=:id"), {"id": request.operation_id}
        )
        == 0
    )


async def test_plpgsql_if_case_requires_parentheses(maintenance_engine: AsyncEngine) -> None:
    """Reproduce the old parser failure and verify the unchanged CASE truth table."""
    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            assert str(await connection.scalar(text("SELECT current_database()"))).endswith("_test")
            with pytest.raises(DBAPIError, match="syntax error at end of input") as original:
                async with connection.begin_nested():
                    await connection.execute(
                        text(
                            "DO $$ BEGIN IF false OR CASE WHEN true THEN false ELSE true END THEN "
                            "NULL; END IF; END $$;"
                        )
                    )
            assert getattr(original.value.orig, "sqlstate", None) == "42601"
            await connection.execute(text("""
                DO $$ DECLARE method TEXT; invalid BOOLEAN; expected BOOLEAN; BEGIN
                  FOREACH method IN ARRAY ARRAY['cash','card','qr'] LOOP
                    FOREACH invalid IN ARRAY ARRAY[false,true] LOOP
                      expected:=false;
                      IF false OR (CASE WHEN method='cash' THEN invalid ELSE NOT invalid END) THEN
                        expected:=true;
                      END IF;
                      IF expected IS DISTINCT FROM
                        (CASE WHEN method='cash' THEN invalid ELSE NOT invalid END) THEN
                        RAISE EXCEPTION 'posting evidence CASE truth table changed';
                      END IF;
                    END LOOP;
                  END LOOP;
                END $$;
            """))
        finally:
            await transaction.rollback()
