"""Mixed checkout storage boundaries and reversible DDL in the disposable test DB."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from alembic.migration import MigrationContext
from alembic.operations import Operations
from tests.isolation.test_physical_cash_checkout_migration import (
    downgrade_electronic_for_roundtrip,
    mixed_cash_refund_migration,
    mixed_quarantine_disposition_migration,
    mixed_quarantine_read_migration,
    mixed_return_migration,
    mixed_zero_return_migration,
    payment_component_migration,
    physical_disposition_migration,
    refund_payment_component_migration,
    upgrade_electronic_after_roundtrip,
)

FUNCTIONS = text(
    "SELECT proname,pg_get_functiondef(oid),proowner,proacl,prosecdef,proconfig FROM pg_proc "
    "WHERE pronamespace='public'::regnamespace AND (proname LIKE '%mixed%checkout%' "
    "OR proname IN ('trg_unit_cart_payment_boundary','trg_guard_sale_immutability',"
    "'assert_physical_checkout','trg_physical_checkout_guard','trg_unit_cart_v2_guard',"
    "'trg_mixed_return_guard','trg_mixed_return_complete','audit_redact_table_snapshot')) "
    "ORDER BY proname"
)
RELATION = text(
    "SELECT relowner,relacl,relrowsecurity,relforcerowsecurity FROM pg_class "
    "WHERE oid='public.pos_mixed_checkout'::regclass"
)
POLICIES = text(
    "SELECT policyname,roles,cmd,qual,with_check FROM pg_policies "
    "WHERE schemaname='public' AND tablename='pos_mixed_checkout' ORDER BY policyname"
)


def migration() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "alembic/versions/0188_mixed_cash_checkout.py"
    spec = importlib.util.spec_from_file_location("mixed_cash_checkout_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_mixed_marker_forces_rls_and_grants_only_append_and_read(
    maintenance_engine: AsyncEngine,
) -> None:
    async with maintenance_engine.connect() as connection:
        assert str(await connection.scalar(text("SELECT current_database()"))).endswith("_test")
        relation = (
            await connection.execute(
                text(
                    "SELECT pg_get_userbyid(relowner) AS owner,relrowsecurity,relforcerowsecurity "
                    "FROM pg_class WHERE oid='public.pos_mixed_checkout'::regclass"
                )
            )
        ).one()
        assert relation.owner == "aurum_schema_owner"
        assert relation.relrowsecurity and relation.relforcerowsecurity
        privileges = (
            await connection.execute(
                text(
                    "SELECT role,privilege,has_table_privilege(role,'public.pos_mixed_checkout',"
                    "privilege) AS allowed FROM unnest(ARRAY['aurum_app','aurum_support']) role "
                    "CROSS JOIN unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE',"
                    "'REFERENCES','TRIGGER']) privilege ORDER BY role,privilege"
                )
            )
        ).all()
        allowed = {(row.role, row.privilege) for row in privileges if row.allowed}
        assert allowed == {
            ("aurum_app", "SELECT"),
            ("aurum_app", "INSERT"),
            ("aurum_support", "SELECT"),
        }
        assert not await connection.scalar(
            text(
                "SELECT EXISTS(SELECT 1 FROM pg_class c,"
                "LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) acl "
                "WHERE c.oid='public.pos_mixed_checkout'::regclass AND acl.grantee=0)"
            )
        )


async def test_app_policy_binds_tenant_cashier_and_branch_permission(
    maintenance_engine: AsyncEngine,
) -> None:
    async with maintenance_engine.connect() as connection:
        policies = (await connection.execute(POLICIES)).all()
    assert {row.policyname for row in policies} == {
        "schema_owner",
        "support",
        "tenant_actor",
        "mixed_return_read",
    }
    app = next(row for row in policies if row.policyname == "tenant_actor")
    assert app.roles == ["aurum_app"]
    assert app.cmd == "ALL"
    for expression in (app.qual, app.with_check):
        assert "tenant_id = current_tenant_id()" in expression
        assert "cashier_user_id = current_app_user_id()" in expression
        assert "tenant_actor_has_scoped_permission" in expression
        assert "pos.sell" in expression and "branch_id" in expression
    support = next(row for row in policies if row.policyname == "support")
    assert support.roles == ["aurum_support"]
    assert support.cmd == "SELECT" and support.qual == "true"
    assert support.with_check is None
    refund = next(row for row in policies if row.policyname == "mixed_return_read")
    assert refund.roles == ["aurum_app"] and refund.cmd == "SELECT"
    assert refund.with_check is None
    assert "tenant_id = current_tenant_id()" in refund.qual
    assert "tenant_actor_has_scoped_permission" in refund.qual
    assert "pos.refund" in refund.qual and "branch_id" in refund.qual


async def test_mixed_guard_functions_keep_owner_safe_search_path_and_private_acl(
    maintenance_engine: AsyncEngine,
) -> None:
    async with maintenance_engine.connect() as connection:
        functions = (
            await connection.execute(
                text(
                    "SELECT p.proname,pg_get_userbyid(p.proowner) AS owner,p.prosecdef,p.proconfig,"
                    "has_function_privilege('aurum_app',p.oid,'EXECUTE') AS app_execute,"
                    "has_function_privilege('aurum_support',p.oid,'EXECUTE') AS support_execute,"
                    "EXISTS(SELECT 1 FROM aclexplode("
                    "COALESCE(p.proacl,acldefault('f',p.proowner))) "
                    "a WHERE a.grantee=0) AS public_execute FROM pg_proc p "
                    "WHERE p.pronamespace='public'::regnamespace AND p.proname IN "
                    "('assert_mixed_checkout','trg_mixed_stock_checkout_binding',"
                    "'trg_mixed_checkout_guard','trg_mixed_checkout_complete',"
                    "'trg_mixed_checkout_cart_frozen','trg_mixed_checkout_movement_frozen',"
                    "'trg_mixed_checkout_return_boundary') ORDER BY p.proname"
                )
            )
        ).all()
    assert len(functions) == 7
    definers = {
        "assert_mixed_checkout",
        "trg_mixed_checkout_complete",
        "trg_mixed_checkout_cart_frozen",
        "trg_mixed_checkout_movement_frozen",
        "trg_mixed_checkout_return_boundary",
        "trg_mixed_stock_checkout_binding",
    }
    for function in functions:
        assert function.owner == "aurum_schema_owner"
        assert function.prosecdef == (function.proname in definers)
        assert function.proconfig == ["search_path=pg_catalog, pg_temp"]
        assert not function.app_execute
        assert not function.support_execute
        assert not function.public_execute


async def test_mixed_completion_constraints_cover_marker_and_both_ledgers(
    maintenance_engine: AsyncEngine,
) -> None:
    async with maintenance_engine.connect() as connection:
        triggers = (
            await connection.execute(
                text(
                    "SELECT t.tgname,c.relname,t.tgdeferrable,t.tginitdeferred "
                    "FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
                    "WHERE t.tgname IN ('mixed_checkout_complete','mixed_stock_checkout_complete',"
                    "'mixed_legacy_checkout_complete') ORDER BY t.tgname"
                )
            )
        ).all()
    assert {(row.tgname, row.relname) for row in triggers} == {
        ("mixed_checkout_complete", "pos_mixed_checkout"),
        ("mixed_stock_checkout_complete", "unit_stock_cart_command_v2"),
        ("mixed_legacy_checkout_complete", "batch_movement"),
    }
    assert all(row.tgdeferrable and row.tginitdeferred for row in triggers)


def _downgrade_payment_journal_dependencies() -> None:
    downgrade_electronic_for_roundtrip()
    refund_payment_component_migration().downgrade()
    payment_component_migration().downgrade()


def _upgrade_payment_journal_dependencies() -> None:
    payment_component_migration().upgrade()
    refund_payment_component_migration().upgrade()
    upgrade_electronic_after_roundtrip()


async def test_disposable_roundtrip_restores_rows_guards_policies_and_acl_or_refuses_history(
    maintenance_engine: AsyncEngine,
) -> None:
    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        with Operations.context(MigrationContext.configure(connection)):
            functions = connection.execute(FUNCTIONS).all()
            relation = connection.execute(RELATION).one()
            policies = connection.execute(POLICIES).all()
            stock = text(
                "SELECT to_jsonb(row) FROM unit_stock_cart_command_v2 row ORDER BY tenant_id,id"
            )
            original = connection.execute(stock).scalars().all()
            if connection.scalar(
                text(
                    "SELECT EXISTS(SELECT 1 FROM pos_unit_payment_attempt_binding) OR "
                    "EXISTS(SELECT 1 FROM pos_mixed_checkout) OR EXISTS(SELECT 1 "
                    "FROM unit_stock_cart_command_v2 WHERE mixed_checkout_sale_id IS NOT NULL) OR "
                    "EXISTS(SELECT 1 FROM pos_physical_checkout "
                    "WHERE payment_journal_version>=2) OR "
                    "EXISTS(SELECT 1 FROM pos_physical_cash_refund "
                    "WHERE payment_journal_version=2) OR "
                    "EXISTS(SELECT 1 FROM pos_mixed_cash_refund "
                    "WHERE payment_journal_version=2)"
                )
            ):
                with pytest.raises(DBAPIError) as error:
                    with connection.begin_nested():
                        _downgrade_payment_journal_dependencies()
                        migration().downgrade()
                assert getattr(error.value.orig, "sqlstate", None) == "55000"
            else:
                _downgrade_payment_journal_dependencies()
                # Keep physical history; suspend only the later audit insertion.
                physical = physical_disposition_migration()
                physical._redactor(physical.AUDIT_NEW, physical.AUDIT_OLD)
                mixed_quarantine_disposition_migration().downgrade()
                mixed_quarantine_read_migration().downgrade()
                mixed_zero_return_migration().downgrade()
                mixed_cash_refund_migration().downgrade()
                mixed_return_migration().downgrade()
                migration().downgrade()
                assert connection.scalar(text("SELECT to_regclass('pos_mixed_checkout')")) is None
                assert not connection.scalar(
                    text(
                        "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
                        "WHERE table_schema='public' AND table_name='unit_stock_cart_command_v2' "
                        "AND column_name='mixed_checkout_sale_id')"
                    )
                )
                migration().upgrade()
                mixed_return_migration().upgrade()
                mixed_cash_refund_migration().upgrade()
                mixed_zero_return_migration().upgrade()
                mixed_quarantine_read_migration().upgrade()
                mixed_quarantine_disposition_migration().upgrade()
                physical._redactor(physical.AUDIT_OLD, physical.AUDIT_NEW)
                _upgrade_payment_journal_dependencies()
            assert connection.execute(FUNCTIONS).all() == functions
            assert connection.execute(RELATION).one() == relation
            assert connection.execute(POLICIES).all() == policies
            assert connection.execute(stock).scalars().all() == original

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()
