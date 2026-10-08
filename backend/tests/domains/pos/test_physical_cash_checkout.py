"""Physical cash checkout posts one complete sale or rolls the whole operation back."""

import asyncio
from datetime import datetime
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError, PermissionDeniedError
from app.domains.inventory.unit_dispensing_v2_repository import UnitDispensingV2Repository
from app.domains.inventory.unit_dispensing_v2_service import PhysicalUnitLedgerV2Service
from app.domains.inventory.unit_ledger_v2_repository import UnitLedgerV2Repository
from app.domains.inventory.unit_ledger_v2_service import PreparedUnitLedgerV2Service
from app.domains.inventory.unit_physical_blocking import PhysicalLotBlockingService
from app.domains.inventory.unit_stock_v2 import SaleFormatV2, UnitStockBatchV2
from app.domains.pos.models import Sale
from app.domains.pos.physical_cash_checkout_repository import PhysicalCashCheckoutRepository
from app.domains.pos.physical_cash_checkout_service import PhysicalCashCheckoutService
from app.domains.pos.physical_documents import (
    PhysicalCheckoutResultV3,
    PhysicalReceiptDocumentV3,
    build_physical_receipt,
)
from app.domains.pos.unit_cash_confirmation import CashConfirmationRequestV2
from app.domains.pos.unit_checkout_repository import UnitCheckoutRepository
from tests.domains.incoming.test_unit_acceptance import (
    ReceivingRuntime,
    seed_receiving_actor,
    set_receiving_context,
)
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance_v2 import seed_v2_incoming
from tests.domains.inventory.test_unit_ledger import seed_ledger_context
from tests.domains.inventory.test_unit_ledger_v2_dispensing import cart_line, dispense, remaining
from tests.domains.inventory.test_unit_retail_ledger_v2 import retail_line
from tests.domains.pos.test_unit_cart_quote import add_legacy
from tests.domains.pos.test_unit_physical_cart import physical_request, put_physical
from tests.domains.pos.test_unit_physical_checkout_preparation import (
    checkout_source,
    saved_request,
)
from tests.domains.pos.test_unit_physical_checkout_preparation import facts as stock_and_sale_facts
from tests.domains.pos.test_unit_prescription import attach
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


async def facts(session: AsyncSession) -> dict[str, list[object]]:
    result = await stock_and_sale_facts(session)
    for table in ("pos_physical_checkout", "register"):
        result[table] = list(
            (
                await session.execute(
                    text(
                        f"SELECT to_jsonb(row) FROM {table} row WHERE tenant_id=:tenant "
                        "ORDER BY to_jsonb(row)::text"
                    ),
                    {"tenant": runtime(session).stock.tenant_id},
                )
            ).scalars()
        )
    return result


async def complete(
    session: AsyncSession, request: CashConfirmationRequestV2
) -> PhysicalCheckoutResultV3:
    context = runtime(session)
    return await PhysicalCashCheckoutService(PhysicalCashCheckoutRepository(session)).complete(
        tenant_id=context.stock.tenant_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        request=request,
    )


@pytest.mark.parametrize(
    "sale_format,quantity,units", [("pack", 1, 30), ("blister", 2, 20), ("unit", 5, 5)]
)
async def test_physical_cash_posts_exact_format_and_stock_once(
    db_session: AsyncSession, sale_format: SaleFormatV2, quantity: int, units: int
) -> None:
    source = await checkout_source(db_session)
    item = await put_physical(
        db_session, source.sale.id, sale_format=sale_format, quantity=quantity
    )
    request = await saved_request(db_session, source.sale)
    result = await complete(db_session, request)
    await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    assert result.sale_id == source.sale.id
    assert result.operation_id == request.operation_id == result.stock_command_id
    assert result.lines[0].id == item.id
    assert result.lines[0].sale_format == sale_format
    assert result.lines[0].quantity == quantity
    assert result.lines[0].base_units == units
    assert result.total_amount == request.expected_total_amount
    assert len(result.payments) == 1
    assert result.payments[0].method == "cash"
    assert result.payments[0].amount == result.total_amount
    assert await remaining(db_session, source.stock) == 60 - units
    row = (
        await db_session.execute(
            text("SELECT status,total_amount FROM sale WHERE id=:id"), {"id": source.sale.id}
        )
    ).one()
    assert row.status == "completed"
    assert row.total_amount == result.total_amount
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM sale_payment WHERE sale_id=:id"), {"id": source.sale.id}
        )
        == 1
    )
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM sale_item WHERE sale_id=:id"), {"id": source.sale.id}
        )
        == 0
    )
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM unit_stock_cart_command_v2 WHERE id=:id"),
            {"id": request.operation_id},
        )
        == 1
    )
    before = await facts(db_session)
    assert await complete(db_session, request) == result
    assert await facts(db_session) == before


async def test_three_formats_share_one_atomic_sale_and_receipt(db_session: AsyncSession) -> None:
    source = await checkout_source(db_session)
    formats: tuple[SaleFormatV2, ...] = ("pack", "blister", "unit")
    for position, (sale_format, quantity) in enumerate(zip(formats, (1, 2, 5), strict=True), 1):
        await put_physical(
            db_session,
            source.sale.id,
            position=position,
            sale_format=sale_format,
            quantity=quantity,
            discount=Decimal("0.50"),
        )
    result = await complete(db_session, await saved_request(db_session, source.sale))
    await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    assert tuple(line.base_units for line in result.lines) == (30, 20, 5)
    assert tuple(line.position for line in result.lines) == (1, 2, 3)
    assert sum(line.net_amount for line in result.lines) == result.total_amount
    assert await remaining(db_session, source.stock) == 5
    receipt = build_physical_receipt(result)
    assert tuple(line.unit_label for line in receipt.lines) == ("уп.", "блист.", "табл.")


async def test_sparse_cart_positions_preserve_display_order_in_dense_receipt(
    db_session: AsyncSession,
) -> None:
    source = await checkout_source(db_session)
    first = await put_physical(db_session, source.sale.id, position=3, sale_format="blister")
    second = await put_physical(
        db_session, source.sale.id, position=9, sale_format="unit", quantity=2
    )
    result = await complete(db_session, await saved_request(db_session, source.sale))
    await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    assert tuple(line.id for line in result.lines) == (first.id, second.id)
    assert tuple(line.position for line in result.lines) == (1, 2)
    assert tuple(line.base_units for line in result.lines) == (10, 2)
    receipt = build_physical_receipt(result)
    assert tuple(line.position for line in receipt.lines) == (1, 2)
    assert tuple(line.unit_label for line in receipt.lines) == ("блист.", "табл.")
    assert await remaining(db_session, source.stock) == 48


async def test_replay_uses_saved_price_name_and_result_after_later_block(
    db_session: AsyncSession,
) -> None:
    source = await checkout_source(db_session)
    await put_physical(db_session, source.sale.id)
    request = await saved_request(db_session, source.sale)
    original = await complete(db_session, request)
    context = runtime(db_session)
    await PhysicalLotBlockingService(UnitDispensingV2Repository(db_session)).set_blocked(
        tenant_id=context.stock.tenant_id,
        branch_id=context.stock.branch_id,
        lot_id=source.stock.lot_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        is_blocked=True,
    )
    await db_session.execute(
        text(
            "UPDATE tenant_catalog SET brand_name='Changed current name',base_price=999.00 "
            "WHERE id=:id"
        ),
        {"id": context.stock.catalog_id},
    )
    before = await facts(db_session)
    replay = await complete(db_session, request)
    assert replay == original
    assert build_physical_receipt(replay) == build_physical_receipt(original)
    assert await facts(db_session) == before
    with pytest.raises(ConflictError):
        await complete(
            db_session, request.model_copy(update={"cash_tendered": request.cash_tendered + 1})
        )


@pytest.mark.parametrize(
    "failure", ["cash_missing", "stale_price", "shortage", "block", "rx", "legacy"]
)
async def test_rejected_checkout_leaves_no_payment_stock_or_completed_sale(
    db_session: AsyncSession, failure: str
) -> None:
    source = await checkout_source(db_session, quantity=1)
    await put_physical(db_session, source.sale.id, sale_format="pack")
    request = await saved_request(db_session, source.sale)
    context = runtime(db_session)
    if failure == "cash_missing":
        request = await physical_request(db_session, source.sale.id)
    elif failure == "stale_price":
        await db_session.execute(
            text("UPDATE tenant_catalog SET base_price=999.00 WHERE id=:id"),
            {"id": context.stock.catalog_id},
        )
    elif failure == "shortage":
        await dispense(db_session, source.stock, (cart_line(source.stock, "pack", 1),))
    elif failure == "block":
        await PhysicalLotBlockingService(UnitDispensingV2Repository(db_session)).set_blocked(
            tenant_id=context.stock.tenant_id,
            branch_id=context.stock.branch_id,
            lot_id=source.stock.lot_id,
            actor_id=context.actor_id,
            allowed_branch_ids={context.stock.branch_id},
            is_blocked=True,
        )
    elif failure == "rx":
        await db_session.execute(
            text("UPDATE tenant_catalog SET dispensing_type='prescription' WHERE id=:id"),
            {"id": context.stock.catalog_id},
        )
    else:
        await add_legacy(db_session, source.sale)
    before = await facts(db_session)
    with pytest.raises((BusinessRuleError, ConflictError, NotFoundError)):
        await complete(db_session, request)
    assert await facts(db_session) == before


async def test_prescription_is_checked_before_actual_stock_and_payment(
    db_session: AsyncSession,
) -> None:
    source = await checkout_source(db_session)
    await db_session.execute(
        text("UPDATE tenant_catalog SET dispensing_type='prescription' WHERE id=:id"),
        {"id": source.stock.incoming.stock.catalog_id},
    )
    item = await put_physical(db_session, source.sale.id)
    request = await saved_request(db_session, source.sale)
    before = await facts(db_session)
    with pytest.raises(BusinessRuleError):
        await complete(db_session, request)
    assert await facts(db_session) == before
    await attach(db_session, source.sale.id, item.id)
    result = await complete(db_session, request)
    assert result.lines[0].id == item.id
    await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))


@pytest.mark.parametrize("completed", [False, True])
@pytest.mark.parametrize("scope", ["actor", "tenant", "branch", "actual_actor"])
async def test_initial_checkout_and_replay_require_actual_cashier_scope(
    db_session: AsyncSession, completed: bool, scope: str
) -> None:
    source = await checkout_source(db_session)
    await put_physical(db_session, source.sale.id)
    request = await saved_request(db_session, source.sale)
    if completed:
        await complete(db_session, request)
    context = runtime(db_session)
    if scope == "actual_actor":
        await set_receiving_context(db_session, context.stock, uuid4())
    with pytest.raises((PermissionDeniedError, NotFoundError)):
        await PhysicalCashCheckoutService(PhysicalCashCheckoutRepository(db_session)).complete(
            tenant_id=uuid4() if scope == "tenant" else context.stock.tenant_id,
            actor_id=uuid4() if scope == "actor" else context.actor_id,
            allowed_branch_ids={uuid4() if scope == "branch" else context.stock.branch_id},
            request=request,
        )


async def test_database_still_rejects_completion_without_physical_binding(
    db_session: AsyncSession,
) -> None:
    source = await checkout_source(db_session)
    await put_physical(db_session, source.sale.id)
    request = await saved_request(db_session, source.sale)
    before = await facts(db_session)
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "UPDATE sale SET status='completed',total_amount=:total,completed_at=now() "
                    "WHERE id=:id"
                ),
                {"id": source.sale.id, "total": request.expected_total_amount},
            )
            await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    assert await facts(db_session) == before


async def test_checkout_does_not_commit_callers_transaction(db_session: AsyncSession) -> None:
    source = await checkout_source(db_session)
    await put_physical(db_session, source.sale.id, sale_format="blister")
    request = await saved_request(db_session, source.sale)
    before = await facts(db_session)
    async with db_session.begin_nested() as caller:
        await complete(db_session, request)
        assert await remaining(db_session, source.stock) == 50
        await caller.rollback()
    assert await facts(db_session) == before


async def test_late_failure_rolls_back_payment_manifest_stock_and_receipt_number(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = await checkout_source(db_session)
    await put_physical(db_session, source.sale.id, sale_format="blister")
    request = await saved_request(db_session, source.sale)

    async def refuse_completion(
        repository: PhysicalCashCheckoutRepository,
        sale: Sale,
        receipt: PhysicalReceiptDocumentV3,
        completed_at: datetime,
    ) -> None:
        assert (
            await repository.session.scalar(
                text("SELECT count(*) FROM sale_payment WHERE sale_id=:id"),
                {"id": sale.id},
            )
            == 1
        )
        assert (
            await repository.session.scalar(
                text("SELECT count(*) FROM pos_physical_checkout WHERE sale_id=:id"),
                {"id": sale.id},
            )
            == 1
        )
        assert await remaining(repository.session, source.stock) == 50
        raise BusinessRuleError("Simulated final completion refusal")

    monkeypatch.setattr(PhysicalCashCheckoutRepository, "complete_sale", refuse_completion)
    before = await facts(db_session)
    with pytest.raises(BusinessRuleError, match="final completion refusal"):
        await complete(db_session, request)
    assert await facts(db_session) == before


async def test_bound_stock_command_cannot_commit_without_checkout_and_cash(
    db_session: AsyncSession,
) -> None:
    source = await checkout_source(db_session)
    context = runtime(db_session)
    before = await facts(db_session)
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await PhysicalUnitLedgerV2Service(
                UnitDispensingV2Repository(db_session)
            ).dispense_priced_cart(
                tenant_id=context.stock.tenant_id,
                branch_id=context.stock.branch_id,
                actor_id=context.actor_id,
                allowed_branch_ids={context.stock.branch_id},
                command_id=uuid4(),
                lines=(retail_line(source.stock, "unit", 3),),
                checkout_sale_id=source.sale.id,
            )
            assert await remaining(db_session, source.stock) == 57
            await db_session.execute(
                text("SET CONSTRAINTS physical_stock_checkout_complete IMMEDIATE")
            )
    assert await facts(db_session) == before


async def test_pending_incoming_stock_is_rejected_before_payment(db_session: AsyncSession) -> None:
    source = await checkout_source(db_session, quantity=1)
    await dispense(db_session, source.stock, (cart_line(source.stock, "pack", 1),))
    pending = await seed_v2_incoming(db_session)
    ledger = PreparedUnitLedgerV2Service(UnitLedgerV2Repository(db_session))
    await ledger._claim_incoming_lot(
        pending.item_ids[0],
        actor_id=pending.actor_id,
        allowed_branch_ids={pending.stock.branch_id},
    )
    await ledger._prepare_incoming_lot(
        pending.item_ids[0],
        actor_id=pending.actor_id,
        allowed_branch_ids={pending.stock.branch_id},
    )
    assert (
        await db_session.scalar(
            text("SELECT status FROM incoming_document WHERE id=:id"),
            {"id": pending.document_id},
        )
        == "draft"
    )
    await put_physical(db_session, source.sale.id)
    request = await saved_request(db_session, source.sale)
    before = await facts(db_session)
    with pytest.raises(DBAPIError):
        await complete(db_session, request)
    assert await facts(db_session) == before


async def test_manifest_and_completion_are_isolated_from_real_foreign_tenant(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> None:
    source = await checkout_source(db_session)
    await put_physical(db_session, source.sale.id)
    request = await saved_request(db_session, source.sale)
    original = await complete(db_session, request)
    await db_session.commit()
    async with AsyncSession(db_engine) as support:
        async with support.begin():
            await support.execute(text("SELECT set_config('app.support_session','true',true)"))
            foreign = await seed_ledger_context(support)
    foreign_actor = await seed_receiving_actor(db_engine, maintenance_engine, foreign)
    await set_receiving_context(db_session, foreign, foreign_actor)
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM pos_physical_checkout WHERE id=:id"),
            {"id": request.operation_id},
        )
        == 0
    )
    assert (
        await PhysicalCashCheckoutRepository(db_session).get(
            original.tenant_id,
            request.operation_id,
        )
        is None
    )
    with pytest.raises(NotFoundError):
        await PhysicalCashCheckoutService(PhysicalCashCheckoutRepository(db_session)).complete(
            tenant_id=foreign.tenant_id,
            actor_id=foreign_actor,
            allowed_branch_ids={foreign.branch_id},
            request=request,
        )
    context = runtime(db_session)
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await complete(db_session, request) == original


async def test_simultaneous_same_sale_and_cash_operation_waits_then_replays_once(
    db_session: AsyncSession,
) -> None:
    source = await checkout_source(db_session)
    await put_physical(db_session, source.sale.id, quantity=5)
    request = await saved_request(db_session, source.sale)
    context = cast(ReceivingRuntime, db_session.info["receiving_runtime"])
    await db_session.commit()
    await set_receiving_context(db_session, context.stock, context.actor_id)
    original = await complete(db_session, request)
    entered = asyncio.Event()

    async def competing_checkout() -> PhysicalCheckoutResultV3:
        async with context.factory() as second:
            second.info["receiving_runtime"] = context
            await set_receiving_context(second, context.stock, context.actor_id)
            entered.set()
            result = await complete(second, request)
            await second.commit()
            return result

    task = asyncio.create_task(competing_checkout())
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(task), timeout=0.1)
        await db_session.commit()
        replay = await asyncio.wait_for(task, timeout=5)
        assert replay == original
        await set_receiving_context(db_session, context.stock, context.actor_id)
        assert await remaining(db_session, source.stock) == 55
        assert (
            await db_session.scalar(
                text("SELECT count(*) FROM sale_payment WHERE sale_id=:id"),
                {"id": request.sale_id},
            )
            == 1
        )
        assert (
            await db_session.scalar(
                text("SELECT count(*) FROM pos_physical_checkout WHERE sale_id=:id"),
                {"id": request.sale_id},
            )
            == 1
        )
        assert (
            await db_session.scalar(
                text("SELECT sum(units_taken) FROM unit_stock_movement_v2 WHERE command_id=:id"),
                {"id": request.operation_id},
            )
            == 5
        )
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await db_session.rollback()


async def test_access_revoked_while_waiting_for_stock_rolls_back_checkout(
    db_session: AsyncSession,
    maintenance_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = await checkout_source(db_session)
    await put_physical(db_session, source.sale.id, quantity=5)
    request = await saved_request(db_session, source.sale)
    context = runtime(db_session)
    before = await facts(db_session)
    await db_session.commit()
    entered = asyncio.Event()
    original_lock = UnitCheckoutRepository.lock_physical_batches

    async def signal_stock_wait(
        repository: UnitCheckoutRepository,
        tenant_id: UUID,
        branch_id: UUID,
        catalog_ids: list[UUID],
    ) -> tuple[UnitStockBatchV2, ...]:
        entered.set()
        return await original_lock(repository, tenant_id, branch_id, catalog_ids)

    monkeypatch.setattr(UnitCheckoutRepository, "lock_physical_batches", signal_stock_wait)

    async def checkout() -> PhysicalCheckoutResultV3:
        async with context.factory() as caller:
            caller.info["receiving_runtime"] = context
            await set_receiving_context(caller, context.stock, context.actor_id)
            return await complete(caller, request)

    async with context.factory() as blocker:
        await set_receiving_context(blocker, context.stock, context.actor_id)
        await blocker.execute(
            text("SELECT id FROM unit_stock_lot_v2 WHERE id=:id FOR UPDATE"),
            {"id": source.stock.lot_id},
        )
        task = asyncio.create_task(checkout())
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(task), timeout=0.1)
            async with maintenance_engine.begin() as owner:
                await owner.execute(
                    text("UPDATE app_user SET status='blocked' WHERE id=:id"),
                    {"id": context.actor_id},
                )
            await blocker.rollback()
            with pytest.raises(PermissionDeniedError):
                await asyncio.wait_for(task, timeout=5)
        finally:
            await blocker.rollback()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            async with maintenance_engine.begin() as owner:
                await owner.execute(
                    text("UPDATE app_user SET status='active' WHERE id=:id"),
                    {"id": context.actor_id},
                )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await facts(db_session) == before
