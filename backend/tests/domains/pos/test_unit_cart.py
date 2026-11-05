"""Unit cart drafts preserve legacy totals, payments and physical stock."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError, PermissionDeniedError
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.pos.models import Sale
from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService
from app.domains.pos.unit_cart_repository import UnitCartRepository
from app.domains.pos.unit_cart_service import UnitCartService
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.pos.test_unit_return import runtime, seed_return_source

db_session = receiving_session_fixture


async def seed_cart_draft(session: AsyncSession, *, step: int = 1) -> Sale:
    context = runtime(session)
    await seed_return_source(session, step=step)
    register = await FoundationService(FoundationRepository(session)).create_register(
        tenant_id=context.stock.tenant_id,
        fields={"branch_id": context.stock.branch_id, "name": "Unit cart test"},
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
    return await pos.create_sale(
        tenant_id=context.stock.tenant_id,
        register_id=register.id,
        cashier_user_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
    )


async def put_cart_item(
    session: AsyncSession,
    sale_id: UUID,
    *,
    item_id: UUID | None = None,
    quantity: int = 1,
    discount: Decimal = Decimal("0.00"),
) -> UUID:
    context = runtime(session)
    item = await UnitCartService(UnitCartRepository(session)).upsert_item(
        tenant_id=context.stock.tenant_id,
        sale_id=sale_id,
        item_id=item_id or uuid4(),
        catalog_id=context.stock.catalog_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        position=1,
        sale_format="unit",
        quantity=quantity,
        discount_amount=discount,
    )
    return item.id


async def test_editable_cart_never_allocates_stock_or_changes_legacy_totals(
    db_session: AsyncSession,
) -> None:
    sale = await seed_cart_draft(db_session)
    context = runtime(db_session)
    entries_before = await db_session.scalar(
        text("SELECT count(*) FROM unit_stock_entry WHERE tenant_id=:tenant"),
        {"tenant": context.stock.tenant_id},
    )
    item_id = await put_cart_item(db_session, sale.id)
    await put_cart_item(db_session, sale.id, item_id=item_id, quantity=2, discount=Decimal("0.01"))
    service = UnitCartService(UnitCartRepository(db_session))
    items = await service.list_items(
        tenant_id=context.stock.tenant_id,
        sale_id=sale.id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
    )
    assert (
        len(items) == 1 and items[0].quantity == 2 and items[0].discount_amount == Decimal("0.01")
    )
    assert await db_session.scalar(
        text("SELECT total_amount FROM sale WHERE id=:sale"), {"sale": sale.id}
    ) == Decimal("0.00")
    for table in ("sale_item", "sale_payment", "pos_payment_attempt"):
        assert (
            await db_session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE sale_id=:sale"), {"sale": sale.id}
            )
            == 0
        )
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM unit_stock_entry WHERE tenant_id=:tenant"),
            {"tenant": context.stock.tenant_id},
        )
        == entries_before
    )
    await service.remove_item(
        tenant_id=context.stock.tenant_id,
        sale_id=sale.id,
        item_id=item_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
    )
    assert (
        await service.list_items(
            tenant_id=context.stock.tenant_id,
            sale_id=sale.id,
            actor_id=context.actor_id,
            allowed_branch_ids={context.stock.branch_id},
        )
        == ()
    )
    with pytest.raises(NotFoundError):
        await service.remove_item(
            tenant_id=context.stock.tenant_id,
            sale_id=sale.id,
            item_id=item_id,
            actor_id=context.actor_id,
            allowed_branch_ids={context.stock.branch_id},
        )


@pytest.mark.parametrize("quantity", [True, 0, -1, 3])
async def test_cart_rejects_nonwhole_or_invalid_dispense_step(
    db_session: AsyncSession, quantity: int
) -> None:
    sale = await seed_cart_draft(db_session, step=2)
    with pytest.raises(BusinessRuleError):
        await put_cart_item(db_session, sale.id, quantity=quantity)
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM pos_unit_cart_item WHERE sale_id=:sale"), {"sale": sale.id}
        )
        == 0
    )


@pytest.mark.parametrize(
    "discount", [Decimal("-0.01"), Decimal("0.001"), Decimal("999.00"), Decimal("NaN")]
)
async def test_cart_rejects_invalid_or_excessive_discount(
    db_session: AsyncSession, discount: Decimal
) -> None:
    sale = await seed_cart_draft(db_session)
    with pytest.raises(BusinessRuleError):
        await put_cart_item(db_session, sale.id, discount=discount)


async def test_cart_rejects_branch_actor_and_closed_shift(db_session: AsyncSession) -> None:
    sale = await seed_cart_draft(db_session)
    context = runtime(db_session)
    service = UnitCartService(UnitCartRepository(db_session))
    with pytest.raises(PermissionDeniedError):
        await service.list_items(
            tenant_id=context.stock.tenant_id,
            sale_id=sale.id,
            actor_id=context.actor_id,
            allowed_branch_ids={uuid4()},
        )
    with pytest.raises(PermissionDeniedError):
        await service.list_items(
            tenant_id=context.stock.tenant_id,
            sale_id=sale.id,
            actor_id=uuid4(),
            allowed_branch_ids={context.stock.branch_id},
        )
    await db_session.execute(
        text("UPDATE shift SET status='closed',closed_at=now() WHERE id=:id"), {"id": sale.shift_id}
    )
    with pytest.raises(BusinessRuleError):
        await put_cart_item(db_session, sale.id)


async def test_cart_item_identity_cannot_move_to_another_draft(db_session: AsyncSession) -> None:
    sale = await seed_cart_draft(db_session)
    item_id = await put_cart_item(db_session, sale.id)
    context = runtime(db_session)
    second = await POSService(POSRepository(db_session)).create_sale(
        tenant_id=context.stock.tenant_id,
        register_id=sale.register_id,
        cashier_user_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
    )
    with pytest.raises(ConflictError):
        await put_cart_item(db_session, second.id, item_id=item_id)
