"""Cancellation migration owns only its new journal and checkout fences."""

import importlib.util
from pathlib import Path
from types import ModuleType

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from alembic.migration import MigrationContext
from alembic.operations import Operations

TABLE = "pos_physical_cash_cancellation"
FUNCTIONS = ("trg_physical_cash_cancellation_guard", "trg_cancelled_cash_checkout_fence")


def migration() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[2] / "alembic/versions/0237_physical_cash_cancellation.py"
    )
    spec = importlib.util.spec_from_file_location("physical_cash_cancellation_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def existing_security(connection: Connection) -> tuple[tuple[object, ...], ...]:
    queries = (
        (
            "routine",
            """
          SELECT oid,pg_get_functiondef(oid),proowner,proacl,prosecdef,proconfig
          FROM pg_proc WHERE pronamespace='public'::regnamespace AND prokind='f'
            AND proname NOT IN ('trg_physical_cash_cancellation_guard',
              'trg_cancelled_cash_checkout_fence') ORDER BY oid
        """,
        ),
        (
            "relation",
            """
          SELECT oid,relowner,relacl,relrowsecurity,relforcerowsecurity FROM pg_class
          WHERE relnamespace='public'::regnamespace AND relkind IN ('r','p','v')
            AND relname<>'pos_physical_cash_cancellation' ORDER BY oid
        """,
        ),
        (
            "column",
            """
          SELECT attrelid,attnum,attname,attacl FROM pg_attribute
          WHERE attrelid IN (SELECT oid FROM pg_class
            WHERE relnamespace='public'::regnamespace AND relkind IN ('r','p','v')
              AND relname<>'pos_physical_cash_cancellation')
            AND attnum>0 AND NOT attisdropped ORDER BY attrelid,attnum
        """,
        ),
        (
            "policy",
            """
          SELECT polrelid,polname,polroles,polcmd::TEXT,polpermissive,
            pg_get_expr(polqual,polrelid),pg_get_expr(polwithcheck,polrelid)
          FROM pg_policy WHERE polrelid IN (SELECT oid FROM pg_class
            WHERE relnamespace='public'::regnamespace
              AND relname<>'pos_physical_cash_cancellation') ORDER BY polrelid,polname
        """,
        ),
        (
            "trigger",
            """
          SELECT tgrelid,tgname,tgfoid,tgtype,tgenabled,tgdeferrable,tginitdeferred,tgargs
          FROM pg_trigger WHERE NOT tgisinternal AND tgrelid IN (SELECT oid FROM pg_class
            WHERE relnamespace='public'::regnamespace
              AND relname<>'pos_physical_cash_cancellation')
            AND tgname<>'a05_cancelled_cash_checkout_fence' ORDER BY tgrelid,tgname
        """,
        ),
    )
    return tuple((kind, *row) for kind, query in queries for row in connection.execute(text(query)))


async def test_empty_cash_cancellation_roundtrip_preserves_existing_security(
    maintenance_engine: AsyncEngine,
) -> None:
    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        # API tests keep cancellation rows in rollback-only transactions. Never
        # erase history to manufacture a downgrade rehearsal.
        assert not connection.scalar(text(f"SELECT EXISTS(SELECT 1 FROM {TABLE})"))
        before = existing_security(connection)
        with Operations.context(MigrationContext.configure(connection)):
            migration().downgrade()
            assert connection.scalar(text(f"SELECT to_regclass('{TABLE}')")) is None
            migration().upgrade()
        assert existing_security(connection) == before

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()


async def test_cash_cancellation_rls_audit_and_definer_fences_are_narrow(
    maintenance_engine: AsyncEngine,
) -> None:
    async with maintenance_engine.connect() as connection:
        row = (await connection.execute(text("""
          SELECT relowner::regrole::TEXT,relrowsecurity,relforcerowsecurity
          FROM pg_class WHERE oid='public.pos_physical_cash_cancellation'::regclass
        """))).one()
        assert tuple(row) == ("aurum_schema_owner", True, True)
        grants = (await connection.execute(text("""
          SELECT has_table_privilege('aurum_app','pos_physical_cash_cancellation','SELECT'),
            has_table_privilege('aurum_app','pos_physical_cash_cancellation','INSERT'),
            has_table_privilege('aurum_app','pos_physical_cash_cancellation','UPDATE'),
            has_table_privilege('aurum_app','pos_physical_cash_cancellation','DELETE'),
            has_table_privilege('aurum_support','pos_physical_cash_cancellation','SELECT'),
            has_table_privilege('aurum_support','pos_physical_cash_cancellation','INSERT'),
            has_table_privilege('aurum_edge_cash_owner','pos_physical_cash_cancellation','SELECT')
        """))).one()
        assert tuple(grants) == (True, True, False, False, True, False, False)
        for function in FUNCTIONS:
            details = (
                await connection.execute(
                    text("""
              SELECT proowner::regrole::TEXT,prosecdef,proconfig,
                has_function_privilege('aurum_app',oid,'EXECUTE'),
                has_function_privilege('aurum_support',oid,'EXECUTE')
              FROM pg_proc WHERE oid=CAST(:function AS regprocedure)
            """),
                    {"function": f"public.{function}()"},
                )
            ).one()
            assert tuple(details) == (
                "aurum_schema_owner",
                True,
                ["search_path=pg_catalog, pg_temp"],
                False,
                False,
            )
        triggers = list((await connection.execute(text("""
          SELECT c.relname,t.tgname,t.tgtype,p.proname FROM pg_trigger t
          JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_proc p ON p.oid=t.tgfoid
          WHERE NOT t.tgisinternal AND (
            c.oid='public.pos_physical_cash_cancellation'::regclass
            OR t.tgname='a05_cancelled_cash_checkout_fence') ORDER BY c.relname,t.tgname
        """))).all())
        assert [(row.relname, row.tgname) for row in triggers] == [
            ("pos_mixed_checkout", "a05_cancelled_cash_checkout_fence"),
            ("pos_physical_cash_cancellation", "a00_tenant_access"),
            ("pos_physical_cash_cancellation", "a10_physical_cash_cancellation_guard"),
            ("pos_physical_cash_cancellation", "z90_audit"),
            ("pos_physical_checkout", "a05_cancelled_cash_checkout_fence"),
        ]
        assert [row.tgtype for row in triggers] == [7, 31, 31, 29, 7]
        assert [row.proname for row in triggers] == [
            "trg_cancelled_cash_checkout_fence",
            "trg_enforce_tenant_access",
            "trg_physical_cash_cancellation_guard",
            "trg_audit_log",
            "trg_cancelled_cash_checkout_fence",
        ]
