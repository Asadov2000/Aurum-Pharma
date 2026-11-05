"""Legacy discount and refund revenue stays exact while historical cost stays unknown."""

from collections.abc import Awaitable
from decimal import Decimal
from typing import Protocol
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.pos.profit_report_repository import ProfitReportRepository
from app.domains.pos.profit_report_service import ProfitReportService
from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService
from tests.domains.pos.test_sales_summary import _today
from tests.domains.pos.test_stock_on_date import _stock_reader


class Identified(Protocol):
    id: UUID


class Scaffold(Protocol):
    def __call__(self, *, sale_price: int, batch_qty: int) -> Awaitable[dict[str, Identified]]: ...


@pytest.mark.parametrize("refunded", [False, True])
async def test_discounted_legacy_full_refund_keeps_voided_sale_and_unknown_cost(
    db_session: AsyncSession, pos_scaffold: Scaffold, refunded: bool
) -> None:
    entities = await pos_scaffold(sale_price=10, batch_qty=10)
    tenant_id = entities["tenant"].id
    service = POSService(POSRepository(db_session))
    shift = await service.open_shift(
        tenant_id=tenant_id,
        register_id=entities["register"].id,
        opened_by_user_id=entities["cashier"].id,
        opening_cash=Decimal("0"),
    )
    sale = await service.create_sale(
        tenant_id=tenant_id,
        register_id=entities["register"].id,
        cashier_user_id=entities["cashier"].id,
    )
    items, _ = await service.add_item(
        sale_id=sale.id, catalog_id=entities["item"].id, qty=Decimal("3")
    )
    await service.repo.update_item(
        items[0], total_price=Decimal("25.00"), discount_amount=Decimal("5.00")
    )
    await service.repo.update_sale(sale, total_amount=Decimal("25.00"))
    await service.add_payment(sale_id=sale.id, payment_method="cash", amount=Decimal("25"))
    await service.complete(sale_id=sale.id)
    if refunded:
        await service.refund(
            parent_sale_id=sale.id,
            items=[(items[0].id, Decimal("3"))],
            reason="other",
            comment=None,
            cashier_user_id=entities["cashier"].id,
        )
    await _stock_reader(db_session, tenant_id)
    day = await _today(db_session)
    result = await ProfitReportService(ProfitReportRepository(db_session)).read(
        tenant_id=tenant_id, date_from=day, date_to=day, branch_id=entities["branch"].id
    )
    summary = await service.get_sales_summary_overview(
        tenant_id=tenant_id, date_from=day, date_to=day, branch_id=entities["branch"].id
    )
    z = await service.build_z_report(shift.id)
    assert result.sales_revenue == summary.gross_sales - summary.total_discounts == 25
    assert result.refunds == summary.total_refunds == (25 if refunded else 0)
    assert result.net_revenue == summary.net
    assert result.net_revenue == z.total_sales - z.total_discounts - z.total_refunds
    assert result.total == (2 if refunded else 1)
    assert result.cost_status == "partial" and result.gross_profit is None
    assert result.net_sales_cost is None and result.known_net_sales_cost == 0
    assert result.known_gross_profit == 0
    assert all(row.cost_status == "unknown" and row.cost is None for row in result.rows)
    assert all(row.source_kind == "legacy" for row in result.rows)
