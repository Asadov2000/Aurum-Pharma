"""Purchase cost is conserved per physical pack and retained in immutable history."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta
from decimal import Decimal, Inexact, localcontext
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.errors import ConflictError
from app.domains.catalog.unit_sales import SaleFormat
from app.domains.inventory.unit_ledger_models import UnitStockLot, UnitStockOpenedPack
from app.domains.inventory.unit_ledger_repository import UnitLedgerRepository
from app.domains.inventory.unit_ledger_service import (
    CommittedUnitStockResult,
    PreparedLotResult,
    PreparedUnitLedgerService,
)
from app.domains.inventory.unit_stock import UnitStockShortage
from tests.domains.inventory.test_unit_ledger import (
    PACKAGING,
    TODAY,
    ClaimedOperationBarrierRepository,
    LedgerContext,
    seed_ledger_context,
)


async def prepare_priced(
    session: AsyncSession,
    context: LedgerContext,
    *,
    packs: int = 1,
    cost: Decimal | None = Decimal("10.00"),
    operation_id: UUID | None = None,
) -> PreparedLotResult:
    return await PreparedUnitLedgerService(UnitLedgerRepository(session)).prepare_lot(
        tenant_id=context.tenant_id,
        branch_id=context.branch_id,
        catalog_id=context.catalog_id,
        operation_id=operation_id or uuid4(),
        packaging=PACKAGING,
        closed_packs=packs,
        expires_at=TODAY + timedelta(days=30),
        allowed_branch_ids={context.branch_id},
        purchase_cost_per_pack=cost,
    )


async def take(
    session: AsyncSession,
    context: LedgerContext,
    quantity: int,
    *,
    sale_format: SaleFormat = "unit",
    operation_id: UUID | None = None,
) -> CommittedUnitStockResult:
    return await PreparedUnitLedgerService(UnitLedgerRepository(session)).dispense(
        tenant_id=context.tenant_id,
        branch_id=context.branch_id,
        catalog_id=context.catalog_id,
        operation_id=operation_id or uuid4(),
        packaging=PACKAGING,
        sale_format=sale_format,
        quantity=quantity,
        today=TODAY,
        allowed_branch_ids={context.branch_id},
    )


def total_cost(result: CommittedUnitStockResult) -> Decimal:
    return sum((part.amount for part in result.cost_slices), Decimal("0.00"))


async def test_thirty_tablets_conserve_original_pack_cost(db_session: AsyncSession) -> None:
    context = await seed_ledger_context(db_session)
    prepared = await prepare_priced(db_session, context)
    results = [await take(db_session, context, 1) for _ in range(30)]
    amounts = [total_cost(result) for result in results]
    assert set(amounts) == {Decimal("0.33"), Decimal("0.34")}
    assert amounts.count(Decimal("0.34")) == 10
    assert sum(amounts) == Decimal("10.00")
    slices = [part for result in results for part in result.cost_slices]
    assert len(slices) == 30
    assert {part.lot_id for part in slices} == {prepared.lot_id}
    assert {part.pack_ordinal_start for part in slices} == {1}
    assert [part.unit_offset for part in slices] == list(range(30))
    assert all(part.pack_count == part.base_units == 1 for part in slices)
    assert all(part.original_pack_cost == Decimal("10.00") for part in slices)
    assert all(part.currency == "TJS" and part.version == 1 for part in slices)


async def test_multiple_pack_ranges_and_partial_units_conserve_cost(
    db_session: AsyncSession,
) -> None:
    context = await seed_ledger_context(db_session)
    await prepare_priced(db_session, context, packs=5)
    first = await take(db_session, context, 2, sale_format="pack")
    partial = await take(db_session, context, 7)
    last_closed = await take(db_session, context, 2, sale_format="pack")
    remainder = await take(db_session, context, 23)
    assert total_cost(first) == total_cost(last_closed) == Decimal("20.00")
    assert total_cost(partial) + total_cost(remainder) == Decimal("10.00")
    assert total_cost(first) + total_cost(partial) + total_cost(last_closed) + total_cost(
        remainder
    ) == Decimal("50.00")
    assert len(first.cost_slices) == len(last_closed.cost_slices) == 1
    assert (first.cost_slices[0].pack_ordinal_start, first.cost_slices[0].pack_count) == (1, 2)
    assert (
        last_closed.cost_slices[0].pack_ordinal_start,
        last_closed.cost_slices[0].pack_count,
    ) == (
        4,
        2,
    )
    assert (
        partial.cost_slices[0].pack_ordinal_start
        == remainder.cost_slices[0].pack_ordinal_start
        == 3
    )
    assert partial.cost_slices[0].unit_offset == 0
    assert remainder.cost_slices[0].unit_offset == 7


async def test_aggregated_units_restart_rounding_at_each_physical_pack(
    db_session: AsyncSession,
) -> None:
    context = await seed_ledger_context(db_session)
    await prepare_priced(db_session, context, packs=4)
    first = await take(db_session, context, 67)
    last = await take(db_session, context, 53)
    assert total_cost(first) + total_cost(last) == Decimal("40.00")
    assert sum(part.base_units for part in first.cost_slices) == 67
    assert sum(part.base_units for part in last.cost_slices) == 53
    assert len(first.cost_slices) == 1
    assert first.cost_slices[0].pack_count == 3
    assert first.cost_slices[0].pack_ordinal_start == 1
    assert len(last.cost_slices) == 2
    assert last.cost_slices[0].unit_offset == 7


async def test_unknown_purchase_cost_remains_unknown(db_session: AsyncSession) -> None:
    context = await seed_ledger_context(db_session)
    prepared = await prepare_priced(db_session, context, cost=None)
    result = await take(db_session, context, 1)
    assert result.cost_slices == ()
    assert (
        await db_session.scalar(
            text("SELECT purchase_cost_per_pack FROM unit_stock_lot WHERE id=:lot"),
            {"lot": prepared.lot_id},
        )
        is None
    )
    assert (
        await db_session.scalar(
            text(
                "SELECT count(*) FROM unit_stock_entry "
                "WHERE lot_id=:lot AND cost_amount IS NOT NULL"
            ),
            {"lot": prepared.lot_id},
        )
        == 0
    )


async def test_initialization_replay_includes_exact_cost_and_preserves_history(
    db_session: AsyncSession,
) -> None:
    context = await seed_ledger_context(db_session)
    operation_id = uuid4()
    first = await prepare_priced(db_session, context, operation_id=operation_id)
    assert (
        await prepare_priced(db_session, context, cost=Decimal("10.0"), operation_id=operation_id)
        == first
    )
    for changed in (Decimal("10.01"), None):
        with pytest.raises(ConflictError):
            await prepare_priced(db_session, context, cost=changed, operation_id=operation_id)
    dispense_id = uuid4()
    result = await take(db_session, context, 1, operation_id=dispense_id)
    await take(db_session, context, 29)
    assert await take(db_session, context, 1, operation_id=dispense_id) == result


@pytest.mark.parametrize(
    "cost",
    [Decimal("-0.01"), Decimal("1000000000000.00"), Decimal("NaN"), Decimal("1.001")],
)
async def test_invalid_purchase_cost_creates_no_stock(
    db_session: AsyncSession, cost: Decimal
) -> None:
    context = await seed_ledger_context(db_session)
    with pytest.raises(ValueError):
        await prepare_priced(db_session, context, cost=cost)
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM unit_stock_operation WHERE tenant_id=:tenant"),
            {"tenant": context.tenant_id},
        )
        == 0
    )


async def test_cost_history_and_original_lot_snapshot_cannot_be_tampered(
    db_session: AsyncSession,
) -> None:
    context = await seed_ledger_context(db_session)
    prepared = await prepare_priced(db_session, context)
    result = await take(db_session, context, 1)
    for statement in (
        "UPDATE unit_stock_lot SET purchase_cost_per_pack=20 WHERE id=:lot",
        "UPDATE unit_stock_lot SET initial_closed_packs=2 WHERE id=:lot",
        "UPDATE unit_stock_lot SET cost_version=NULL WHERE id=:lot",
        "UPDATE unit_stock_entry SET cost_amount=999 WHERE id=:entry",
        "UPDATE unit_stock_entry SET cost_unit_offset=2 WHERE id=:entry",
        "DELETE FROM unit_stock_entry WHERE id=:entry",
        "UPDATE unit_stock_opened_pack SET pack_ordinal=2 WHERE lot_id=:lot",
    ):
        with pytest.raises(DBAPIError):
            async with db_session.begin_nested():
                await db_session.execute(
                    text(statement),
                    {"lot": prepared.lot_id, "entry": result.cost_slices[0].entry_id},
                )
    assert await take(db_session, context, 1, operation_id=result.operation_id) == result


async def test_aggregate_money_overflow_rolls_back_every_entry(db_session: AsyncSession) -> None:
    context = await seed_ledger_context(db_session)
    prepared = await prepare_priced(db_session, context, packs=2, cost=Decimal("999999999999.99"))
    with pytest.raises(DBAPIError):
        await take(db_session, context, 2, sale_format="pack")
    assert (
        await db_session.scalar(
            text("SELECT closed_packs FROM unit_stock_lot WHERE id=:lot"),
            {"lot": prepared.lot_id},
        )
        == 2
    )
    assert (
        await db_session.scalar(
            text(
                "SELECT count(*) FROM unit_stock_operation "
                "WHERE tenant_id=:tenant AND kind='dispense'"
            ),
            {"tenant": context.tenant_id},
        )
        == 0
    )


async def test_multiple_lots_cannot_overflow_operation_money_limit(
    db_session: AsyncSession,
) -> None:
    context = await seed_ledger_context(db_session)
    for _ in range(2):
        await prepare_priced(db_session, context, cost=Decimal("600000000000.00"))
    with pytest.raises(DBAPIError):
        await take(db_session, context, 2, sale_format="pack")
    assert (
        await db_session.scalar(
            text("SELECT sum(closed_packs) FROM unit_stock_lot WHERE tenant_id=:tenant"),
            {"tenant": context.tenant_id},
        )
        == 2
    )
    assert (
        await db_session.scalar(
            text(
                "SELECT count(*) FROM unit_stock_entry "
                "WHERE tenant_id=:tenant AND kind<>'initialize'"
            ),
            {"tenant": context.tenant_id},
        )
        == 0
    )


async def test_database_cost_allocation_is_independent_of_decimal_context(
    db_session: AsyncSession,
) -> None:
    context = await seed_ledger_context(db_session)
    with localcontext() as ambient:
        ambient.prec = 1
        ambient.traps[Inexact] = True
        await prepare_priced(db_session, context, cost=Decimal("999999999999.99"))
        first = await take(db_session, context, 1)
        remainder = await take(db_session, context, 29)
    assert first.cost_slices[0].amount == Decimal("33333333333.33")
    assert total_cost(first) + total_cost(remainder) == Decimal("999999999999.99")


class IncorrectInitialCountRepository(UnitLedgerRepository):
    async def insert_lot(self, lot: UnitStockLot) -> None:
        lot.initial_closed_packs = 2
        await super().insert_lot(lot)


class IncorrectPackOrdinalRepository(UnitLedgerRepository):
    async def insert_pack(self, pack: UnitStockOpenedPack) -> None:
        pack.pack_ordinal = 2
        await super().insert_pack(pack)


async def test_database_rejects_initial_snapshot_mismatching_received_quantity(
    db_session: AsyncSession,
) -> None:
    context = await seed_ledger_context(db_session)
    service = PreparedUnitLedgerService(IncorrectInitialCountRepository(db_session))
    with pytest.raises(DBAPIError):
        await service.prepare_lot(
            tenant_id=context.tenant_id,
            branch_id=context.branch_id,
            catalog_id=context.catalog_id,
            operation_id=uuid4(),
            packaging=PACKAGING,
            closed_packs=1,
            expires_at=TODAY + timedelta(days=30),
            allowed_branch_ids={context.branch_id},
            purchase_cost_per_pack=Decimal("10.00"),
        )


async def test_database_rejects_opened_pack_with_wrong_physical_ordinal(
    db_session: AsyncSession,
) -> None:
    context = await seed_ledger_context(db_session)
    await prepare_priced(db_session, context, packs=2)
    with pytest.raises(DBAPIError):
        await PreparedUnitLedgerService(IncorrectPackOrdinalRepository(db_session)).dispense(
            tenant_id=context.tenant_id,
            branch_id=context.branch_id,
            catalog_id=context.catalog_id,
            operation_id=uuid4(),
            packaging=PACKAGING,
            sale_format="unit",
            quantity=1,
            today=TODAY,
            allowed_branch_ids={context.branch_id},
        )
    assert total_cost(await take(db_session, context, 30)) == Decimal("10.00")


async def test_rollback_restores_cost_position_and_can_reuse_command(
    db_session: AsyncSession,
) -> None:
    context = await seed_ledger_context(db_session)
    await prepare_priced(db_session, context)
    operation_id = uuid4()
    transaction = await db_session.begin_nested()
    abandoned = await take(db_session, context, 7, operation_id=operation_id)
    await transaction.rollback()
    repeated = await take(db_session, context, 7, operation_id=operation_id)
    assert total_cost(abandoned) == total_cost(repeated)
    assert repeated.cost_slices[0].unit_offset == 0
    assert total_cost(repeated) + total_cost(await take(db_session, context, 23)) == Decimal(
        "10.00"
    )


@pytest_asyncio.fixture
async def committed_priced_ledger(
    db_engine: AsyncEngine,
) -> AsyncIterator[tuple[async_sessionmaker[AsyncSession], LedgerContext]]:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory.begin() as session:
        assert str(await session.scalar(text("SELECT current_database()"))).endswith("_test")
        await session.execute(text("SELECT set_config('app.support_session','true',true)"))
        context = await seed_ledger_context(session)
        await prepare_priced(session, context)
    yield factory, context


async def concurrent_priced_take(
    factory: async_sessionmaker[AsyncSession],
    context: LedgerContext,
    operation_id: UUID,
    *,
    barrier: asyncio.Barrier | None = None,
) -> CommittedUnitStockResult | UnitStockShortage:
    try:
        async with factory.begin() as session:
            await session.execute(text("SELECT set_config('app.support_session','true',true)"))
            repo = (
                UnitLedgerRepository(session)
                if barrier is None
                else ClaimedOperationBarrierRepository(session, barrier)
            )
            return await PreparedUnitLedgerService(repo).dispense(
                tenant_id=context.tenant_id,
                branch_id=context.branch_id,
                catalog_id=context.catalog_id,
                operation_id=operation_id,
                packaging=PACKAGING,
                sale_format="unit",
                quantity=1,
                today=TODAY,
                allowed_branch_ids={context.branch_id},
            )
    except UnitStockShortage as error:
        return error


async def test_simultaneous_last_unit_charges_once(
    committed_priced_ledger: tuple[async_sessionmaker[AsyncSession], LedgerContext],
) -> None:
    factory, context = committed_priced_ledger
    async with factory.begin() as session:
        await session.execute(text("SELECT set_config('app.support_session','true',true)"))
        first = await take(session, context, 29)
    barrier = asyncio.Barrier(2)
    results = await asyncio.wait_for(
        asyncio.gather(
            *(concurrent_priced_take(factory, context, uuid4(), barrier=barrier) for _ in range(2))
        ),
        timeout=15,
    )
    winners = [result for result in results if isinstance(result, CommittedUnitStockResult)]
    assert len(winners) == 1
    assert sum(isinstance(result, UnitStockShortage) for result in results) == 1
    assert total_cost(first) + total_cost(winners[0]) == Decimal("10.00")


async def test_simultaneous_replay_returns_same_immutable_cost_slices(
    committed_priced_ledger: tuple[async_sessionmaker[AsyncSession], LedgerContext],
) -> None:
    factory, context = committed_priced_ledger
    operation_id = uuid4()
    results = await asyncio.wait_for(
        asyncio.gather(*(concurrent_priced_take(factory, context, operation_id) for _ in range(4))),
        timeout=15,
    )
    assert all(result == results[0] for result in results)
    assert isinstance(results[0], CommittedUnitStockResult)
    assert len(results[0].cost_slices) == 1
    async with factory.begin() as session:
        assert await session.scalar(
            text("SELECT sum(cost_amount) FROM unit_stock_entry WHERE tenant_id=:tenant"),
            {"tenant": context.tenant_id},
        ) == total_cost(results[0])
