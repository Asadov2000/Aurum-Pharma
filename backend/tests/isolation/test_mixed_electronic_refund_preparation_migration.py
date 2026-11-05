"""Preparation schema preserves strict grants and refuses to discard durable history."""

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from alembic.migration import MigrationContext
from alembic.operations import Operations
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.pos.test_mixed_electronic_refund_activation import activate
from tests.domains.pos.test_mixed_electronic_refund_binding import binding, persist_review
from tests.isolation.test_physical_cash_checkout_migration import (
    downgrade_mixed_refund_evidence_for_roundtrip,
    mixed_electronic_refund_outcome_migration,
    mixed_electronic_refund_posting_migration,
    mixed_electronic_refund_preparation_migration,
    run_historical_rehearsal,
)

db_session = receiving_session_fixture


FENCE_TRIGGERS = text(
    "SELECT c.relname,t.tgname,pg_get_triggerdef(t.oid) FROM pg_trigger t "
    "JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_proc p ON p.oid=t.tgfoid "
    "WHERE c.relnamespace='public'::regnamespace "
    "AND p.pronamespace='public'::regnamespace "
    "AND p.proname='trg_mixed_electronic_refund_binding_fence' "
    "AND NOT t.tgisinternal ORDER BY c.relname,t.tgname"
)


def fence_trigger_snapshot(connection: Connection) -> list[tuple[str, str, str]]:
    rows = connection.execute(FENCE_TRIGGERS).all()
    assert len(rows) == 4
    assert {row[0]: row[1] for row in rows} == {
        "sale": "a01_mixed_electronic_refund_binding_fence",
        "pos_physical_return_command": "a01_mixed_electronic_refund_binding_fence",
        "pos_mixed_return_command": "a11_mixed_electronic_refund_binding_fence",
        "pos_refund_attempt": "a01_mixed_electronic_refund_binding_fence",
    }
    mixed_before_insert = (
        connection.execute(
            text(
                "SELECT tgname FROM pg_trigger "
                "WHERE tgrelid='public.pos_mixed_return_command'::regclass "
                "AND NOT tgisinternal AND (tgtype & 2)=2 AND (tgtype & 4)=4 "
                "AND tgname IN ('a10_mixed_return_guard',"
                "'a11_mixed_electronic_refund_binding_fence') ORDER BY tgname"
            )
        )
        .scalars()
        .all()
    )
    assert mixed_before_insert == [
        "a10_mixed_return_guard",
        "a11_mixed_electronic_refund_binding_fence",
    ]
    return [(row[0], row[1], row[2]) for row in rows]


def test_fence_upgrade_and_downgrade_share_exact_ordered_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = mixed_electronic_refund_preparation_migration()
    statements: list[str] = []
    monkeypatch.setattr(migration, "_sql", statements.append)
    migration.upgrade()
    creates = list(statements)
    statements.clear()
    migration.downgrade()
    expected = {
        "sale": "a01_mixed_electronic_refund_binding_fence",
        "pos_physical_return_command": "a01_mixed_electronic_refund_binding_fence",
        "pos_mixed_return_command": "a11_mixed_electronic_refund_binding_fence",
        "pos_refund_attempt": "a01_mixed_electronic_refund_binding_fence",
    }
    assert set(migration.FENCED) == set(expected)
    for table, trigger in expected.items():
        assert (
            sum(
                f"CREATE TRIGGER {trigger} BEFORE INSERT OR UPDATE OR DELETE" in statement
                and f"ON public.{table} FOR EACH ROW" in statement
                for statement in creates
            )
            == 1
        )
        assert statements.count(f"DROP TRIGGER {trigger} ON public.{table};") == 1
    assert "a10_mixed_return_guard" < expected["pos_mixed_return_command"]


def assert_preparation_catalog(
    connection: Connection, tables: tuple[str, ...], functions: tuple[str, ...]
) -> None:
    for table in tables:
        assert connection.scalar(
            text(
                "SELECT relrowsecurity AND relforcerowsecurity "
                "AND relowner='aurum_schema_owner'::regrole FROM pg_class "
                "WHERE oid=CAST(:table AS regclass)"
            ),
            {"table": f"public.{table}"},
        )
        for role, allowed in (
            ("aurum_app", {"SELECT", "INSERT"}),
            ("aurum_support", {"SELECT"}),
        ):
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
                    {"role": role, "table": f"public.{table}", "privilege": privilege},
                ) is (privilege in allowed)
        policies = connection.execute(
            text(
                "SELECT policyname,qual,with_check FROM pg_policies "
                "WHERE schemaname='public' AND tablename=:table ORDER BY policyname"
            ),
            {"table": table},
        ).all()
        assert [row.policyname for row in policies] == [
            "schema_owner",
            "support",
            "tenant_actor",
        ]
        assert "actor_id = current_app_user_id()" in policies[2].qual
        assert "pos.refund" in policies[2].qual and "pos.refund" in policies[2].with_check
    for signature in functions:
        definition = connection.scalar(
            text("SELECT pg_get_functiondef(CAST(:signature AS regprocedure))"),
            {"signature": f"public.{signature}"},
        )
        assert isinstance(definition, str)
        assert "SECURITY DEFINER" in definition
        assert "SET search_path TO 'pg_catalog', 'pg_temp'" in definition
        for role in ("aurum_app", "aurum_support", "public"):
            if role == "public":
                assert not connection.scalar(
                    text(
                        "SELECT EXISTS(SELECT 1 FROM pg_proc p, "
                        "LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a "
                        "WHERE p.oid=CAST(:signature AS regprocedure) "
                        "AND a.grantee=0 AND a.privilege_type='EXECUTE')"
                    ),
                    {"signature": f"public.{signature}"},
                )
            else:
                assert not connection.scalar(
                    text("SELECT has_function_privilege(:role,:signature,'EXECUTE')"),
                    {"role": role, "signature": f"public.{signature}"},
                )


async def test_preparation_roundtrip_preserves_exact_security_and_historical_guards(
    maintenance_engine: AsyncEngine,
) -> None:
    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        migration = mixed_electronic_refund_preparation_migration()
        security = text(
            "SELECT relname,relowner,relacl::text,relrowsecurity,relforcerowsecurity "
            "FROM pg_class WHERE relname=ANY(:tables) ORDER BY relname"
        )
        parameters = {"tables": list(migration.TABLES)}
        before_security = connection.execute(security, parameters).all()
        before_fences = fence_trigger_snapshot(connection)
        historical = text(
            "SELECT proname,pg_get_functiondef(oid),proowner,proacl::text,prosecdef "
            "FROM pg_proc WHERE pronamespace='public'::regnamespace "
            "AND proname IN ('trg_guard_sale_immutability','audit_redact_table_snapshot',"
            "'trg_unit_electronic_refund_binding_guard','trg_mixed_return_guard') "
            "ORDER BY proname,oid"
        )
        head_historical = connection.execute(historical).all()
        definitions = text(
            "SELECT pg_get_functiondef(CAST(:signature AS regprocedure)),"
            "proowner,proacl::text,prosecdef,proconfig FROM pg_proc "
            "WHERE oid=CAST(:signature AS regprocedure)"
        )
        head_functions = {
            signature: connection.execute(definitions, {"signature": f"public.{signature}"}).one()
            for signature in migration.FUNCTIONS
        }
        with Operations.context(MigrationContext.configure(connection)):
            if any(
                connection.scalar(text(f"SELECT EXISTS(SELECT 1 FROM public.{table})"))
                for table in migration.TABLES
            ):
                with pytest.raises(DBAPIError, match="downgrade_requires_empty_history") as error:
                    with connection.begin_nested():
                        migration.downgrade()
                assert getattr(error.value.orig, "sqlstate", None) == "55000"
                return
            if not downgrade_mixed_refund_evidence_for_roundtrip(connection):
                return
            before_historical = connection.execute(historical).all()
            before_functions = {
                signature: connection.execute(
                    definitions, {"signature": f"public.{signature}"}
                ).one()
                for signature in migration.FUNCTIONS
            }
            migration.downgrade()
            for table in migration.TABLES:
                assert (
                    connection.scalar(
                        text("SELECT to_regclass(:table)"), {"table": f"public.{table}"}
                    )
                    is None
                )
            assert connection.execute(historical).all() == before_historical
            assert connection.execute(FENCE_TRIGGERS).all() == []
            migration.upgrade()
            assert connection.execute(historical).all() == before_historical
            assert {
                signature: connection.execute(
                    definitions, {"signature": f"public.{signature}"}
                ).one()
                for signature in migration.FUNCTIONS
            } == before_functions
            mixed_electronic_refund_outcome_migration().upgrade()
            mixed_electronic_refund_posting_migration().upgrade()
        assert fence_trigger_snapshot(connection) == before_fences
        assert connection.execute(security, parameters).all() == before_security
        assert connection.execute(historical).all() == head_historical
        assert {
            signature: connection.execute(definitions, {"signature": f"public.{signature}"}).one()
            for signature in migration.FUNCTIONS
        } == head_functions
        assert_preparation_catalog(connection, migration.TABLES, migration.FUNCTIONS)

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(run_historical_rehearsal, rehearse)
        finally:
            await transaction.rollback()


@pytest.mark.parametrize("state", ["bound", "cancelled", "activated"])
async def test_nonempty_preparation_cannot_be_downgraded(
    db_session: AsyncSession, maintenance_engine: AsyncEngine, state: str
) -> None:
    request, _ = await persist_review(db_session)
    if state == "cancelled":
        await binding(db_session, request, "cancel")
    else:
        await binding(db_session, request, "bind")
        if state == "activated":
            await activate(db_session, request)
    await db_session.commit()

    def refuse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        migration = mixed_electronic_refund_preparation_migration()
        before = {
            table: connection.execute(
                text(f"SELECT to_jsonb(row) FROM public.{table} row ORDER BY tenant_id,id")
            )
            .scalars()
            .all()
            for table in migration.TABLES
        }
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(DBAPIError, match="downgrade_requires_empty_history") as error:
                with connection.begin_nested():
                    migration.downgrade()
            assert getattr(error.value.orig, "sqlstate", None) == "55000"
        assert {
            table: connection.execute(
                text(f"SELECT to_jsonb(row) FROM public.{table} row ORDER BY tenant_id,id")
            )
            .scalars()
            .all()
            for table in migration.TABLES
        } == before

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(refuse)
        finally:
            await transaction.rollback()
