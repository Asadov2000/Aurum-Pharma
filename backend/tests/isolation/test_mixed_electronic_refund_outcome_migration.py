"""Outcome schema roundtrip, private guards and reverse legacy evidence fences."""

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from alembic.migration import MigrationContext
from alembic.operations import Operations
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import set_receiving_context
from tests.domains.pos.test_mixed_electronic_refund_outcome import (
    activated,
    confirmation,
    outcome,
)
from tests.domains.pos.test_unit_return import runtime
from tests.isolation.test_physical_cash_checkout_migration import (
    downgrade_mixed_refund_posting_for_roundtrip,
    mixed_electronic_refund_outcome_migration,
    mixed_electronic_refund_posting_migration,
)

db_session = receiving_session_fixture
TABLE = "pos_mixed_electronic_refund_outcome"


CROSS_TRIGGER_SNAPSHOT = text(
    "SELECT c.relname,t.tgname,pg_get_triggerdef(t.oid) FROM pg_trigger t "
    "JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_proc p ON p.oid=t.tgfoid "
    "WHERE c.relnamespace='public'::regnamespace "
    "AND p.pronamespace='public'::regnamespace "
    "AND p.proname='trg_guard_mixed_refund_outcome_cross_type' "
    "AND NOT t.tgisinternal ORDER BY c.relname,t.tgname"
)


def assert_reciprocal_trigger_order(connection: Connection) -> None:
    for table, old, new in (
        (
            "pos_payment_attempt",
            "trg_unit_outcome_payment_cross_type",
            "trg_v2_mixed_outcome_payment_cross_type",
        ),
        (
            "pos_refund_reference",
            "trg_unit_outcome_refund_cross_type",
            "trg_v2_mixed_outcome_refund_cross_type",
        ),
        (
            "pos_unit_electronic_refund_outcome",
            "a10_unit_electronic_refund_outcome_guard",
            "a11_mixed_outcome_reference_cross_type",
        ),
    ):
        names = (
            connection.execute(
                text(
                    "SELECT tgname FROM pg_trigger WHERE tgrelid=CAST(:table AS regclass) "
                    "AND NOT tgisinternal AND tgenabled='O' AND (tgtype & 2)=2 "
                    "AND (tgtype & 4)=4 AND tgname IN (:old,:new) ORDER BY tgname"
                ),
                {"table": f"public.{table}", "old": old, "new": new},
            )
            .scalars()
            .all()
        )
        assert names == [old, new]


def test_cross_type_trigger_names_preserve_existing_order_and_exact_downgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = mixed_electronic_refund_outcome_migration()
    expected = {
        "pos_payment_attempt": "trg_v2_mixed_outcome_payment_cross_type",
        "pos_refund_reference": "trg_v2_mixed_outcome_refund_cross_type",
        "pos_unit_electronic_refund_outcome": "a11_mixed_outcome_reference_cross_type",
    }
    assert dict(migration.CROSS_FENCES) == expected
    statements: list[str] = []
    monkeypatch.setattr(migration, "_sql", statements.append)
    redactions: list[bool] = []
    monkeypatch.setattr(
        migration, "_audit_redaction", lambda *, restore=False: redactions.append(restore)
    )
    migration.upgrade()
    creates = list(statements)
    statements.clear()
    migration.downgrade()
    assert redactions == [False, True]
    for table, name in expected.items():
        assert (
            sum(f"CREATE TRIGGER {name}" in sql and f"ON public.{table}" in sql for sql in creates)
            == 1
        )
        assert statements.count(f"DROP TRIGGER {name} ON public.{table};") == 1


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
        audit_sql = text(
            "SELECT pg_get_functiondef(oid),proowner,proacl::text,prosecdef,proconfig "
            "FROM pg_proc WHERE oid="
            "'public.audit_redact_table_snapshot(text,jsonb)'::regprocedure"
        )
        head_audit_before = connection.execute(audit_sql).one()
        assert_reciprocal_trigger_order(connection)
        cross_before = connection.execute(CROSS_TRIGGER_SNAPSHOT).all()
        with Operations.context(MigrationContext.configure(connection)):
            if connection.scalar(text(f"SELECT EXISTS(SELECT 1 FROM public.{TABLE})")):
                with pytest.raises(DBAPIError, match="downgrade_requires_empty_history"):
                    with connection.begin_nested():
                        mixed_electronic_refund_outcome_migration().downgrade()
                return
            if not downgrade_mixed_refund_posting_for_roundtrip(connection):
                return
            audit_before = connection.execute(audit_sql).one()
            mixed_electronic_refund_outcome_migration().downgrade()
            assert connection.scalar(text("SELECT to_regclass(:name)"), {"name": TABLE}) is None
            assert audit_before[0].count(mixed_electronic_refund_outcome_migration().AUDIT_NEW) == 1
            audit_base = connection.execute(audit_sql).one()
            assert audit_base[0] == audit_before[0].replace(
                mixed_electronic_refund_outcome_migration().AUDIT_NEW,
                mixed_electronic_refund_outcome_migration().AUDIT_OLD,
                1,
            )
            assert audit_base[1:] == audit_before[1:]
            assert connection.execute(CROSS_TRIGGER_SNAPSHOT).all() == []
            mixed_electronic_refund_outcome_migration().upgrade()
            assert connection.execute(audit_sql).one() == audit_before
            mixed_electronic_refund_posting_migration().upgrade()
        assert_reciprocal_trigger_order(connection)
        assert connection.execute(CROSS_TRIGGER_SNAPSHOT).all() == cross_before
        assert connection.execute(snapshot).one() == security
        assert connection.execute(audit_sql).one() == head_audit_before
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
            for function in mixed_electronic_refund_outcome_migration().FUNCTIONS:
                assert not connection.scalar(
                    text("SELECT has_function_privilege(:role,:function,'EXECUTE')"),
                    {"role": role, "function": f"public.{function}"},
                )
        for table, trigger in mixed_electronic_refund_outcome_migration().CROSS_FENCES:
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


async def test_outcome_audit_redactor_removes_only_terminal_evidence(
    maintenance_engine: AsyncEngine,
) -> None:
    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            assert str(await connection.scalar(text("SELECT current_database()"))).endswith("_test")
            redacted = await connection.scalar(
                text("SELECT public.audit_redact_table_snapshot(:table,CAST(:snapshot AS jsonb))"),
                {
                    "table": TABLE,
                    "snapshot": (
                        '{"terminal_id":"private","document_number":"private",'
                        '"original_payment_id":"retained","method":"card",'
                        '"amount":"0.99","currency":"TJS"}'
                    ),
                },
            )
            assert redacted == {
                "original_payment_id": "retained",
                "method": "card",
                "amount": "0.99",
                "currency": "TJS",
            }
        finally:
            await transaction.rollback()


@pytest.mark.parametrize(
    "legacy_table",
    ["pos_payment_attempt", "pos_refund_reference", "pos_unit_electronic_refund_outcome"],
)
async def test_reverse_guard_rejects_evidence_already_recorded_by_mixed_refund(
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
                    "EXECUTE FUNCTION public.trg_guard_mixed_refund_outcome_cross_type()"
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


@pytest.mark.parametrize(
    "legacy_table",
    ["pos_payment_attempt", "pos_refund_reference", "pos_unit_electronic_refund_outcome"],
)
@pytest.mark.parametrize("isolation", ["REPEATABLE READ", "SERIALIZABLE"])
async def test_legacy_evidence_rejects_snapshot_predating_outcome_commit(
    db_session: AsyncSession,
    maintenance_engine: AsyncEngine,
    legacy_table: str,
    isolation: str,
) -> None:
    """The old guard misses this committed outcome; advisory locks cannot refresh RR.

    Invoke the installed reciprocal guard with both real legacy row layouts on
    disposable probe tables, avoiding unrelated legacy transition constraints.
    The separate installation test checks that both production triggers use it.
    """
    request, activation = await activated(db_session)
    payment = next(p for p in activation.review.preview.payments if p.method == "card")
    evidence = confirmation(payment.original_payment_id)
    context = runtime(db_session)
    await db_session.commit()
    await set_receiving_context(db_session, context.stock, context.actor_id)
    async with maintenance_engine.connect() as connection:
        await connection.execution_options(isolation_level=isolation)
        transaction = await connection.begin()
        try:
            assert str(await connection.scalar(text("SELECT current_database()"))).endswith("_test")
            assert await connection.scalar(text("SHOW transaction_isolation")) == isolation.lower()
            count = text(
                f"SELECT count(*) FROM public.{TABLE} "
                "WHERE tenant_id=:tenant AND operation_id=:operation"
            )
            keys = {"tenant": context.stock.tenant_id, "operation": request.operation_id}
            assert await connection.scalar(count, keys) == 0  # pin legacy snapshot
            recorded = await outcome(db_session, request, evidence)
            await db_session.commit()  # second connection commits after the pinned snapshot
            assert len(recorded.confirmations) == 1
            # A stale EXISTS in the old guard still sees no row after that commit.
            assert await connection.scalar(count, keys) == 0
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
                    "EXECUTE FUNCTION public.trg_guard_mixed_refund_outcome_cross_type()"
                )
            )
            with pytest.raises(DBAPIError, match="outcome_isolation_required") as denied:
                async with connection.begin_nested():
                    await connection.execute(
                        text(
                            f"INSERT INTO pg_temp.{legacy_table} "
                            "VALUES (:tenant,:terminal,:document)"
                        ),
                        {
                            "tenant": context.stock.tenant_id,
                            "terminal": evidence.terminal_id,
                            "document": evidence.document_number,
                        },
                    )
            assert getattr(denied.value.orig, "sqlstate", None) == "23514"
            assert (
                await connection.scalar(text(f"SELECT count(*) FROM pg_temp.{legacy_table}")) == 0
            )
        finally:
            await transaction.rollback()


async def test_recorded_evidence_blocks_downgrade_without_losing_rows_or_guards(
    db_session: AsyncSession, maintenance_engine: AsyncEngine
) -> None:
    request, activation = await activated(db_session)
    payment = next(p for p in activation.review.preview.payments if p.method == "card")
    await outcome(db_session, request, confirmation(payment.original_payment_id))
    await db_session.commit()

    def refuse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        query = text(f"SELECT to_jsonb(row) FROM public.{TABLE} row WHERE operation_id=:operation")
        keys = {"operation": request.operation_id}
        before = connection.scalar(query, keys)
        assert before is not None
        guards_before = connection.execute(CROSS_TRIGGER_SNAPSHOT).all()
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(
                DBAPIError, match="mixed_electronic_refund_outcome_downgrade_requires_empty_history"
            ) as denied:
                with connection.begin_nested():
                    mixed_electronic_refund_outcome_migration().downgrade()
            assert getattr(denied.value.orig, "sqlstate", None) == "55000"
        assert connection.scalar(query, keys) == before
        assert connection.execute(CROSS_TRIGGER_SNAPSHOT).all() == guards_before

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(refuse)
        finally:
            await transaction.rollback()
