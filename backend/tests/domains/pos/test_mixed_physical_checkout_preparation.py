"""Mixed preflight checks both inventories without enabling financial completion."""

import asyncio
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError, PermissionDeniedError
from app.domains.inventory.repository import InventoryRepository
from app.domains.inventory.unit_stock_v2 import SaleFormatV2
from app.domains.pos.mixed_checkout_preparation import (
    MixedPhysicalCheckoutPreparation,
    MixedPhysicalCheckoutPreparationService,
)
from app.domains.pos.models import Sale, SaleItem
from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService
from app.domains.pos.unit_cart_repository import UnitCartRepository
from app.domains.pos.unit_cash_confirmation import CashConfirmationRequestV2
from app.domains.pos.unit_checkout_repository import UnitCheckoutRepository
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import set_receiving_context
from tests.domains.inventory.test_unit_ledger_v2_dispensing import cart_line, dispense
from tests.domains.pos.test_unit_cart_quote import add_legacy
from tests.domains.pos.test_unit_cash_confirmation import confirmation_request
from tests.domains.pos.test_unit_cash_intent import save
from tests.domains.pos.test_unit_physical_cart import physical_request, put_physical
from tests.domains.pos.test_unit_physical_checkout_preparation import (
    checkout_source,
    saved_request,
)
from tests.domains.pos.test_unit_physical_checkout_preparation import facts as stock_and_sale_facts
from tests.domains.pos.test_unit_physical_checkout_preparation import prepare as prepare_physical
from tests.domains.pos.test_unit_prescription import attach
from tests.domains.pos.test_unit_return import runtime
from tests.domains.pos.unit_shift_history_helpers import close_historical_unit_shift

db_session = receiving_session_fixture


async def facts(session: AsyncSession) -> dict[str, list[object]]:
    result = await stock_and_sale_facts(session)
    tables = ("batch", "batch_movement", "register", "pos_physical_checkout", "prescription_log")
    for table in tables:
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


async def legacy_with_stock(session: AsyncSession, sale: Sale) -> SaleItem:
    item = await add_legacy(session, sale)
    await InventoryRepository(session).insert_movement(
        tenant_id=sale.tenant_id,
        batch_id=item.batch_id,
        movement_type="incoming",
        qty_delta=Decimal("0.001"),
    )
    return item


async def second_legacy(session: AsyncSession, sale: Sale, first: SaleItem) -> SaleItem:
    item = await POSRepository(session).insert_item(
        tenant_id=sale.tenant_id,
        sale_id=sale.id,
        catalog_id=first.catalog_id,
        batch_id=first.batch_id,
        qty=first.qty,
        unit_price=first.unit_price,
        total_price=first.total_price,
        discount_amount=first.discount_amount,
        position=2,
        currency="TJS",
    )
    await POSRepository(session).update_sale(sale, total_amount=Decimal("0.04"))
    return item


async def prepare(
    session: AsyncSession, request: CashConfirmationRequestV2
) -> MixedPhysicalCheckoutPreparation:
    context = runtime(session)
    return await MixedPhysicalCheckoutPreparationService(UnitCheckoutRepository(session)).prepare(
        tenant_id=context.stock.tenant_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        request=request,
    )


@pytest.mark.parametrize("sale_format,units", [("pack", 30), ("blister", 10), ("unit", 1)])
async def test_mixed_formats_share_quote_without_writing_either_inventory(
    db_session: AsyncSession, sale_format: SaleFormatV2, units: int
) -> None:
    source = await checkout_source(db_session)
    legacy = await legacy_with_stock(db_session, source.sale)
    physical = await put_physical(db_session, source.sale.id, sale_format=sale_format)
    request = await saved_request(db_session, source.sale)
    before = await facts(db_session)
    result = await prepare(db_session, request)
    assert result.state == "checked_mixed_physical_draft"
    assert result.confirmation.confirmation.request == request
    assert len(result.legacy_allocations) == 1
    allocation = result.legacy_allocations[0]
    assert (allocation.batch_id, allocation.catalog_id) == (legacy.batch_id, legacy.catalog_id)
    assert allocation.source_item_ids == (legacy.id,)
    assert allocation.quantity == Decimal("0.001")
    assert result.physical_stock_plan.lines[0].line_id == physical.id
    assert result.physical_stock_plan.lines[0].base_units == units
    assert await prepare(db_session, request) == result
    assert await facts(db_session) == before


@pytest.mark.parametrize("enough_stock", [False, True])
async def test_repeated_legacy_rows_share_one_aggregate_allocation(
    db_session: AsyncSession, enough_stock: bool
) -> None:
    source = await checkout_source(db_session)
    first = await legacy_with_stock(db_session, source.sale)
    second = await second_legacy(db_session, source.sale, first)
    if enough_stock:
        await InventoryRepository(db_session).insert_movement(
            tenant_id=source.sale.tenant_id,
            batch_id=first.batch_id,
            movement_type="incoming",
            qty_delta=Decimal("0.001"),
        )
    await put_physical(db_session, source.sale.id)
    request = await saved_request(db_session, source.sale)
    before = await facts(db_session)
    if enough_stock:
        result = await prepare(db_session, request)
        assert len(result.legacy_allocations) == 1
        assert result.legacy_allocations[0].quantity == Decimal("0.002")
        assert result.legacy_allocations[0].source_item_ids == tuple(
            sorted((first.id, second.id), key=str)
        )
    else:
        with pytest.raises(BusinessRuleError, match="Insufficient legacy stock"):
            await prepare(db_session, request)
    assert await facts(db_session) == before


@pytest.mark.parametrize("consumed", ["legacy", "physical"])
async def test_preflight_is_not_a_reservation_or_cross_inventory_substitution(
    db_session: AsyncSession, consumed: str
) -> None:
    source = await checkout_source(db_session, quantity=1)
    legacy = await legacy_with_stock(db_session, source.sale)
    await put_physical(db_session, source.sale.id, sale_format="pack")
    request = await saved_request(db_session, source.sale)
    await prepare(db_session, request)
    if consumed == "legacy":
        await InventoryRepository(db_session).insert_movement(
            tenant_id=source.sale.tenant_id,
            batch_id=legacy.batch_id,
            movement_type="write_off",
            qty_delta=-legacy.qty,
        )
    else:
        await dispense(db_session, source.stock, (cart_line(source.stock, "pack", 1),))
    before = await facts(db_session)
    with pytest.raises(BusinessRuleError):
        await prepare(db_session, request)
    assert await facts(db_session) == before


@pytest.mark.parametrize("change", ["blocked", "expired", "earlier_batch"])
async def test_legacy_eligibility_and_fefo_are_rechecked_after_saving_cash(
    db_session: AsyncSession, change: str
) -> None:
    source = await checkout_source(db_session)
    legacy = await legacy_with_stock(db_session, source.sale)
    await put_physical(db_session, source.sale.id)
    request = await saved_request(db_session, source.sale)
    if change == "earlier_batch":
        inventory = InventoryRepository(db_session)
        earlier = await inventory.create_batch(
            tenant_id=source.sale.tenant_id,
            branch_id=source.sale.branch_id,
            catalog_id=legacy.catalog_id,
            expires_at=date.today() + timedelta(days=30),
            purchase_price=Decimal("3.00"),
            sale_price=Decimal("25.00"),
            qty_initial=Decimal("1.000"),
            qty_remaining=Decimal("0.000"),
        )
        await inventory.insert_movement(
            tenant_id=source.sale.tenant_id,
            batch_id=earlier.id,
            movement_type="incoming",
            qty_delta=Decimal("1.000"),
        )
    else:
        statement = (
            "UPDATE batch SET is_blocked=true WHERE id=:id"
            if change == "blocked"
            else "UPDATE batch SET expires_at=current_date-1 WHERE id=:id"
        )
        await db_session.execute(text(statement), {"id": legacy.batch_id})
    before = await facts(db_session)
    with pytest.raises(BusinessRuleError):
        await prepare(db_session, request)
    assert await facts(db_session) == before


@pytest.mark.parametrize("scope", ["actor", "actual_actor", "tenant", "branch", "closed_shift"])
async def test_mixed_preflight_requires_current_cashier_and_open_allowed_shift(
    db_session: AsyncSession, maintenance_engine: AsyncEngine, scope: str
) -> None:
    source = await checkout_source(db_session)
    await legacy_with_stock(db_session, source.sale)
    await put_physical(db_session, source.sale.id)
    request = await saved_request(db_session, source.sale)
    context = runtime(db_session)
    if scope == "actual_actor":
        await set_receiving_context(db_session, context.stock, uuid4())
    elif scope == "closed_shift":
        await close_historical_unit_shift(db_session, maintenance_engine, source.sale.shift_id)
    before = await facts(db_session)
    with pytest.raises((PermissionDeniedError, NotFoundError, BusinessRuleError, ConflictError)):
        await MixedPhysicalCheckoutPreparationService(UnitCheckoutRepository(db_session)).prepare(
            tenant_id=uuid4() if scope == "tenant" else context.stock.tenant_id,
            actor_id=uuid4() if scope == "actor" else context.actor_id,
            allowed_branch_ids={uuid4() if scope == "branch" else context.stock.branch_id},
            request=request,
        )
    assert await facts(db_session) == before


@pytest.mark.parametrize("change", ["missing_intent", "legacy_discount", "physical_quantity"])
async def test_cash_evidence_is_required_and_current_quote_is_revalidated(
    db_session: AsyncSession, change: str
) -> None:
    source = await checkout_source(db_session)
    legacy = await legacy_with_stock(db_session, source.sale)
    physical = await put_physical(db_session, source.sale.id)
    request = await physical_request(db_session, source.sale.id)
    if change != "missing_intent":
        await save(db_session, request)
    if change == "legacy_discount":
        await db_session.execute(
            text("UPDATE sale_item SET discount_amount=0.01,total_price=0.01 WHERE id=:id"),
            {"id": legacy.id},
        )
    elif change == "physical_quantity":
        await put_physical(db_session, source.sale.id, item_id=physical.id, quantity=2)
    before = await facts(db_session)
    with pytest.raises((NotFoundError, BusinessRuleError, ConflictError)):
        await prepare(db_session, request)
    assert await facts(db_session) == before


@pytest.mark.parametrize(
    "coverage", ["none", "physical", "partial_legacy", "legacy", "explicit", "global"]
)
async def test_rx_requires_each_legacy_and_physical_link_or_global_sale_evidence(
    db_session: AsyncSession, coverage: str
) -> None:
    source = await checkout_source(db_session)
    first = await legacy_with_stock(db_session, source.sale)
    second = await second_legacy(db_session, source.sale, first)
    await InventoryRepository(db_session).insert_movement(
        tenant_id=source.sale.tenant_id,
        batch_id=first.batch_id,
        movement_type="incoming",
        qty_delta=Decimal("0.001"),
    )
    physical = await put_physical(db_session, source.sale.id)
    request = await saved_request(db_session, source.sale)
    await db_session.execute(
        text("UPDATE tenant_catalog SET dispensing_type='prescription' WHERE id=:id"),
        {"id": first.catalog_id},
    )
    catalog = await UnitCartRepository(db_session).lock_catalog(
        source.sale.tenant_id, first.catalog_id
    )
    assert catalog is not None and catalog.dispensing_type == "prescription"
    if coverage in {"physical", "partial_legacy", "explicit"}:
        await attach(db_session, source.sale.id, physical.id)
    pos = POSService(POSRepository(db_session))
    item_ids: tuple[UUID | None, ...] = ()
    if coverage == "global":
        item_ids = (None,)
    elif coverage in {"legacy", "explicit"}:
        item_ids = (first.id, second.id)
    elif coverage == "partial_legacy":
        item_ids = (first.id,)
    for item_id in item_ids:
        await pos.add_prescription(
            sale_id=source.sale.id,
            fields={"sale_item_id": item_id, "prescription_number": "SYNTHETIC-MIXED-RX"},
            actor_id=source.sale.cashier_user_id,
            allowed_branch_ids={source.sale.branch_id},
        )
    before = await facts(db_session)
    if coverage in {"explicit", "global"}:
        await prepare(db_session, request)
    else:
        with pytest.raises(BusinessRuleError):
            await prepare(db_session, request)
    assert await facts(db_session) == before


async def test_pending_orm_changes_are_not_flushed_by_preflight(db_session: AsyncSession) -> None:
    source = await checkout_source(db_session)
    await legacy_with_stock(db_session, source.sale)
    await put_physical(db_session, source.sale.id)
    request = await saved_request(db_session, source.sale)
    before = await facts(db_session)
    original = source.sale.is_test
    source.sale.is_test = not original
    try:
        with pytest.raises(ConflictError, match="Persist pending draft changes"):
            await prepare(db_session, request)
        with db_session.no_autoflush:
            assert (
                await db_session.scalar(
                    text("SELECT is_test FROM sale WHERE id=:id"), {"id": source.sale.id}
                )
                == original
            )
            assert await facts(db_session) == before
    finally:
        source.sale.is_test = original
        await db_session.refresh(source.sale)


@pytest.mark.parametrize("mode", ["prepared", "physical_only", "legacy_only"])
async def test_combined_preflight_rejects_other_cart_modes(
    db_session: AsyncSession, mode: str
) -> None:
    if mode == "prepared":
        request = await confirmation_request(db_session)
        await save(db_session, request)
    else:
        source = await checkout_source(db_session)
        if mode == "legacy_only":
            await legacy_with_stock(db_session, source.sale)
        else:
            await put_physical(db_session, source.sale.id)
        request = await saved_request(db_session, source.sale)
    before = await facts(db_session)
    with pytest.raises(BusinessRuleError, match="requires legacy and physical"):
        await prepare(db_session, request)
    assert await facts(db_session) == before


async def test_existing_physical_preparation_keeps_mixed_checkout_disabled(
    db_session: AsyncSession,
) -> None:
    source = await checkout_source(db_session)
    await legacy_with_stock(db_session, source.sale)
    await put_physical(db_session, source.sale.id)
    request = await saved_request(db_session, source.sale)
    before = await facts(db_session)
    await prepare(db_session, request)
    with pytest.raises(BusinessRuleError, match="Mixed legacy and physical"):
        await prepare_physical(db_session, request)
    assert await facts(db_session) == before


async def test_mixed_catalog_contention_fails_fast_without_writes(db_session: AsyncSession) -> None:
    source = await checkout_source(db_session)
    await legacy_with_stock(db_session, source.sale)
    await put_physical(db_session, source.sale.id)
    request = await saved_request(db_session, source.sale)
    context = runtime(db_session)
    await db_session.commit()
    await set_receiving_context(db_session, context.stock, context.actor_id)
    before = await facts(db_session)
    async with context.factory() as holder:
        await set_receiving_context(holder, context.stock, context.actor_id)
        await holder.execute(
            text("SELECT id FROM tenant_catalog WHERE id=:id FOR SHARE"),
            {"id": context.stock.catalog_id},
        )
        try:
            with pytest.raises(ConflictError, match="Checkout stock is busy"):
                await asyncio.wait_for(prepare(db_session, request), timeout=5)
            assert await db_session.scalar(text("SELECT 1")) == 1
            assert await facts(db_session) == before
        finally:
            await holder.rollback()
