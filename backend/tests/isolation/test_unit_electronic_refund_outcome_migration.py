"""Outcome schema roundtrip, private guards and reverse legacy evidence fences."""

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from alembic.migration import MigrationContext
from alembic.operations import Operations
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.pos.test_unit_electronic_refund_outcome import activated, confirmation, outcome
from tests.domains.pos.test_unit_return import runtime
from tests.isolation.test_physical_cash_checkout_migration import (
    electronic_refund_outcome_migration,
)

db_session = receiving_session_fixture
TABLE = "pos_unit_electronic_refund_outcome"


async def test_outcome_schema_roundtrip_security_and_legacy_triggers(
    maintenance_engine: AsyncEngine,
) -> None:
    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        snapshot = text(
            "SELECT relowner,relacl::text,relrowsecurity,relforcerowsecurity FROM pg_class "
            f"WHERE oid='public.{TABLE}'::regclass"
        )
        security = connection.execute(snapshot).one()
        with Operations.context(MigrationContext.configure(connection)):
            if connection.scalar(text(f"SELECT EXISTS(SELECT 1 FROM public.{TABLE})")):
                with pytest.raises(DBAPIError, match="downgrade_requires_empty_history"):
                    with connection.begin_nested():
                        electronic_refund_outcome_migration().downgrade()
                return
            electronic_refund_outcome_migration().downgrade()
            assert connection.scalar(text("SELECT to_regclass(:name)"), {"name": TABLE}) is None
            electronic_refund_outcome_migration().upgrade()
        assert connection.execute(snapshot).one() == security
        assert security.relrowsecurity and security.relforcerowsecurity
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
            for function in electronic_refund_outcome_migration().FUNCTIONS:
                assert not connection.scalar(
                    text("SELECT has_function_privilege(:role,:function,'EXECUTE')"),
                    {"role": role, "function": f"public.{function}"},
                )
        for table, trigger in (
            ("pos_payment_attempt", "trg_unit_outcome_payment_cross_type"),
            ("pos_refund_reference", "trg_unit_outcome_refund_cross_type"),
        ):
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_trigger WHERE tgrelid=CAST(:table AS regclass) "
                        "AND tgname=:trigger AND NOT tgisinternal AND tgenabled='O'"
                    ),
                    {"table": f"public.{table}", "trigger": trigger},
                )
                == 1
            )
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM pg_policies "
                    "WHERE schemaname='public' AND tablename=:table"
                ),
                {"table": TABLE},
            )
            == 3
        )

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()


@pytest.mark.parametrize("legacy_table", ["pos_payment_attempt", "pos_refund_reference"])
async def test_reverse_guard_rejects_evidence_already_recorded_by_unit_refund(
    db_session: AsyncSession, maintenance_engine: AsyncEngine, legacy_table: str
) -> None:
    request, activation = await activated(db_session)
    payment = next(p for p in activation.review.preview.payments if p.method == "card")
    evidence = confirmation(payment.original_payment_id)
    await outcome(db_session, request, evidence)
    tenant = runtime(db_session).stock.tenant_id
    await db_session.commit()
    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            assert str(await connection.scalar(text("SELECT current_database()"))).endswith("_test")
            # Probe the private trigger with both legacy row layouts, leaving the
            # real append-only legacy payment/reference history untouched.
            document_field = (
                "external_reference" if legacy_table == "pos_payment_attempt" else "document_number"
            )
            await connection.execute(
                text(
                    f"CREATE TEMP TABLE {legacy_table} "
                    f"(tenant_id UUID,terminal_id TEXT,{document_field} TEXT) ON COMMIT DROP"
                )
            )
            await connection.execute(
                text(
                    f"CREATE TRIGGER probe BEFORE INSERT ON pg_temp.{legacy_table} FOR EACH ROW "
                    "EXECUTE FUNCTION public.trg_guard_unit_refund_outcome_cross_type()"
                )
            )
            with pytest.raises(DBAPIError, match="Terminal evidence was already used"):
                async with connection.begin_nested():
                    await connection.execute(
                        text(
                            f"INSERT INTO pg_temp.{legacy_table} "
                            "VALUES (:tenant,:terminal,:document)"
                        ),
                        {
                            "tenant": tenant,
                            "terminal": evidence.terminal_id,
                            "document": evidence.document_number,
                        },
                    )
        finally:
            await transaction.rollback()
