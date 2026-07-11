"""Refund flow — partial vs full, parent voiding, inventory return."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.inventory.repository import InventoryRepository
from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService


async def _open_shift_and_sell(db_session: AsyncSession, scaffold, qty: int):  # type: ignore[no-untyped-def]
    """Returns (service, scaffold_dict, completed_sale, first_item)."""
    s = await scaffold(sale_price=10, batch_qty=qty * 2)
    service = POSService(POSRepository(db_session))
    await service.open_shift(
        tenant_id=s["tenant"].id,
        register_id=s["register"].id,
        opened_by_user_id=s["cashier"].id,
        opening_cash=Decimal("0"),
    )
    sale = await service.create_sale(
        tenant_id=s["tenant"].id,
        register_id=s["register"].id,
        cashier_user_id=s["cashier"].id,
    )
    items, _ = await service.add_item(sale_id=sale.id, catalog_id=s["item"].id, qty=Decimal(qty))
    await service.add_payment(
        sale_id=sale.id,
        payment_method="cash",
        amount=Decimal(qty * 10),
    )
    completed = await service.complete(sale_id=sale.id)
    return service, s, completed, items[0]


async def test_partial_refund_does_not_void_parent(db_session: AsyncSession, pos_scaffold) -> None:
    service, s, parent, item = await _open_shift_and_sell(db_session, pos_scaffold, qty=5)
    inv_repo = InventoryRepository(db_session)
    batch_before = await inv_repo.get_batch(item.batch_id)
    assert batch_before is not None
    qty_before = batch_before.qty_remaining

    ret = await service.refund(
        parent_sale_id=parent.id,
        items=[(item.id, Decimal("2"))],
        reason="defect",
        comment=None,
        cashier_user_id=s["cashier"].id,
    )
    assert ret.sale_type == "return"
    assert ret.parent_sale_id == parent.id
    assert ret.status == "completed"
    return_items = await POSRepository(db_session).list_items(ret.id)
    assert return_items[0].parent_sale_item_id == item.id

    # Parent stays completed — partial refund
    await db_session.refresh(parent)
    assert parent.status == "completed"
    assert parent.voided_at is None

    # Inventory came back. session.get returns the cached object; the
    # trigger updated the DB, so refresh to see the new qty.
    batch_after = await inv_repo.get_batch(item.batch_id)
    assert batch_after is not None
    await db_session.refresh(batch_after)
    assert batch_after.qty_remaining == qty_before + Decimal("2.000")


async def test_full_refund_voids_parent(db_session: AsyncSession, pos_scaffold) -> None:
    service, s, parent, item = await _open_shift_and_sell(db_session, pos_scaffold, qty=3)

    ret = await service.refund(
        parent_sale_id=parent.id,
        items=[(item.id, item.qty)],
        reason="not_needed",
        comment=None,
        cashier_user_id=s["cashier"].id,
    )

    await db_session.refresh(parent)
    assert parent.status == "voided"
    assert parent.voided_at is not None
    assert parent.voided_by_sale_id == ret.id


async def test_voided_sale_receipt_number_is_never_reused(
    db_session: AsyncSession, pos_scaffold
) -> None:
    service, s, parent, item = await _open_shift_and_sell(db_session, pos_scaffold, qty=2)
    returned = await service.refund(
        parent_sale_id=parent.id,
        items=[(item.id, item.qty)],
        reason="full return",
        comment=None,
        cashier_user_id=s["cashier"].id,
    )

    next_sale = await service.create_sale(
        tenant_id=s["tenant"].id,
        register_id=s["register"].id,
        cashier_user_id=s["cashier"].id,
    )
    await service.add_item(
        sale_id=next_sale.id,
        catalog_id=s["item"].id,
        qty=Decimal("1"),
    )
    await service.add_payment(
        sale_id=next_sale.id,
        payment_method="cash",
        amount=Decimal("10"),
    )
    completed = await service.complete(sale_id=next_sale.id)

    assert parent.receipt_number == "000001"
    assert returned.receipt_number == "000002"
    assert completed.receipt_number == "000003"


async def test_refund_more_than_sold_blocked(db_session: AsyncSession, pos_scaffold) -> None:
    import pytest

    from app.core.errors import BusinessRuleError

    service, s, parent, item = await _open_shift_and_sell(db_session, pos_scaffold, qty=2)
    with pytest.raises(BusinessRuleError):
        await service.refund(
            parent_sale_id=parent.id,
            items=[(item.id, Decimal("99"))],
            reason="bug",
            comment=None,
            cashier_user_id=s["cashier"].id,
        )


async def test_duplicate_refund_lines_are_validated_as_one_quantity(
    db_session: AsyncSession, pos_scaffold
) -> None:
    import pytest

    from app.core.errors import BusinessRuleError

    service, s, parent, item = await _open_shift_and_sell(db_session, pos_scaffold, qty=3)
    with pytest.raises(BusinessRuleError):
        await service.refund(
            parent_sale_id=parent.id,
            items=[(item.id, Decimal("2")), (item.id, Decimal("2"))],
            reason="duplicate input",
            comment=None,
            cashier_user_id=s["cashier"].id,
        )


async def test_double_refund_tracks_already_refunded(
    db_session: AsyncSession, pos_scaffold
) -> None:
    import pytest

    from app.core.errors import BusinessRuleError

    service, s, parent, item = await _open_shift_and_sell(db_session, pos_scaffold, qty=4)
    # First refund: 2 of 4
    await service.refund(
        parent_sale_id=parent.id,
        items=[(item.id, Decimal("2"))],
        reason="one",
        comment=None,
        cashier_user_id=s["cashier"].id,
    )
    # Second refund: 2 more — fine
    await service.refund(
        parent_sale_id=parent.id,
        items=[(item.id, Decimal("2"))],
        reason="two",
        comment=None,
        cashier_user_id=s["cashier"].id,
    )
    # Now the parent is fully refunded
    await db_session.refresh(parent)
    assert parent.status == "voided"

    # A third refund must fail (nothing left)
    with pytest.raises(BusinessRuleError):
        await service.refund(
            parent_sale_id=parent.id,
            items=[(item.id, Decimal("1"))],
            reason="three",
            comment=None,
            cashier_user_id=s["cashier"].id,
        )
