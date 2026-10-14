"""Independent real cashiers cannot spend the same physical pack or last unit."""

import asyncio
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import BusinessRuleError, ConflictError
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.pos.models import Sale
from app.domains.pos.physical_documents import PhysicalCheckoutResultV3
from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService
from app.domains.pos.unit_cash_confirmation import CashConfirmationRequestV2
from tests.domains.incoming.test_unit_acceptance import (
    ReceivingRuntime,
    seed_receiving_actor,
    set_receiving_context,
)
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.inventory.test_unit_ledger_v2_dispensing import PhysicalSource, remaining
from tests.domains.pos.test_physical_cash_checkout import complete, facts
from tests.domains.pos.test_unit_physical_cart import put_physical
from tests.domains.pos.test_unit_physical_checkout_preparation import checkout_source, saved_request
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


async def new_sale(session: AsyncSession) -> Sale:
    context = runtime(session)
    register = await FoundationService(FoundationRepository(session)).create_register(
        tenant_id=context.stock.tenant_id,
        fields={"branch_id": context.stock.branch_id, "name": "Competing physical cashier"},
        created_by=context.actor_id,
    )
    pos = POSService(POSRepository(session))
    await pos.open_shift(
        tenant_id=context.stock.tenant_id,
        register_id=register.id,
        opened_by_user_id=context.actor_id,
        opening_cash=Decimal("0.00"),
        allowed_branch_ids={context.stock.branch_id},
    )
    return await pos.create_sale(
        tenant_id=context.stock.tenant_id,
        register_id=register.id,
        cashier_user_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
    )


async def compete(
    context: ReceivingRuntime, request: CashConfirmationRequestV2, start: asyncio.Barrier
) -> PhysicalCheckoutResultV3 | BusinessRuleError | ConflictError:
    async with context.factory() as session:
        session.info["receiving_runtime"] = context
        await set_receiving_context(session, context.stock, context.actor_id)
        await session.execute(text("SET LOCAL lock_timeout='10s'"))
        await start.wait()
        try:
            result = await complete(session, request)
            await session.commit()
            return result
        except (BusinessRuleError, ConflictError) as error:
            await session.rollback()
            return error


async def assert_unpaid_draft(session: AsyncSession, request: CashConfirmationRequestV2) -> None:
    sale = (
        await session.execute(
            text("""
                SELECT status,total_amount,operation_id,receipt_number,completed_at
                FROM public.sale WHERE id=:sale
            """),
            {"sale": request.sale_id},
        )
    ).one()
    assert tuple(sale) == ("draft", Decimal("0.00"), None, None, None)
    for table, column in (
        ("sale_payment", "sale_id"),
        ("pos_physical_checkout", "sale_id"),
        ("unit_stock_cart_command_v2", "id"),
        ("unit_stock_cart_line_v2", "command_id"),
        ("unit_stock_movement_v2", "command_id"),
    ):
        identifier = request.sale_id if column == "sale_id" else request.operation_id
        assert (
            await session.scalar(
                text(f"SELECT count(*) FROM public.{table} WHERE {column}=:id"), {"id": identifier}
            )
            == 0
        )
    assert (
        await session.scalar(
            text("SELECT count(*) FROM public.pos_unit_cash_intent WHERE id=:id"),
            {"id": request.operation_id},
        )
        == 1
    )


async def assert_stock_conserved(
    session: AsyncSession, source: PhysicalSource, expected_remaining: int
) -> None:
    assert await remaining(session, source) == expected_remaining
    closed = await session.scalar(
        text("SELECT closed_packs FROM public.unit_stock_lot_v2 WHERE id=:lot"),
        {"lot": source.lot_id},
    )
    assert closed == 0
    opened = (
        await session.execute(
            text("""
                SELECT pack_ordinal,remaining_units FROM public.unit_stock_opened_pack_v2
                WHERE lot_id=:lot ORDER BY pack_ordinal
            """),
            {"lot": source.lot_id},
        )
    ).all()
    assert len(opened) <= 1
    assert all(0 <= row.remaining_units <= 30 for row in opened)
    assert len({row.pack_ordinal for row in opened}) == len(opened)
    assert sum(row.remaining_units for row in opened) == expected_remaining
    assert (
        await session.scalar(
            text("SELECT sum(units_taken) FROM public.unit_stock_movement_v2 WHERE lot_id=:lot"),
            {"lot": source.lot_id},
        )
        == 30 - expected_remaining
    )


async def assert_winner_replays_once(
    session: AsyncSession,
    context: ReceivingRuntime,
    request: CashConfirmationRequestV2,
    original: PhysicalCheckoutResultV3,
    source: PhysicalSource,
    scenario: str,
) -> None:
    session.info["receiving_runtime"] = context
    await set_receiving_context(session, context.stock, context.actor_id)
    winner_units = sum(line.base_units for line in original.lines)
    expected_remaining = 0 if scenario == "last_unit" else 30 - winner_units
    await assert_stock_conserved(session, source, expected_remaining)
    assert (
        await session.scalar(
            text("SELECT count(*) FROM public.sale_payment WHERE sale_id=:sale"),
            {"sale": request.sale_id},
        )
        == 1
    )
    assert (
        await session.scalar(
            text("SELECT count(*) FROM public.pos_physical_checkout WHERE sale_id=:sale"),
            {"sale": request.sale_id},
        )
        == 1
    )
    assert (
        await session.scalar(
            text("SELECT sum(units_taken) FROM public.unit_stock_movement_v2 WHERE command_id=:id"),
            {"id": request.operation_id},
        )
        == winner_units
    )
    assert (
        await session.scalar(
            text("SELECT status FROM public.sale WHERE id=:sale"), {"sale": request.sale_id}
        )
        == "completed"
    )
    payment = (
        await session.execute(
            text("SELECT payment_method,amount FROM public.sale_payment WHERE sale_id=:sale"),
            {"sale": request.sale_id},
        )
    ).one()
    assert tuple(payment) == ("cash", request.expected_total_amount)
    before_replay = await facts(session)
    assert await complete(session, request) == original
    assert await facts(session) == before_replay
    await assert_stock_conserved(session, source, expected_remaining)


def winning_result(
    results: list[PhysicalCheckoutResultV3 | BusinessRuleError | ConflictError],
) -> tuple[int, PhysicalCheckoutResultV3]:
    winners = [
        index for index, value in enumerate(results) if isinstance(value, PhysicalCheckoutResultV3)
    ]
    assert len(winners) == 1
    winner = winners[0]
    original = results[winner]
    assert isinstance(original, PhysicalCheckoutResultV3)
    assert isinstance(results[1 - winner], BusinessRuleError | ConflictError)
    return winner, original


@pytest.mark.parametrize("scenario", ["last_unit", "pack_vs_unit"])
async def test_two_cashiers_compete_for_one_physical_lot_without_partial_sales(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
    scenario: str,
) -> None:
    first = runtime(db_session)
    second_actor = await seed_receiving_actor(db_engine, maintenance_engine, first.stock)
    second = ReceivingRuntime(first.factory, first.stock, second_actor)
    source = await checkout_source(db_session, quantity=1)
    if scenario == "last_unit":
        await put_physical(db_session, source.sale.id, quantity=29)
        await complete(db_session, await saved_request(db_session, source.sale))
        first_sale = await new_sale(db_session)
    else:
        first_sale = source.sale
    await put_physical(
        db_session, first_sale.id, sale_format="pack" if scenario == "pack_vs_unit" else "unit"
    )
    first_request = await saved_request(db_session, first_sale)
    await db_session.commit()
    db_session.info["receiving_runtime"] = second
    await set_receiving_context(db_session, second.stock, second.actor_id)
    second_sale = await new_sale(db_session)
    await put_physical(db_session, second_sale.id)
    second_request = await saved_request(db_session, second_sale)
    await db_session.commit()
    assert first.actor_id != second.actor_id
    assert first_sale.id != second_sale.id
    assert first_sale.register_id != second_sale.register_id
    assert first_sale.shift_id != second_sale.shift_id
    contexts = (first, second)
    requests = (first_request, second_request)
    start = asyncio.Barrier(2)
    tasks = [
        asyncio.create_task(compete(ctx, req, start))
        for ctx, req in zip(contexts, requests, strict=True)
    ]
    try:
        results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=20)
        winner, original = winning_result(results)
        loser = 1 - winner
        db_session.info["receiving_runtime"] = contexts[loser]
        await set_receiving_context(db_session, first.stock, contexts[loser].actor_id)
        await assert_unpaid_draft(db_session, requests[loser])
        before_retry = await facts(db_session)
        with pytest.raises(BusinessRuleError, match="Insufficient compatible physical stock"):
            await complete(db_session, requests[loser])
        assert await facts(db_session) == before_retry
        await assert_unpaid_draft(db_session, requests[loser])
        await db_session.rollback()
        await assert_winner_replays_once(
            db_session, contexts[winner], requests[winner], original, source.stock, scenario
        )
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await db_session.rollback()
        db_session.info["receiving_runtime"] = first
        await set_receiving_context(db_session, first.stock, first.actor_id)
