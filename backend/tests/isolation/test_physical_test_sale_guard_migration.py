"""Training-sale guard roundtrip changes only two private function predicates."""

import importlib.util
from pathlib import Path
from types import ModuleType

from sqlalchemy import text
from sqlalchemy.engine import Connection, RowMapping
from sqlalchemy.ext.asyncio import AsyncEngine

from alembic.migration import MigrationContext
from alembic.operations import Operations

GUARDS = (
    (
        "public.assert_physical_checkout(uuid,uuid,boolean)",
        "   OR s.currency<>'TJS' OR s.status NOT IN ('draft','completed')",
    ),
    (
        "public.trg_physical_stock_checkout_binding()",
        " IF s.id IS NULL OR s.status<>'draft' OR s.sale_type<>'sale' OR s.currency<>'TJS'",
    ),
)
HISTORY_TABLES = (
    "sale",
    "sale_payment",
    "shift",
    "register",
    "pos_unit_cash_intent",
    "pos_physical_checkout",
    "unit_stock_entry_v2",
    "unit_stock_lot_v2",
    "unit_stock_opened_pack_v2",
    "unit_stock_cart_command_v2",
    "unit_stock_cart_line_v2",
    "unit_stock_movement_v2",
)
UNCHANGED_FUNCTIONS = (
    "public.trg_guard_sale_immutability()",
    "public.trg_unit_cart_payment_boundary()",
    "public.assert_mixed_checkout(uuid,uuid,boolean)",
    "public.trg_mixed_stock_checkout_binding()",
)


def migration() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "alembic/versions/0197_physical_test_sale_guard.py"
    spec = importlib.util.spec_from_file_location("physical_test_sale_guard_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def definitions(connection: Connection, signatures: tuple[str, ...]) -> dict[str, str]:
    result: dict[str, str] = {}
    for signature in signatures:
        definition = connection.scalar(
            text("SELECT pg_get_functiondef(CAST(:signature AS regprocedure))"),
            {"signature": signature},
        )
        assert isinstance(definition, str)
        result[signature] = definition
    return result


def function_metadata(connection: Connection) -> list[RowMapping]:
    return list(
        connection.execute(
            text("""
                SELECT oid,pg_get_userbyid(proowner) AS owner,proacl::TEXT,
                  prosecdef,proconfig,provolatile::TEXT,
                  has_function_privilege('aurum_app',oid,'EXECUTE') AS app_execute,
                  has_function_privilege('aurum_support',oid,'EXECUTE') AS support_execute,
                  EXISTS(SELECT 1 FROM aclexplode(proacl) acl
                    WHERE acl.grantee=0) AS public_execute
                FROM pg_proc WHERE oid IN (
                  to_regprocedure(:assertion),to_regprocedure(:binding)) ORDER BY oid
            """),
            {"assertion": GUARDS[0][0], "binding": GUARDS[1][0]},
        ).mappings()
    )


def history_fingerprints(connection: Connection) -> dict[str, tuple[int, str]]:
    # Count plus a commutative fingerprint detects row mutations without copying
    # receipts or procurement facts into assertion output or sorting full history.
    result: dict[str, tuple[int, str]] = {}
    for table in HISTORY_TABLES:
        count, fingerprint = connection.execute(
            text(
                "SELECT count(*),coalesce(sum("
                "hashtextextended(to_jsonb(row)::TEXT,0)::NUMERIC),0)::TEXT "
                f"FROM public.{table} row"
            )
        ).one()
        result[table] = (int(count), str(fingerprint))
    return result


async def test_guard_roundtrip_preserves_private_metadata_mixed_guards_and_history(
    maintenance_engine: AsyncEngine,
) -> None:
    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        assert connection.scalar(text("SELECT current_user")) == "aurum_schema_owner"
        module = migration()
        assert module.GUARDS == GUARDS
        signatures = tuple(signature for signature, _anchor in GUARDS)
        before = definitions(connection, signatures)
        metadata = function_metadata(connection)
        assert len(metadata) == 2
        assert {row["prosecdef"] for row in metadata} == {False, True}
        for row in metadata:
            assert row["owner"] == "aurum_schema_owner"
            assert row["proconfig"] == ["search_path=pg_catalog, pg_temp"]
            assert not row["app_execute"] and not row["support_execute"]
            assert not row["public_execute"]
        original: dict[str, str] = {}
        for signature, anchor in GUARDS:
            replacement = anchor + " OR s.is_test"
            assert before[signature].count(replacement) == 1
            original[signature] = before[signature].replace(replacement, anchor, 1)
        untouched = definitions(connection, UNCHANGED_FUNCTIONS)
        history = history_fingerprints(connection)
        tables = text("""
            SELECT relname,relacl::TEXT,relrowsecurity,relforcerowsecurity
            FROM pg_class WHERE relnamespace='public'::regnamespace
              AND relname=ANY(:tables) ORDER BY relname
        """)
        table_metadata = connection.execute(tables, {"tables": list(HISTORY_TABLES)}).all()
        with Operations.context(MigrationContext.configure(connection)):
            module.downgrade()
            assert definitions(connection, signatures) == original
            assert function_metadata(connection) == metadata
            assert definitions(connection, UNCHANGED_FUNCTIONS) == untouched
            assert history_fingerprints(connection) == history
            module.upgrade()
        assert definitions(connection, signatures) == before
        assert function_metadata(connection) == metadata
        assert definitions(connection, UNCHANGED_FUNCTIONS) == untouched
        assert history_fingerprints(connection) == history
        assert connection.execute(tables, {"tables": list(HISTORY_TABLES)}).all() == table_metadata

    # Never exercise earlier checkout migrations or commit a guard downgrade.
    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()
