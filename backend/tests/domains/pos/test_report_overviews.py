from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService


async def _local_today(db: AsyncSession) -> date:
    return (
        await db.execute(text("SELECT (now() AT TIME ZONE 'Asia/Dushanbe')::date"))
    ).scalar_one()


async def test_top_products_uses_net_quantity_and_revenue(
    db_session: AsyncSession, pos_scaffold
) -> None:
    scaffold = await pos_scaffold(sale_price=Decimal("10"), batch_qty=20)
    service = POSService(POSRepository(db_session))
    await service.open_shift(
        tenant_id=scaffold["tenant"].id,
        register_id=scaffold["register"].id,
        opened_by_user_id=scaffold["cashier"].id,
        opening_cash=Decimal("0"),
    )
    sale = await service.create_sale(
        tenant_id=scaffold["tenant"].id,
        register_id=scaffold["register"].id,
        cashier_user_id=scaffold["cashier"].id,
    )
    items, _ = await service.add_item(
        sale_id=sale.id,
        catalog_id=scaffold["item"].id,
        qty=Decimal("2"),
    )
    await service.add_payment(
        sale_id=sale.id,
        payment_method="cash",
        amount=Decimal("20"),
    )
    await service.complete(sale_id=sale.id)
    await service.refund(
        parent_sale_id=sale.id,
        items=[(items[0].id, Decimal("1"))],
        reason="quality_issue",
        comment=None,
        cashier_user_id=scaffold["cashier"].id,
    )

    today = await _local_today(db_session)
    report = await service.get_top_products_overview(
        tenant_id=scaffold["tenant"].id,
        date_from=today,
        date_to=today,
        branch_id=scaffold["branch"].id,
        sort_by="revenue",
        limit=20,
    )

    assert report.branch_name == scaffold["branch"].name
    assert len(report.rows) == 1
    assert report.rows[0].catalog_id == scaffold["item"].id
    assert report.rows[0].quantity == Decimal("1")
    assert report.rows[0].revenue == Decimal("10.00")
    assert report.rows[0].receipts_count == 1


async def test_stock_overview_filters_and_paginates(db_session: AsyncSession, pos_scaffold) -> None:
    scaffold = await pos_scaffold(batch_qty=Decimal("12"))
    scaffold["item"].brand_name = "Парацетамол тестовый"
    today = await _local_today(db_session)
    scaffold["batch"].expires_at = today
    await db_session.flush()
    service = POSService(POSRepository(db_session))

    report = await service.get_stock_on_date_overview(
        tenant_id=scaffold["tenant"].id,
        on_date=today,
        branch_id=scaffold["branch"].id,
        query="парацетамол",
        expires_within_days=0,
        page=1,
        page_size=10,
    )

    assert report.total == 1
    assert report.total_qty == Decimal("12")
    assert len(report.rows) == 1
    assert report.rows[0].name == "Парацетамол тестовый"

    empty_page = await service.get_stock_on_date_overview(
        tenant_id=scaffold["tenant"].id,
        on_date=today,
        branch_id=scaffold["branch"].id,
        query="парацетамол",
        expires_within_days=0,
        page=2,
        page_size=10,
    )
    assert empty_page.total == 1
    assert empty_page.rows == []
