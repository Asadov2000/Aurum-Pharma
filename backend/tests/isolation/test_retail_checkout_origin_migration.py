"""Origin schema ownership, closed write boundary and reversible empty-history migration."""

import importlib.util
import re
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from alembic.migration import MigrationContext
from alembic.operations import Operations
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import set_receiving_context
from tests.domains.pos.test_retail_checkout_origin_integration import finish, prepare, read_origin
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture
TABLE = "pos_retail_checkout_origin"
UNCHANGED = (
    "public.assert_current_branch_writer(uuid,uuid,uuid,boolean)",
    "public.assert_physical_checkout(uuid,uuid,boolean)",
    "public.assert_mixed_checkout(uuid,uuid,boolean)",
    "public.trg_guard_sale_immutability()",
)


def migration() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "alembic/versions/0225_retail_checkout_origin.py"
    spec = importlib.util.spec_from_file_location("retail_origin_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_origin_migration_has_no_backfill_and_keeps_the_canonical_writer_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = migration()
    statements: list[str] = []
    monkeypatch.setattr(module, "_sql", statements.append)
    module.upgrade()
    sql = "\n".join(statements)
    assert module.revision == "0225" and module.down_revision == "0224"
    assert module.SOURCE_TABLES == ("pos_physical_checkout", "pos_mixed_checkout")
    assert sql.count("CREATE TRIGGER z80_retail_checkout_origin AFTER INSERT") == 2
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "GRANT SELECT ON public.pos_retail_checkout_origin TO aurum_app,aurum_support" in sql
    for statement in statements:
        if statement != module.SQL:
            assert (
                re.search(r"INSERT\s+INTO\s+public\.pos_retail_checkout_origin", statement, re.I)
                is None
            )
    capture = re.sub(r"\s+", "", module.SQL)
    assert "IFSESSION_USER<>'aurum_app'THENRETURNNEW;ENDIF;" in capture
    assert "IFsale_row.is_testTHENRETURNNEW;ENDIF;" in capture
    assert (
        "public.assert_current_branch_writer(NEW.tenant_id,NEW.branch_id,NEW.register_id,false)"
        in capture
    )
    assert "FORSHAREOFstream;" in capture
    assert "epoch.capability='cloud_full'" in capture
    assert "node.node_kind='cloud'" in capture
    assert "pg_trigger_depth()<>2" in capture
    assert "UPDATEpublic.sync_stream" not in capture
    assert "INSERTINTOpublic.sync_outbox" not in capture
    assert "result_payload JSONB" not in sql and "receipt_payload JSONB" not in sql
    statements.clear()
    module.downgrade()
    drops = "\n".join(statements)
    assert drops.index("downgrade_requires_empty_history") < drops.index("DROP TRIGGER")
    assert drops.index("DROP FUNCTION") < drops.index("DROP TABLE")


async def test_origin_relations_and_functions_have_narrow_private_privileges(
    maintenance_engine: AsyncEngine,
) -> None:
    async with maintenance_engine.connect() as connection:
        owner, rls, forced = (await connection.execute(text("""
                    SELECT pg_get_userbyid(relowner),relrowsecurity,relforcerowsecurity
                    FROM pg_class WHERE oid='public.pos_retail_checkout_origin'::regclass
                """))).one()
        assert (owner, rls, forced) == ("aurum_schema_owner", True, True)
        roles = (
            "aurum_app",
            "aurum_support",
            "aurum_worker",
            "aurum_catalog_worker",
            "aurum_billing_worker",
            "aurum_mailer",
            "aurum_edge_cash_owner",
            "aurum_edge_cash_executor",
        )
        for role in roles:
            for privilege in (
                "SELECT",
                "INSERT",
                "UPDATE",
                "DELETE",
                "TRUNCATE",
                "REFERENCES",
                "TRIGGER",
            ):
                granted = await connection.scalar(
                    text(
                        "SELECT has_table_privilege("
                        ":role,'public.pos_retail_checkout_origin',:privilege)"
                    ),
                    {"role": role, "privilege": privilege},
                )
                assert granted is (role in {"aurum_app", "aurum_support"} and privilege == "SELECT")
        for signature in migration().FUNCTIONS:
            metadata = (
                await connection.execute(
                    text("""
                        SELECT pg_get_userbyid(proowner),prosecdef,proconfig,
                          EXISTS(SELECT 1 FROM aclexplode(proacl) WHERE grantee=0)
                        FROM pg_proc WHERE oid=CAST(:signature AS regprocedure)
                    """),
                    {"signature": f"public.{signature}"},
                )
            ).one()
            assert metadata == (
                "aurum_schema_owner",
                True,
                ["search_path=pg_catalog, pg_temp"],
                False,
            )
            for role in roles:
                assert (
                    await connection.scalar(
                        text("SELECT has_function_privilege(:role,:signature,'EXECUTE')"),
                        {"role": role, "signature": f"public.{signature}"},
                    )
                    is False
                )
        policies = (await connection.execute(text("""
                    SELECT qual FROM pg_policies WHERE schemaname='public'
                      AND tablename='pos_retail_checkout_origin' AND policyname='tenant_actor'
                """))).scalar_one()
        assert "current_tenant_id" in policies and "current_app_user_id" in policies
        assert "cashier_user_id" in policies and "branch_id" in policies
        assert "is_tenant_support_session" in policies and "is_support_session()" not in policies


async def test_empty_migration_roundtrip_or_history_refusal_preserves_existing_guards(
    maintenance_engine: AsyncEngine,
) -> None:
    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        assert connection.scalar(text("SELECT current_user")) == "aurum_schema_owner"
        module = migration()
        definition = text("""
            SELECT pg_get_functiondef(oid),proowner,proacl::TEXT,prosecdef,proconfig
            FROM pg_proc WHERE oid=CAST(:signature AS regprocedure)
        """)
        old = {
            signature: connection.execute(definition, {"signature": signature}).one()
            for signature in UNCHANGED
        }
        security = text("""
            SELECT relowner,relacl::TEXT,relrowsecurity,relforcerowsecurity
            FROM pg_class WHERE oid='public.pos_retail_checkout_origin'::regclass
        """)
        before_security = connection.execute(security).one()
        before_functions = {
            signature: connection.execute(definition, {"signature": f"public.{signature}"}).one()
            for signature in module.FUNCTIONS
        }
        with Operations.context(MigrationContext.configure(connection)):
            if connection.scalar(
                text("SELECT EXISTS(SELECT 1 FROM public.pos_retail_checkout_origin)")
            ):
                with pytest.raises(DBAPIError, match="downgrade_requires_empty_history") as denied:
                    with connection.begin_nested():
                        module.downgrade()
                assert getattr(denied.value.orig, "sqlstate", None) == "55000"
            else:
                module.downgrade()
                assert (
                    connection.scalar(
                        text("SELECT to_regclass('public.pos_retail_checkout_origin')")
                    )
                    is None
                )
                module.upgrade()
        assert {
            signature: connection.execute(definition, {"signature": signature}).one()
            for signature in UNCHANGED
        } == old
        assert connection.execute(security).one() == before_security
        for signature, metadata in before_functions.items():
            assert (
                connection.execute(definition, {"signature": f"public.{signature}"}).one()
                == metadata
            )

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()


async def test_committed_origin_denies_owner_mutation_and_destructive_downgrade(
    db_session: AsyncSession, maintenance_engine: AsyncEngine
) -> None:
    result = await finish(db_session, await prepare(db_session, "physical-cash"))
    saved = await read_origin(db_session, result)
    context = runtime(db_session)
    assert str(await db_session.scalar(text("SELECT current_database()"))).endswith("_test")
    # This committed synthetic record tests history protection only in CI's
    # disposable database; no shared database is contacted by these fixtures.
    await db_session.commit()

    def refuse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        for statement in (
            "UPDATE pos_retail_checkout_origin SET writer_epoch=writer_epoch+1 WHERE id=:id",
            "DELETE FROM pos_retail_checkout_origin WHERE id=:id",
        ):
            with pytest.raises(DBAPIError, match="retail_checkout_origin_immutable") as denied:
                with connection.begin_nested():
                    connection.execute(text(statement), {"id": result.operation_id})
            assert getattr(denied.value.orig, "sqlstate", None) == "23514"
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(DBAPIError, match="downgrade_requires_empty_history") as denied:
                with connection.begin_nested():
                    migration().downgrade()
            assert getattr(denied.value.orig, "sqlstate", None) == "55000"
        assert (
            connection.scalar(
                text("SELECT count(*) FROM pos_retail_checkout_origin WHERE id=:id"),
                {"id": result.operation_id},
            )
            == 1
        )

    async with maintenance_engine.begin() as connection:
        await connection.run_sync(refuse)
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await read_origin(db_session, result) == saved
