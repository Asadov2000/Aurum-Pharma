"""Sales-summary aggregates, range filtering, the inverted-range guard, and the
empty-period file."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessRuleError
from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService


def _today():  # type: ignore[no-untyped-def]
    return datetime.now(UTC).date()


async def _complete_sale(service: POSService, s, *, qty, payments):  # type: ignore[no-untyped-def]
    sale = await service.create_sale(
        tenant_id=s["tenant"].id, register_id=s["register"].id, cashier_user_id=s["cashier"].id
    )
    created, _ = await service.add_item(sale_id=sale.id, catalog_id=s["item"].id, qty=qty)
    for method, amount in payments:
        await service.add_payment(sale_id=sale.id, payment_method=method, amount=amount)
    await service.complete(sale_id=sale.id)
    return sale, created


async def test_sales_summary_aggregates_and_breakdown(
    db_session: AsyncSession, pos_scaffold
) -> None:
    s = await pos_scaffold(sale_price=Decimal("10"), batch_qty=100)
    service = POSService(POSRepository(db_session))
    await service.open_shift(
        tenant_id=s["tenant"].id,
        register_id=s["register"].id,
        opened_by_user_id=s["cashier"].id,
        opening_cash=Decimal("0"),
    )

    sale_a, created_a = await _complete_sale(
        service, s, qty=Decimal("2"), payments=[("cash", Decimal("20"))]
    )
    await _complete_sale(service, s, qty=Decimal("1"), payments=[("card", Decimal("10"))])
    await _complete_sale(
        service, s, qty=Decimal("1"), payments=[("cash", Decimal("5")), ("card", Decimal("5"))]
    )
    await service.refund(
        parent_sale_id=sale_a.id,
        items=[(created_a[0].id, Decimal("1"))],
        reason="defect",
        comment=None,
        cashier_user_id=s["cashier"].id,
    )

    today = _today()
    data = await service.build_sales_summary(
        tenant_id=s["tenant"].id, date_from=today, date_to=today, branch_id=None
    )

    assert data.gross_sales == Decimal("40.00")
    assert data.total_discounts == Decimal("0")
    assert data.total_refunds == Decimal("10.00")
    assert data.net == Decimal("30.00")  # 40 - 0 - 10
    assert data.sales_count == 3
    assert data.returns_count == 1

    assert data.payment_breakdown.cash == Decimal("20.00")
    assert data.payment_breakdown.card == Decimal("10.00")
    assert data.payment_breakdown.mixed == Decimal("10.00")
    assert data.payment_breakdown.bank_transfer == Decimal("0")

    # 3 sales + 1 return row, returns shown separately (not folded into sales).
    assert len(data.rows) == 4
    kinds = sorted(r.kind for r in data.rows)
    assert kinds == ["return", "sale", "sale", "sale"]


async def test_sales_summary_range_excludes_sales_outside_period(
    db_session: AsyncSession, pos_scaffold
) -> None:
    s = await pos_scaffold(sale_price=Decimal("10"))
    service = POSService(POSRepository(db_session))
    await service.open_shift(
        tenant_id=s["tenant"].id,
        register_id=s["register"].id,
        opened_by_user_id=s["cashier"].id,
        opening_cash=Decimal("0"),
    )
    await _complete_sale(service, s, qty=Decimal("1"), payments=[("cash", Decimal("10"))])

    today = _today()
    # A window entirely before today must not see today's sale.
    past = await service.build_sales_summary(
        tenant_id=s["tenant"].id,
        date_from=today - timedelta(days=10),
        date_to=today - timedelta(days=1),
        branch_id=None,
    )
    assert past.gross_sales == Decimal("0")
    assert past.sales_count == 0
    assert past.rows == []

    # Today's window sees it.
    now = await service.build_sales_summary(
        tenant_id=s["tenant"].id, date_from=today, date_to=today, branch_id=None
    )
    assert now.gross_sales == Decimal("10.00")
    assert now.sales_count == 1


async def test_sales_summary_rejects_inverted_range(db_session: AsyncSession, pos_scaffold) -> None:
    s = await pos_scaffold()
    service = POSService(POSRepository(db_session))
    today = _today()
    with pytest.raises(BusinessRuleError):
        await service.get_sales_summary_xlsx(
            tenant_id=s["tenant"].id,
            date_from=today,
            date_to=today - timedelta(days=1),
            branch_id=None,
        )


async def test_sales_summary_empty_period_is_valid_zero_file(
    db_session: AsyncSession, pos_scaffold
) -> None:
    s = await pos_scaffold()
    service = POSService(POSRepository(db_session))
    today = _today()

    data = await service.build_sales_summary(
        tenant_id=s["tenant"].id, date_from=today, date_to=today, branch_id=None
    )
    assert data.gross_sales == Decimal("0")
    assert data.sales_count == 0
    assert data.rows == []

    xlsx = await service.get_sales_summary_xlsx(
        tenant_id=s["tenant"].id, date_from=today, date_to=today, branch_id=None
    )
    assert xlsx[:2] == b"PK"  # valid workbook, not an error
    assert len(xlsx) > 500
