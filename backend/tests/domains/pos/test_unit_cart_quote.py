"""Mixed retail quote uses actual cashier context without stock or financial writes."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from decimal import Decimal, localcontext
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import BusinessRuleError, ConflictError, PermissionDeniedError
from app.domains.inventory.repository import InventoryRepository
from app.domains.pos.models import Sale, SaleItem
from app.domains.pos.repository import POSRepository
from app.domains.pos.unit_cart_quote import MixedCartQuoteV2, UnitCartQuoteService
from app.domains.pos.unit_cart_repository import UnitCartRepository
from app.domains.pos.unit_cart_service import UnitCartService
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.pos.test_unit_cart import put_cart_item, seed_cart_draft
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


async def add_legacy(session: AsyncSession, sale: Sale) -> SaleItem:
    context = runtime(session)
    batch = await InventoryRepository(session).create_batch(
        tenant_id=context.stock.tenant_id,
        branch_id=context.stock.branch_id,
        catalog_id=context.stock.catalog_id,
        expires_at=date.today() + timedelta(days=180),
        purchase_price=Decimal("3.00"),
        sale_price=Decimal("25.00"),
        qty_initial=Decimal("10.000"),
        qty_remaining=Decimal("0.000"),
    )
    repository = POSRepository(session)
    item = await repository.insert_item(
        tenant_id=context.stock.tenant_id,
        sale_id=sale.id,
        catalog_id=context.stock.catalog_id,
        batch_id=batch.id,
        qty=Decimal("0.001"),
        unit_price=Decimal("25.00"),
        total_price=Decimal("0.02"),
        discount_amount=Decimal("0.00"),
        position=1,
        currency="TJS",
    )
    await repository.update_sale(sale, total_amount=Decimal("0.02"))
    return item


async def quote(session: AsyncSession, sale_id: UUID) -> MixedCartQuoteV2:
    context = runtime(session)
    return await UnitCartQuoteService(UnitCartRepository(session)).quote(
        tenant_id=context.stock.tenant_id,
        sale_id=sale_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
    )


async def test_mixed_quote_preserves_fractional_legacy_and_explicit_units(
    db_session: AsyncSession,
) -> None:
    sale = await seed_cart_draft(db_session)
    context = runtime(db_session)
    legacy = await add_legacy(db_session, sale)
    await put_cart_item(db_session, sale.id, discount=Decimal("0.01"))
    await UnitCartService(UnitCartRepository(db_session)).upsert_item(
        tenant_id=context.stock.tenant_id,
        sale_id=sale.id,
        item_id=uuid4(),
        catalog_id=context.stock.catalog_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        position=2,
        sale_format="pack",
        quantity=2,
    )
    before = await db_session.scalar(
        text("SELECT count(*) FROM unit_stock_entry WHERE tenant_id=:tenant"),
        {"tenant": context.stock.tenant_id},
    )
    result = await quote(db_session, sale.id)
    assert [line.inventory_kind for line in result.lines] == [
        "legacy_pack",
        "prepared_unit",
        "prepared_unit",
    ]
    assert [line.quantity for line in result.lines] == [Decimal("0.001"), 1, 2]
    assert [line.position for line in result.lines] == [1, 2, 3]
    assert result.lines[0].id == legacy.id
    assert result.lines[0].net_amount == Decimal("0.02")
    assert result.total_amount == Decimal("51.01")
    assert MixedCartQuoteV2.model_validate_json(result.model_dump_json()) == result
    assert await db_session.scalar(
        text("SELECT total_amount FROM sale WHERE id=:sale"), {"sale": sale.id}
    ) == Decimal("0.02")
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM unit_stock_entry WHERE tenant_id=:tenant"),
            {"tenant": context.stock.tenant_id},
        )
        == before
    )
    for table in ("sale_payment", "pos_payment_attempt"):
        assert (
            await db_session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE sale_id=:sale"), {"sale": sale.id}
            )
            == 0
        )
    for table in ("pos_unit_sale_line", "pos_unit_sale_command"):
        assert (
            await db_session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id=:tenant"),
                {"tenant": context.stock.tenant_id},
            )
            == 0
        )
    assert "cost" not in result.model_dump_json() and "purchase" not in result.model_dump_json()


async def test_quote_reprices_only_unit_intents_and_detects_changes(
    db_session: AsyncSession,
) -> None:
    sale = await seed_cart_draft(db_session)
    context = runtime(db_session)
    await add_legacy(db_session, sale)
    item_id = await put_cart_item(db_session, sale.id)
    first = await quote(db_session, sale.id)
    assert (await quote(db_session, sale.id)).revision_hash == first.revision_hash
    await db_session.execute(
        text(
            "UPDATE tenant_catalog SET base_price=100,unit_sale_preparation="
            "jsonb_set(unit_sale_preparation,'{unit_price}','\"2.00\"') WHERE id=:id"
        ),
        {"id": context.stock.catalog_id},
    )
    second = await quote(db_session, sale.id)
    assert second.total_amount == Decimal("2.02")
    assert second.lines[0] == first.lines[0]
    assert second.revision_hash != first.revision_hash
    assert first.total_amount == Decimal("1.02")
    await put_cart_item(db_session, sale.id, item_id=item_id, quantity=2)
    assert (await quote(db_session, sale.id)).revision_hash != second.revision_hash


@pytest.mark.parametrize("damage", ["legacy_total", "unit_step", "inactive"])
async def test_quote_rejects_stale_or_inconsistent_retail_facts(
    db_session: AsyncSession, damage: str
) -> None:
    sale = await seed_cart_draft(db_session)
    context = runtime(db_session)
    legacy = await add_legacy(db_session, sale)
    await put_cart_item(db_session, sale.id)
    if damage == "legacy_total":
        await db_session.execute(
            text("UPDATE sale_item SET total_price=0.03 WHERE id=:id"), {"id": legacy.id}
        )
    elif damage == "unit_step":
        await db_session.execute(
            text(
                "UPDATE tenant_catalog SET unit_sale_preparation="
                "jsonb_set(unit_sale_preparation,'{dispense_step}','2') WHERE id=:id"
            ),
            {"id": context.stock.catalog_id},
        )
    else:
        await db_session.execute(
            text("UPDATE tenant_catalog SET is_active=false WHERE id=:id"),
            {"id": context.stock.catalog_id},
        )
    with pytest.raises(BusinessRuleError):
        await quote(db_session, sale.id)


async def test_quote_rejects_empty_cart(db_session: AsyncSession) -> None:
    sale = await seed_cart_draft(db_session)
    with pytest.raises(BusinessRuleError):
        await quote(db_session, sale.id)


async def test_quote_is_actual_cashier_and_branch_scoped(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> None:
    context = runtime(db_session)
    other = await seed_receiving_actor(db_engine, maintenance_engine, context.stock)
    sale = await seed_cart_draft(db_session)
    await put_cart_item(db_session, sale.id)
    service = UnitCartQuoteService(UnitCartRepository(db_session))
    with pytest.raises(PermissionDeniedError):
        await service.quote(
            tenant_id=context.stock.tenant_id,
            sale_id=sale.id,
            actor_id=context.actor_id,
            allowed_branch_ids={uuid4()},
        )
    await set_receiving_context(db_session, context.stock, other)
    with pytest.raises(PermissionDeniedError):
        await service.quote(
            tenant_id=context.stock.tenant_id,
            sale_id=sale.id,
            actor_id=other,
            allowed_branch_ids={context.stock.branch_id},
        )
    with pytest.raises(PermissionDeniedError):
        await quote(db_session, sale.id)


async def test_payment_activity_prevents_new_quote(db_session: AsyncSession) -> None:
    sale = await seed_cart_draft(db_session)
    await add_legacy(db_session, sale)
    context = runtime(db_session)
    await db_session.execute(
        text(
            "INSERT INTO sale_payment(id,tenant_id,sale_id,payment_method,amount) "
            "VALUES(:id,:tenant,:sale,'cash',0.02)"
        ),
        {"id": uuid4(), "tenant": context.stock.tenant_id, "sale": sale.id},
    )
    with pytest.raises(ConflictError):
        await quote(db_session, sale.id)


async def test_quote_money_ignores_ambient_decimal_precision(db_session: AsyncSession) -> None:
    sale = await seed_cart_draft(db_session)
    await add_legacy(db_session, sale)
    await put_cart_item(db_session, sale.id, quantity=7, discount=Decimal("0.01"))
    with localcontext() as decimal_context:
        decimal_context.prec = 2
        result = await quote(db_session, sale.id)
    assert result.total_amount == Decimal("7.01")


async def test_quote_blocks_direct_child_changes_until_transaction_ends(
    db_session: AsyncSession,
) -> None:
    sale = await seed_cart_draft(db_session)
    context = runtime(db_session)
    item = await add_legacy(db_session, sale)
    await put_cart_item(db_session, sale.id)
    await db_session.commit()
    ready, attempting, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def reader() -> MixedCartQuoteV2:
        async with context.factory() as session:
            await set_receiving_context(session, context.stock, context.actor_id)
            session.info["receiving_runtime"] = context
            result = await quote(session, sale.id)
            ready.set()
            await release.wait()
            await session.rollback()
            return result

    async def writer() -> None:
        await ready.wait()
        async with context.factory() as session:
            await set_receiving_context(session, context.stock, context.actor_id)
            attempting.set()
            await session.execute(
                text("UPDATE sale_item SET qty=0.002,total_price=0.05 WHERE id=:id"),
                {"id": item.id},
            )
            await session.commit()

    reading = asyncio.create_task(reader())
    writing = asyncio.create_task(writer())
    try:
        await asyncio.wait_for(attempting.wait(), timeout=5)
        await asyncio.sleep(0.05)
        assert not writing.done()
    finally:
        release.set()
    result, _ = await asyncio.wait_for(asyncio.gather(reading, writing), timeout=10)
    assert result.lines[0].quantity == Decimal("0.001")
    assert result.total_amount == Decimal("1.02")
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert (await quote(db_session, sale.id)).total_amount == Decimal("1.05")
