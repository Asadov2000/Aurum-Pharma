"""Cash refund bindings retain history and reject forged financial documents."""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.domains.pos.physical_cash_refund_models import PhysicalCashRefund
from app.domains.pos.physical_cash_refund_repository import PhysicalCashRefundRepository
from app.domains.pos.physical_cash_refund_service import PhysicalCashRefundService
from app.domains.pos.physical_return_allocation import PhysicalReturnSelection
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.pos.test_physical_cash_checkout import complete
from tests.domains.pos.test_unit_physical_cart import put_physical
from tests.domains.pos.test_unit_physical_checkout_preparation import checkout_source, saved_request
from tests.domains.pos.test_unit_return import runtime
from tests.isolation.test_physical_cash_checkout_migration import (
    downgrade_refund_for_roundtrip,
    run_historical_rehearsal,
    upgrade_refund_after_roundtrip,
)

db_session = receiving_session_fixture


class ForgedRefundRepository(PhysicalCashRefundRepository):
    def __init__(self, session: AsyncSession, corruption: str) -> None:
        super().__init__(session)
        self.corruption = corruption

    async def insert(self, marker: PhysicalCashRefund) -> None:
        if self.corruption == "receipt":
            marker.receipt_payload = {**marker.receipt_payload, "total_amount": "999.00"}
        elif self.corruption == "result":
            marker.result_payload = {**marker.result_payload, "total_amount": "999.00"}
        elif self.corruption == "hash":
            marker.request_hash = "0" * 64
        elif self.corruption == "parent":
            marker.parent_sale_id = uuid4()
        else:
            marker.payment_id = uuid4()
        await super().insert(marker)


@pytest.mark.parametrize("corruption", ["receipt", "result", "hash", "parent", "payment"])
async def test_forged_refund_rolls_back_finance_and_quarantine(
    db_session: AsyncSession, corruption: str
) -> None:
    source = await checkout_source(db_session)
    item = await put_physical(db_session, source.sale.id, sale_format="pack")
    await complete(db_session, await saved_request(db_session, source.sale))
    context = runtime(db_session)
    sale_id, line_id, shift_id = source.sale.id, item.id, source.sale.shift_id
    with pytest.raises(DBAPIError):
        await PhysicalCashRefundService(ForgedRefundRepository(db_session, corruption)).refund(
            tenant_id=context.stock.tenant_id,
            actor_id=context.actor_id,
            operation_id=uuid4(),
            parent_sale_id=sale_id,
            shift_id=shift_id,
            selections=(PhysicalReturnSelection(line_id, "unit", 1),),
        )
    for table in ("pos_physical_cash_refund", "pos_physical_return_command"):
        assert not await db_session.scalar(
            text(f"SELECT EXISTS(SELECT 1 FROM {table} WHERE tenant_id=:tenant)"),
            {"tenant": context.stock.tenant_id},
        )
    assert not await db_session.scalar(
        text("SELECT EXISTS(SELECT 1 FROM sale WHERE parent_sale_id=:parent)"),
        {"parent": sale_id},
    )


async def test_refund_migration_roundtrip_preserves_guards_or_refuses_history(
    maintenance_engine: AsyncEngine,
) -> None:
    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        with Operations.context(MigrationContext.configure(connection)):
            if connection.scalar(
                text(
                    "SELECT EXISTS(SELECT 1 FROM pos_unit_payment_attempt_binding) OR "
                    "EXISTS(SELECT 1 FROM pos_physical_cash_refund) OR "
                    "EXISTS(SELECT 1 FROM pos_physical_zero_return) OR "
                    "EXISTS(SELECT 1 FROM pos_mixed_return_command) OR "
                    "EXISTS(SELECT 1 FROM pos_mixed_checkout)"
                )
            ):
                with pytest.raises(DBAPIError) as error:
                    with connection.begin_nested():
                        downgrade_refund_for_roundtrip()
                assert getattr(error.value.orig, "sqlstate", None) == "55000"
                return
            guard = text(
                "SELECT pg_get_functiondef(oid),proowner,proacl,prosecdef FROM pg_proc "
                "WHERE oid='trg_guard_sale_immutability()'::regprocedure"
            )
            original = connection.execute(guard).one()
            downgrade_refund_for_roundtrip()
            assert connection.scalar(text("SELECT to_regclass('pos_physical_cash_refund')")) is None
            upgrade_refund_after_roundtrip()
            assert connection.execute(guard).one() == original
            assert (
                connection.scalar(
                    text(
                        "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
                        "WHERE oid='pos_physical_cash_refund'::regclass"
                    )
                )
                is True
            )

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(run_historical_rehearsal, rehearse)
        finally:
            await transaction.rollback()


async def test_refund_binding_and_completed_sale_are_immutable(db_session: AsyncSession) -> None:
    source = await checkout_source(db_session)
    item = await put_physical(db_session, source.sale.id, sale_format="pack")
    await complete(db_session, await saved_request(db_session, source.sale))
    context = runtime(db_session)
    result = await PhysicalCashRefundService(PhysicalCashRefundRepository(db_session)).refund(
        tenant_id=context.stock.tenant_id,
        actor_id=context.actor_id,
        operation_id=uuid4(),
        parent_sale_id=source.sale.id,
        shift_id=source.sale.shift_id,
        selections=(PhysicalReturnSelection(item.id, "unit", 1),),
    )
    statements = (
        (
            "UPDATE pos_physical_cash_refund SET request_hash=request_hash WHERE id=:id",
            result.operation_id,
        ),
        ("DELETE FROM pos_physical_cash_refund WHERE id=:id", result.operation_id),
        ("UPDATE sale SET total_amount=total_amount+1 WHERE id=:id", result.sale_id),
        ("UPDATE sale_payment SET amount=amount+1 WHERE id=:id", result.payment_id),
        ("DELETE FROM sale_payment WHERE id=:id", result.payment_id),
    )
    for statement, identifier in statements:
        with pytest.raises(DBAPIError):
            async with db_session.begin_nested():
                await db_session.execute(text(statement), {"id": identifier})
    payloads = (
        await db_session.execute(
            text(
                "SELECT new_values->>'result_payload',new_values->>'receipt_payload' "
                "FROM audit_log WHERE tenant_id=:tenant AND record_id=:id "
                "AND table_name='pos_physical_cash_refund' AND action='INSERT'"
            ),
            {"tenant": result.tenant_id, "id": result.operation_id},
        )
    ).all()
    assert payloads and all(tuple(row) == ("***", "***") for row in payloads)
