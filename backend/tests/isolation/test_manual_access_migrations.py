"""Transactional rollback rehearsal, exclusively in the disposable test DB."""

from __future__ import annotations

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


def _migration(filename: str) -> ModuleType:
    source = Path(__file__).resolve().parents[2] / "alembic" / "versions" / filename
    spec = importlib.util.spec_from_file_location(f"manual_access_{source.stem}", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_manual_access_migrations_roundtrip(maintenance_engine: AsyncEngine) -> None:
    migrations = [
        _migration("0155_tenant_access_policy.py"),
        _migration("0156_owner_pharmacy_launch.py"),
        _migration("0157_retire_subscription_access.py"),
    ]

    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        later_tables = (
            "staff_pay_period",
            "staff_pay_entry",
            "staff_pay_revision",
            "unit_stock_operation",
            "unit_stock_lot",
            "unit_stock_opened_pack",
            "unit_stock_entry",
            "unit_stock_operation_v2",
            "unit_stock_lot_v2",
            "unit_stock_entry_v2",
            "unit_stock_cart_command_v2",
            "unit_stock_cart_line_v2",
            "unit_stock_opened_pack_v2",
            "unit_stock_movement_v2",
            "pos_unit_sale_command",
            "pos_unit_sale_line",
            "pos_unit_return_command",
            "pos_unit_return_slice",
            "pos_unit_cart_item",
            "pos_unit_cash_intent",
            "pos_unit_payment_plan_intent",
            "pos_unit_payment_plan_binding",
            "pos_unit_payment_plan_binding_cancel",
            "pos_unit_checkout_payment_component",
            "pos_unit_refund_payment_component",
            "pos_unit_payment_attempt_binding",
            "pos_unit_electronic_refund_intent",
            "pos_mixed_electronic_refund_intent",
            "pos_mixed_electronic_refund_binding",
            "pos_mixed_electronic_refund_binding_cancel",
            "pos_mixed_electronic_refund_activation",
            "pos_mixed_electronic_refund_posting",
            "pos_mixed_electronic_refund_outcome",
            "pos_training_checkout",
            "pos_retail_checkout_origin",
            "pos_retail_checkout_event",
            "sync_retail_event_producer_gate",
            "sync_retail_event_anchor",
            "sync_physical_stock_producer_gate",
            "sync_physical_stock_source",
            "sync_physical_stock_event",
            "sync_physical_stock_anchor",
            "sync_physical_stock_projection",
            "sync_physical_stock_baseline",
            "pos_unit_electronic_refund_binding",
            "pos_unit_electronic_refund_binding_cancel",
            "pos_unit_electronic_refund_activation",
            "pos_unit_electronic_refund_outcome",
            "pos_unit_electronic_refund_posting",
            "pos_physical_checkout",
            "pos_physical_return_command",
            "pos_physical_return_line",
            "pos_physical_return_slice",
            "pos_physical_cash_refund",
            "pos_physical_zero_return",
            "pos_mixed_checkout",
            "pos_mixed_return_command",
            "pos_mixed_return_line",
            "pos_mixed_return_slice",
            "pos_mixed_cash_refund",
            "pos_mixed_zero_return",
            "mixed_customer_return_disposition",
            "physical_customer_return_disposition",
            "unit_legacy_reconciliation_record",
            "unit_legacy_transition",
            "unit_stock_opening_pack_v2",
            "physical_writeoff_command",
            "physical_writeoff_entry",
            "physical_writeoff_cancellation",
        )
        # This is a selective 0155-0157 rehearsal, not a production rollback past
        # Later inventory and financial migrations through 0217 remain installed.
        # Preserve history and audit guards.
        connection.execute(text("""
            LOCK TABLE public.staff_pay_period, public.staff_pay_entry,
              public.staff_pay_revision, public.unit_stock_operation,
              public.unit_stock_lot, public.unit_stock_opened_pack,
              public.unit_stock_entry, public.pos_unit_sale_command,
              public.unit_stock_operation_v2, public.unit_stock_lot_v2,
              public.unit_stock_entry_v2,
              public.unit_stock_cart_command_v2, public.unit_stock_cart_line_v2,
              public.unit_stock_opened_pack_v2, public.unit_stock_movement_v2,
              public.pos_unit_sale_line, public.pos_unit_return_command,
              public.pos_unit_return_slice, public.pos_unit_cart_item,
              public.pos_unit_cash_intent, public.pos_unit_payment_plan_intent,
              public.pos_unit_payment_plan_binding, public.pos_unit_payment_plan_binding_cancel,
              public.pos_unit_checkout_payment_component,
              public.pos_unit_refund_payment_component,
              public.pos_unit_payment_attempt_binding,
              public.pos_unit_electronic_refund_intent,
              public.pos_mixed_electronic_refund_intent,
              public.pos_mixed_electronic_refund_binding,
              public.pos_mixed_electronic_refund_binding_cancel,
              public.pos_mixed_electronic_refund_activation,
              public.pos_mixed_electronic_refund_posting,
              public.pos_mixed_electronic_refund_outcome,
              public.pos_training_checkout,
              public.pos_retail_checkout_origin,
              public.pos_retail_checkout_event,
              public.sync_retail_event_producer_gate,
              public.sync_retail_event_anchor,
              public.sync_physical_stock_producer_gate,
              public.sync_physical_stock_source,
              public.sync_physical_stock_event,
              public.sync_physical_stock_anchor,
              public.sync_physical_stock_projection,
              public.sync_physical_stock_baseline,
              public.pos_unit_electronic_refund_binding,
              public.pos_unit_electronic_refund_binding_cancel,
              public.pos_unit_electronic_refund_outcome,
              public.pos_unit_electronic_refund_posting,
              public.pos_unit_electronic_refund_activation,
              public.pos_physical_checkout,
              public.pos_physical_return_command, public.pos_physical_return_line,
              public.pos_physical_return_slice, public.pos_physical_cash_refund,
              public.pos_physical_zero_return, public.pos_mixed_checkout,
              public.pos_mixed_return_command, public.pos_mixed_return_line,
              public.pos_mixed_return_slice, public.pos_mixed_cash_refund,
              public.pos_mixed_zero_return, public.mixed_customer_return_disposition,
              public.physical_customer_return_disposition,
              public.unit_legacy_reconciliation_record,public.unit_legacy_transition,
              public.unit_stock_opening_pack_v2,
              public.physical_writeoff_command,public.physical_writeoff_entry,
              public.physical_writeoff_cancellation
              IN ACCESS EXCLUSIVE MODE
        """))
        trigger_sql = text("""
            SELECT c.relname, t.tgname, t.tgenabled::text AS tgenabled,
              pg_get_triggerdef(t.oid) AS definition,
              pg_get_functiondef(t.tgfoid) AS function_definition
            FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
            WHERE t.tgrelid IN ('public.staff_pay_period'::regclass,
              'public.staff_pay_entry'::regclass, 'public.staff_pay_revision'::regclass,
              'public.unit_stock_operation'::regclass, 'public.unit_stock_lot'::regclass,
              'public.unit_stock_opened_pack'::regclass, 'public.unit_stock_entry'::regclass,
              'public.unit_stock_operation_v2'::regclass, 'public.unit_stock_lot_v2'::regclass,
              'public.unit_stock_entry_v2'::regclass,
              'public.unit_stock_cart_command_v2'::regclass,
              'public.unit_stock_cart_line_v2'::regclass,
              'public.unit_stock_opened_pack_v2'::regclass,
              'public.unit_stock_movement_v2'::regclass,
              'public.pos_unit_sale_command'::regclass, 'public.pos_unit_sale_line'::regclass,
              'public.pos_unit_return_command'::regclass, 'public.pos_unit_return_slice'::regclass,
              'public.pos_unit_cart_item'::regclass, 'public.pos_unit_cash_intent'::regclass,
              'public.pos_unit_payment_plan_intent'::regclass,
              'public.pos_unit_payment_plan_binding'::regclass,
              'public.pos_unit_payment_plan_binding_cancel'::regclass,
              'public.pos_unit_checkout_payment_component'::regclass,
              'public.pos_unit_refund_payment_component'::regclass,
              'public.pos_unit_payment_attempt_binding'::regclass,
              'public.pos_unit_electronic_refund_intent'::regclass,
              'public.pos_mixed_electronic_refund_intent'::regclass,
              'public.pos_mixed_electronic_refund_binding'::regclass,
              'public.pos_mixed_electronic_refund_binding_cancel'::regclass,
              'public.pos_mixed_electronic_refund_activation'::regclass,
              'public.pos_mixed_electronic_refund_posting'::regclass,
              'public.pos_mixed_electronic_refund_outcome'::regclass,
              'public.pos_training_checkout'::regclass,
              'public.pos_retail_checkout_origin'::regclass,
              'public.pos_retail_checkout_event'::regclass,
              'public.sync_retail_event_producer_gate'::regclass,
              'public.sync_retail_event_anchor'::regclass,
              'public.sync_physical_stock_producer_gate'::regclass,
              'public.sync_physical_stock_source'::regclass,
              'public.sync_physical_stock_event'::regclass,
              'public.sync_physical_stock_anchor'::regclass,
              'public.sync_physical_stock_projection'::regclass,
              'public.sync_physical_stock_baseline'::regclass,
              'public.pos_unit_electronic_refund_binding'::regclass,
              'public.pos_unit_electronic_refund_binding_cancel'::regclass,
              'public.pos_unit_electronic_refund_outcome'::regclass,
              'public.pos_unit_electronic_refund_posting'::regclass,
              'public.pos_unit_electronic_refund_activation'::regclass,
              'public.pos_physical_checkout'::regclass,
              'public.pos_physical_return_command'::regclass,
              'public.pos_physical_return_line'::regclass,
              'public.pos_physical_return_slice'::regclass,
              'public.pos_physical_cash_refund'::regclass,
              'public.pos_physical_zero_return'::regclass,
              'public.pos_mixed_checkout'::regclass,
              'public.pos_mixed_return_command'::regclass,
              'public.pos_mixed_return_line'::regclass,
              'public.pos_mixed_return_slice'::regclass,
              'public.pos_mixed_cash_refund'::regclass,
              'public.pos_mixed_zero_return'::regclass,
              'public.mixed_customer_return_disposition'::regclass,
              'public.physical_customer_return_disposition'::regclass,
              'public.unit_legacy_reconciliation_record'::regclass,
              'public.unit_legacy_transition'::regclass,
              'public.unit_stock_opening_pack_v2'::regclass,
              'public.physical_writeoff_command'::regclass,
              'public.physical_writeoff_entry'::regclass,
              'public.physical_writeoff_cancellation'::regclass)
              AND NOT t.tgisinternal
            ORDER BY c.relname, t.tgname
        """)
        security_sql = text("""
            SELECT relname, relrowsecurity, relforcerowsecurity, relacl::text
            FROM pg_class WHERE oid IN ('public.staff_pay_period'::regclass,
              'public.staff_pay_entry'::regclass, 'public.staff_pay_revision'::regclass,
              'public.unit_stock_operation'::regclass, 'public.unit_stock_lot'::regclass,
              'public.unit_stock_opened_pack'::regclass, 'public.unit_stock_entry'::regclass,
              'public.unit_stock_operation_v2'::regclass, 'public.unit_stock_lot_v2'::regclass,
              'public.unit_stock_entry_v2'::regclass,
              'public.unit_stock_cart_command_v2'::regclass,
              'public.unit_stock_cart_line_v2'::regclass,
              'public.unit_stock_opened_pack_v2'::regclass,
              'public.unit_stock_movement_v2'::regclass,
              'public.pos_unit_sale_command'::regclass, 'public.pos_unit_sale_line'::regclass,
              'public.pos_unit_return_command'::regclass, 'public.pos_unit_return_slice'::regclass,
              'public.pos_unit_cart_item'::regclass, 'public.pos_unit_cash_intent'::regclass,
              'public.pos_unit_payment_plan_intent'::regclass,
              'public.pos_unit_payment_plan_binding'::regclass,
              'public.pos_unit_payment_plan_binding_cancel'::regclass,
              'public.pos_unit_checkout_payment_component'::regclass,
              'public.pos_unit_refund_payment_component'::regclass,
              'public.pos_unit_payment_attempt_binding'::regclass,
              'public.pos_unit_electronic_refund_intent'::regclass,
              'public.pos_mixed_electronic_refund_intent'::regclass,
              'public.pos_mixed_electronic_refund_binding'::regclass,
              'public.pos_mixed_electronic_refund_binding_cancel'::regclass,
              'public.pos_mixed_electronic_refund_activation'::regclass,
              'public.pos_mixed_electronic_refund_posting'::regclass,
              'public.pos_mixed_electronic_refund_outcome'::regclass,
              'public.pos_training_checkout'::regclass,
              'public.pos_retail_checkout_origin'::regclass,
              'public.pos_retail_checkout_event'::regclass,
              'public.sync_retail_event_producer_gate'::regclass,
              'public.sync_retail_event_anchor'::regclass,
              'public.sync_physical_stock_producer_gate'::regclass,
              'public.sync_physical_stock_source'::regclass,
              'public.sync_physical_stock_event'::regclass,
              'public.sync_physical_stock_anchor'::regclass,
              'public.sync_physical_stock_projection'::regclass,
              'public.sync_physical_stock_baseline'::regclass,
              'public.pos_unit_electronic_refund_binding'::regclass,
              'public.pos_unit_electronic_refund_binding_cancel'::regclass,
              'public.pos_unit_electronic_refund_outcome'::regclass,
              'public.pos_unit_electronic_refund_posting'::regclass,
              'public.pos_unit_electronic_refund_activation'::regclass,
              'public.pos_physical_checkout'::regclass,
              'public.pos_physical_return_command'::regclass,
              'public.pos_physical_return_line'::regclass,
              'public.pos_physical_return_slice'::regclass,
              'public.pos_physical_cash_refund'::regclass,
              'public.pos_physical_zero_return'::regclass,
              'public.pos_mixed_checkout'::regclass,
              'public.pos_mixed_return_command'::regclass,
              'public.pos_mixed_return_line'::regclass,
              'public.pos_mixed_return_slice'::regclass,
              'public.pos_mixed_cash_refund'::regclass,
              'public.pos_mixed_zero_return'::regclass,
              'public.mixed_customer_return_disposition'::regclass,
              'public.physical_customer_return_disposition'::regclass,
              'public.unit_legacy_reconciliation_record'::regclass,
              'public.unit_legacy_transition'::regclass,
              'public.unit_stock_opening_pack_v2'::regclass,
              'public.physical_writeoff_command'::regclass,
              'public.physical_writeoff_entry'::regclass,
              'public.physical_writeoff_cancellation'::regclass)
            ORDER BY relname
        """)
        policies_sql = text("""
            SELECT polrelid::regclass::text, polname, polcmd, polpermissive, polroles,
              pg_get_expr(polqual, polrelid), pg_get_expr(polwithcheck, polrelid)
            FROM pg_policy WHERE polrelid IN ('public.staff_pay_period'::regclass,
              'public.staff_pay_entry'::regclass, 'public.staff_pay_revision'::regclass,
              'public.unit_stock_operation'::regclass, 'public.unit_stock_lot'::regclass,
              'public.unit_stock_opened_pack'::regclass, 'public.unit_stock_entry'::regclass,
              'public.unit_stock_operation_v2'::regclass, 'public.unit_stock_lot_v2'::regclass,
              'public.unit_stock_entry_v2'::regclass,
              'public.unit_stock_cart_command_v2'::regclass,
              'public.unit_stock_cart_line_v2'::regclass,
              'public.unit_stock_opened_pack_v2'::regclass,
              'public.unit_stock_movement_v2'::regclass,
              'public.pos_unit_sale_command'::regclass, 'public.pos_unit_sale_line'::regclass,
              'public.pos_unit_return_command'::regclass, 'public.pos_unit_return_slice'::regclass,
              'public.pos_unit_cart_item'::regclass, 'public.pos_unit_cash_intent'::regclass,
              'public.pos_unit_payment_plan_intent'::regclass,
              'public.pos_unit_payment_plan_binding'::regclass,
              'public.pos_unit_payment_plan_binding_cancel'::regclass,
              'public.pos_unit_checkout_payment_component'::regclass,
              'public.pos_unit_refund_payment_component'::regclass,
              'public.pos_unit_payment_attempt_binding'::regclass,
              'public.pos_unit_electronic_refund_intent'::regclass,
              'public.pos_mixed_electronic_refund_intent'::regclass,
              'public.pos_mixed_electronic_refund_binding'::regclass,
              'public.pos_mixed_electronic_refund_binding_cancel'::regclass,
              'public.pos_mixed_electronic_refund_activation'::regclass,
              'public.pos_mixed_electronic_refund_posting'::regclass,
              'public.pos_mixed_electronic_refund_outcome'::regclass,
              'public.pos_training_checkout'::regclass,
              'public.pos_retail_checkout_origin'::regclass,
              'public.pos_retail_checkout_event'::regclass,
              'public.sync_retail_event_producer_gate'::regclass,
              'public.sync_retail_event_anchor'::regclass,
              'public.sync_physical_stock_producer_gate'::regclass,
              'public.sync_physical_stock_source'::regclass,
              'public.sync_physical_stock_event'::regclass,
              'public.sync_physical_stock_anchor'::regclass,
              'public.sync_physical_stock_projection'::regclass,
              'public.sync_physical_stock_baseline'::regclass,
              'public.pos_unit_electronic_refund_binding'::regclass,
              'public.pos_unit_electronic_refund_binding_cancel'::regclass,
              'public.pos_unit_electronic_refund_outcome'::regclass,
              'public.pos_unit_electronic_refund_posting'::regclass,
              'public.pos_unit_electronic_refund_activation'::regclass,
              'public.pos_physical_checkout'::regclass,
              'public.pos_physical_return_command'::regclass,
              'public.pos_physical_return_line'::regclass,
              'public.pos_physical_return_slice'::regclass,
              'public.pos_physical_cash_refund'::regclass,
              'public.pos_physical_zero_return'::regclass,
              'public.pos_mixed_checkout'::regclass,
              'public.pos_mixed_return_command'::regclass,
              'public.pos_mixed_return_line'::regclass,
              'public.pos_mixed_return_slice'::regclass,
              'public.pos_mixed_cash_refund'::regclass,
              'public.pos_mixed_zero_return'::regclass,
              'public.mixed_customer_return_disposition'::regclass,
              'public.physical_customer_return_disposition'::regclass,
              'public.unit_legacy_reconciliation_record'::regclass,
              'public.unit_legacy_transition'::regclass,
              'public.unit_stock_opening_pack_v2'::regclass,
              'public.physical_writeoff_command'::regclass,
              'public.physical_writeoff_entry'::regclass,
              'public.physical_writeoff_cancellation'::regclass)
            ORDER BY polrelid::regclass::text, polname
        """)
        triggers_before = connection.execute(trigger_sql).all()
        security_before = connection.execute(security_sql).all()
        policies_before = connection.execute(policies_sql).all()
        redactor_sql = text(
            "SELECT pg_get_functiondef("
            "'public.audit_redact_table_snapshot(text,jsonb)'::regprocedure)"
        )
        redactor_before = connection.scalar(redactor_sql)
        counts_before = [
            connection.scalar(text(f"SELECT count(*) FROM public.{table}"))
            for table in later_tables
        ]
        access_triggers = [row for row in triggers_before if row.tgname == "a00_tenant_access"]
        assert {row.relname for row in access_triggers} == set(later_tables)
        assert all(row.tgenabled == "O" for row in access_triggers)
        with Operations.context(MigrationContext.configure(connection)):
            if counts_before[0]:
                with pytest.raises(DBAPIError) as error:
                    with connection.begin_nested():
                        _migration("0160_manual_staff_pay.py").downgrade()
                assert error.value.orig.sqlstate == "55000"
            # These later triggers depend on the 0155 function. Drop only those
            # dependencies under the table locks; the outer transaction always
            # rolls back, including when any rehearsal assertion fails.
            for table in later_tables:
                connection.execute(text(f"DROP TRIGGER a00_tenant_access ON public.{table}"))
            for migration in reversed(migrations):
                migration.downgrade()
            for table in ("tenant_access_policy", "tenant_launch_activation"):
                assert (
                    connection.scalar(
                        text("SELECT to_regclass(:table)"), {"table": f"public.{table}"}
                    )
                    is None
                )
            for migration in migrations:
                migration.upgrade()
            for trigger in access_triggers:
                connection.execute(text(trigger.definition))
            assert connection.execute(trigger_sql).all() == triggers_before
            assert connection.execute(security_sql).all() == security_before
            assert connection.execute(policies_sql).all() == policies_before
            assert connection.scalar(redactor_sql) == redactor_before
            assert [
                connection.scalar(text(f"SELECT count(*) FROM public.{table}"))
                for table in later_tables
            ] == counts_before
            for table in ("tenant_access_policy", "tenant_launch_activation"):
                assert connection.scalar(
                    text("""
                    SELECT relrowsecurity AND relforcerowsecurity FROM pg_class
                    WHERE oid = to_regclass(:table)
                    """),
                    {"table": f"public.{table}"},
                )
            definition = str(connection.scalar(text("""
                SELECT pg_get_functiondef(
                  ('public.approve_billing_bank_payment'
                   || '(uuid,uuid,uuid,text,uuid,uuid,integer)')::regprocedure
                )
                """)))
            assert "UPDATE public.tenant" not in definition
            assert "v_access_restored := true" not in definition

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()
