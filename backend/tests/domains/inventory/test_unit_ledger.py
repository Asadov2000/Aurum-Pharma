"""Prepared physical stock is transactional and independent of legacy stock."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.errors import BusinessRuleError, ConflictError, PermissionDeniedError
from app.domains.catalog.repository import CatalogRepository
from app.domains.catalog.service import CatalogService
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.inventory.unit_ledger_models import UnitStockOperation
from app.domains.inventory.unit_ledger_repository import UnitLedgerRepository
from app.domains.inventory.unit_ledger_service import PreparedUnitLedgerService
from app.domains.inventory.unit_stock import UnitPackaging, UnitStockShortage

TODAY = date(2026, 10, 4)
PACKAGING = UnitPackaging(unit="tablet", units_per_pack=30, dispense_step=1)


@dataclass(frozen=True)
class LedgerContext:
    tenant_id: UUID
    branch_id: UUID
    catalog_id: UUID


async def seed_ledger_context(session: AsyncSession) -> LedgerContext:
    suffix = uuid4().hex
    foundation = FoundationService(FoundationRepository(session))
    tenant = await foundation.create_tenant(
        payload={
            "name": f"Unit ledger {suffix}",
            "status": "active",
            "contact_email": f"unit-ledger-{suffix}@aurum.test",
        }
    )
    branch = await foundation.create_branch(tenant_id=tenant.id, fields={"name": "Main"})
    item = await CatalogService(CatalogRepository(session)).create_item(
        tenant_id=tenant.id,
        fields={
            "brand_name": f"Unit ledger item {suffix}",
            "unit_sale_preparation": {
                **PACKAGING.model_dump(),
                "state": "prepared",
                "unit_price": "1.00",
            },
        },
    )
    return LedgerContext(tenant.id, branch.id, item.id)


async def prepare(
    session: AsyncSession,
    context: LedgerContext,
    *,
    packs: int = 1,
    operation_id: UUID | None = None,
) -> UUID:
    result = await PreparedUnitLedgerService(UnitLedgerRepository(session)).prepare_lot(
        tenant_id=context.tenant_id,
        branch_id=context.branch_id,
        catalog_id=context.catalog_id,
        operation_id=operation_id or uuid4(),
        packaging=PACKAGING,
        closed_packs=packs,
        expires_at=TODAY + timedelta(days=30),
        allowed_branch_ids={context.branch_id},
    )
    assert result.base_units == packs * 30
    return result.lot_id


@pytest_asyncio.fixture
async def committed_ledger(
    db_engine: AsyncEngine,
) -> AsyncIterator[tuple[async_sessionmaker[AsyncSession], LedgerContext]]:
    """Committed immutable rows live only in the disposable integration database."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory.begin() as session:
        database = await session.scalar(text("SELECT current_database()"))
        assert str(database).endswith("_test")
        await session.execute(text("SELECT set_config('app.support_session', 'true', true)"))
        context = await seed_ledger_context(session)
        await prepare(session, context)
    yield factory, context


async def concurrent_dispense(
    factory: async_sessionmaker[AsyncSession],
    context: LedgerContext,
    operation_id: UUID,
    quantity: int,
    *,
    claim_barrier: asyncio.Barrier | None = None,
) -> object:
    try:
        async with factory.begin() as session:
            await session.execute(text("SELECT set_config('app.support_session', 'true', true)"))
            repository = (
                UnitLedgerRepository(session)
                if claim_barrier is None
                else ClaimedOperationBarrierRepository(session, claim_barrier)
            )
            return await PreparedUnitLedgerService(repository).dispense(
                tenant_id=context.tenant_id,
                branch_id=context.branch_id,
                catalog_id=context.catalog_id,
                operation_id=operation_id,
                packaging=PACKAGING,
                sale_format="unit",
                quantity=quantity,
                today=TODAY,
                allowed_branch_ids={context.branch_id},
            )
    except UnitStockShortage as error:
        return error


class ClaimedOperationBarrierRepository(UnitLedgerRepository):
    """Hold both commands' FK locks before either requests the catalog lock."""

    def __init__(self, session: AsyncSession, barrier: asyncio.Barrier) -> None:
        super().__init__(session)
        self.barrier = barrier

    async def claim_operation(
        self, operation: UnitStockOperation
    ) -> tuple[UnitStockOperation, bool]:
        result = await super().claim_operation(operation)
        assert result[1], "Both competing commands must claim distinct operations"
        await self.barrier.wait()
        return result


async def test_concurrent_last_tablet_has_exactly_one_winner(
    committed_ledger: tuple[async_sessionmaker[AsyncSession], LedgerContext],
) -> None:
    factory, context = committed_ledger
    await concurrent_dispense(factory, context, uuid4(), 29)
    barrier = asyncio.Barrier(2)
    results = await asyncio.wait_for(
        asyncio.gather(
            concurrent_dispense(factory, context, uuid4(), 1, claim_barrier=barrier),
            concurrent_dispense(factory, context, uuid4(), 1, claim_barrier=barrier),
        ),
        timeout=15,
    )
    assert sum(isinstance(result, UnitStockShortage) for result in results) == 1
    async with factory.begin() as session:
        assert (
            await session.scalar(
                text(
                    "SELECT sum(remaining_units) FROM unit_stock_opened_pack "
                    "WHERE tenant_id = :tenant"
                ),
                {"tenant": context.tenant_id},
            )
            == 0
        )


async def test_concurrent_same_command_applies_once(
    committed_ledger: tuple[async_sessionmaker[AsyncSession], LedgerContext],
) -> None:
    factory, context = committed_ledger
    # Exercise both unique indexes across repeated concurrent command claims.
    rounds = 8
    for _ in range(rounds):
        operation_id = uuid4()
        results = await asyncio.wait_for(
            asyncio.gather(
                *(concurrent_dispense(factory, context, operation_id, 1) for _ in range(8))
            ),
            timeout=15,
        )
        assert all(result == results[0] for result in results)
        assert not isinstance(results[0], UnitStockShortage)
    async with factory.begin() as session:
        assert (
            await session.scalar(
                text(
                    "SELECT sum(remaining_units) FROM unit_stock_opened_pack "
                    "WHERE tenant_id = :tenant"
                ),
                {"tenant": context.tenant_id},
            )
            == 30 - rounds
        )
        assert (
            await session.scalar(
                text(
                    "SELECT count(*) FROM unit_stock_operation "
                    "WHERE tenant_id = :tenant AND kind = 'dispense'"
                ),
                {"tenant": context.tenant_id},
            )
            == rounds
        )


async def test_concurrent_command_rejects_changed_quantity(
    committed_ledger: tuple[async_sessionmaker[AsyncSession], LedgerContext],
) -> None:
    factory, context = committed_ledger
    operation_id = uuid4()
    results = await asyncio.wait_for(
        asyncio.gather(
            *(
                concurrent_dispense(factory, context, operation_id, quantity)
                for quantity in (1, 2) * 4
            ),
            return_exceptions=True,
        ),
        timeout=15,
    )
    assert sum(isinstance(result, ConflictError) for result in results) == 4
    successful = [result for result in results if not isinstance(result, Exception)]
    assert len(successful) == 4
    assert all(result == successful[0] for result in successful)
    async with factory.begin() as session:
        quantity = await session.scalar(
            text("SELECT quantity FROM unit_stock_operation WHERE tenant_id=:tenant AND id=:id"),
            {"tenant": context.tenant_id, "id": operation_id},
        )
        assert quantity in (1, 2)
        assert (
            await session.scalar(
                text(
                    "SELECT sum(remaining_units) FROM unit_stock_opened_pack "
                    "WHERE tenant_id=:tenant"
                ),
                {"tenant": context.tenant_id},
            )
            == 30 - quantity
        )


async def test_thirty_single_tablets_leave_zero_and_one_opened_pack(
    db_session: AsyncSession,
) -> None:
    context = await seed_ledger_context(db_session)
    lot_id = await prepare(db_session, context)
    service = PreparedUnitLedgerService(UnitLedgerRepository(db_session))
    for index in range(30):
        result = await service.dispense(
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
        assert result.base_units == 1
        assert sum(pick.base_units for pick in result.picks) == 1
        balance = await db_session.scalar(
            text(
                "SELECT closed_packs * 30 + COALESCE((SELECT SUM(remaining_units) "
                "FROM unit_stock_opened_pack WHERE lot_id = :lot), 0) "
                "FROM unit_stock_lot WHERE id = :lot"
            ),
            {"lot": lot_id},
        )
        assert balance == 29 - index
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM unit_stock_opened_pack WHERE lot_id = :lot"), {"lot": lot_id}
        )
        == 1
    )
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM batch WHERE tenant_id = :tenant"),
            {"tenant": context.tenant_id},
        )
        == 0
    )
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM sale WHERE tenant_id = :tenant"),
            {"tenant": context.tenant_id},
        )
        == 0
    )


async def test_initialization_replays_without_extra_stock_and_requires_exact_packaging(
    db_session: AsyncSession,
) -> None:
    context = await seed_ledger_context(db_session)
    operation_id = uuid4()
    first = await prepare(db_session, context, operation_id=operation_id)
    assert await prepare(db_session, context, operation_id=operation_id) == first
    assert (
        await db_session.scalar(
            text("SELECT sum(closed_packs) FROM unit_stock_lot WHERE tenant_id = :tenant"),
            {"tenant": context.tenant_id},
        )
        == 1
    )
    with pytest.raises(BusinessRuleError):
        await PreparedUnitLedgerService(UnitLedgerRepository(db_session)).prepare_lot(
            tenant_id=context.tenant_id,
            branch_id=context.branch_id,
            catalog_id=context.catalog_id,
            operation_id=uuid4(),
            packaging=UnitPackaging(unit="tablet", units_per_pack=60, dispense_step=1),
            closed_packs=1,
            expires_at=TODAY + timedelta(days=30),
            allowed_branch_ids={context.branch_id},
        )


async def test_operation_uuid_is_namespaced_by_tenant(db_session: AsyncSession) -> None:
    first_context = await seed_ledger_context(db_session)
    second_context = await seed_ledger_context(db_session)
    operation_id = uuid4()
    first_lot = await prepare(db_session, first_context, operation_id=operation_id)
    second_lot = await prepare(db_session, second_context, operation_id=operation_id)
    assert first_lot != second_lot
    assert await prepare(db_session, first_context, operation_id=operation_id) == first_lot
    assert await prepare(db_session, second_context, operation_id=operation_id) == second_lot
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM unit_stock_operation WHERE id = :operation"),
            {"operation": operation_id},
        )
        == 2
    )


async def test_fefo_across_lots_preserves_remainder_and_historical_replay(
    db_session: AsyncSession,
) -> None:
    context = await seed_ledger_context(db_session)
    service = PreparedUnitLedgerService(UnitLedgerRepository(db_session))
    later = await service.prepare_lot(
        tenant_id=context.tenant_id,
        branch_id=context.branch_id,
        catalog_id=context.catalog_id,
        operation_id=uuid4(),
        packaging=PACKAGING,
        closed_packs=1,
        expires_at=TODAY + timedelta(days=60),
        allowed_branch_ids={context.branch_id},
    )
    earlier = await service.prepare_lot(
        tenant_id=context.tenant_id,
        branch_id=context.branch_id,
        catalog_id=context.catalog_id,
        operation_id=uuid4(),
        packaging=PACKAGING,
        closed_packs=1,
        expires_at=TODAY + timedelta(days=30),
        allowed_branch_ids={context.branch_id},
    )
    operation_id = uuid4()
    first = await service.dispense(
        tenant_id=context.tenant_id,
        branch_id=context.branch_id,
        catalog_id=context.catalog_id,
        operation_id=operation_id,
        packaging=PACKAGING,
        sale_format="unit",
        quantity=31,
        today=TODAY,
        allowed_branch_ids={context.branch_id},
    )
    assert [(pick.batch_id, pick.base_units) for pick in first.picks] == [
        (earlier.lot_id, 30),
        (later.lot_id, 1),
    ]
    packs = (
        await db_session.execute(
            text(
                "SELECT lot_id, remaining_units FROM unit_stock_opened_pack "
                "WHERE tenant_id = :tenant"
            ),
            {"tenant": context.tenant_id},
        )
    ).all()
    assert [tuple(row) for row in packs] == [(later.lot_id, 29)]
    replay = await service.dispense(
        tenant_id=context.tenant_id,
        branch_id=context.branch_id,
        catalog_id=context.catalog_id,
        operation_id=operation_id,
        packaging=PACKAGING,
        sale_format="unit",
        quantity=31,
        today=TODAY,
        allowed_branch_ids={context.branch_id},
    )
    assert replay == first
    with pytest.raises(UnitStockShortage):
        await service.dispense(
            tenant_id=context.tenant_id,
            branch_id=context.branch_id,
            catalog_id=context.catalog_id,
            operation_id=uuid4(),
            packaging=PACKAGING,
            sale_format="pack",
            quantity=1,
            today=TODAY,
            allowed_branch_ids={context.branch_id},
        )
    await service.dispense(
        tenant_id=context.tenant_id,
        branch_id=context.branch_id,
        catalog_id=context.catalog_id,
        operation_id=uuid4(),
        packaging=PACKAGING,
        sale_format="unit",
        quantity=29,
        today=TODAY,
        allowed_branch_ids={context.branch_id},
    )
    assert (
        await db_session.scalar(
            text("SELECT sum(closed_packs) FROM unit_stock_lot WHERE tenant_id = :tenant"),
            {"tenant": context.tenant_id},
        )
        == 0
    )
    assert (
        await db_session.scalar(
            text(
                "SELECT sum(remaining_units) FROM unit_stock_opened_pack "
                "WHERE tenant_id = :tenant"
            ),
            {"tenant": context.tenant_id},
        )
        == 0
    )


async def test_replay_is_historical_and_changed_command_conflicts(
    db_session: AsyncSession,
) -> None:
    context = await seed_ledger_context(db_session)
    await prepare(db_session, context)
    service = PreparedUnitLedgerService(UnitLedgerRepository(db_session))
    operation_id = uuid4()
    first = await service.dispense(
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
    replay = await service.dispense(
        tenant_id=context.tenant_id,
        branch_id=context.branch_id,
        catalog_id=context.catalog_id,
        operation_id=operation_id,
        packaging=PACKAGING,
        sale_format="unit",
        quantity=1,
        today=TODAY + timedelta(days=60),
        allowed_branch_ids={context.branch_id},
    )
    assert replay == first
    with pytest.raises(ConflictError):
        await service.dispense(
            tenant_id=context.tenant_id,
            branch_id=context.branch_id,
            catalog_id=context.catalog_id,
            operation_id=operation_id,
            packaging=PACKAGING,
            sale_format="unit",
            quantity=2,
            today=TODAY,
            allowed_branch_ids={context.branch_id},
        )
    with pytest.raises(PermissionDeniedError):
        await service.dispense(
            tenant_id=context.tenant_id,
            branch_id=context.branch_id,
            catalog_id=context.catalog_id,
            operation_id=operation_id,
            packaging=PACKAGING,
            sale_format="unit",
            quantity=1,
            today=TODAY,
            allowed_branch_ids=set(),
        )


async def test_shortage_rolls_back_claim_and_outer_rollback_restores_stock(
    db_session: AsyncSession,
) -> None:
    context = await seed_ledger_context(db_session)
    lot_id = await prepare(db_session, context)
    service = PreparedUnitLedgerService(UnitLedgerRepository(db_session))
    operation_id = uuid4()
    with pytest.raises(UnitStockShortage):
        await service.dispense(
            tenant_id=context.tenant_id,
            branch_id=context.branch_id,
            catalog_id=context.catalog_id,
            operation_id=operation_id,
            packaging=PACKAGING,
            sale_format="unit",
            quantity=31,
            today=TODAY,
            allowed_branch_ids={context.branch_id},
        )
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM unit_stock_operation WHERE id = :operation"),
            {"operation": operation_id},
        )
        == 0
    )
    nested = await db_session.begin_nested()
    await service.dispense(
        tenant_id=context.tenant_id,
        branch_id=context.branch_id,
        catalog_id=context.catalog_id,
        operation_id=operation_id,
        packaging=PACKAGING,
        sale_format="pack",
        quantity=1,
        today=TODAY,
        allowed_branch_ids={context.branch_id},
    )
    await nested.rollback()
    assert (
        await db_session.scalar(
            text("SELECT closed_packs FROM unit_stock_lot WHERE id = :lot"), {"lot": lot_id}
        )
        == 1
    )
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM unit_stock_operation WHERE id = :operation"),
            {"operation": operation_id},
        )
        == 0
    )
