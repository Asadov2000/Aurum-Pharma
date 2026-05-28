"""GET /sales listing: filters, pagination, tenant isolation, has_refund."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService


async def _sell(service: POSService, s, qty: int = 1):  # type: ignore[no-untyped-def]
    """Open a shift (idempotent per register) and complete one sale; returns it."""
    try:
        await service.open_shift(
            tenant_id=s["tenant"].id,
            register_id=s["register"].id,
            opened_by_user_id=s["cashier"].id,
            opening_cash=Decimal("0"),
        )
    except Exception:
        pass  # shift already open from a prior call in the same test
    sale = await service.create_sale(
        tenant_id=s["tenant"].id,
        register_id=s["register"].id,
        cashier_user_id=s["cashier"].id,
    )
    await service.add_item(sale_id=sale.id, catalog_id=s["item"].id, qty=Decimal(qty))
    await service.add_payment(sale_id=sale.id, payment_method="cash", amount=Decimal(qty * 10))
    return await service.complete(sale_id=sale.id)


async def test_list_returns_completed_sale_with_resolved_names(
    db_session: AsyncSession, pos_scaffold
) -> None:
    s = await pos_scaffold(sale_price=10, batch_qty=20)
    service = POSService(POSRepository(db_session))
    await _sell(service, s, qty=2)

    rows, total = await service.list_sales(
        tenant_id=s["tenant"].id,
        cashier_id=None,
        branch_id=None,
        register_id=None,
        receipt_number=None,
        date_from=None,
        date_to=None,
        has_refund=None,
        min_total=None,
        max_total=None,
        page=1,
        page_size=50,
    )
    assert total == 1
    row = rows[0]
    assert row["branch_name"] == s["branch"].name
    assert row["register_name"] == s["register"].name
    assert row["cashier_name"] == s["cashier"].full_name
    assert row["payment_methods"] == ["cash"]
    assert row["is_refund"] is False
    assert row["has_refund"] is False
    assert "x2" in row["items_summary"]


async def test_filter_by_receipt_number_is_exact(db_session: AsyncSession, pos_scaffold) -> None:
    s = await pos_scaffold(sale_price=10, batch_qty=20)
    service = POSService(POSRepository(db_session))
    sale = await _sell(service, s)

    rows, total = await service.list_sales(
        tenant_id=s["tenant"].id,
        cashier_id=None,
        branch_id=None,
        register_id=None,
        receipt_number=sale.receipt_number,
        date_from=None,
        date_to=None,
        has_refund=None,
        min_total=None,
        max_total=None,
        page=1,
        page_size=50,
    )
    assert total == 1
    assert rows[0]["receipt_number"] == sale.receipt_number

    # A non-matching number returns nothing.
    _, total_none = await service.list_sales(
        tenant_id=s["tenant"].id,
        cashier_id=None,
        branch_id=None,
        register_id=None,
        receipt_number="999999",
        date_from=None,
        date_to=None,
        has_refund=None,
        min_total=None,
        max_total=None,
        page=1,
        page_size=50,
    )
    assert total_none == 0


async def test_filter_by_date_range(db_session: AsyncSession, pos_scaffold) -> None:
    s = await pos_scaffold(sale_price=10, batch_qty=20)
    service = POSService(POSRepository(db_session))
    await _sell(service, s)

    today = date.today()
    # Today is in range.
    _, in_range = await service.list_sales(
        tenant_id=s["tenant"].id,
        cashier_id=None,
        branch_id=None,
        register_id=None,
        receipt_number=None,
        date_from=today,
        date_to=today,
        has_refund=None,
        min_total=None,
        max_total=None,
        page=1,
        page_size=50,
    )
    assert in_range == 1
    # A past-only window excludes it.
    _, out_range = await service.list_sales(
        tenant_id=s["tenant"].id,
        cashier_id=None,
        branch_id=None,
        register_id=None,
        receipt_number=None,
        date_from=today - timedelta(days=10),
        date_to=today - timedelta(days=5),
        has_refund=None,
        min_total=None,
        max_total=None,
        page=1,
        page_size=50,
    )
    assert out_range == 0


async def test_pagination(db_session: AsyncSession, pos_scaffold) -> None:
    s = await pos_scaffold(sale_price=10, batch_qty=50)
    service = POSService(POSRepository(db_session))
    for _ in range(3):
        await _sell(service, s)

    page1, total = await service.list_sales(
        tenant_id=s["tenant"].id,
        cashier_id=None,
        branch_id=None,
        register_id=None,
        receipt_number=None,
        date_from=None,
        date_to=None,
        has_refund=None,
        min_total=None,
        max_total=None,
        page=1,
        page_size=2,
    )
    assert total == 3
    assert len(page1) == 2
    page2, _ = await service.list_sales(
        tenant_id=s["tenant"].id,
        cashier_id=None,
        branch_id=None,
        register_id=None,
        receipt_number=None,
        date_from=None,
        date_to=None,
        has_refund=None,
        min_total=None,
        max_total=None,
        page=2,
        page_size=2,
    )
    assert len(page2) == 1


async def test_tenant_isolation(db_session: AsyncSession, pos_scaffold) -> None:
    s1 = await pos_scaffold(sale_price=10, batch_qty=20)
    s2 = await pos_scaffold(sale_price=10, batch_qty=20)
    service = POSService(POSRepository(db_session))
    await _sell(service, s1)

    # Querying tenant 2 must not see tenant 1's sale.
    _, total2 = await service.list_sales(
        tenant_id=s2["tenant"].id,
        cashier_id=None,
        branch_id=None,
        register_id=None,
        receipt_number=None,
        date_from=None,
        date_to=None,
        has_refund=None,
        min_total=None,
        max_total=None,
        page=1,
        page_size=50,
    )
    assert total2 == 0


async def test_has_refund_filter(db_session: AsyncSession, pos_scaffold) -> None:
    s = await pos_scaffold(sale_price=10, batch_qty=20)
    service = POSService(POSRepository(db_session))
    refunded_parent = await _sell(service, s, qty=2)
    await _sell(service, s, qty=1)  # a second sale with NO refund

    items = await service.repo.list_items(refunded_parent.id)
    await service.refund(
        parent_sale_id=refunded_parent.id,
        items=[(items[0].id, Decimal("1"))],
        reason="defect",
        comment=None,
        cashier_user_id=s["cashier"].id,
    )

    # has_refund=True → only the parent that now has a return.
    refunded_rows, refunded_total = await service.list_sales(
        tenant_id=s["tenant"].id,
        cashier_id=None,
        branch_id=None,
        register_id=None,
        receipt_number=None,
        date_from=None,
        date_to=None,
        has_refund=True,
        min_total=None,
        max_total=None,
        page=1,
        page_size=50,
    )
    assert refunded_total == 1
    assert str(refunded_rows[0]["id"]) == str(refunded_parent.id)
    assert refunded_rows[0]["has_refund"] is True
    assert refunded_rows[0]["refund_receipt_number"] is not None
