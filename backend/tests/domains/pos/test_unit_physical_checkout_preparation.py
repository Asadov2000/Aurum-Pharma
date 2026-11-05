"""Physical checkout checks the entire draft without posting stock or taking cash."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError, PermissionDeniedError
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.inventory.unit_stock_v2 import SaleFormatV2
from app.domains.pos.models import Sale
from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService
from app.domains.pos.unit_cash_confirmation import CashConfirmationRequestV2
from app.domains.pos.unit_checkout_preparation import (
    UnitCheckoutPreparationService,
    UnitPhysicalCheckoutPreparation,
)
from app.domains.pos.unit_checkout_repository import UnitCheckoutRepository
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import set_receiving_context
from tests.domains.inventory.test_unit_ledger_v2_dispensing import (
    PhysicalSource,
    cart_line,
    dispense,
    remaining,
)
from tests.domains.inventory.test_unit_retail_ledger_v2 import retail_source
from tests.domains.pos.test_unit_cart_quote import add_legacy
from tests.domains.pos.test_unit_cash_confirmation import confirmation_request
from tests.domains.pos.test_unit_cash_intent import save
from tests.domains.pos.test_unit_physical_cart import physical_request, put_physical
from tests.domains.pos.test_unit_prescription import attach
from tests.domains.pos.test_unit_return import runtime
from tests.domains.pos.unit_shift_history_helpers import close_historical_unit_shift

db_session = receiving_session_fixture


@dataclass(frozen=True)
class PhysicalCheckoutSource:
    stock: PhysicalSource
    sale: Sale


async def checkout_source(
    session: AsyncSession,
    *,
    quantity: int = 2,
) -> PhysicalCheckoutSource:
    stock = await retail_source(session, quantity=quantity)
    context = runtime(session)
    assert stock.incoming.stock == context.stock
    assert stock.incoming.actor_id == context.actor_id
    register = await FoundationService(FoundationRepository(session)).create_register(
        tenant_id=context.stock.tenant_id,
        fields={"branch_id": context.stock.branch_id, "name": "Physical checkout test"},
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
    sale = await pos.create_sale(
        tenant_id=context.stock.tenant_id,
        register_id=register.id,
        cashier_user_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
    )
    return PhysicalCheckoutSource(stock, sale)


async def prepare(
    session: AsyncSession,
    request: CashConfirmationRequestV2,
) -> UnitPhysicalCheckoutPreparation:
    context = runtime(session)
    return await UnitCheckoutPreparationService(UnitCheckoutRepository(session)).prepare_physical(
        tenant_id=context.stock.tenant_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        request=request,
    )


async def saved_request(session: AsyncSession, sale: Sale) -> CashConfirmationRequestV2:
    request = await physical_request(session, sale.id)
    await save(session, request)
    return request


async def facts(session: AsyncSession) -> dict[str, list[object]]:
    tenant = runtime(session).stock.tenant_id
    tables = (
        "unit_stock_lot_v2",
        "unit_stock_opened_pack_v2",
        "unit_stock_movement_v2",
        "unit_stock_cart_command_v2",
        "unit_stock_cart_line_v2",
        "unit_stock_entry_v2",
        "sale",
        "sale_item",
        "sale_payment",
        "pos_payment_attempt",
        "pos_unit_cash_intent",
        "audit_log",
    )
    return {
        table: list(
            (
                await session.execute(
                    text(
                        f"SELECT to_jsonb(row) FROM {table} row WHERE tenant_id=:tenant "
                        "ORDER BY to_jsonb(row)::text"
                    ),
                    {"tenant": tenant},
                )
            ).scalars()
        )
        for table in tables
    }


@pytest.mark.parametrize(
    "sale_format,quantity,units",
    [
        ("pack", 1, 30),
        ("blister", 2, 20),
        ("unit", 5, 5),
    ],
)
async def test_all_physical_formats_are_checked_without_posting(
    db_session: AsyncSession,
    sale_format: SaleFormatV2,
    quantity: int,
    units: int,
) -> None:
    source = await checkout_source(db_session)
    item = await put_physical(
        db_session, source.sale.id, sale_format=sale_format, quantity=quantity
    )
    request = await saved_request(db_session, source.sale)
    before = await facts(db_session)
    result = await prepare(db_session, request)
    assert result.state == "checked_physical_draft"
    assert result.confirmation.confirmation.request == request
    assert len(result.stock_plan.lines) == 1
    assert result.stock_plan.lines[0].line_id == item.id
    assert result.stock_plan.lines[0].base_units == units
    assert await prepare(db_session, request) == result
    assert await facts(db_session) == before
    assert await remaining(db_session, source.stock) == 60


async def test_pack_blister_and_units_share_one_physical_stock_plan(
    db_session: AsyncSession,
) -> None:
    source = await checkout_source(db_session)
    formats: tuple[SaleFormatV2, ...] = ("pack", "blister", "unit")
    for position, (sale_format, quantity) in enumerate(zip(formats, (1, 2, 5), strict=True), 1):
        await put_physical(
            db_session,
            source.sale.id,
            position=position,
            sale_format=sale_format,
            quantity=quantity,
        )
    request = await saved_request(db_session, source.sale)
    before = await facts(db_session)
    result = await prepare(db_session, request)
    assert sum(line.base_units for line in result.stock_plan.lines) == 55
    assert (
        sum(
            batch.closed_packs * 30
            + sum(
                len(pack.intact_blisters) * 10
                + sum(fragment.remaining_units for fragment in pack.opened_blisters)
                + pack.loose_units
                for pack in batch.opened_packs
            )
            for batch in result.stock_plan.residual_batches
        )
        == 5
    )
    assert await facts(db_session) == before


async def test_repeated_rows_cannot_each_use_the_same_last_pack(db_session: AsyncSession) -> None:
    source = await checkout_source(db_session, quantity=1)
    for position in (1, 2):
        await put_physical(db_session, source.sale.id, position=position, sale_format="pack")
    request = await saved_request(db_session, source.sale)
    before = await facts(db_session)
    with pytest.raises(BusinessRuleError):
        await prepare(db_session, request)
    assert await facts(db_session) == before


async def test_opened_fragment_is_not_an_intact_blister(db_session: AsyncSession) -> None:
    source = await checkout_source(db_session, quantity=1)
    await dispense(db_session, source.stock, (cart_line(source.stock, "unit", 25),))
    assert await remaining(db_session, source.stock) == 5
    await put_physical(db_session, source.sale.id, sale_format="blister")
    request = await saved_request(db_session, source.sale)
    before = await facts(db_session)
    with pytest.raises(BusinessRuleError):
        await prepare(db_session, request)
    assert await facts(db_session) == before


async def test_preparation_is_not_a_reservation_and_rechecks_consumed_stock(
    db_session: AsyncSession,
) -> None:
    source = await checkout_source(db_session, quantity=1)
    await put_physical(db_session, source.sale.id, sale_format="pack")
    request = await saved_request(db_session, source.sale)
    await prepare(db_session, request)
    await dispense(db_session, source.stock, (cart_line(source.stock, "pack", 1),))
    with pytest.raises(BusinessRuleError):
        await prepare(db_session, request)


async def test_missing_cash_intent_is_not_created_by_preparation(db_session: AsyncSession) -> None:
    source = await checkout_source(db_session)
    await put_physical(db_session, source.sale.id)
    request = await physical_request(db_session, source.sale.id)
    with pytest.raises(NotFoundError):
        await prepare(db_session, request)
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM pos_unit_cash_intent WHERE sale_id=:sale"),
            {"sale": source.sale.id},
        )
        == 0
    )


@pytest.mark.parametrize("change", ["price", "blister", "quantity"])
async def test_saved_cash_cannot_bypass_current_price_composition_or_quantity(
    db_session: AsyncSession,
    change: str,
) -> None:
    source = await checkout_source(db_session)
    item = await put_physical(db_session, source.sale.id, sale_format="blister")
    request = await saved_request(db_session, source.sale)
    if change == "quantity":
        await put_physical(
            db_session, source.sale.id, item_id=item.id, sale_format="blister", quantity=2
        )
    else:
        field, value = (
            ("unit_price", '"3.00"') if change == "price" else ("units_per_blister", "15")
        )
        await db_session.execute(
            text(
                "UPDATE tenant_catalog SET unit_sale_preparation="
                "jsonb_set(unit_sale_preparation,ARRAY[:field],CAST(:value AS jsonb)) WHERE id=:id"
            ),
            {"field": field, "value": value, "id": source.stock.incoming.stock.catalog_id},
        )
    with pytest.raises((BusinessRuleError, ConflictError)):
        await prepare(db_session, request)


@pytest.mark.parametrize("scope", ["actor", "branch", "tenant", "actual_actor"])
async def test_physical_preparation_requires_actual_cashier_and_allowed_scope(
    db_session: AsyncSession,
    scope: str,
) -> None:
    source = await checkout_source(db_session)
    await put_physical(db_session, source.sale.id)
    request = await saved_request(db_session, source.sale)
    context = runtime(db_session)
    if scope == "actual_actor":
        await set_receiving_context(db_session, context.stock, uuid4())
    with pytest.raises((PermissionDeniedError, NotFoundError)):
        await UnitCheckoutPreparationService(UnitCheckoutRepository(db_session)).prepare_physical(
            tenant_id=uuid4() if scope == "tenant" else context.stock.tenant_id,
            actor_id=uuid4() if scope == "actor" else context.actor_id,
            allowed_branch_ids={uuid4() if scope == "branch" else context.stock.branch_id},
            request=request,
        )


async def test_physical_preparation_rechecks_closed_shift(
    db_session: AsyncSession, maintenance_engine: AsyncEngine
) -> None:
    source = await checkout_source(db_session)
    await put_physical(db_session, source.sale.id)
    request = await saved_request(db_session, source.sale)
    await close_historical_unit_shift(db_session, maintenance_engine, source.sale.shift_id)
    with pytest.raises((BusinessRuleError, ConflictError)):
        await prepare(db_session, request)


async def test_physical_prescription_evidence_is_required_and_rechecked(
    db_session: AsyncSession,
) -> None:
    source = await checkout_source(db_session)
    await db_session.execute(
        text("UPDATE tenant_catalog SET dispensing_type='prescription' WHERE id=:id"),
        {"id": source.stock.incoming.stock.catalog_id},
    )
    item = await put_physical(db_session, source.sale.id)
    request = await saved_request(db_session, source.sale)
    with pytest.raises(BusinessRuleError):
        await prepare(db_session, request)
    prescription = await attach(db_session, source.sale.id, item.id)
    await prepare(db_session, request)
    await db_session.execute(
        text("DELETE FROM prescription_log WHERE id=:id"), {"id": prescription}
    )
    with pytest.raises(BusinessRuleError):
        await prepare(db_session, request)


@pytest.mark.parametrize(
    "timezone_name,eligible", [("Asia/Dushanbe", False), ("America/New_York", True)]
)
async def test_expiry_uses_trusted_pharmacy_date(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    timezone_name: str,
    eligible: bool,
) -> None:
    source = await checkout_source(db_session)
    await put_physical(db_session, source.sale.id)
    request = await saved_request(db_session, source.sale)
    foundation = FoundationRepository(db_session)
    tenant = source.stock.incoming.stock.tenant_id
    settings = await foundation.get_settings(tenant)
    if settings is None:
        settings = await foundation.create_default_settings(tenant)
    await foundation.update_settings(settings, report_timezone=timezone_name)
    expiry = date.today() + timedelta(days=180)
    clock = datetime.combine(expiry - timedelta(days=1), time(19, 30), tzinfo=UTC)
    monkeypatch.setattr("app.domains.pos.unit_checkout_preparation.utc_now", lambda: clock)
    if eligible:
        result = await prepare(db_session, request)
        assert result.stock_date == expiry - timedelta(days=1)
    else:
        with pytest.raises(BusinessRuleError):
            await prepare(db_session, request)


async def test_legacy_sale_item_is_explicitly_rejected_by_physical_preparation(
    db_session: AsyncSession,
) -> None:
    source = await checkout_source(db_session)
    await add_legacy(db_session, source.sale)
    await put_physical(db_session, source.sale.id)
    request = await saved_request(db_session, source.sale)
    with pytest.raises(BusinessRuleError):
        await prepare(db_session, request)


async def test_old_prepared_cart_requires_the_old_stock_path(db_session: AsyncSession) -> None:
    request = await confirmation_request(db_session)
    await save(db_session, request)
    with pytest.raises(BusinessRuleError):
        await prepare(db_session, request)


async def test_physical_catalog_lock_contention_fails_fast_without_writes(
    db_session: AsyncSession,
) -> None:
    source = await checkout_source(db_session)
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
