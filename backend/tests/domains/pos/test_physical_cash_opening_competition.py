"""Concurrent cashiers open one pack once and safely retry a busy saved checkout."""

import asyncio
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import ConflictError
from app.domains.inventory.unit_stock_v2 import SaleFormatV2
from app.domains.pos.physical_documents import PhysicalCheckoutResultV3
from app.domains.pos.unit_cash_confirmation import CashConfirmationRequestV2
from tests.domains.incoming.test_unit_acceptance import (
    ReceivingRuntime,
    seed_receiving_actor,
    set_receiving_context,
)
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.inventory.test_unit_ledger_v2_dispensing import PhysicalSource, packs, remaining
from tests.domains.pos.test_physical_cash_checkout import complete, facts
from tests.domains.pos.test_physical_cash_competition import assert_unpaid_draft, new_sale
from tests.domains.pos.test_unit_physical_cart import put_physical
from tests.domains.pos.test_unit_physical_checkout_preparation import checkout_source, saved_request
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


async def prepare_cashiers(
    session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
    first_format: SaleFormatV2,
) -> tuple[
    ReceivingRuntime,
    ReceivingRuntime,
    PhysicalSource,
    CashConfirmationRequestV2,
    CashConfirmationRequestV2,
]:
    first = runtime(session)
    second_actor = await seed_receiving_actor(db_engine, maintenance_engine, first.stock)
    second = ReceivingRuntime(first.factory, first.stock, second_actor)
    source = await checkout_source(session, quantity=1)
    await put_physical(session, source.sale.id, sale_format=first_format)
    first_request = await saved_request(session, source.sale)
    await session.commit()
    session.info["receiving_runtime"] = second
    await set_receiving_context(session, second.stock, second.actor_id)
    second_sale = await new_sale(session)
    await put_physical(session, second_sale.id)
    second_request = await saved_request(session, second_sale)
    await session.commit()
    assert first.actor_id != second.actor_id
    assert source.sale.register_id != second_sale.register_id
    assert source.sale.shift_id != second_sale.shift_id
    return first, second, source.stock, first_request, second_request


async def attempt_cash(
    context: ReceivingRuntime, request: CashConfirmationRequestV2
) -> PhysicalCheckoutResultV3 | ConflictError:
    async with context.factory() as session:
        session.info["receiving_runtime"] = context
        await set_receiving_context(session, context.stock, context.actor_id)
        await session.execute(text("SET LOCAL lock_timeout='3s'"))
        await session.execute(text("SET LOCAL statement_timeout='10s'"))
        try:
            result = await complete(session, request)
            await session.commit()
            return result
        except ConflictError as error:
            await session.rollback()
            return error


async def assert_opening_is_uncommitted(context: ReceivingRuntime, lot_id: UUID) -> None:
    async with context.factory() as session:
        await set_receiving_context(session, context.stock, context.actor_id)
        await session.execute(text("SET LOCAL lock_timeout='3s'"))
        await session.execute(text("SET LOCAL statement_timeout='10s'"))
        assert (
            await session.scalar(
                text("SELECT count(*) FROM unit_stock_opened_pack_v2 WHERE lot_id=:lot"),
                {"lot": lot_id},
            )
            == 0
        )
        assert (
            await session.scalar(
                text("SELECT closed_packs FROM unit_stock_lot_v2 WHERE id=:lot"), {"lot": lot_id}
            )
            == 1
        )


async def assert_single_opening(
    session: AsyncSession,
    source: PhysicalSource,
    results: tuple[PhysicalCheckoutResultV3, PhysicalCheckoutResultV3],
    units: int,
    cost: str,
) -> None:
    opened = await packs(session, source)
    assert len(opened) == 1 and opened[0][2] == 30 - units
    assert await remaining(session, source) == 30 - units
    state = (
        await session.execute(
            text(
                "SELECT count(*) FILTER(WHERE kind='pack_open'),sum(units_taken),sum(cost_amount),"
                "count(DISTINCT pack_id) FROM unit_stock_movement_v2 WHERE lot_id=:lot"
            ),
            {"lot": source.lot_id},
        )
    ).one()
    assert tuple(state) == (1, units, Decimal(cost), 1)
    original_cost = await session.scalar(
        text("SELECT purchase_cost_per_pack FROM unit_stock_lot_v2 WHERE id=:lot"),
        {"lot": source.lot_id},
    )
    assert original_cost == Decimal("10.00")
    assert (
        await session.scalar(
            text("SELECT pack_ordinal FROM unit_stock_opened_pack_v2 WHERE id=:pack"),
            {"pack": opened[0][0]},
        )
        == 1
    )
    assert sum((result.total_amount for result in results), Decimal(0)) == Decimal(units * 2)
    assert len({result.sale_id for result in results}) == 2
    assert len({result.operation_id for result in results}) == 2
    assert len({(result.register_id, result.receipt_number) for result in results}) == 2


async def assert_exact_replay(
    session: AsyncSession,
    context: ReceivingRuntime,
    request: CashConfirmationRequestV2,
    original: PhysicalCheckoutResultV3,
) -> None:
    session.info["receiving_runtime"] = context
    await set_receiving_context(session, context.stock, context.actor_id)
    payments = (
        await session.execute(
            text("SELECT payment_method,amount FROM sale_payment WHERE sale_id=:sale"),
            {"sale": original.sale_id},
        )
    ).all()
    assert [tuple(row) for row in payments] == [("cash", original.total_amount)]
    assert (
        await session.scalar(
            text("SELECT count(*) FROM pos_physical_checkout WHERE sale_id=:sale"),
            {"sale": original.sale_id},
        )
        == 1
    )
    before = await facts(session)
    assert await complete(session, request) == original
    assert await facts(session) == before
    await session.rollback()


@pytest.mark.parametrize("first_format,units,cost", [("unit", 2, "0.66"), ("blister", 11, "3.66")])
async def test_two_cashiers_open_one_pack_once_after_retrying_busy_checkout(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
    first_format: SaleFormatV2,
    units: int,
    cost: str,
) -> None:
    first = runtime(db_session)
    try:
        first, second, source, first_request, second_request = await prepare_cashiers(
            db_session, db_engine, maintenance_engine, first_format
        )
        db_session.info["receiving_runtime"] = first
        await set_receiving_context(db_session, first.stock, first.actor_id)
        await db_session.execute(text("SET LOCAL lock_timeout='3s'"))
        await db_session.execute(text("SET LOCAL statement_timeout='10s'"))
        # Hold the real opening and payment uncommitted while another cashier enters.
        first_result = await complete(db_session, first_request)
        await assert_opening_is_uncommitted(second, source.lot_id)
        busy = await asyncio.wait_for(attempt_cash(second, second_request), timeout=15)
        assert isinstance(busy, ConflictError)
        assert busy.message == "Checkout stock is busy; retry in a new transaction"
        await db_session.commit()
        db_session.info["receiving_runtime"] = second
        await set_receiving_context(db_session, second.stock, second.actor_id)
        await assert_unpaid_draft(db_session, second_request)
        await db_session.rollback()
        # Retry only the confirmed stock-busy failure, with the original saved operation.
        second_result = await asyncio.wait_for(attempt_cash(second, second_request), timeout=15)
        assert isinstance(second_result, PhysicalCheckoutResultV3)
        assert first_result.cashier_id == first.actor_id
        assert second_result.cashier_id == second.actor_id
        await assert_exact_replay(db_session, first, first_request, first_result)
        await assert_exact_replay(db_session, second, second_request, second_result)
        db_session.info["receiving_runtime"] = first
        await set_receiving_context(db_session, first.stock, first.actor_id)
        await assert_single_opening(db_session, source, (first_result, second_result), units, cost)
    finally:
        await db_session.rollback()
        db_session.info["receiving_runtime"] = first
        await set_receiving_context(db_session, first.stock, first.actor_id)
