"""Cash binding rollback preserves old guards and never discards checkout history."""

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


def migration() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / ("alembic/versions/0181_physical_cash_checkout.py")
    spec = importlib.util.spec_from_file_location("physical_cash_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def compatibility_migration() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / (
        "alembic/versions/0182_physical_cash_edge_compatibility.py"
    )
    spec = importlib.util.spec_from_file_location("physical_cash_edge_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def return_journal_migration() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / (
        "alembic/versions/0183_physical_return_journal.py"
    )
    spec = importlib.util.spec_from_file_location("physical_return_journal_dependency", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def downgrade_cash_for_roundtrip() -> None:
    physical_training_guard_migration().downgrade()
    downgrade_refund_for_roundtrip()
    return_journal_migration().downgrade()
    compatibility_migration().downgrade()
    migration().downgrade()


def upgrade_cash_after_roundtrip() -> None:
    migration().upgrade()
    compatibility_migration().upgrade()
    return_journal_migration().upgrade()
    upgrade_refund_after_roundtrip()
    physical_training_guard_migration().upgrade()


def cash_refund_migration() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / ("alembic/versions/0184_physical_cash_refund.py")
    spec = importlib.util.spec_from_file_location("physical_cash_refund_dependency", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cash_refund_policy_migration() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / (
        "alembic/versions/0185_physical_cash_refund_policy.py"
    )
    spec = importlib.util.spec_from_file_location("physical_cash_refund_policy_dependency", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def payment_component_migration() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / (
        "alembic/versions/0210_unit_checkout_payment_components.py"
    )
    spec = importlib.util.spec_from_file_location("unit_checkout_payment_components", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def refund_payment_component_migration() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / (
        "alembic/versions/0211_unit_refund_payment_components.py"
    )
    spec = importlib.util.spec_from_file_location("unit_refund_payment_components", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def electronic_checkout_migration() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / (
        "alembic/versions/0212_unit_electronic_checkout.py"
    )
    spec = importlib.util.spec_from_file_location("unit_electronic_checkout", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def electronic_refund_read_migration() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / (
        "alembic/versions/0213_unit_electronic_refund_read.py"
    )
    spec = importlib.util.spec_from_file_location("unit_electronic_refund_read", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def electronic_refund_intent_migration() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / (
        "alembic/versions/0214_unit_electronic_refund_intents.py"
    )
    spec = importlib.util.spec_from_file_location("unit_electronic_refund_intents", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def electronic_refund_binding_migration() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / (
        "alembic/versions/0215_unit_electronic_refund_binding.py"
    )
    spec = importlib.util.spec_from_file_location("unit_electronic_refund_binding", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def electronic_refund_activation_migration() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / (
        "alembic/versions/0216_unit_electronic_refund_activation.py"
    )
    spec = importlib.util.spec_from_file_location("unit_electronic_refund_activation", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def electronic_refund_outcome_migration() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / (
        "alembic/versions/0217_unit_electronic_refund_outcome.py"
    )
    spec = importlib.util.spec_from_file_location("unit_electronic_refund_outcome", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def electronic_refund_posting_migration() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / (
        "alembic/versions/0218_unit_electronic_refund_posting.py"
    )
    spec = importlib.util.spec_from_file_location("unit_electronic_refund_posting", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def mixed_electronic_checkout_migration() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / (
        "alembic/versions/0219_mixed_electronic_checkout.py"
    )
    spec = importlib.util.spec_from_file_location("mixed_electronic_checkout", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def downgrade_electronic_for_roundtrip() -> None:
    # Unwind later FK dependencies and audit patches before historical function text.
    mixed_electronic_checkout_migration().downgrade()
    electronic_refund_posting_migration().downgrade()
    electronic_refund_outcome_migration().downgrade()
    electronic_refund_activation_migration().downgrade()
    electronic_refund_binding_migration().downgrade()
    electronic_refund_intent_migration().downgrade()
    electronic_refund_read_migration().downgrade()
    electronic_checkout_migration().downgrade()


def upgrade_electronic_after_roundtrip() -> None:
    electronic_checkout_migration().upgrade()
    electronic_refund_read_migration().upgrade()
    electronic_refund_intent_migration().upgrade()
    electronic_refund_binding_migration().upgrade()
    electronic_refund_activation_migration().upgrade()
    electronic_refund_outcome_migration().upgrade()
    electronic_refund_posting_migration().upgrade()
    mixed_electronic_checkout_migration().upgrade()


def downgrade_refund_for_roundtrip() -> None:
    downgrade_electronic_for_roundtrip()
    refund_payment_component_migration().downgrade()
    payment_component_migration().downgrade()
    physical_disposition_migration().downgrade()
    mixed_quarantine_disposition_migration().downgrade()
    mixed_quarantine_read_migration().downgrade()
    mixed_zero_return_migration().downgrade()
    mixed_cash_refund_migration().downgrade()
    mixed_return_migration().downgrade()
    mixed_checkout_migration().downgrade()
    zero_return_migration().downgrade()
    cash_refund_policy_migration().downgrade()
    cash_refund_migration().downgrade()


def upgrade_refund_after_roundtrip() -> None:
    cash_refund_migration().upgrade()
    cash_refund_policy_migration().upgrade()
    zero_return_migration().upgrade()
    mixed_checkout_migration().upgrade()
    mixed_return_migration().upgrade()
    mixed_cash_refund_migration().upgrade()
    mixed_zero_return_migration().upgrade()
    mixed_quarantine_read_migration().upgrade()
    mixed_quarantine_disposition_migration().upgrade()
    physical_disposition_migration().upgrade()
    payment_component_migration().upgrade()
    refund_payment_component_migration().upgrade()
    upgrade_electronic_after_roundtrip()


def physical_disposition_migration() -> ModuleType:
    directory = Path(__file__).resolve().parents[2] / "alembic/versions"
    spec = importlib.util.spec_from_file_location(
        "physical_disposition", directory / "0198_physical_quarantine_disposition.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def physical_training_guard_migration() -> ModuleType:
    directory = Path(__file__).resolve().parents[2] / "alembic/versions"
    spec = importlib.util.spec_from_file_location(
        "physical_training_guard", directory / "0197_physical_test_sale_guard.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def mixed_quarantine_disposition_migration() -> ModuleType:
    directory = Path(__file__).resolve().parents[2] / "alembic/versions"
    path = directory / "0193_mixed_quarantine_disposition.py"
    spec = importlib.util.spec_from_file_location("mixed_quarantine_disposition_dependency", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def mixed_quarantine_read_migration() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "alembic/versions/0192_mixed_quarantine_read.py"
    spec = importlib.util.spec_from_file_location("mixed_quarantine_read_dependency", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def mixed_zero_return_migration() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "alembic/versions/0191_mixed_zero_return.py"
    spec = importlib.util.spec_from_file_location("mixed_zero_return_dependency", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def mixed_cash_refund_migration() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "alembic/versions/0190_mixed_cash_refund.py"
    spec = importlib.util.spec_from_file_location("mixed_cash_refund_dependency", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def mixed_return_migration() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "alembic/versions/0189_mixed_return_journal.py"
    spec = importlib.util.spec_from_file_location("mixed_return_journal_dependency", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def mixed_checkout_migration() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "alembic/versions/0188_mixed_cash_checkout.py"
    spec = importlib.util.spec_from_file_location("mixed_cash_checkout_dependency", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def zero_return_migration() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "alembic/versions/0186_physical_zero_return.py"
    spec = importlib.util.spec_from_file_location("physical_zero_return_dependency", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_cash_binding_roundtrip_preserves_stock_and_guard_acl(
    maintenance_engine: AsyncEngine,
) -> None:
    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        with Operations.context(MigrationContext.configure(connection)):
            history = connection.scalar(
                text(
                    "SELECT EXISTS(SELECT 1 FROM pos_unit_payment_attempt_binding) OR "
                    "EXISTS(SELECT 1 FROM pos_physical_return_command) OR "
                    "EXISTS(SELECT 1 FROM pos_mixed_zero_return) OR "
                    "EXISTS(SELECT 1 FROM pos_mixed_return_command) OR "
                    "EXISTS(SELECT 1 FROM pos_mixed_checkout) OR "
                    "EXISTS(SELECT 1 FROM pos_physical_checkout) OR EXISTS(SELECT 1 "
                    "FROM unit_stock_cart_command_v2 WHERE checkout_sale_id IS NOT NULL)"
                )
            )
            if history:
                with pytest.raises(DBAPIError) as error:
                    with connection.begin_nested():
                        downgrade_cash_for_roundtrip()
                assert getattr(error.value.orig, "sqlstate", None) == "55000"
                return
            query = text(
                "SELECT to_jsonb(row) FROM unit_stock_cart_command_v2 row ORDER BY tenant_id,id"
            )
            before = connection.execute(query).scalars().all()
            guards = text(
                "SELECT proname,pg_get_functiondef(oid),proowner,proacl,prosecdef FROM pg_proc "
                "WHERE pronamespace='public'::regnamespace "
                "AND proname IN ('trg_unit_cart_payment_boundary','trg_guard_sale_immutability',"
                "'trg_unit_cart_guard','trg_unit_cart_v2_guard') ORDER BY proname"
            )
            original = connection.execute(guards).all()
            downgrade_cash_for_roundtrip()
            assert connection.scalar(text("SELECT to_regclass('pos_physical_checkout')")) is None
            assert not connection.scalar(
                text(
                    "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
                    "WHERE table_name='unit_stock_cart_command_v2' "
                    "AND column_name='checkout_sale_id')"
                )
            )
            upgrade_cash_after_roundtrip()
            assert connection.execute(query).scalars().all() == before
            assert connection.execute(guards).all() == original

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()


async def test_edge_compatibility_roundtrip_preserves_guard_owner_and_acl(
    maintenance_engine: AsyncEngine,
) -> None:
    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        query = text(
            "SELECT pg_get_functiondef(oid),proowner,proacl,prosecdef "
            "FROM pg_proc WHERE oid='trg_guard_sale_immutability()'::regprocedure"
        )
        before = connection.execute(query).one()
        with Operations.context(MigrationContext.configure(connection)):
            history = connection.scalar(
                text(
                    "SELECT EXISTS(SELECT 1 FROM pos_unit_payment_attempt_binding) OR "
                    "EXISTS(SELECT 1 FROM pos_physical_cash_refund) OR "
                    "EXISTS(SELECT 1 FROM pos_physical_zero_return) OR "
                    "EXISTS(SELECT 1 FROM pos_mixed_zero_return) OR "
                    "EXISTS(SELECT 1 FROM pos_mixed_return_command) OR "
                    "EXISTS(SELECT 1 FROM pos_mixed_checkout) OR "
                    "EXISTS(SELECT 1 FROM pos_physical_checkout WHERE payment_journal_version=2)"
                )
            )
            if history:
                with pytest.raises(DBAPIError) as error:
                    with connection.begin_nested():
                        downgrade_refund_for_roundtrip()
                assert getattr(error.value.orig, "sqlstate", None) == "55000"
                return
            downgrade_refund_for_roundtrip()
            compatibility_migration().downgrade()
            previous = connection.execute(query).one()
            assert "v_physical_cash BOOLEAN" not in previous[0]
            assert previous[1:] == before[1:]
            compatibility_migration().upgrade()
            upgrade_refund_after_roundtrip()
            assert connection.execute(query).one() == before

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()
