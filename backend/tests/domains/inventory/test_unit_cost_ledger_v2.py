"""Saved physical movement costs conserve each real pack's original purchase price."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal, Inexact, localcontext
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessRuleError
from app.domains.inventory.unit_dispensing_v2_cost import UnitCartDispenseCostResultV2
from app.domains.inventory.unit_dispensing_v2_repository import UnitDispensingV2Repository
from app.domains.inventory.unit_dispensing_v2_service import PhysicalUnitLedgerV2Service
from app.domains.inventory.unit_stock_v2 import UnitPackagingV2
from tests.domains.incoming.test_unit_acceptance import (
    ReceivingRuntime,
    set_receiving_context,
)
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance_v2 import accept_v2, seed_v2_incoming
from tests.domains.inventory.test_unit_ledger_v2_dispensing import (
    PhysicalSource,
    cart_line,
    dispense,
    remaining,
)

db_session = receiving_session_fixture


async def priced_source(
    session: AsyncSession,
    *,
    price: Decimal = Decimal("10.00"),
    quantity: int = 1,
    blister: int | None = 10,
) -> PhysicalSource:
    incoming = await seed_v2_incoming(session, blister)
    await session.execute(
        text(
            "UPDATE incoming_item SET purchase_price=:price,qty=:qty,expires_at=:expiry "
            "WHERE id=:id"
        ),
        {
            "price": price,
            "qty": quantity,
            "expiry": date.today() + timedelta(days=180),
            "id": incoming.item_ids[0],
        },
    )
    await accept_v2(session, incoming)
    row = (
        await session.execute(
            text("SELECT id,packaging FROM unit_stock_lot_v2 WHERE source_item_id=:id"),
            {"id": incoming.item_ids[0]},
        )
    ).one()
    return PhysicalSource(incoming, row.id, UnitPackagingV2.model_validate(row.packaging))


async def costs(
    session: AsyncSession, source: PhysicalSource, command_id: UUID
) -> UnitCartDispenseCostResultV2:
    context = source.incoming
    return await PhysicalUnitLedgerV2Service(UnitDispensingV2Repository(session)).cost_result(
        tenant_id=context.stock.tenant_id,
        branch_id=context.stock.branch_id,
        command_id=command_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
    )


@pytest.mark.parametrize("price", [Decimal("10.00"), Decimal("0.01")])
async def test_thirty_parts_telescope_to_saved_pack_cost(
    db_session: AsyncSession, price: Decimal
) -> None:
    source = await priced_source(db_session, price=price)
    slices = []
    for _ in range(30):
        result = await dispense(db_session, source, (cart_line(source, "unit", 1),))
        cost = await costs(db_session, source, result.command_id)
        assert len(cost.cost_slices) == 1
        slices.extend(cost.cost_slices)
    assert sum((part.amount for part in slices), Decimal("0.00")) == price
    assert [part.unit_offset for part in slices] == list(range(30))
    assert {part.pack_ordinal_start for part in slices} == {1}
    assert all(part.pack_count == part.base_units == part.version == 1 for part in slices)
    assert all(part.original_pack_cost == price and part.currency == "TJS" for part in slices)
    assert await remaining(db_session, source) == 0
    if price == Decimal("10.00"):
        assert [part.amount for part in slices].count(Decimal("0.33")) == 20
        assert [part.amount for part in slices].count(Decimal("0.34")) == 10


async def test_mixed_formats_and_remaining_fragment_conserve_two_pack_costs(
    db_session: AsyncSession,
) -> None:
    source = await priced_source(db_session, quantity=2)
    lines = tuple(
        cart_line(source, kind, qty) for kind, qty in (("unit", 5), ("blister", 1), ("pack", 1))
    )
    first = await dispense(db_session, source, lines)
    priced = await costs(db_session, source, first.command_id)
    assert priced.total_amount == Decimal("15.00")
    assert sum(part.base_units for part in priced.cost_slices) == 45
    assert {part.line_id for part in priced.cost_slices} == {line.id for line in lines}
    assert len(priced.cost_slices) == len([m for m in first.movements if m.units_taken > 0])
    last = await dispense(db_session, source, (cart_line(source, "unit", 15),))
    final_cost = await costs(db_session, source, last.command_id)
    assert priced.total_amount + final_cost.total_amount == Decimal("20.00")
    assert await remaining(db_session, source) == 0


@pytest.mark.parametrize("blister", [10, None])
async def test_known_zero_cost_is_a_real_snapshot_not_unknown(
    db_session: AsyncSession, blister: int | None
) -> None:
    source = await priced_source(db_session, price=Decimal("0.00"), blister=blister)
    physical = await dispense(db_session, source, (cart_line(source, "unit", 3),))
    cost = await costs(db_session, source, physical.command_id)
    assert cost.total_amount == Decimal("0.00")
    assert cost.cost_slices and all(part.version == 1 for part in cost.cost_slices)
    assert all(
        part.original_pack_cost == part.amount == Decimal("0.00") for part in cost.cost_slices
    )
    assert all(not hasattr(movement, "cost_amount") for movement in physical.movements)


async def test_rollback_restores_cost_cursor_and_historical_replay_ignores_live_catalog(
    db_session: AsyncSession,
) -> None:
    source = await priced_source(db_session)
    command_id = uuid4()
    lines = (cart_line(source, "unit", 7),)
    transaction = await db_session.begin_nested()
    abandoned = await dispense(db_session, source, lines, command_id=command_id)
    old_cost = await costs(db_session, source, abandoned.command_id)
    await transaction.rollback()
    repeated = await dispense(db_session, source, lines, command_id=command_id)
    saved = await costs(db_session, source, repeated.command_id)
    assert saved.command_id == old_cost.command_id
    assert saved.total_amount == old_cost.total_amount
    assert len(saved.cost_slices) == len(old_cost.cost_slices)
    for original, persisted in zip(old_cost.cost_slices, saved.cost_slices, strict=True):
        assert original.movement_id != persisted.movement_id
        assert replace(original, movement_id=persisted.movement_id) == persisted
    assert saved.cost_slices[0].unit_offset == 0
    final = await dispense(db_session, source, (cart_line(source, "unit", 23),))
    assert saved.total_amount + (
        await costs(db_session, source, final.command_id)
    ).total_amount == (Decimal("10.00"))
    await db_session.execute(
        text("UPDATE tenant_catalog SET is_active=false,unit_sale_preparation=NULL WHERE id=:id"),
        {"id": source.incoming.stock.catalog_id},
    )
    assert await dispense(db_session, source, lines, command_id=command_id) == repeated
    assert await costs(db_session, source, command_id) == saved


async def test_cost_projection_ignores_ambient_decimal_precision(
    db_session: AsyncSession,
) -> None:
    price = Decimal("999999999999.99")
    source = await priced_source(db_session, price=price)
    first = await dispense(db_session, source, (cart_line(source, "unit", 1),))
    with localcontext() as context:
        context.prec = 1
        context.traps[Inexact] = True
        first_cost = await costs(db_session, source, first.command_id)
    assert first_cost.total_amount == Decimal("33333333333.33")
    last = await dispense(db_session, source, (cart_line(source, "unit", 29),))
    assert first_cost.total_amount + (
        await costs(db_session, source, last.command_id)
    ).total_amount == (price)


async def test_aggregate_cost_overflow_rolls_back_entire_cart(
    db_session: AsyncSession,
) -> None:
    source = await priced_source(db_session, price=Decimal("600000000000.00"), quantity=2)
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await dispense(db_session, source, (cart_line(source, "pack", 2),))
            await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    assert await remaining(db_session, source) == 60
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM unit_stock_movement_v2 WHERE lot_id=:lot"),
            {"lot": source.lot_id},
        )
        == 0
    )


async def test_concurrent_exact_replay_persists_one_cost_allocation(
    db_session: AsyncSession,
) -> None:
    runtime = cast(ReceivingRuntime, db_session.info["receiving_runtime"])
    source = await priced_source(db_session)
    lines = (cart_line(source, "unit", 7),)
    command_id = uuid4()
    await db_session.commit()

    async def attempt() -> UnitCartDispenseCostResultV2:
        async with runtime.factory.begin() as session:
            await set_receiving_context(session, runtime.stock, runtime.actor_id)
            await dispense(session, source, lines, command_id=command_id)
            return await costs(session, source, command_id)

    results = await asyncio.wait_for(asyncio.gather(*(attempt() for _ in range(4))), timeout=15)
    assert all(result == results[0] for result in results)
    await set_receiving_context(db_session, runtime.stock, runtime.actor_id)
    assert await remaining(db_session, source) == 23
    assert (
        await db_session.scalar(
            text("SELECT sum(cost_amount) FROM unit_stock_movement_v2 WHERE command_id=:id"),
            {"id": command_id},
        )
        == results[0].total_amount
    )


async def test_fragment_then_intact_blister_and_remainder_never_overlap_cost_ranges(
    db_session: AsyncSession,
) -> None:
    source = await priced_source(db_session)
    allocations = []
    for sale_format, quantity in (("unit", 3), ("blister", 1), ("unit", 17)):
        result = await dispense(db_session, source, (cart_line(source, sale_format, quantity),))
        priced = await costs(db_session, source, result.command_id)
        allocations.extend(priced.cost_slices)
    cursor = 0
    for allocation in allocations:
        assert allocation.pack_ordinal_start == allocation.pack_count == 1
        assert allocation.unit_offset == cursor
        cursor += allocation.base_units
    assert cursor == 30
    assert sum((allocation.amount for allocation in allocations), Decimal("0.00")) == Decimal(
        "10.00"
    )
    assert await remaining(db_session, source) == 0


async def test_concurrent_different_commands_charge_the_last_tablet_exactly_once(
    db_session: AsyncSession,
) -> None:
    runtime = cast(ReceivingRuntime, db_session.info["receiving_runtime"])
    source = await priced_source(db_session)
    earlier = await dispense(db_session, source, (cart_line(source, "unit", 29),))
    earlier_cost = await costs(db_session, source, earlier.command_id)
    await db_session.commit()

    async def attempt() -> UnitCartDispenseCostResultV2 | None:
        async with runtime.factory.begin() as session:
            await set_receiving_context(session, runtime.stock, runtime.actor_id)
            try:
                result = await dispense(session, source, (cart_line(source, "unit", 1),))
            except BusinessRuleError:
                return None
            return await costs(session, source, result.command_id)

    results = await asyncio.wait_for(asyncio.gather(*(attempt() for _ in range(4))), timeout=15)
    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    assert earlier_cost.total_amount + winners[0].total_amount == Decimal("10.00")
    assert winners[0].cost_slices[0].unit_offset == 29
    await set_receiving_context(db_session, runtime.stock, runtime.actor_id)
    assert await remaining(db_session, source) == 0
    assert await db_session.scalar(
        text("SELECT sum(cost_amount) FROM unit_stock_movement_v2 WHERE lot_id=:id"),
        {"id": source.lot_id},
    ) == Decimal("10.00")
