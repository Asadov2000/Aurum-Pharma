"""Local-day boundary tests for the sargable date filters (list_sales,
sales_summary, stock_on_date, dashboard.today_sales). Asia/Dushanbe is UTC+5,
so local day D = [D-1 19:00Z, D 19:00Z). We pin completed_at/created_at to the
four edge instants and assert in/out matches the old ::date semantics exactly.

Sales are immutable after completion, so tests inject the completion clock
instead of rewriting finalized rows."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.dashboard.repository import DashboardRepository
from app.domains.inventory.repository import InventoryRepository
from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService

TZ = "Asia/Dushanbe"  # UTC+5, no DST
DAY = date(2026, 3, 15)

# Edge instants in UTC for the local day DAY (UTC+5):
PREV_2359 = datetime(2026, 3, 14, 18, 59, tzinfo=UTC)  # 23:59 local of DAY-1 → OUT
DAY_0001 = datetime(2026, 3, 14, 19, 1, tzinfo=UTC)  # 00:01 local of DAY     → IN
DAY_2359 = datetime(2026, 3, 15, 18, 59, tzinfo=UTC)  # 23:59 local of DAY    → IN
NEXT_0001 = datetime(2026, 3, 15, 19, 1, tzinfo=UTC)  # 00:01 local of DAY+1  → OUT


async def _open_shift(service: POSService, s) -> None:  # type: ignore[no-untyped-def]
    try:
        await service.open_shift(
            tenant_id=s["tenant"].id,
            register_id=s["register"].id,
            opened_by_user_id=s["cashier"].id,
            opening_cash=Decimal("0"),
        )
    except Exception:
        pass  # already open


async def _complete_sale_at(  # type: ignore[no-untyped-def]
    service: POSService,
    s,
    ts: datetime,
):
    sale = await service.create_sale(
        tenant_id=s["tenant"].id,
        register_id=s["register"].id,
        cashier_user_id=s["cashier"].id,
    )
    await service.add_item(sale_id=sale.id, catalog_id=s["item"].id, qty=Decimal(1))
    await service.add_payment(sale_id=sale.id, payment_method="cash", amount=Decimal(10))
    timed_service = POSService(service.repo, now=lambda value=ts: value)
    await timed_service.complete(sale_id=sale.id)
    return sale


async def test_list_sales_local_day_boundaries(db_session: AsyncSession, pos_scaffold) -> None:
    s = await pos_scaffold(batch_qty=100, sale_price=10)
    service = POSService(POSRepository(db_session))
    await _open_shift(service, s)
    for ts in (PREV_2359, DAY_0001, DAY_2359, NEXT_0001):
        await _complete_sale_at(service, s, ts)

    _, total = await POSRepository(db_session).list_sales(
        tenant_id=s["tenant"].id,
        cashier_id=None,
        branch_id=None,
        register_id=None,
        receipt_number=None,
        date_from=DAY,
        date_to=DAY,
        has_refund=None,
        min_total=None,
        max_total=None,
        page=1,
        page_size=50,
        tz=TZ,
    )
    # Only the two inside the local day (00:01 and 23:59 of DAY) count.
    assert total == 2


async def test_sales_summary_local_day_boundaries(db_session: AsyncSession, pos_scaffold) -> None:
    s = await pos_scaffold(batch_qty=100, sale_price=10)
    service = POSService(POSRepository(db_session))
    await _open_shift(service, s)
    for ts in (PREV_2359, DAY_0001, DAY_2359, NEXT_0001):
        await _complete_sale_at(service, s, ts)

    summary = await POSRepository(db_session).sales_summary(
        tenant_id=s["tenant"].id,
        date_from=DAY,
        date_to=DAY,
        branch_id=None,
        tz=TZ,
    )
    assert summary["sales_count"] == 2


async def test_stock_on_date_excludes_next_local_day(
    db_session: AsyncSession, pos_scaffold
) -> None:
    s = await pos_scaffold(batch_qty=100, sale_price=10)
    inv = InventoryRepository(db_session)
    batch = s["batch"]
    # A receipt inside DAY (counts) and one just into DAY+1 local (must not).
    for ts, qty in ((DAY_2359, Decimal(10)), (NEXT_0001, Decimal(5))):
        mv = await inv.insert_movement(
            tenant_id=s["tenant"].id,
            batch_id=batch.id,
            movement_type="correction",
            qty_delta=qty,
            source_table=None,
            source_id=None,
        )
        await db_session.execute(
            text("UPDATE batch_movement SET created_at = :ts WHERE id = :id"),
            {"ts": ts, "id": mv.id},
        )
    await db_session.flush()

    rows = await POSRepository(db_session).stock_on_date(
        tenant_id=s["tenant"].id, on_date=DAY, branch_id=None, tz=TZ
    )
    # Scaffold's incoming (qty 100) is dated "now" (far after DAY) → also excluded;
    # only the +10 movement on DAY counts.
    assert len(rows) == 1
    assert Decimal(str(rows[0]["qty"])) == Decimal("10")


async def test_today_sales_counts_only_local_today(db_session: AsyncSession, pos_scaffold) -> None:
    s = await pos_scaffold(batch_qty=100, sale_price=10)
    service = POSService(POSRepository(db_session))
    await _open_shift(service, s)

    today_local = datetime.now(ZoneInfo(TZ)).date()
    today_noon = datetime.combine(today_local, time(12, 0), ZoneInfo(TZ)).astimezone(UTC)
    yesterday_noon = datetime.combine(
        today_local - timedelta(days=1), time(12, 0), ZoneInfo(TZ)
    ).astimezone(UTC)

    await _complete_sale_at(service, s, today_noon)
    await _complete_sale_at(service, s, yesterday_noon)

    row = await DashboardRepository(db_session).today_sales(s["tenant"].id, tz=TZ)
    assert int(row["receipts"]) == 1
    assert Decimal(str(row["revenue"])) == Decimal("10")
