"""Combined checkout preparation cannot spend cash, allocate stock or reuse stale evidence."""

import asyncio
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError, PermissionDeniedError
from app.domains.pos.unit_cart_repository import UnitCartRepository
from app.domains.pos.unit_cart_service import UnitCartService
from app.domains.pos.unit_cash_confirmation import CashConfirmationRequestV2
from app.domains.pos.unit_checkout_preparation import (
    UnitCheckoutPreparation,
    UnitCheckoutPreparationService,
)
from app.domains.pos.unit_checkout_repository import UnitCheckoutRepository
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import set_receiving_context
from tests.domains.pos.test_unit_cart import put_cart_item, seed_cart_draft
from tests.domains.pos.test_unit_cart_quote import quote
from tests.domains.pos.test_unit_cash_confirmation import confirmation_request
from tests.domains.pos.test_unit_cash_intent import save
from tests.domains.pos.test_unit_prescription import attach
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


async def prepare(
    session: AsyncSession, request: CashConfirmationRequestV2, *, today: date | None = None
) -> UnitCheckoutPreparation:
    context = runtime(session)
    return await UnitCheckoutPreparationService(UnitCheckoutRepository(session)).prepare(
        tenant_id=context.stock.tenant_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        request=request,
        today=today or date.today(),
    )


async def request_for_cart(session: AsyncSession, sale_id: UUID) -> CashConfirmationRequestV2:
    current = await quote(session, sale_id)
    return CashConfirmationRequestV2(
        operation_id=uuid4(),
        sale_id=current.sale_id,
        expected_revision_hash=current.revision_hash,
        expected_total_amount=current.total_amount,
        cash_tendered=current.total_amount + Decimal("1.00"),
    )


async def test_combined_preparation_preserves_all_financial_and_stock_facts(
    db_session: AsyncSession,
) -> None:
    request = await confirmation_request(db_session)
    saved = await save(db_session, request)
    context = runtime(db_session)
    tables = (
        "unit_stock_entry",
        "unit_stock_operation",
        "pos_unit_sale_line",
        "pos_unit_sale_command",
        "sale_payment",
        "pos_payment_attempt",
        "pos_unit_cash_intent",
        "audit_log",
    )
    counts = {
        table: await db_session.scalar(
            text(f"SELECT count(*) FROM {table} WHERE tenant_id=:tenant"),
            {"tenant": context.stock.tenant_id},
        )
        for table in tables
    }
    stock_before = (
        await db_session.execute(
            text("SELECT id,closed_packs FROM unit_stock_lot WHERE tenant_id=:tenant ORDER BY id"),
            {"tenant": context.stock.tenant_id},
        )
    ).all()
    result = await prepare(db_session, request)
    assert result.state == "checked_draft" and result.confirmation == saved
    assert len(result.stock_plans) == 1
    assert result.stock_plans[0].plan.base_units == 1
    assert await prepare(db_session, request) == result
    for table, count in counts.items():
        assert (
            await db_session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id=:tenant"),
                {"tenant": context.stock.tenant_id},
            )
            == count
        )
    assert (
        await db_session.execute(
            text("SELECT id,closed_packs FROM unit_stock_lot WHERE tenant_id=:tenant ORDER BY id"),
            {"tenant": context.stock.tenant_id},
        )
    ).all() == stock_before
    assert (
        await db_session.scalar(
            text("SELECT status FROM sale WHERE id=:sale"), {"sale": request.sale_id}
        )
        == "draft"
    )


async def test_unsaved_cash_evidence_does_not_get_created(db_session: AsyncSession) -> None:
    request = await confirmation_request(db_session)
    with pytest.raises(NotFoundError, match="Saved cash"):
        await prepare(db_session, request)
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM pos_unit_cash_intent WHERE sale_id=:sale"),
            {"sale": request.sale_id},
        )
        == 0
    )


@pytest.mark.parametrize("change", ["quantity", "price", "cash"])
async def test_saved_cash_evidence_never_bypasses_current_revalidation(
    db_session: AsyncSession, change: str
) -> None:
    request = await confirmation_request(db_session)
    await save(db_session, request)
    context = runtime(db_session)
    if change == "quantity":
        await db_session.execute(
            text("UPDATE pos_unit_cart_item SET quantity=2 WHERE sale_id=:sale"),
            {"sale": request.sale_id},
        )
    elif change == "price":
        await db_session.execute(
            text("UPDATE tenant_catalog SET base_price=26.00 WHERE id=:id"),
            {"id": context.stock.catalog_id},
        )
        # This cart uses units, so changing its unit price changes its quote.
        await db_session.execute(
            text(
                "UPDATE tenant_catalog SET unit_sale_preparation=jsonb_set("
                "unit_sale_preparation,'{unit_price}',to_jsonb(CAST(:price AS text))) WHERE id=:id"
            ),
            {"id": context.stock.catalog_id, "price": "1.10"},
        )
    else:
        request = request.model_copy(update={"cash_tendered": Decimal("3.00")})
    with pytest.raises(ConflictError):
        await prepare(db_session, request)


async def test_current_rx_policy_is_checked_after_cash_was_saved(db_session: AsyncSession) -> None:
    sale = await seed_cart_draft(db_session)
    item_id = await put_cart_item(db_session, sale.id)
    request = await request_for_cart(db_session, sale.id)
    await save(db_session, request)
    context = runtime(db_session)
    await db_session.execute(
        text("UPDATE tenant_catalog SET dispensing_type='prescription' WHERE id=:id"),
        {"id": context.stock.catalog_id},
    )
    with pytest.raises(BusinessRuleError, match="Rx unit positions"):
        await prepare(db_session, request)
    prescription_id = await attach(db_session, sale.id, item_id)
    await prepare(db_session, request)
    await db_session.execute(
        text("DELETE FROM prescription_log WHERE id=:id"), {"id": prescription_id}
    )
    with pytest.raises(BusinessRuleError, match="Rx unit positions"):
        await prepare(db_session, request)


@pytest.mark.parametrize("condition", ["expiry", "packaging"])
async def test_current_received_stock_eligibility_is_rechecked(
    db_session: AsyncSession, condition: str
) -> None:
    request = await confirmation_request(db_session)
    await save(db_session, request)
    context = runtime(db_session)
    today = date.today()
    if condition == "expiry":
        today += timedelta(days=181)
    else:
        await db_session.execute(
            text(
                "UPDATE tenant_catalog SET unit_sale_preparation=jsonb_set("
                "unit_sale_preparation,'{units_per_pack}','60'::jsonb) WHERE id=:id"
            ),
            {"id": context.stock.catalog_id},
        )
        request = await request_for_cart(db_session, request.sale_id)
        await save(db_session, request)
    with pytest.raises(BusinessRuleError, match="Insufficient received stock"):
        await prepare(db_session, request, today=today)


async def test_repeated_rows_cannot_claim_the_same_received_units(db_session: AsyncSession) -> None:
    sale = await seed_cart_draft(db_session)
    context = runtime(db_session)
    packs = int(
        await db_session.scalar(
            text("SELECT sum(closed_packs) FROM unit_stock_lot WHERE tenant_id=:tenant"),
            {"tenant": context.stock.tenant_id},
        )
    )
    for position in (1, 2):
        await UnitCartService(UnitCartRepository(db_session)).upsert_item(
            tenant_id=context.stock.tenant_id,
            sale_id=sale.id,
            item_id=uuid4(),
            catalog_id=context.stock.catalog_id,
            actor_id=context.actor_id,
            allowed_branch_ids={context.stock.branch_id},
            position=position,
            sale_format="pack",
            quantity=packs,
        )
    request = await request_for_cart(db_session, sale.id)
    await save(db_session, request)
    with pytest.raises(BusinessRuleError, match="entire unit cart"):
        await prepare(db_session, request)


@pytest.mark.parametrize("scope", ["actor", "branch", "tenant"])
async def test_preparation_checks_actual_actor_and_allowed_scope(
    db_session: AsyncSession, scope: str
) -> None:
    request = await confirmation_request(db_session)
    await save(db_session, request)
    context = runtime(db_session)
    with pytest.raises((PermissionDeniedError, NotFoundError)):
        await UnitCheckoutPreparationService(UnitCheckoutRepository(db_session)).prepare(
            tenant_id=uuid4() if scope == "tenant" else context.stock.tenant_id,
            actor_id=uuid4() if scope == "actor" else context.actor_id,
            allowed_branch_ids={uuid4() if scope == "branch" else context.stock.branch_id},
            request=request,
            today=date.today(),
        )


async def test_preparation_is_not_a_reservation_and_stock_is_rechecked(
    db_session: AsyncSession,
) -> None:
    sale = await seed_cart_draft(db_session)
    context = runtime(db_session)
    packs = int(
        await db_session.scalar(
            text("SELECT sum(closed_packs) FROM unit_stock_lot WHERE tenant_id=:tenant"),
            {"tenant": context.stock.tenant_id},
        )
    )
    await UnitCartService(UnitCartRepository(db_session)).upsert_item(
        tenant_id=context.stock.tenant_id,
        sale_id=sale.id,
        item_id=uuid4(),
        catalog_id=context.stock.catalog_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        position=1,
        sale_format="pack",
        quantity=packs,
    )
    request = await request_for_cart(db_session, sale.id)
    await save(db_session, request)
    await db_session.commit()
    ready, attempting, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def reader() -> None:
        async with context.factory() as session:
            await set_receiving_context(session, context.stock, context.actor_id)
            session.info["receiving_runtime"] = context
            await prepare(session, request)
            ready.set()
            await release.wait()
            await session.rollback()

    async def writer() -> None:
        from app.domains.pos.unit_line_repository import UnitSaleLineRepository
        from app.domains.pos.unit_line_service import PreparedUnitSaleLineService

        await ready.wait()
        async with context.factory() as session:
            await set_receiving_context(session, context.stock, context.actor_id)
            attempting.set()
            await PreparedUnitSaleLineService(UnitSaleLineRepository(session)).post_received_line(
                tenant_id=context.stock.tenant_id,
                branch_id=context.stock.branch_id,
                catalog_id=context.stock.catalog_id,
                actor_id=context.actor_id,
                operation_id=uuid4(),
                sale_format="pack",
                quantity=packs,
                today=date.today(),
                allowed_branch_ids={context.stock.branch_id},
            )
            await session.commit()

    reading, writing = asyncio.create_task(reader()), asyncio.create_task(writer())
    try:
        await asyncio.wait_for(attempting.wait(), timeout=5)
        await asyncio.sleep(0.05)
        assert not writing.done()
    finally:
        release.set()
    await asyncio.wait_for(asyncio.gather(reading, writing), timeout=10)
    await set_receiving_context(db_session, context.stock, context.actor_id)
    with pytest.raises(BusinessRuleError, match="Insufficient received stock"):
        await prepare(db_session, request)
