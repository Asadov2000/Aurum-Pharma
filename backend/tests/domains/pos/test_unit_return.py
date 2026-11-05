"""Received returns allocate original money into quarantine without sellable stock."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.errors import BusinessRuleError, ConflictError, PermissionDeniedError
from app.domains.inventory.unit_stock import UnitStockShortage
from app.domains.pos.unit_line_repository import UnitSaleLineRepository
from app.domains.pos.unit_line_service import (
    PreparedUnitSaleLineResult,
    PreparedUnitSaleLineService,
)
from app.domains.pos.unit_return_repository import UnitReturnRepository
from app.domains.pos.unit_return_service import UnitReturnResult, UnitReturnService
from tests.domains.incoming.test_unit_acceptance import (
    IncomingContext,
    ReceivingRuntime,
    accept_prepared,
    seed_prepared_incoming,
    seed_receiving_actor,
    set_receiving_context,
)
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.pos.test_received_unit_line import standalone_stock

db_session = receiving_session_fixture


def runtime(session: AsyncSession) -> ReceivingRuntime:
    return cast(ReceivingRuntime, session.info["receiving_runtime"])


async def seed_return_source(
    session: AsyncSession,
    *,
    factor: int = 30,
    step: int = 1,
    cost: Decimal = Decimal("10.00"),
    expiry_days: int = 180,
) -> IncomingContext:
    context = runtime(session)
    await session.execute(
        text(
            "UPDATE tenant_catalog SET base_price=25.00,unit_sale_preparation="
            "jsonb_set(jsonb_set(unit_sale_preparation,'{units_per_pack}',"
            "to_jsonb(CAST(:factor AS int))),'{dispense_step}',to_jsonb(CAST(:step AS int))) "
            "WHERE id=:id"
        ),
        {"id": context.stock.catalog_id, "factor": factor, "step": step},
    )
    incoming = await seed_prepared_incoming(session, cost=cost)
    await session.execute(
        text("UPDATE incoming_item SET expires_at=:expiry WHERE id=:id"),
        {"id": incoming.item_ids[0], "expiry": date.today() + timedelta(days=expiry_days)},
    )
    await accept_prepared(session, incoming)
    return incoming


async def post_return_original(
    session: AsyncSession,
    *,
    quantity: int,
    sale_format: str = "unit",
    discount: Decimal = Decimal("0.00"),
    received: bool = True,
) -> PreparedUnitSaleLineResult:
    context = runtime(session)
    service = PreparedUnitSaleLineService(UnitSaleLineRepository(session))
    if sale_format not in ("pack", "unit"):
        raise ValueError("unsupported test sale format")
    # Keep the literal format typed at the service boundary.
    if received:
        return await service.post_received_line(
            tenant_id=context.stock.tenant_id,
            branch_id=context.stock.branch_id,
            catalog_id=context.stock.catalog_id,
            operation_id=uuid4(),
            actor_id=context.actor_id,
            sale_format="pack" if sale_format == "pack" else "unit",
            quantity=quantity,
            discount_amount=discount,
            today=date.today(),
            allowed_branch_ids={context.stock.branch_id},
        )
    return await service.post_line(
        tenant_id=context.stock.tenant_id,
        branch_id=context.stock.branch_id,
        catalog_id=context.stock.catalog_id,
        operation_id=uuid4(),
        sale_format="pack" if sale_format == "pack" else "unit",
        quantity=quantity,
        discount_amount=discount,
        today=date.today(),
        allowed_branch_ids={context.stock.branch_id},
    )


async def return_units(
    session: AsyncSession,
    original_line_id: UUID,
    quantity: int,
    *,
    operation_id: UUID | None = None,
    actor_id: UUID | None = None,
) -> UnitReturnResult:
    context = runtime(session)
    return await UnitReturnService(UnitReturnRepository(session)).return_received_line(
        tenant_id=context.stock.tenant_id,
        branch_id=context.stock.branch_id,
        actor_id=actor_id or context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        operation_id=operation_id or uuid4(),
        original_line_id=original_line_id,
        quantity=quantity,
    )


@pytest.mark.parametrize("factor", [3, 7, 30])
@pytest.mark.parametrize("cost", [Decimal("10.00"), Decimal("0.00")])
async def test_split_return_conserves_original_net_discount_and_physical_cost(
    db_session: AsyncSession,
    factor: int,
    cost: Decimal,
) -> None:
    await seed_return_source(db_session, factor=factor, cost=cost)
    original = await post_return_original(db_session, quantity=factor, discount=Decimal("0.01"))
    parts = [await return_units(db_session, original.snapshot.id, 1) for _ in range(factor)]
    assert [part.allocation_offset for part in parts] == list(range(factor))
    assert all(part.state == "prepared_quarantine" and part.base_units == 1 for part in parts)
    assert parts[0].net_amount == Decimal("0.99")
    assert parts[-1].net_amount == Decimal("1.00")
    assert all(part.gross_amount == Decimal("1.00") for part in parts)
    assert parts[0].discount_amount == Decimal("0.01")
    assert parts[-1].discount_amount == Decimal("0.00")
    assert sum((part.gross_amount for part in parts), Decimal()) == original.snapshot.gross_amount
    assert sum((part.discount_amount for part in parts), Decimal()) == Decimal("0.01")
    assert sum((part.net_amount for part in parts), Decimal()) == original.snapshot.net_amount
    assert sum((part.cost_amount for part in parts), Decimal()) == cost
    if cost:
        assert parts[-1].cost_amount >= parts[0].cost_amount
    else:
        assert all(part.cost_amount == Decimal("0.00") for part in parts)
    with pytest.raises(BusinessRuleError):
        await return_units(db_session, original.snapshot.id, 1)


async def test_partial_original_range_keeps_pack_last_cent_instead_of_dividing_sale_cost(
    db_session: AsyncSession,
) -> None:
    await seed_return_source(db_session)
    await post_return_original(db_session, quantity=1)
    original = await post_return_original(db_session, quantity=29)
    first = await return_units(db_session, original.snapshot.id, 28)
    last = await return_units(db_session, original.snapshot.id, 1)
    assert first.cost_amount == Decimal("9.33")
    assert last.cost_amount == Decimal("0.34")
    assert first.cost_amount + last.cost_amount == original.snapshot.original_cost_amount


async def test_combined_and_split_returns_have_equal_original_totals(
    db_session: AsyncSession,
) -> None:
    await seed_return_source(db_session, factor=3)
    one = await post_return_original(db_session, quantity=3, discount=Decimal("0.01"))
    two = await post_return_original(db_session, quantity=3, discount=Decimal("0.01"))
    combined = await return_units(db_session, one.snapshot.id, 3)
    split = [await return_units(db_session, two.snapshot.id, amount) for amount in (1, 2)]
    assert combined.net_amount == sum((part.net_amount for part in split), Decimal())
    assert combined.discount_amount == sum((part.discount_amount for part in split), Decimal())
    assert combined.gross_amount == sum((part.gross_amount for part in split), Decimal())
    assert combined.cost_amount == sum((part.cost_amount for part in split), Decimal())


async def test_return_crosses_fefo_source_lots_with_their_distinct_original_prices(
    db_session: AsyncSession,
) -> None:
    await seed_return_source(db_session, factor=3, cost=Decimal("10.00"), expiry_days=30)
    await seed_return_source(db_session, factor=3, cost=Decimal("21.00"), expiry_days=60)
    original = await post_return_original(db_session, quantity=7)
    assert original.snapshot.original_cost_amount == Decimal("27.00")
    first = await return_units(db_session, original.snapshot.id, 6)
    last = await return_units(db_session, original.snapshot.id, 1)
    assert first.cost_amount == Decimal("20.00") and last.cost_amount == Decimal("7.00")
    assert len({part.lot_id for result in (first, last) for part in result.slices}) == 2


async def test_pack_return_uses_display_pack_quantity_and_original_discount(
    db_session: AsyncSession,
) -> None:
    await seed_return_source(db_session)
    original = await post_return_original(
        db_session,
        quantity=2,
        sale_format="pack",
        discount=Decimal("0.01"),
    )
    first = await return_units(db_session, original.snapshot.id, 1)
    last = await return_units(db_session, original.snapshot.id, 1)
    assert first.base_units == last.base_units == 30
    assert first.net_amount == Decimal("24.99") and last.net_amount == Decimal("25.00")
    assert first.cost_amount == last.cost_amount == Decimal("10.00")


async def test_step_return_rejects_partial_dispense_step_without_claim(
    db_session: AsyncSession,
) -> None:
    await seed_return_source(db_session, factor=30, step=3)
    original = await post_return_original(db_session, quantity=6)
    with pytest.raises((ValueError, BusinessRuleError)):
        await return_units(db_session, original.snapshot.id, 1)
    accepted = await return_units(db_session, original.snapshot.id, 3)
    assert accepted.allocation_offset == 0 and accepted.base_units == 3


async def test_replay_keeps_original_amounts_after_prices_change_and_conflicts_on_payload(
    db_session: AsyncSession,
) -> None:
    await seed_return_source(db_session)
    original = await post_return_original(db_session, quantity=3, discount=Decimal("0.01"))
    other = await post_return_original(db_session, quantity=3)
    operation_id = uuid4()
    first = await return_units(db_session, original.snapshot.id, 1, operation_id=operation_id)
    await db_session.execute(
        text(
            "UPDATE tenant_catalog SET base_price=999.00,unit_sale_preparation="
            "jsonb_set(unit_sale_preparation,'{unit_price}','\"99.00\"') WHERE id=:id"
        ),
        {"id": runtime(db_session).stock.catalog_id},
    )
    assert (
        await return_units(
            db_session,
            original.snapshot.id,
            1,
            operation_id=operation_id,
        )
        == first
    )
    for line_id, quantity in ((original.snapshot.id, 2), (other.snapshot.id, 1)):
        with pytest.raises(ConflictError):
            await return_units(db_session, line_id, quantity, operation_id=operation_id)


async def test_standalone_prepared_line_cannot_be_returned_through_received_path(
    db_session: AsyncSession,
) -> None:
    incoming = await seed_return_source(db_session)
    await standalone_stock(db_session, incoming)
    original = await post_return_original(db_session, quantity=1, received=False)
    with pytest.raises(BusinessRuleError):
        await return_units(db_session, original.snapshot.id, 1)


async def test_return_never_increases_sellable_stock_or_creates_legacy_financial_effects(
    db_session: AsyncSession,
) -> None:
    await seed_return_source(db_session)
    original = await post_return_original(db_session, quantity=60)
    context = runtime(db_session)
    before = await db_session.scalar(
        text("SELECT count(*) FROM unit_stock_entry WHERE tenant_id=:tenant"),
        {"tenant": context.stock.tenant_id},
    )
    await return_units(db_session, original.snapshot.id, 60)
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM unit_stock_entry WHERE tenant_id=:tenant"),
            {"tenant": context.stock.tenant_id},
        )
        == before
    )
    assert (
        await db_session.scalar(
            text(
                "SELECT coalesce(sum(closed_packs),0) FROM unit_stock_lot WHERE tenant_id=:tenant"
            ),
            {"tenant": context.stock.tenant_id},
        )
        == 0
    )
    for table in ("sale", "sale_item", "sale_payment", "shift"):
        assert (
            await db_session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id=:tenant"),
                {"tenant": context.stock.tenant_id},
            )
            == 0
        )
    with pytest.raises(UnitStockShortage):
        await post_return_original(db_session, quantity=1)


async def test_refund_only_actor_can_return_without_sell_or_incoming_permission(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    context = runtime(db_session)
    await seed_return_source(db_session)
    original = await post_return_original(db_session, quantity=3)
    await db_session.commit()
    actor = await seed_receiving_actor(
        db_engine,
        maintenance_engine,
        context.stock,
        permissions=("pos.refund",),
    )
    await set_receiving_context(db_session, context.stock, actor)
    returned = await return_units(db_session, original.snapshot.id, 1, actor_id=actor)
    assert returned.cost_amount == Decimal("0.33")
    assert (
        await return_units(
            db_session,
            original.snapshot.id,
            1,
            actor_id=actor,
            operation_id=returned.command_id,
        )
        == returned
    )


async def test_actor_scope_and_permission_are_rechecked_before_initial_and_replay(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    context = runtime(db_session)
    await seed_return_source(db_session)
    original = await post_return_original(db_session, quantity=3)
    returned = await return_units(db_session, original.snapshot.id, 1)
    await db_session.commit()
    no_refund = await seed_receiving_actor(
        db_engine,
        maintenance_engine,
        context.stock,
        permissions=("pos.sell",),
    )
    service = UnitReturnService(UnitReturnRepository(db_session))
    for operation_id in (uuid4(), returned.command_id):
        with pytest.raises(PermissionDeniedError):
            await return_units(
                db_session,
                original.snapshot.id,
                1,
                operation_id=operation_id,
                actor_id=uuid4(),
            )
        for tenant, branch, allowed in (
            (uuid4(), context.stock.branch_id, {context.stock.branch_id}),
            (context.stock.tenant_id, uuid4(), {context.stock.branch_id}),
            (context.stock.tenant_id, context.stock.branch_id, set()),
        ):
            with pytest.raises(PermissionDeniedError):
                await service.return_received_line(
                    tenant_id=tenant,
                    branch_id=branch,
                    actor_id=context.actor_id,
                    allowed_branch_ids=allowed,
                    operation_id=operation_id,
                    original_line_id=original.snapshot.id,
                    quantity=1,
                )
        await set_receiving_context(db_session, context.stock, no_refund)
        with pytest.raises(PermissionDeniedError):
            await return_units(
                db_session,
                original.snapshot.id,
                1,
                operation_id=operation_id,
                actor_id=no_refund,
            )
        await set_receiving_context(db_session, context.stock, context.actor_id)


async def test_support_context_cannot_impersonate_refund_actor(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
) -> None:
    context = runtime(db_session)
    await seed_return_source(db_session)
    original = await post_return_original(db_session, quantity=1)
    await db_session.commit()
    async with AsyncSession(db_engine) as support:
        async with support.begin():
            await support.execute(
                text(
                    "SELECT set_config('app.support_session','true',true),"
                    "set_config('app.tenant_id',:tenant,true),"
                    "set_config('app.user_id',:actor,true)"
                ),
                {"tenant": str(context.stock.tenant_id), "actor": str(context.actor_id)},
            )
            with pytest.raises(PermissionDeniedError):
                await UnitReturnService(UnitReturnRepository(support)).return_received_line(
                    tenant_id=context.stock.tenant_id,
                    branch_id=context.stock.branch_id,
                    actor_id=context.actor_id,
                    allowed_branch_ids={context.stock.branch_id},
                    operation_id=uuid4(),
                    original_line_id=original.snapshot.id,
                    quantity=1,
                )


async def test_outer_rollback_removes_return_claim_and_slices_without_advancing_cursor(
    db_session: AsyncSession,
) -> None:
    context = runtime(db_session)
    await seed_return_source(db_session)
    original = await post_return_original(db_session, quantity=3)
    await db_session.commit()
    await set_receiving_context(db_session, context.stock, context.actor_id)
    operation_id = uuid4()
    await return_units(db_session, original.snapshot.id, 2, operation_id=operation_id)
    await db_session.rollback()
    await set_receiving_context(db_session, context.stock, context.actor_id)
    for table in ("pos_unit_return_command", "pos_unit_return_slice"):
        assert (
            await db_session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id=:tenant"),
                {"tenant": context.stock.tenant_id},
            )
            == 0
        )
    retried = await return_units(db_session, original.snapshot.id, 2, operation_id=operation_id)
    assert retried.allocation_offset == 0 and retried.cost_amount == Decimal("0.66")


async def concurrent_return(
    factory: async_sessionmaker[AsyncSession],
    context: ReceivingRuntime,
    line_id: UUID,
    operation_id: UUID,
) -> UnitReturnResult | BusinessRuleError:
    try:
        async with factory.begin() as session:
            session.info["receiving_runtime"] = context
            await set_receiving_context(session, context.stock, context.actor_id)
            return await return_units(session, line_id, 1, operation_id=operation_id)
    except BusinessRuleError as error:
        return error


@pytest.mark.parametrize("same_operation", [True, False])
async def test_concurrent_retry_or_last_return_has_one_committed_allocation(
    db_session: AsyncSession,
    same_operation: bool,
) -> None:
    context = runtime(db_session)
    await seed_return_source(db_session)
    original = await post_return_original(db_session, quantity=1)
    await db_session.commit()
    operation_id = uuid4()
    results = await asyncio.wait_for(
        asyncio.gather(
            *(
                concurrent_return(
                    context.factory,
                    context,
                    original.snapshot.id,
                    operation_id if same_operation else uuid4(),
                )
                for _ in range(3)
            )
        ),
        timeout=15,
    )
    winners = [result for result in results if isinstance(result, UnitReturnResult)]
    assert len(winners) == (3 if same_operation else 1)
    assert all(result == winners[0] for result in winners)
    assert sum(isinstance(result, BusinessRuleError) for result in results) == (
        0 if same_operation else 2
    )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM pos_unit_return_command WHERE original_line_id=:line"),
            {"line": original.snapshot.id},
        )
        == 1
    )
    assert (
        await db_session.scalar(
            text("SELECT sum(base_units) FROM pos_unit_return_slice WHERE command_id=:id"),
            {"id": winners[0].command_id},
        )
        == 1
    )
