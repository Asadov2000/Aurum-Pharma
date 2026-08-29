"""Real PostgreSQL concurrency checks for the POS transaction boundary."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.errors import BusinessRuleError
from app.domains.auth.models import AppUser
from app.domains.catalog.repository import CatalogRepository
from app.domains.catalog.service import CatalogService
from app.domains.customer_returns.models import CustomerReturnQuarantineItem
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.inventory.models import Batch, BatchMovement
from app.domains.inventory.repository import InventoryRepository
from app.domains.pos.models import POSCommand, Sale, SalePayment, Shift
from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService
from app.domains.sync.models import SyncOutboxEvent
from app.domains.sync.repository import SyncOutboxRepository


@dataclass(frozen=True)
class CommittedPOS:
    tenant_id: UUID
    register_id: UUID
    catalog_id: UUID
    batch_id: UUID
    cashier_id: UUID
    shift_id: UUID
    initial_qty: Decimal


@pytest_asyncio.fixture
async def committed_pos(
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> AsyncIterator[tuple[async_sessionmaker[AsyncSession], CommittedPOS]]:
    factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)
    initial_qty = Decimal("20")

    async with factory.begin() as session:
        foundation = FoundationService(FoundationRepository(session))
        catalog = CatalogService(CatalogRepository(session))
        inventory = InventoryRepository(session)
        nick = uuid4().hex[:8]

        tenant = await foundation.create_tenant(
            payload={"name": f"Concurrency {nick}", "contact_email": f"c-{nick}@aurum.tj"}
        )
        await foundation.update_tenant(tenant.id, fields={"status": "active"})
        branch = await foundation.create_branch(tenant_id=tenant.id, fields={"name": "Main"})
        register = await foundation.create_register(
            tenant_id=tenant.id,
            fields={"branch_id": branch.id, "name": "Register"},
        )
        item = await catalog.create_item(
            tenant_id=tenant.id,
            fields={"brand_name": f"Drug {nick}", "dispensing_type": "otc"},
        )
        batch = await inventory.create_batch(
            tenant_id=tenant.id,
            branch_id=branch.id,
            catalog_id=item.id,
            expires_at=date.today() + timedelta(days=180),
            purchase_price=Decimal("3"),
            sale_price=Decimal("10"),
            qty_initial=initial_qty,
            qty_remaining=Decimal("0"),
        )
        await inventory.insert_movement(
            tenant_id=tenant.id,
            batch_id=batch.id,
            movement_type="incoming",
            qty_delta=initial_qty,
        )
        cashier = AppUser(
            email=f"cashier-{nick}@aurum.tj",
            full_name="Concurrency Cashier",
            home_tenant_id=tenant.id,
            status="active",
        )
        session.add(cashier)
        await session.flush()
        shift = await POSService(POSRepository(session)).open_shift(
            tenant_id=tenant.id,
            register_id=register.id,
            opened_by_user_id=cashier.id,
            opening_cash=Decimal("0"),
        )

        context = CommittedPOS(
            tenant_id=tenant.id,
            register_id=register.id,
            catalog_id=item.id,
            batch_id=batch.id,
            cashier_id=cashier.id,
            shift_id=shift.id,
            initial_qty=initial_qty,
        )

    try:
        yield factory, context
    finally:
        guarded_tables = (
            ("batch_movement", "trg_guard_batch_movement_immutability"),
            (
                "customer_return_disposition",
                "trg_immutable_customer_return_disposition",
            ),
            (
                "customer_return_quarantine_item",
                "trg_immutable_customer_return_quarantine",
            ),
            ("pos_command", "trg_pos_command_immutable"),
            ("prescription_log", "trg_guard_prescription_log_immutability"),
            ("sale_payment", "trg_guard_sale_payment_immutability"),
            ("sale_item", "trg_guard_sale_item_immutability"),
            ("sale", "trg_guard_sale_immutability"),
        )
        async with maintenance_engine.begin() as connection:
            for table, trigger in guarded_tables:
                await connection.execute(
                    text(f"ALTER TABLE public.{table} DISABLE TRIGGER {trigger}")
                )

        try:
            async with maintenance_engine.begin() as connection:
                await connection.execute(
                    text(
                        "DELETE FROM public.customer_return_disposition "
                        "WHERE tenant_id = :tenant_id"
                    ),
                    {"tenant_id": context.tenant_id},
                )
                await connection.execute(
                    text(
                        "DELETE FROM public.customer_return_quarantine_item "
                        "WHERE tenant_id = :tenant_id"
                    ),
                    {"tenant_id": context.tenant_id},
                )
                await connection.execute(
                    text("DELETE FROM public.audit_log WHERE tenant_id = :tenant_id"),
                    {"tenant_id": context.tenant_id},
                )
            async with db_engine.begin() as connection:
                await connection.execute(
                    text("SELECT set_config('app.support_session', 'true', true)")
                )
                await connection.execute(
                    text("DELETE FROM public.tenant WHERE id = :tenant_id"),
                    {"tenant_id": context.tenant_id},
                )
                await connection.execute(
                    text("DELETE FROM public.app_user WHERE id = :user_id"),
                    {"user_id": context.cashier_id},
                )
        finally:
            async with maintenance_engine.begin() as connection:
                for table, trigger in reversed(guarded_tables):
                    await connection.execute(
                        text(f"ALTER TABLE public.{table} ENABLE TRIGGER {trigger}")
                    )


async def _create_paid_sale(
    factory: async_sessionmaker[AsyncSession],
    context: CommittedPOS,
    *,
    qty: Decimal,
) -> tuple[UUID, UUID]:
    async with factory.begin() as session:
        service = POSService(POSRepository(session))
        sale = await service.create_sale(
            tenant_id=context.tenant_id,
            register_id=context.register_id,
            cashier_user_id=context.cashier_id,
        )
        items, _ = await service.add_item(
            sale_id=sale.id,
            catalog_id=context.catalog_id,
            qty=qty,
        )
        await service.add_payment(
            sale_id=sale.id,
            payment_method="cash",
            amount=qty * Decimal("10"),
        )
        return sale.id, items[0].id


async def _complete(
    factory: async_sessionmaker[AsyncSession],
    sale_id: UUID,
) -> tuple[UUID, str | None]:
    async with factory.begin() as session:
        sale = await POSService(POSRepository(session)).complete(sale_id=sale_id)
        return sale.id, sale.receipt_number


async def _atomic_checkout(
    factory: async_sessionmaker[AsyncSession],
    context: CommittedPOS,
    *,
    operation_id: UUID,
    qty: Decimal,
) -> tuple[UUID, UUID, str]:
    async with factory.begin() as session:
        result = await POSService(POSRepository(session)).checkout(
            tenant_id=context.tenant_id,
            register_id=context.register_id,
            cashier_user_id=context.cashier_id,
            operation_id=operation_id,
            items=[(context.catalog_id, qty)],
            payments=[("cash", qty * Decimal("10"), None)],
        )
        return result.sale_id, result.event_id, result.receipt_number


async def _create_draft_command(
    factory: async_sessionmaker[AsyncSession],
    context: CommittedPOS,
    *,
    operation_id: UUID,
) -> UUID:
    async with factory.begin() as session:
        sale = await POSService(POSRepository(session)).create_sale_command(
            tenant_id=context.tenant_id,
            register_id=context.register_id,
            cashier_user_id=context.cashier_id,
            operation_id=operation_id,
        )
        return sale.id


async def _add_payment(
    factory: async_sessionmaker[AsyncSession],
    *,
    sale_id: UUID,
    operation_id: UUID,
    amount: Decimal = Decimal("10"),
) -> UUID:
    async with factory.begin() as session:
        payment = await POSService(POSRepository(session)).add_payment(
            sale_id=sale_id,
            payment_method="cash",
            amount=amount,
            operation_id=operation_id,
            metadata={"terminal_id": "T-1"},
        )
        return payment.id


async def _refund(
    factory: async_sessionmaker[AsyncSession],
    context: CommittedPOS,
    *,
    parent_sale_id: UUID,
    parent_item_id: UUID,
    qty: Decimal,
    operation_id: UUID,
) -> UUID:
    async with factory.begin() as session:
        sale = await POSService(POSRepository(session)).refund(
            parent_sale_id=parent_sale_id,
            items=[(parent_item_id, qty)],
            reason="concurrency",
            comment=None,
            cashier_user_id=context.cashier_id,
            operation_id=operation_id,
        )
        return sale.id


async def _movement_count(session: AsyncSession, sale_id: UUID, movement_type: str) -> int:
    stmt = (
        select(func.count())
        .select_from(BatchMovement)
        .where(
            BatchMovement.source_id == sale_id,
            BatchMovement.movement_type == movement_type,
        )
    )
    return int((await session.execute(stmt)).scalar_one())


async def test_complete_is_idempotent_under_concurrent_requests(committed_pos) -> None:
    factory, context = committed_pos
    sale_id, _ = await _create_paid_sale(factory, context, qty=Decimal("2"))

    first, second = await asyncio.gather(
        _complete(factory, sale_id),
        _complete(factory, sale_id),
    )

    assert first == second
    async with factory() as session:
        assert await _movement_count(session, sale_id, "sale") == 1
        batch = await session.get(Batch, context.batch_id)
        assert batch is not None
        assert batch.qty_remaining == context.initial_qty - Decimal("2")


async def test_concurrent_identical_atomic_sales_have_one_effect(committed_pos) -> None:
    factory, context = committed_pos
    operation_id = uuid4()

    first, second = await asyncio.gather(
        _atomic_checkout(
            factory,
            context,
            operation_id=operation_id,
            qty=Decimal("2"),
        ),
        _atomic_checkout(
            factory,
            context,
            operation_id=operation_id,
            qty=Decimal("2.000"),
        ),
    )

    assert first == second
    async with factory() as session:
        sale_count = await session.scalar(
            select(func.count()).select_from(Sale).where(Sale.operation_id == operation_id)
        )
        outbox_count = await session.scalar(
            select(func.count())
            .select_from(SyncOutboxEvent)
            .where(SyncOutboxEvent.operation_id == operation_id)
        )
        batch = await session.get(Batch, context.batch_id)
        assert sale_count == 1
        assert outbox_count == 1
        assert batch is not None
        assert batch.qty_remaining == context.initial_qty - Decimal("2")
        assert await _movement_count(session, first[0], "sale") == 1


async def test_concurrent_identical_draft_commands_have_one_effect(committed_pos) -> None:
    factory, context = committed_pos
    operation_id = uuid4()

    first, second = await asyncio.gather(
        _create_draft_command(factory, context, operation_id=operation_id),
        _create_draft_command(factory, context, operation_id=operation_id),
    )

    assert first == second
    async with factory() as session:
        command_count = await session.scalar(
            select(func.count())
            .select_from(POSCommand)
            .where(
                POSCommand.tenant_id == context.tenant_id,
                POSCommand.operation_id == operation_id,
            )
        )
        sale_count = await session.scalar(
            select(func.count()).select_from(Sale).where(Sale.id == first)
        )
        assert command_count == 1
        assert sale_count == 1


async def test_concurrent_distinct_atomic_sales_do_not_oversell(committed_pos) -> None:
    factory, context = committed_pos
    operation_ids = (uuid4(), uuid4())

    results = await asyncio.gather(
        _atomic_checkout(
            factory,
            context,
            operation_id=operation_ids[0],
            qty=Decimal("15"),
        ),
        _atomic_checkout(
            factory,
            context,
            operation_id=operation_ids[1],
            qty=Decimal("15"),
        ),
        return_exceptions=True,
    )

    assert sum(isinstance(result, tuple) for result in results) == 1
    assert sum(isinstance(result, BusinessRuleError) for result in results) == 1
    async with factory() as session:
        batch = await session.get(Batch, context.batch_id)
        sale_count = await session.scalar(
            select(func.count()).select_from(Sale).where(Sale.operation_id.in_(operation_ids))
        )
        outbox_count = await session.scalar(
            select(func.count())
            .select_from(SyncOutboxEvent)
            .where(SyncOutboxEvent.operation_id.in_(operation_ids))
        )
        assert batch is not None
        assert batch.qty_remaining == Decimal("5")
        assert sale_count == 1
        assert outbox_count == 1


async def test_atomic_checkout_late_failure_rolls_back_transaction(
    committed_pos,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, context = committed_pos
    operation_id = uuid4()

    async def _fail_enqueue(self: SyncOutboxRepository, **fields: object) -> SyncOutboxEvent:
        del self, fields
        raise RuntimeError("injected outbox failure")

    monkeypatch.setattr(SyncOutboxRepository, "enqueue", _fail_enqueue)
    with pytest.raises(RuntimeError, match="injected outbox failure"):
        await _atomic_checkout(
            factory,
            context,
            operation_id=operation_id,
            qty=Decimal("2"),
        )

    async with factory() as session:
        batch = await session.get(Batch, context.batch_id)
        sale_count = await session.scalar(
            select(func.count()).select_from(Sale).where(Sale.operation_id == operation_id)
        )
        outbox_count = await session.scalar(
            select(func.count())
            .select_from(SyncOutboxEvent)
            .where(SyncOutboxEvent.operation_id == operation_id)
        )
        movement_count = await session.scalar(
            select(func.count())
            .select_from(BatchMovement)
            .where(
                BatchMovement.batch_id == context.batch_id,
                BatchMovement.movement_type == "sale",
            )
        )
        assert batch is not None
        assert batch.qty_remaining == context.initial_qty
        assert sale_count == 0
        assert outbox_count == 0
        assert movement_count == 0


async def test_concurrent_payment_retry_inserts_one_row(committed_pos) -> None:
    factory, context = committed_pos
    async with factory.begin() as session:
        service = POSService(POSRepository(session))
        sale = await service.create_sale(
            tenant_id=context.tenant_id,
            register_id=context.register_id,
            cashier_user_id=context.cashier_id,
        )
        await service.add_item(
            sale_id=sale.id,
            catalog_id=context.catalog_id,
            qty=Decimal("1"),
        )
        sale_id = sale.id
    operation_id = uuid4()

    first, second = await asyncio.gather(
        _add_payment(factory, sale_id=sale_id, operation_id=operation_id),
        _add_payment(factory, sale_id=sale_id, operation_id=operation_id),
    )

    assert first == second
    async with factory() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(SalePayment)
            .where(
                SalePayment.sale_id == sale_id,
                SalePayment.operation_id == operation_id,
            )
        )
        assert count == 1


async def test_concurrent_distinct_payments_cannot_exceed_sale_total(committed_pos) -> None:
    factory, context = committed_pos
    async with factory.begin() as session:
        service = POSService(POSRepository(session))
        sale = await service.create_sale(
            tenant_id=context.tenant_id,
            register_id=context.register_id,
            cashier_user_id=context.cashier_id,
        )
        await service.add_item(
            sale_id=sale.id,
            catalog_id=context.catalog_id,
            qty=Decimal("1"),
        )
        sale_id = sale.id

    results = await asyncio.gather(
        _add_payment(
            factory,
            sale_id=sale_id,
            operation_id=uuid4(),
            amount=Decimal("7"),
        ),
        _add_payment(
            factory,
            sale_id=sale_id,
            operation_id=uuid4(),
            amount=Decimal("7"),
        ),
        return_exceptions=True,
    )

    assert sum(isinstance(result, UUID) for result in results) == 1
    assert sum(isinstance(result, BusinessRuleError) for result in results) == 1
    async with factory() as session:
        repo = POSRepository(session)
        assert await repo.payments_total(sale_id) == Decimal("7.00")


async def test_concurrent_completes_allocate_unique_register_receipts(committed_pos) -> None:
    factory, context = committed_pos
    sale_a, _ = await _create_paid_sale(factory, context, qty=Decimal("1"))
    sale_b, _ = await _create_paid_sale(factory, context, qty=Decimal("1"))

    results = await asyncio.gather(_complete(factory, sale_a), _complete(factory, sale_b))

    assert {receipt for _, receipt in results} == {"000001", "000002"}
    async with factory() as session:
        seqs = (
            await session.execute(select(Sale.receipt_seq).where(Sale.id.in_([sale_a, sale_b])))
        ).scalars()
        assert set(seqs.all()) == {1, 2}


async def test_concurrent_refunds_cannot_exceed_original_quantity(committed_pos) -> None:
    factory, context = committed_pos
    parent_id, item_id = await _create_paid_sale(factory, context, qty=Decimal("5"))
    await _complete(factory, parent_id)

    results = await asyncio.gather(
        _refund(
            factory,
            context,
            parent_sale_id=parent_id,
            parent_item_id=item_id,
            qty=Decimal("3"),
            operation_id=uuid4(),
        ),
        _refund(
            factory,
            context,
            parent_sale_id=parent_id,
            parent_item_id=item_id,
            qty=Decimal("3"),
            operation_id=uuid4(),
        ),
        return_exceptions=True,
    )

    assert sum(isinstance(result, UUID) for result in results) == 1
    assert sum(isinstance(result, BusinessRuleError) for result in results) == 1


async def test_concurrent_refund_retry_has_one_effect(committed_pos) -> None:
    factory, context = committed_pos
    parent_id, item_id = await _create_paid_sale(factory, context, qty=Decimal("5"))
    await _complete(factory, parent_id)
    operation_id = uuid4()

    first, second = await asyncio.gather(
        _refund(
            factory,
            context,
            parent_sale_id=parent_id,
            parent_item_id=item_id,
            qty=Decimal("2"),
            operation_id=operation_id,
        ),
        _refund(
            factory,
            context,
            parent_sale_id=parent_id,
            parent_item_id=item_id,
            qty=Decimal("2"),
            operation_id=operation_id,
        ),
    )

    assert first == second
    async with factory() as session:
        assert await _movement_count(session, first, "sale_return") == 0
        quarantine_count = await session.scalar(
            select(func.count())
            .select_from(CustomerReturnQuarantineItem)
            .where(
                CustomerReturnQuarantineItem.tenant_id == context.tenant_id,
                CustomerReturnQuarantineItem.return_sale_id == first,
            )
        )
        assert quarantine_count == 1
        batch = await session.get(Batch, context.batch_id)
        assert batch is not None
        assert batch.qty_remaining == context.initial_qty - Decimal("5")


async def test_close_and_complete_race_has_consistent_result(committed_pos) -> None:
    factory, context = committed_pos
    sale_id, _ = await _create_paid_sale(factory, context, qty=Decimal("1"))

    async def close_shift() -> None:
        async with factory.begin() as session:
            await POSService(POSRepository(session)).close_shift(
                shift_id=context.shift_id,
                closing_cash_actual=Decimal("10"),
                closed_by_user_id=context.cashier_id,
            )

    results = await asyncio.gather(
        _complete(factory, sale_id),
        close_shift(),
        return_exceptions=True,
    )
    assert not any(
        isinstance(result, Exception) and not isinstance(result, BusinessRuleError)
        for result in results
    )

    async with factory() as session:
        sale = await session.get(Sale, sale_id)
        shift = await session.get(Shift, context.shift_id)
        assert sale is not None
        assert shift is not None
        if shift.status == "closed":
            assert shift.totals is not None
            if sale.status == "completed":
                assert shift.totals["sales_count"] == 1
                assert await _movement_count(session, sale_id, "sale") == 1
            else:
                assert sale.status == "draft"
                assert shift.totals["sales_count"] == 0
                assert await _movement_count(session, sale_id, "sale") == 0
        else:
            # Closing a shift that still contains an active draft is rejected.
            # The concurrently completed sale remains valid and the cashier can
            # close the shift again after this operation finishes.
            assert shift.status == "open"
            assert sale.status == "completed"
            assert await _movement_count(session, sale_id, "sale") == 1
